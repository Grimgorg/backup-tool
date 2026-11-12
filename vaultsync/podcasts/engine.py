"""Podcasts sub-app logic: archives the episodes of the podcasts on the watch list into numbered folders.

Layout of the archive folder:   <archive folder>/<podcast name>/<NN>_<folder>/<YYYY-MM-DD> - <title>.mp3 (+ .txt notes)

Which folder an episode goes to is decided by the podcast's folder rules (first matching rule wins, patterns are
regular expressions matched against the episode title, case-insensitive). Episodes matching no rule get a
folder named after the start of their title. A folder's number follows the order in which its first episode
appeared and stays fixed after that (kept in the app data folder, state.json per podcast). If that state is
missing, existing numbered folders in the archive are picked up again.

The watch list (podcasts.json) is kept in the app's settings folder; see vaultsync.paths.
"""
import datetime
import email.utils
import html
import json
import os
import re
import shutil
import socket
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from vaultsync import APP_NAME, __version__, paths
from vaultsync.common import Cancelled, SyncError, format_size, load_json, save_json, write_atomic

USER_AGENT = 'Mozilla/5.0 (compatible) VaultSync/%s podcast-archiver' % __version__
CONFIG_FILE = os.path.join(paths.CONFIG_DIR, 'podcasts', 'podcasts.json')
STATE_ROOT = os.path.join(paths.DATA_DIR, 'podcasts')
LOCK_FILE = os.path.join(STATE_ROOT, 'lock')
NS = {
    'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd',
    'content': 'http://purl.org/rss/1.0/modules/content/',
}
EXTENSIONS = {
    'audio/mpeg': '.mp3', 'audio/mp3': '.mp3', 'audio/x-m4a': '.m4a', 'audio/mp4': '.m4a', 'audio/aac': '.aac',
    'audio/ogg': '.ogg', 'audio/opus': '.opus', 'video/mp4': '.mp4', 'video/quicktime': '.mov',
}
FOLDER_RE = re.compile(r'^(\d+)_(.+)$')


# --- names and text -------------------------------------------------------------------------------

def safe_name(text, max_len=150):
    """Makes a string usable as a file or folder name on Windows, macOS, Linux and exFAT."""
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'\s*\|\s*', ' - ', text)
    text = re.sub(r'\s*:\s*', ' - ', text)
    text = text.replace('"', "'").replace('/', '-').replace('\\', '-')
    text = re.sub(r'[?*<>\x00-\x1f]', '', text)
    text = re.sub(r'(\s*-\s*){2,}', ' - ', text)
    text = re.sub(r'\s+', ' ', text).strip(' .-')
    if len(text) > max_len:
        text = text[:max_len].rstrip(' .-')
    return text or 'Untitled'


def clean_title(title, podcast_name):
    """Drops a trailing "| <podcast name>" from episode titles."""
    title = re.sub(r'\s*\|\s*%s\s*$' % re.escape(podcast_name), '', title.strip(), flags=re.I)
    return re.sub(r'\s+', ' ', title).strip()


def html_to_text(s):
    if not s:
        return ''
    s = re.sub(r'(?i)<br\s*/?>', '\n', s)
    s = re.sub(r'(?i)</p\s*>|</li\s*>|</h\d\s*>', '\n', s)
    s = re.sub(r'(?i)<li[^>]*>', '- ', s)
    s = re.sub(r'<[^>]+>', '', s)
    s = html.unescape(s)
    s = re.sub(r'[ \t]+\n', '\n', s)
    return re.sub(r'\n{3,}', '\n\n', s).strip()


def fmt_duration(sec):
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return sec or '?'
    return '%d:%02d:%02d' % (sec // 3600, sec // 60 % 60, sec % 60)


def guess_folder(title):
    name = re.split(r'\s+\|\s+|\s+-\s+|\s+Folge\b|\s+Episode\b|:\s', title, maxsplit=1)[0]
    return name.strip() or title


def classify(title, rules):
    """Returns (folder, matched): the folder of the first rule with a matching pattern, or a guess."""
    for name, patterns in rules:
        if any(p.search(title) for p in patterns):
            return name, True
    return guess_folder(title), False


# --- watch list -----------------------------------------------------------------------------------

def default_config():
    return {'archive_dir': os.path.join(os.path.expanduser('~'), 'Podcasts'), 'podcasts': []}


def normalize_podcast(p):
    return {
        'name': p['name'].strip(),
        'feed': p['feed'].strip(),
        'enabled': bool(p.get('enabled', True)),
        'sessions': [{'name': s['name'].strip(), 'match': [m for m in s.get('match', []) if m.strip()]}
                     for s in p.get('sessions', [])],
    }


def load_config():
    cfg = default_config()
    cfg.update(load_json(CONFIG_FILE, {}))
    cfg['podcasts'] = [normalize_podcast(p) for p in cfg['podcasts']]
    return cfg


def save_config(cfg):
    save_json(CONFIG_FILE, {'archive_dir': cfg['archive_dir'], 'podcasts': cfg['podcasts']})


def validate_podcast(p, others):
    """Returns an error message for an invalid watch list entry, or None. others: the other entries."""
    name = p['name']
    if not name:
        return 'Please enter a name.'
    if safe_name(name) != name:
        return ('"{}" cannot be used as a folder name. Leave out characters like : / \\ ? * " < > | and '
                'leading or trailing dots, dashes and spaces.'.format(name))
    if any(o['name'].casefold() == name.casefold() for o in others):
        return 'A podcast named "{}" is already on the watch list.'.format(name)
    if not re.match(r'^https?://', p['feed'], re.I):
        return 'Please enter the address of the RSS feed (starting with http:// or https://).'
    for rule in p['sessions']:
        if not rule['name'] or safe_name(rule['name']) != rule['name']:
            return 'Folder name "{}" is not usable as a folder name.'.format(rule['name'])
        if not rule['match']:
            return 'Folder "{}" has no patterns.'.format(rule['name'])
        for pattern in rule['match']:
            try:
                re.compile(pattern)
            except re.error as e:
                return 'Pattern "{}" of folder "{}" is invalid: {}'.format(pattern, rule['name'], e)
    return None


def compile_rules(podcast):
    return [(s['name'], [re.compile(m, re.I) for m in s['match']]) for s in podcast['sessions']]


def import_legacy_config(path, cfg):
    """Adds the podcasts of a podcasts.json from the old archive.py script (or exported from this app) to cfg.
    The old file lived in the archive folder, so that folder becomes the archive folder if none was set yet.
    Returns (added names, skipped names with reasons)."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    added, skipped = [], []
    for raw in data.get('podcasts', []):
        try:
            p = normalize_podcast(raw)
        except (KeyError, AttributeError, TypeError):
            skipped.append('{} (incomplete entry)'.format(raw.get('name', '?') if isinstance(raw, dict) else '?'))
            continue
        error = validate_podcast(p, cfg['podcasts'])
        if error:
            skipped.append('{} ({})'.format(p['name'], error))
            continue
        cfg['podcasts'].append(p)
        added.append(p['name'])
    if data.get('archive_dir'):
        cfg['archive_dir'] = data['archive_dir']
    elif added and not os.path.isdir(cfg['archive_dir']):
        cfg['archive_dir'] = os.path.dirname(os.path.abspath(path))
    return added, skipped


def podcast_dir(cfg, podcast):
    return os.path.join(cfg['archive_dir'], podcast['name'])


def state_dir(name):
    return os.path.join(STATE_ROOT, safe_name(name))


def rename_state(old_name, new_name):
    old, new = state_dir(old_name), state_dir(new_name)
    if os.path.isdir(old) and not os.path.exists(new):
        os.rename(old, new)


def forget_state(name):
    shutil.rmtree(state_dir(name), ignore_errors=True)


def load_state(name):
    state = load_json(os.path.join(state_dir(name), 'state.json'), {})
    state.setdefault('sessions', {})
    state.setdefault('episodes', {})
    return state


def save_state(name, state):
    write_atomic(os.path.join(state_dir(name), 'state.json'),
                 json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))


# --- network --------------------------------------------------------------------------------------

def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def search_feeds(term, limit=25):
    """Finds podcasts by name in the Apple Podcasts directory. Returns dicts with name, author, feed."""
    query = urllib.parse.urlencode({'media': 'podcast', 'entity': 'podcast', 'limit': limit, 'term': term})
    with fetch('https://itunes.apple.com/search?' + query, timeout=20) as r:
        data = json.load(r)
    return [{'name': x.get('collectionName') or x.get('trackName') or '', 'author': x.get('artistName') or '',
             'feed': x['feedUrl']} for x in data.get('results', []) if x.get('feedUrl')]


def probe_feed(url):
    """Reads a feed and returns (title, number of episodes, newest episode title)."""
    with fetch(url, timeout=30) as r:
        root = ET.fromstring(r.read())
    channel = root.find('channel')
    if channel is None:
        raise SyncError('This address is not an RSS podcast feed.')
    items = [it for it in channel.findall('item') if it.find('enclosure') is not None]
    newest = items[0].findtext('title', '') if items else ''
    return (channel.findtext('title') or '').strip(), len(items), newest.strip()


# --- check ----------------------------------------------------------------------------------------

@dataclass
class Episode:
    guid: str
    title: str
    date: datetime.datetime
    url: str
    length: int
    link: str
    duration: str
    notes: str
    ext: str
    folder: str = ''
    rel: str = ''          # path below the podcast folder, always with "/"
    on_disk: bool = False


@dataclass
class PodcastCheck:
    podcast: dict
    error: str = None
    state: dict = None
    feed_data: bytes = None
    episodes: list = field(default_factory=list)
    new: list = field(default_factory=list)       # Episodes to download
    moves: list = field(default_factory=list)     # (Episode, old rel path): archived before under another folder
    unmatched: list = field(default_factory=list)  # Episodes no rule matched (only when the podcast has rules)
    new_folders: list = field(default_factory=list)

    @property
    def new_size(self):
        return sum(e.length for e in self.new)


@dataclass
class Plan:
    archive_dir: str
    checks: list

    @property
    def new(self):
        return [e for c in self.checks for e in c.new]

    @property
    def new_size(self):
        return sum(c.new_size for c in self.checks)

    @property
    def move_count(self):
        return sum(len(c.moves) for c in self.checks)

    @property
    def errors(self):
        return [c for c in self.checks if c.error]

    @property
    def empty(self):
        return not self.new and not self.move_count


def parse_feed(data, podcast_name):
    root = ET.fromstring(data)
    channel = root.find('channel')
    if channel is None:
        raise SyncError('The feed is not an RSS podcast feed.')
    episodes = []
    for it in channel.findall('item'):
        enc = it.find('enclosure')
        if enc is None or not enc.get('url'):
            continue
        url = enc.get('url')
        ext = EXTENSIONS.get((enc.get('type') or '').split(';')[0].strip().lower())
        if not ext:
            url_ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
            ext = url_ext if url_ext in EXTENSIONS.values() else '.mp3'
        try:
            date = email.utils.parsedate_to_datetime(it.findtext('pubDate'))
        except (TypeError, ValueError):
            continue  # without a date there is no stable file name
        try:
            length = int(enc.get('length') or 0)
        except ValueError:
            length = 0
        episodes.append(Episode(
            guid=(it.findtext('guid') or url).strip(),
            title=clean_title(it.findtext('title') or '', podcast_name),
            date=date, url=url, length=length,
            link=it.findtext('link') or '',
            duration=it.findtext('itunes:duration', namespaces=NS),
            notes=it.findtext('content:encoded', namespaces=NS) or it.findtext('description') or '',
            ext=ext))
    episodes.sort(key=lambda e: e.date)
    return episodes


def existing_folders(base):
    """Numbered folders already in a podcast's archive folder: {folder name (casefolded): number}."""
    found = {}
    if os.path.isdir(base):
        for name in os.listdir(base):
            m = FOLDER_RE.match(name)
            if m and os.path.isdir(os.path.join(base, name)):
                found.setdefault(m.group(2).casefold(), int(m.group(1)))
    return found


def folder_name(state, folder):
    return '%02d_%s' % (state['sessions'][folder], safe_name(folder))


def assign(podcast, state, episodes, base):
    """Sets folder and rel of each episode, numbering new folders. Returns (unmatched episodes, new folders)."""
    rules = compile_rules(podcast)
    on_disk = existing_folders(base)
    unmatched, new_folders = [], []
    for ep in episodes:
        ep.folder, matched = classify(ep.title, rules)
        if not matched:
            unmatched.append(ep)
        if ep.folder not in state['sessions']:
            taken = set(state['sessions'].values())
            number = on_disk.get(safe_name(ep.folder).casefold())
            if number is None or number in taken:
                number = max(taken | set(on_disk.values()) | {0}) + 1
                new_folders.append('%02d_%s' % (number, safe_name(ep.folder)))
            state['sessions'][ep.folder] = number
    used = set()
    for ep in episodes:
        stem = '%s - %s' % (ep.date.strftime('%Y-%m-%d'), safe_name(ep.title))
        base_rel = folder_name(state, ep.folder) + '/' + stem
        rel, n = base_rel + ep.ext, 1
        while rel.casefold() in used:  # same date and title twice: don't let one hide the other
            n += 1
            rel = '%s (%d)%s' % (base_rel, n, ep.ext)
        used.add(rel.casefold())
        ep.rel = rel
    return (unmatched if rules else []), new_folders


def notes_text(ep):
    return ('%s\n\nSession:  %s\nDatum:    %s\nDauer:    %s\nLink:     %s\nAudio:    %s\n\n%s\n'
            % (ep.title, ep.folder, ep.date.strftime('%Y-%m-%d %H:%M'), fmt_duration(ep.duration), ep.link,
               ep.url, html_to_text(ep.notes)))


# --- lock -----------------------------------------------------------------------------------------

LOCK_ID = '%s %d' % (socket.gethostname(), os.getpid())


def pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == 'nt':
        # Never os.kill on Windows: there it terminates the process.
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock():
    """Allows only one podcast download at a time, also across several app windows."""
    os.makedirs(STATE_ROOT, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, LOCK_ID.encode('utf-8'))
            os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(LOCK_FILE, encoding='utf-8') as f:
                    host, _, pid = f.read().strip().rpartition(' ')
                stale = host != socket.gethostname() or not pid_alive(int(pid))
            except (OSError, ValueError):
                stale = True
            if not stale:
                return False
            try:
                os.remove(LOCK_FILE)
            except FileNotFoundError:
                pass
    return False


def release_lock():
    try:
        with open(LOCK_FILE, encoding='utf-8') as f:
            mine = f.read().strip() == LOCK_ID
        if mine:
            os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass


# --- engine ---------------------------------------------------------------------------------------

class Engine:
    """Runs one operation at a time in a worker thread and reports through callbacks:
    log(line), status(text), progress(fraction or None)."""

    def __init__(self, log, status, progress):
        self._log_cb = log
        self.status = status
        self.progress = progress
        self._cancel = threading.Event()
        self._logfile = None

    def cancel(self):
        self._cancel.set()

    def _check_cancelled(self):
        if self._cancel.is_set():
            raise Cancelled()

    def log(self, line):
        if self._logfile:
            self._logfile.write(line + '\n')
            self._logfile.flush()
        self._log_cb(line)

    def _start(self, name):
        self._cancel.clear()
        path = os.path.join(paths.log_dir(), 'podcasts-{}-{:%Y-%m-%d_%H%M%S}.log'.format(
            name, datetime.datetime.now()))
        self._logfile = open(path, 'w', encoding='utf-8')

    def _end(self):
        self.progress(None)
        if self._logfile:
            self._logfile.close()
            self._logfile = None

    # --- check ------------------------------------------------------------------------------------

    def check(self, cfg, names=None):
        """Reads the feeds of the enabled podcasts (or of the podcasts named in names) and returns the Plan of
        what apply() would download and move. Saves only the folder numbering, in the app data folder."""
        podcasts = [p for p in cfg['podcasts'] if (p['name'] in names if names else p['enabled'])]
        self._start('check')
        try:
            checks = []
            for i, podcast in enumerate(podcasts):
                self._check_cancelled()
                self.status('Reading feed {} of {}: {}'.format(i + 1, len(podcasts), podcast['name']))
                self.progress(i / max(len(podcasts), 1))
                try:
                    checks.append(self._check_podcast(cfg, podcast))
                except Cancelled:
                    raise
                except Exception as e:  # one broken feed must not stop the others
                    self.log('ERROR: {}: {}'.format(podcast['name'], e))
                    checks.append(PodcastCheck(podcast, error=str(e) or e.__class__.__name__))
            return Plan(cfg['archive_dir'], checks)
        finally:
            self._end()

    def _check_podcast(self, cfg, podcast):
        name = podcast['name']
        base = podcast_dir(cfg, podcast)
        self.log('')
        self.log('> {}: {}'.format(name, podcast['feed']))
        with fetch(podcast['feed']) as r:
            data = r.read()
        episodes = parse_feed(data, name)
        state = load_state(name)
        known_folders = set(state['sessions'])
        unmatched, new_folders = assign(podcast, state, episodes, base)
        check = PodcastCheck(podcast, state=state, feed_data=data, episodes=episodes, unmatched=unmatched,
                             new_folders=new_folders)
        for ep in episodes:
            ep.on_disk = os.path.exists(os.path.join(base, ep.rel))
            rec = state['episodes'].get(ep.guid)
            if ep.on_disk:
                state['episodes'][ep.guid] = {'path': ep.rel, 'size': (rec or {}).get('size'), 'title': ep.title}
            elif rec and rec['path'] != ep.rel and os.path.exists(os.path.join(base, rec['path'])):
                check.moves.append((ep, rec['path']))
            else:
                check.new.append(ep)
        # Remember what is in the archive, but don't reserve numbers for folders that don't exist yet: they
        # are only fixed when something is downloaded into them (apply saves the full state).
        saved = dict(state, sessions={f: n for f, n in state['sessions'].items() if f in known_folders or
                                      os.path.isdir(os.path.join(base, folder_name(state, f)))})
        save_state(name, saved)

        self.log('{} episodes in the feed, {} in the archive, {} new.'.format(
            len(episodes), sum(e.on_disk for e in episodes), len(check.new)))
        for folder in new_folders:
            self.log('New folder: ' + folder)
        if check.moves:
            self.log('{} episode(s) are archived in another folder and will be moved.'.format(len(check.moves)))
        if unmatched:
            self.log('Note: {} episode(s) match no folder rule and get a folder named after their title:'
                     .format(len(unmatched)))
            for ep in unmatched:
                self.log('  {}  ->  {}'.format(ep.title, ep.folder))
        return check

    # --- apply ------------------------------------------------------------------------------------

    def apply(self, cfg, plan):
        """Moves and downloads what plan lists and refreshes the show notes. Returns (downloaded, moved,
        failed rel paths); episodes that failed are tried again next time."""
        self._start('download')
        if not acquire_lock():
            self._end()
            raise SyncError('Podcasts are already being downloaded by another {} window.'.format(APP_NAME))
        try:
            checks = [c for c in plan.checks if not c.error]
            sizes = [e.length for c in checks for e in c.new if e.length]
            fallback = (sum(sizes) // len(sizes)) if sizes else 50 * 1024 ** 2
            tracker = _Progress(sum(e.length or fallback for c in checks for e in c.new), fallback, self)
            tracker.count_total = sum(len(c.new) for c in checks)
            downloaded = moved = 0
            failed = []
            for check in checks:
                d, m, f = self._apply_podcast(cfg, check, tracker)
                downloaded, moved, failed = downloaded + d, moved + m, failed + f
            return downloaded, moved, failed
        finally:
            release_lock()
            self._end()

    def _apply_podcast(self, cfg, check, tracker):
        podcast, state = check.podcast, check.state
        name, base = podcast['name'], podcast_dir(cfg, podcast)
        self.log('')
        self.log('> ' + name)
        write_atomic(os.path.join(state_dir(name), 'feed.xml'), check.feed_data)
        save_state(name, state)

        moved = 0
        for ep, old_rel in check.moves:
            self._check_cancelled()
            dest, old = os.path.join(base, ep.rel), os.path.join(base, old_rel)
            if os.path.exists(dest) or not os.path.exists(old):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(old, dest)
            old_txt = os.path.splitext(old)[0] + '.txt'
            if os.path.exists(old_txt):
                os.remove(old_txt)
            old_dir = os.path.dirname(old)
            if os.path.isdir(old_dir) and not [n for n in os.listdir(old_dir) if not n.startswith('.')]:
                shutil.rmtree(old_dir)
            self.log('Moved: {} -> {}'.format(old_rel, ep.rel))
            state['episodes'][ep.guid] = {'path': ep.rel, 'size': os.path.getsize(dest), 'title': ep.title}
            moved += 1
        save_state(name, state)

        downloaded, failed = 0, []
        for ep in check.new:
            dest = os.path.join(base, ep.rel)
            if os.path.exists(dest):
                tracker.skip(ep)
                continue
            tracker.begin(ep, name)
            self.log('[{}] {}'.format(os.path.dirname(ep.rel), os.path.basename(ep.rel)))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            size = self._download(ep.url, dest, ep.length, tracker.update)
            tracker.end(ep)
            if size is None:
                failed.append('{}/{}'.format(name, ep.rel))
                continue
            downloaded += 1
            self._write_notes(base, ep)
            state['episodes'][ep.guid] = {'path': ep.rel, 'size': size, 'title': ep.title}
            save_state(name, state)

        # Show notes of episodes that were already there (they can change in the feed).
        self.status('Updating show notes: ' + name)
        for ep in check.episodes:
            if os.path.exists(os.path.join(base, ep.rel)):
                self._write_notes(base, ep)

        summary = '{:%Y-%m-%d %H:%M}: {} new, {} moved, {} failed, {} episodes in the archive.'.format(
            datetime.datetime.now(), downloaded, moved, len(failed),
            sum(1 for e in check.episodes if os.path.exists(os.path.join(base, e.rel))))
        with open(os.path.join(state_dir(name), 'history.log'), 'a', encoding='utf-8') as f:
            f.write(summary + '\n')
        self.log(summary)
        return downloaded, moved, failed

    @staticmethod
    def _write_notes(base, ep):
        path = os.path.splitext(os.path.join(base, ep.rel))[0] + '.txt'
        text = notes_text(ep)
        try:
            with open(path, encoding='utf-8') as f:
                if f.read() == text:
                    return
        except (FileNotFoundError, UnicodeDecodeError):
            pass
        write_atomic(path, text)

    def _download(self, url, dest, expected, on_bytes, retries=3):
        part = dest + '.part'
        for attempt in range(1, retries + 1):
            self._check_cancelled()
            try:
                with fetch(url, timeout=120) as r:
                    total = int(r.headers.get('Content-Length') or expected or 0)
                    done = 0
                    with open(part, 'wb') as f:
                        while True:
                            if self._cancel.is_set():
                                raise Cancelled()
                            chunk = r.read(1024 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            done += len(chunk)
                            on_bytes(done, total)
                if total and done != total:
                    raise IOError('incomplete ({} of {} bytes)'.format(done, total))
                os.replace(part, dest)
                return done
            except Cancelled:
                _remove(part)
                raise
            except Exception as e:  # network errors of any kind: retry
                self.log('    Error (attempt {}/{}): {}'.format(attempt, retries, e))
                _remove(part)
                if attempt < retries:
                    self._cancel.wait(3 * attempt)
        return None


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


class _Progress:
    """Turns per-episode byte counts into overall progress and a status line with speed and time left."""

    def __init__(self, total, fallback, engine):
        self.total = max(total, 1)
        self.fallback = fallback
        self.engine = engine
        self.done = 0           # bytes of finished episodes (estimated size if the feed gives none)
        self.count = 0
        self.count_total = 0
        self.current = 0
        self.started = time.monotonic()
        self.transferred = 0
        self.last = 0.0
        self.label = ''

    def _estimate(self, ep):
        return ep.length or self.fallback

    def begin(self, ep, podcast_name):
        self.count += 1
        self.current = 0
        self.label = 'Downloading {} of {} ({}): {}'.format(self.count, self.count_total, podcast_name, ep.title)
        self.engine.status(self.label)

    def update(self, done, total):
        self.transferred += done - self.current
        self.current = done
        now = time.monotonic()
        if now - self.last < 0.3:
            return
        self.last = now
        overall = self.done + min(done, total or done)
        self.engine.progress(min(1.0, overall / self.total))
        speed = self.transferred / max(now - self.started, 0.001)
        text = '{} - {}'.format(self.label, format_size(done))
        if total:
            text += ' of ' + format_size(total)
        if speed > 0:
            left = datetime.timedelta(seconds=int(max(self.total - overall, 0) / speed))
            text += ', {:.1f} MB/s, about {} left in total'.format(speed / 1024 ** 2, left)
        self.engine.status(text)

    def end(self, ep):
        self.done += self._estimate(ep)

    def skip(self, ep):
        self.count += 1
        self.done += self._estimate(ep)
