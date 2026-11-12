# Vault Sync

Desktop app for Windows, macOS and Linux that keeps local copies of online collections up to date. Each kind
of source is its own sub-app with its own tab, and they can run at the same time.

## GOG Library tab

Keeps a local folder an exact mirror of a GOG library (offline installers and extras), built on
[gogrepoc](https://github.com/Kalanyr/gogrepoc).

1. **Log in** with a GOG account (Google/Discord sign-ins are not supported by gogrepoc). Two-step codes
   and sign-in links are asked for in a dialog when GOG requires them.
2. **Check for changes** scans the library and lists every file that would be downloaded and deleted,
   with sizes. Nothing is changed yet.
3. **Apply changes** moves the files to delete into `!orphaned`, downloads the new files, checks that
   nothing is left to download, and only then deletes `!orphaned`. If a download fails or the sync is
   cancelled, the old files are moved back. A sync interrupted by a crash is recovered at the next start.

Files are sorted into `Installer/` and `Extras/<category>/` subfolders per game. gogrepoc only finds files
directly in the game folder, so they are flattened before and sorted again after each run.

## Podcasts tab

Archives every episode of the podcasts on the watch list:
`<archive folder>/<podcast>/<NN>_<folder>/<YYYY-MM-DD> - <title>.mp3`, with the show notes in a `.txt`
file next to each episode.

- **Watch list:** add podcasts by RSS feed, or find the feed by name (**Find...**, searches the Apple Podcasts
  directory). Each podcast can be left out of checks without removing it. **Import...** reads a
  `podcasts.json` of the older `archive.py` script.
- **Folder rules** decide which folder an episode goes into: the first rule with a pattern (regular expression,
  case-insensitive) that matches the episode title wins. Episodes matching no rule get a folder named after the
  start of their title. Folder numbers follow the order of each folder's first episode and stay fixed. After a
  rule change, episodes already archived are moved, not downloaded again.
- **Check for new episodes** reads the feeds and lists what is new per folder; **Download new episodes** fetches
  it. Failed downloads are retried at the next run.

## User data

Settings, the GOG login (secret) and manifest, podcast state and logs are kept in the usual places per OS.
**Open data folder** in the app opens the data folder.

| | Settings | Data | Logs |
|---|---|---|---|
| Windows | `%APPDATA%\Vault Sync` | `%LOCALAPPDATA%\Vault Sync` | `%LOCALAPPDATA%\Vault Sync\Logs` |
| macOS | `~/Library/Application Support/Vault Sync` | same | `~/Library/Logs/Vault Sync` |
| Linux | `~/.config/vault-sync` | `~/.local/share/vault-sync` | `~/.local/state/vault-sync/logs` |

On Linux the `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME` variables are respected. Setting
`VAULT_SYNC_HOME` keeps everything in that one folder instead (portable use).

On first start, the login, manifest and settings of the older GoG Sync (kept next to `GoG Sync.exe`) are
copied over. The old files are not changed.

## Download

Builds for each OS are attached to the [releases](../../releases), and every run of the
[Build workflow](../../actions/workflows/build.yml) has them as artifacts.

- **Windows:** `Vault Sync.exe`.
- **macOS (Apple Silicon):** `Vault Sync.app`. It is not signed, so the first start needs right-click → Open,
  or `xattr -dr com.apple.quarantine "Vault Sync.app"`.
- **Linux (x64):** `vault-sync`, a single executable (`chmod +x vault-sync`).

## Development

```sh
git submodule update --init                       # gogrepoc
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller   # Windows: .venv\Scripts\python.exe
.venv/bin/python app.py                           # run from source
.venv/bin/python -m unittest discover -t . -s tests
.venv/bin/python build.py                         # build for the current OS into dist/
```

On Linux, Tk must be installed (e.g. `sudo apt install python3-tk`).

- `app.py` – entry point and main window; also runs gogrepoc when started with `--gogrepoc`.
- `vaultsync/paths.py` – user data folders; `ui_common.py` – shared UI; `common.py` – shared helpers.
- `vaultsync/gog/` – GOG sub-app: `engine.py` (sync logic), `organize.py` (sorting), `tab.py` (UI).
- `vaultsync/podcasts/` – podcasts sub-app: `engine.py` (archive logic), `tab.py` (UI).
- `.github/workflows/build.yml` – tests and builds for all three OSes; pushing a tag `v*` publishes a release.
