# <img src="vaultsync/assets/icon.png" width="48" align="top" alt=""> Vault Sync

Console app for Windows, macOS and Linux that keeps local copies of online collections up to date. It runs in the
terminal and is controlled with the keyboard. Each kind of source has its own tab, and the tabs can run at the same
time: the GOG library, podcasts and Tabletop Simulator mods.

It is a single program file. Its settings and data are kept in a `vault-sync-data` folder next to it, so the program
and its data can be moved or copied together.

## Keys

The footer always shows the keys of the current tab; greyed-out keys can't be used right now (for example while a
task runs).

| Everywhere | |
|---|---|
| `F1` `F2` `F3` | GOG Library, Podcasts, Tabletop Simulator tab |
| `Tab` / `Shift+Tab` | move between fields, lists and buttons |
| `Esc` | leave a text field (so the one-letter keys work again), or close a dialog |
| `Ctrl+O` | open the data folder |
| `Ctrl+U` | install an available update |
| `Ctrl+Q` | quit (running tasks are cancelled first, after asking) |

Every tab has the same main keys: `c` check (changes nothing), `d` carry out what the check found, `x` cancel the
running task, `l` switch between the results and the log, `o` open the tab's folder.

## GOG Library tab

Keeps a local folder an exact mirror of a GOG library (offline installers and extras), built on
[gogrepoc](https://github.com/Kalanyr/gogrepoc).

1. **Log in** (`i`) with a GOG account (Google/Discord sign-ins are not supported by gogrepoc). Two-step codes and
   sign-in links are asked for in a dialog when GOG requires them; the dialog can open the link in the browser.
2. **Check** (`c`) scans the library and lists every file that would be downloaded and deleted, with sizes. Nothing
   is changed yet.
3. **Apply** (`d`) moves the files to delete into `!orphaned`, downloads the new files, checks that nothing is left
   to download, and only then deletes `!orphaned`. If a download fails or the sync is cancelled, the old files are
   moved back. A sync interrupted by a crash is recovered at the next start.

Settings: output folder, languages and operating systems (space-separated, e.g. `en de` and `windows linux`),
extras, full library scan, verifying downloads, and sorting. Files are sorted into `Installer/` and
`Extras/<category>/` subfolders per game. gogrepoc only finds files directly in the game folder, so they are
flattened before and sorted again after each run.

## Podcasts tab

Archives every episode of the podcasts on the watch list:
`<archive folder>/<podcast>/<NN>_<folder>/<YYYY-MM-DD> - <title>.mp3`, with the show notes in a `.txt`
file next to each episode.

- **Watch list:** `a` adds a podcast by RSS feed; in the dialog `Ctrl+F` finds the feed by name (searches the Apple
  Podcasts directory) and `Ctrl+T` tests it. `e` or `Enter` edits, `Del` removes, `Space` leaves a podcast out of
  checks without removing it. `i` imports a `podcasts.json` of the older `archive.py` script.
- **Folder rules** decide which folder an episode goes into: the first rule with a pattern (regular expression,
  case-insensitive) that matches the episode title wins. Episodes matching no rule get a folder named after the
  start of their title. Folder numbers follow the order of each folder's first episode and stay fixed. After a
  rule change, episodes already archived are moved, not downloaded again. In the podcast dialog's rule list: `a`
  add, `e` edit, `Del` remove, `Shift+Up/Down` move.
- **Check** (`c`) reads the feeds and lists what is new per folder; **Download** (`d`) fetches it. Failed downloads
  are retried at the next run.

## Tabletop Simulator tab

Backs up Tabletop Simulator mods together with every image, 3D model, asset bundle, PDF and sound they use, so a
mod still works when its original links are gone.

- The **Mods** list shows every mod in the TTS `Mods/Workshop` folder, including the folders you made in TTS.
  `Space` (or `Enter`) leaves a mod out of backups or includes it again; `a` includes all, `n` none, `F5` reloads.
- **Check** (`c`) counts each mod's assets and how many are already in TTS's cache. Nothing is downloaded.
- **Back up** (`d`) downloads every missing asset into TTS's own cache folders (with the file names TTS uses) and
  writes one zip per mod into the backup folder; mods in a TTS folder go into the same subfolder. Assets that can't
  be downloaded any more are listed in the log and in the zip's `manifest.json`.
- **Restore** (`r`) unpacks every backup zip of a folder into the TTS folders and adds the mods to TTS's mod list.
  Files that already exist are kept. This works on any computer, also without internet.
- Links that appear only inside a mod's scripts (e.g. alternative table textures) are backed up too.
- **Downloadable content:** the Arkham Horror LCG (SCE) mod downloads campaigns, scenarios and player cards from
  GitHub in-game. With this option on, those packs are saved as TTS Saved Objects
  (`Saves/Saved Objects/Arkham SCE Downloads/<mod version>/...`) and packed with their assets into a second zip,
  `<mod> - Downloadable content.zip`. After a restore, place them in TTS from **Objects → Saved Objects** (the
  mod's own download window always needs internet). Language packs: German by default, e.g. `German,French`,
  `all` or `none`. English + German is about 22,000 assets (roughly 30–45 GB), and the zip needs the same again.
  If the packs can't be fetched, the backup continues with packs from an earlier run and shows a warning.

The zips are compatible with the stand-alone `tts-backup` command line tool.

## User data

Everything is kept in `vault-sync-data` next to the program (next to `app.py` when running from source):

| | |
|---|---|
| `gog/` | settings, the GOG login (secret) and manifests |
| `podcasts/` | watch list and folder numbering |
| `tts/` | settings |
| `logs/` | a log file per run |
| `app.json` | the last selected tab |

The folder next to the program must be writable; otherwise Vault Sync says so and stops. Setting `VAULT_SYNC_HOME`
uses another folder instead.

Versions before 2.0 kept this data in the usual per-OS folders (`%APPDATA%` and `%LOCALAPPDATA%` on Windows,
`~/Library/...` on macOS, `~/.config`, `~/.local/share` and `~/.local/state` on Linux). The first start of 2.0 moves
it into `vault-sync-data` and says so. Files that already exist there are not replaced and stay in the old folder.

## Download

Every change on `main` is built and published automatically as a new [release](../../releases) with one file per
OS. Pull requests get test builds as artifacts of their [Build workflow](../../actions/workflows/build.yml) run. The
version is shown in the header.

- **Windows (x64):** `vault-sync.exe`. Runs best in Windows Terminal (the default terminal on Windows 11).
- **macOS (Apple Silicon):** `vault-sync`. It is not signed, so remove the quarantine flag once:
  `xattr -d com.apple.quarantine vault-sync`.
- **Linux (x64):** `vault-sync` (`chmod +x vault-sync` if needed).

Start it from a terminal (`./vault-sync`), or double-click it, which opens a terminal window.

## Updates

When a release build starts, it checks GitHub for a newer release in the background; the header shows the result
next to the version ("Up to date", "Update available" or "Couldn't check for updates"). A newer release appears as a
bar above the tabs: **Update now** (`Ctrl+U`) downloads the build for this OS (with progress, and **Cancel**), puts
it in place of the current one and restarts it in the same terminal; running tasks are cancelled first, after
asking. **What's new** opens the release page, **Later** hides the bar until the next start. If anything goes wrong,
the bar says so and the current version keeps working.
Test builds (versions with `-dev`) and running from source never update themselves. Versions before 2.0 (the window
app) don't update to 2.x by themselves; download 2.x once by hand.

## Development

```sh
git submodule update --init                       # gogrepoc
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller   # Windows: .venv\Scripts\python.exe
.venv/bin/python app.py                           # run from source (in a terminal)
.venv/bin/python -m unittest discover -t . -s tests
.venv/bin/python build.py                         # build for the current OS into dist/
```

- `app.py` – entry point; also runs gogrepoc when started with `--gogrepoc`.
- `vaultsync/tui/` – the console app ([Textual](https://textual.textualize.io/)): `app.py` (tabs, quitting),
  `common.py` (dialogs and the tab base class), one module per tab, `update.py` (update bar).
- `vaultsync/paths.py` – the data folder; `common.py` – shared helpers; `updater.py` – self-update.
- `vaultsync/gog/` – GOG sync logic: `engine.py`, `organize.py` (sorting).
- `vaultsync/podcasts/engine.py` – podcast archive logic.
- `vaultsync/tts/` – Tabletop Simulator logic: `engine.py` (check/backup/restore); `mods.py`, `scanner.py`,
  `naming.py`, `downloader.py`, `package.py` (finding, downloading and packing assets) and `arkham.py` (Arkham SCE
  downloadable content).
- `.github/workflows/build.yml` – tests and builds for all three OSes; every push to `main` publishes a release.
- `VERSION`, `version.py` – versioning, see below.
- `vaultsync/assets/` – app icon (README image and Windows exe icon), generated by `make_icon.py` (needs Pillow; the
  files are committed).

### Versions

`VERSION` holds the major.minor version (e.g. `2.0`). Every push to `main` is released as the next patch version
(`2.0.0`, `2.0.1`, ...), counted from the `v*` tags of earlier releases; the workflow creates the tag. For a new
minor or major version, change `VERSION` (e.g. to `2.1`); the next release is then `2.1.0`. Pull requests and
local builds get test versions such as `2.0.3-dev+abc1234`, and running from source shows `2.0-dev`.
`python version.py` prints the version a build of the current checkout would get. Pushes to `main` in quick
succession are built one after the other; if several are waiting, only the newest is released. If a push did not
start the workflow, run it by hand on `main` (Actions → Build → Run workflow) with **release** ticked.

## License

Vault Sync's own code is released under the [MIT License](LICENSE).

The builds also contain [gogrepoc](https://github.com/Kalanyr/gogrepoc) (included as a git submodule), which is
licensed under the GNU GPL v3 or later, and the other packages listed in `requirements.txt` under their own licenses.
Because gogrepoc is bundled, the ready-made builds as a whole are distributed under the terms of the GPL v3 or later.
