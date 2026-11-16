# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Vault Sync

Tk desktop app (Windows, macOS, Linux) that keeps local copies of online collections up to date. Each kind of source
is a sub-app with its own tab: GOG library (`vaultsync/gog/`), podcasts (`vaultsync/podcasts/`) and Tabletop
Simulator mod backups (`vaultsync/tts/`). Tabs run their tasks independently, so several can run at the same time.

## Commands

```sh
git submodule update --init                                   # gogrepoc (GOG tab); needed to run and build
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller   # Windows: .venv\Scripts\python.exe
.venv/bin/python app.py                                       # run from source
.venv/bin/python -m unittest discover -t . -s tests           # all tests
.venv/bin/python -m unittest tests.test_tts_engine.EngineTest.test_backup_and_restore   # one test
.venv/bin/python build.py                                     # PyInstaller build for the current OS into dist/
python version.py [--release]                                 # version a build of this checkout would get
python make_icon.py                                           # redraw the icon files in vaultsync/assets (needs Pillow)
```

- Run tests from the repo root with `-t .`: `tests/__init__.py` sets `VAULT_SYNC_HOME` to a temp folder before
  `vaultsync.paths` is imported, so tests never touch real user data. Importing a test module any other way skips that.
- CI uses Python 3.13. `sv-ttk` needs Tk 8.6+, so the macOS system Python (Tk 8.5) can run the tests but not the app.
- No linter is configured. Code style: single quotes, `'...'.format()`, 120-column lines (the ported files in
  `vaultsync/tts/` other than `engine.py`/`tab.py` keep the double-quote/f-string style of the tts-backup CLI).

## Architecture

- `app.py` builds the window and one tab per class in `TABS`, remembers the selected tab and window size, and on close
  cancels busy tabs and waits for them (`App.task_finished`). It also has two non-UI modes, handled before Tk is
  imported: `--gogrepoc ...` runs the bundled gogrepoc (the GOG engine starts the app itself as a child process with
  this, so the frozen build needs no separate Python), and `--version` prints the version.
- Every sub-app is split into `engine.py` (no UI imports; runs in a worker thread) and `tab.py` (a `TaskTab` from
  `vaultsync/ui_common.py`). The engine takes `log`, `status`, `progress` callbacks (and `ask` for GOG), checks a
  cancel event, raises `Cancelled` / `SyncError` (`vaultsync/common.py`), and writes a log file per run into
  `paths.log_dir()`. The tab passes `self.log_cb` etc. and starts work with `run_task(func, on_done)`; worker
  callbacks are queued and applied on the UI thread. `set_busy()` enables/disables the tab's widgets.
- Sub-apps follow a two-step flow: a check/preview that changes nothing and returns a plan, then an apply step after
  the user confirms. Errors in one item (feed, game, mod) are logged and reported, not fatal to the whole run.
- User data locations come only from `vaultsync/paths.py` (`config_dir()`, `data_dir()`, `log_dir()`, per-sub-app
  subfolders). `VAULT_SYNC_HOME` puts everything in one folder.
- GOG: gogrepoc (git submodule) keeps token and manifest in `DATA_DIR/gog`. Files to delete are moved to `!orphaned`
  first and only deleted after all downloads succeed; on failure, cancel or a crash they are moved back
  (`Engine.recover` runs at the next start). `organize.py` flattens game folders before gogrepoc runs and sorts them
  into `Installer/` and `Extras/<category>/` afterwards, because gogrepoc only sees files directly in a game folder.
- Self-update: `vaultsync/updater.py` (no UI) reads the latest GitHub release of `Grimgorg/backup-tool`, picks the
  asset by the file names the Build workflow gives them (`VaultSync-<os>-<arch>-<version>`), and replaces the running
  build by renaming it to `<name>.old` (works on all three OSes) and moving the new one in; `cleanup_previous()`
  removes the `.old` at the next start. `vaultsync/updater_ui.py` has the dialogs; `App` starts the check. Only frozen
  builds with a plain `x.y.z` version check, so keep asset names and the version format in sync with `build.yml`.
- TTS: logic ported from the stand-alone `tts-backup` CLI; the zip format (with `manifest.json`) is shared with it, so
  keep it compatible. Assets are cached under TTS's own naming (URL with non-alphanumerics removed). `arkham.py` handles
  the Arkham Horror SCE mod's in-game downloads (saved as Saved Objects per mod version).

## Versions and releases

- `VERSION` holds `major.minor`. `version.py` numbers releases from the existing `v*` tags (next patch number);
  non-release builds get `<next>-dev+<sha>`, and running from source shows `<VERSION>-dev`.
- `build.py` writes `vaultsync/_version.py` (git-ignored) only for the duration of the PyInstaller build; the app
  reads `__version__` from it, falling back to the source version.
- `.github/workflows/build.yml`: every push to `main` is tested, built for all three OSes, smoke-tested (`--version`
  must match, `--gogrepoc -h`, window start under xvfb on Linux) and **published as a GitHub release with a new tag**.
  So every commit pushed to `main` is a release; land changes through PRs. Bump `VERSION` for a new minor/major.
