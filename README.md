# GoG Sync

Windows app that keeps a local folder an exact mirror of a GOG library (offline installers and extras),
built on [gogrepoc](https://github.com/Kalanyr/gogrepoc).

1. **Log in** with a GOG account (Google/Discord sign-ins are not supported by gogrepoc). Two-step codes
   and sign-in links are asked for in a dialog when GOG requires them.
2. **Check for changes** scans the library and lists every file that would be downloaded and deleted,
   with sizes. Nothing is changed yet.
3. **Apply changes** moves the files to delete into `!orphaned`, downloads the new files, checks that
   nothing is left to download, and only then deletes `!orphaned`. If a download fails or the sync is
   cancelled, the old files are moved back.

Files are sorted into `Installer\` and `Extras\<category>\` subfolders per game. gogrepoc only finds files
directly in the game folder, so they are flattened before and sorted again after each run.

## Files

- `GoG Sync.exe` – the app (built, not in git). It keeps its data next to itself: `gog-token.dat` (login,
  secret), `gog-manifest.dat`, `settings.json` and `logs\`.
- `gog_sync.py` – UI; `sync_engine.py` – sync logic; `organize.py` – sorting into subfolders.
- `gogrepoc\` – git submodule; run `git submodule update --init` after cloning.
- `sync.ps1`, `login.cmd` – the older command-line version of the same sync.

## Build

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install requests html5lib python-dateutil pytz pyOpenSSL psutil pyinstaller sv-ttk
.\build.ps1
```

To run from source instead: `.venv\Scripts\python.exe gog_sync.py`.
