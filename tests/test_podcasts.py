import datetime
import email.utils
import functools
import http.server
import json
import os
import shutil
import tempfile
import threading
import unittest

from vaultsync import paths
from vaultsync.podcasts import engine as pc

AUDIO = {'/a1.mp3': b'A' * 3000, '/a2.mp3': b'B' * 5000, '/b1.mp3': b'C' * 1000, '/x.m4a': b'D' * 700}


def feed_xml(items):
    out = ['<?xml version="1.0"?><rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
           '<channel><title>Test Cast</title>']
    for i, (title, path, day, mime) in enumerate(items):
        date = email.utils.format_datetime(datetime.datetime(2024, 1, day, 12, tzinfo=datetime.timezone.utc))
        out.append('<item><title>{}</title><guid>g{}</guid><pubDate>{}</pubDate><description>&lt;p&gt;Notes {}'
                   '&lt;/p&gt;</description><enclosure url="{{base}}{}" length="{}" type="{}"/>'
                   '<itunes:duration>3725</itunes:duration></item>'
                   .format(title, i, date, i, path, len(AUDIO[path]), mime))
    out.append('</channel></rss>')
    return ''.join(out)


class Handler(http.server.BaseHTTPRequestHandler):
    feed = ''

    def do_GET(self):
        if self.path == '/feed.xml':
            body = self.feed.replace('{base}', 'http://127.0.0.1:%d' % self.server.server_port).encode()
        elif self.path in AUDIO:
            body = AUDIO[self.path]
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class NamesTest(unittest.TestCase):
    def test_safe_name_matches_existing_archive_names(self):
        self.assertEqual(pc.safe_name('T.E.A.R.S. | Teil 2: des Abenteuers'), 'T.E.A.R.S. - Teil 2 - des Abenteuers')
        self.assertEqual(pc.safe_name('Was? <Wer>*'), 'Was Wer')
        self.assertEqual(pc.safe_name(' ..- '), 'Untitled')

    def test_clean_title_drops_podcast_suffix(self):
        self.assertEqual(pc.clean_title('Folge 3 | Am Tavernentresen', 'Am Tavernentresen'), 'Folge 3')

    def test_classify_first_rule_wins_else_guess(self):
        podcast = pc.normalize_podcast({'name': 'P', 'feed': 'http://x', 'sessions': [
            {'name': 'Shadowrun', 'match': ['^Shadowrun']}, {'name': 'Oneshots', 'match': ['one[ -]?shot']}]})
        rules = pc.compile_rules(podcast)
        self.assertEqual(pc.classify('Shadowrun One-Shot #1', rules), ('Shadowrun', True))
        self.assertEqual(pc.classify('Ein Oneshot', rules), ('Oneshots', True))
        self.assertEqual(pc.classify('Coriolis - Folge 2', rules), ('Coriolis', False))

    def test_validate(self):
        good = pc.normalize_podcast({'name': 'P', 'feed': 'https://x/feed', 'sessions': []})
        self.assertIsNone(pc.validate_podcast(good, []))
        self.assertIn('already', pc.validate_podcast(good, [dict(good, name='p')]))
        self.assertIn('folder name', pc.validate_podcast(dict(good, name='A/B'), []))
        self.assertIn('RSS', pc.validate_podcast(dict(good, feed='feed.xml'), []))
        bad_rule = dict(good, sessions=[{'name': 'X', 'match': ['(']}])
        self.assertIn('invalid', pc.validate_podcast(bad_rule, []))


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='vault-sync-archive-')
        Handler.feed = feed_xml([('Alpha - Teil 1', '/a1.mp3', 1, 'audio/mpeg'),
                                 ('Beta: Start', '/b1.mp3', 2, 'audio/mpeg'),
                                 ('Alpha - Teil 2', '/a2.mp3', 3, 'audio/mpeg'),
                                 ('Video Special', '/x.m4a', 4, 'audio/x-m4a')])
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        feed = 'http://127.0.0.1:%d/feed.xml' % self.server.server_port
        self.cfg = {'archive_dir': self.archive, 'podcasts': [pc.normalize_podcast(
            {'name': 'Test Cast', 'feed': feed, 'sessions': [{'name': 'Alpha', 'match': ['^Alpha']}]})]}
        self.log = []
        self.engine = pc.Engine(log=self.log.append, status=lambda s: None, progress=lambda v: None)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.archive, ignore_errors=True)
        pc.forget_state('Test Cast')

    def files(self):
        base = os.path.join(self.archive, 'Test Cast')
        return sorted(os.path.relpath(os.path.join(r, f), base).replace(os.sep, '/')
                      for r, _, fs in os.walk(base) for f in fs)

    def test_check_download_recheck_and_move(self):
        plan = self.engine.check(self.cfg)
        self.assertEqual(plan.errors, [])
        self.assertEqual(len(plan.new), 4)
        self.assertEqual(plan.new_size, 9700)
        self.assertEqual(self.files(), [], 'check must not touch the archive')

        downloaded, moved, failed = self.engine.apply(self.cfg, plan)
        self.assertEqual((downloaded, moved, failed), (4, 0, []))
        self.assertEqual(self.files(), [
            '01_Alpha/2024-01-01 - Alpha - Teil 1.mp3', '01_Alpha/2024-01-01 - Alpha - Teil 1.txt',
            '01_Alpha/2024-01-03 - Alpha - Teil 2.mp3', '01_Alpha/2024-01-03 - Alpha - Teil 2.txt',
            '02_Beta/2024-01-02 - Beta - Start.mp3', '02_Beta/2024-01-02 - Beta - Start.txt',
            '03_Video Special/2024-01-04 - Video Special.m4a', '03_Video Special/2024-01-04 - Video Special.txt'])
        with open(os.path.join(self.archive, 'Test Cast', '01_Alpha', '2024-01-01 - Alpha - Teil 1.txt'),
                  encoding='utf-8') as f:
            notes = f.read()
        self.assertIn('Dauer:    1:02:05', notes)
        self.assertIn('Notes 0', notes)
        self.assertFalse(os.path.exists(pc.LOCK_FILE))

        self.assertTrue(self.engine.check(self.cfg).empty)

        # A new rule puts "Beta" episodes into a new folder: they are moved, not downloaded again.
        self.cfg['podcasts'][0]['sessions'].append({'name': 'Beta Arc', 'match': ['^Beta']})
        plan = self.engine.check(self.cfg)
        self.assertEqual((len(plan.new), plan.move_count), (0, 1))
        self.assertEqual(self.engine.apply(self.cfg, plan), (0, 1, []))
        self.assertIn('04_Beta Arc/2024-01-02 - Beta - Start.mp3', self.files())
        self.assertFalse(any(f.startswith('02_Beta/') for f in self.files()))

    def test_lost_state_picks_up_existing_folders(self):
        base = os.path.join(self.archive, 'Test Cast')
        os.makedirs(os.path.join(base, '07_Alpha'))
        os.makedirs(os.path.join(base, '02_Beta'))
        with open(os.path.join(base, '07_Alpha', '2024-01-01 - Alpha - Teil 1.mp3'), 'wb') as f:
            f.write(AUDIO['/a1.mp3'])
        plan = self.engine.check(self.cfg)
        check = plan.checks[0]
        self.assertEqual(sorted({os.path.dirname(e.rel) for e in check.episodes}),
                         ['02_Beta', '07_Alpha', '08_Video Special'])
        self.assertEqual(len(plan.new), 3)
        # The check keeps numbers of existing folders only, so it reserves nothing for "Video Special".
        with open(os.path.join(pc.state_dir('Test Cast'), 'state.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['sessions'], {'Alpha': 7, 'Beta': 2})

    def test_cancel_removes_partial_file(self):
        plan = self.engine.check(self.cfg)
        self.engine._download = functools.partial(self._cancel_then_download, self.engine._download)
        with self.assertRaises(pc.Cancelled):
            self.engine.apply(self.cfg, plan)
        self.assertFalse([f for f in self.files() if f.endswith(('.part', '.mp3'))])
        self.assertFalse(os.path.exists(pc.LOCK_FILE))

    def _cancel_then_download(self, real, *args, **kwargs):
        self.engine.cancel()
        return real(*args, **kwargs)

    def test_broken_feed_is_reported_and_others_continue(self):
        self.cfg['podcasts'].insert(0, pc.normalize_podcast(
            {'name': 'Broken', 'feed': 'http://127.0.0.1:%d/missing.xml' % self.server.server_port}))
        plan = self.engine.check(self.cfg)
        self.assertEqual([c.podcast['name'] for c in plan.errors], ['Broken'])
        self.assertEqual(len(plan.new), 4)
        pc.forget_state('Broken')


class ConfigTest(unittest.TestCase):
    def test_import_legacy_config(self):
        folder = tempfile.mkdtemp(prefix='vault-sync-import-')
        path = os.path.join(folder, 'podcasts.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'_info': 'old', 'podcasts': [
                {'name': 'Am Tavernentresen', 'feed': 'https://feed.podbean.com/amtavernentresen/feed.xml',
                 'sessions': [{'name': 'Shadowrun', 'match': ['^Shadowrun']}]},
                {'name': 'Bad/Name', 'feed': 'https://x'}]}, f)
        cfg = pc.default_config()
        cfg['archive_dir'] = os.path.join(folder, 'does-not-exist')
        added, skipped = pc.import_legacy_config(path, cfg)
        self.assertEqual(added, ['Am Tavernentresen'])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(cfg['archive_dir'], folder)
        self.assertTrue(cfg['podcasts'][0]['enabled'])
        pc.save_config(cfg)
        self.assertEqual(pc.load_config()['podcasts'], cfg['podcasts'])
        self.assertTrue(pc.CONFIG_FILE.startswith(paths.CONFIG_DIR))
        shutil.rmtree(folder)


if __name__ == '__main__':
    unittest.main()
