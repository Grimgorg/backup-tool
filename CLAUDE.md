# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Vault Sync

Console app (Textual; Windows, macOS, Linux), built as one executable per OS, that keeps local copies of online
collections up to date. Each kind of source is a sub-app with its own tab: GOG library (`vaultsync/gog/`), podcasts
(`vaultsync/podcasts/`) and Tabletop Simulator mod backups (`vaultsync/tts/`). Tabs run their tasks independently,
so several can run at the same time. User data lives in `vault-sync-data/` next to the executable (or next to
`app.py` from source).

## Commands

```sh
git submodule update --init                                   # gogrepoc (GOG tab); needed to run and build
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller   # Windows: .venv\Scripts\python.exe
.venv/bin/python app.py                                       # run from source (needs a real terminal)
.venv/bin/python -m unittest discover -t . -s tests           # all tests
.venv/bin/python -m unittest tests.test_tts_engine.EngineTest.test_backup_and_restore   # one test
.venv/bin/python build.py                                     # PyInstaller one-file build for this OS into dist/
python version.py [--release]                                 # version a build of this checkout would get
python make_icon.py                                           # redraw the icon files in vaultsync/assets (needs Pillow)
```

- Run tests from the repo root with `-t .`: `tests/__init__.py` sets `VAULT_SYNC_HOME` to a temp folder before
  `vaultsync.paths` is imported, so tests never touch real user data (or move it, see `migrate_system_folders`).
  Importing a test module any other way skips that.
- Running `app.py` or a build without `VAULT_SYNC_HOME` on a machine with pre-2.0 data moves that data (see below).
- CI uses Python 3.13. No linter is configured. Code style: single quotes, `'...'.format()`, 120-column lines (the
  ported files in `vaultsync/tts/` other than `engine.py` keep the double-quote/f-string style of the tts-backup CLI).

## Architecture

- `app.py` handles the non-UI modes first: `--gogrepoc ...` runs the bundled gogrepoc (the GOG engine starts the
  program itself as a child process with this, so the frozen build needs no separate Python), `--version`,
  `--check-https` and `--check-tui` (starts the app headless and exits; all three used by the CI smoke test).
  Otherwise it runs `vaultsync.tui.app.main()`. It first calls `use_bundled_certificates()`: the macOS build's
  OpenSSL can't find system certificates, so urllib HTTPS (updates, feeds, TTS downloads) would fail without
  certifi's bundle.
- Every sub-app's logic is in `engine.py` (no UI imports; runs in a worker thread). The engine takes `log`, `status`,
  `progress` callbacks (and `ask` for GOG), checks a cancel event, raises `Cancelled` / `SyncError`
  (`vaultsync/common.py`), and writes a log file per run into `paths.log_dir()`.
- UI: `vaultsync/tui/`. `app.py` (`VaultSyncApp`) holds the header, `UpdateBar` (`update.py`), a `TabbedContent` with
  one pane per entry in `TABS`, and the footer; it remembers the tab in `app.json`, and on quit cancels busy panes
  and waits (`task_finished`). Each tab is a `TaskPane` subclass (`common.py`; `gog.py`, `podcasts.py`, `tts.py`):
  it passes `self.log_cb` etc. to its engine and starts work with `run_task(func, on_done)`; worker events are
  queued and applied on the UI thread. Widgets with class `idle-only` are disabled while busy; keys are `BINDINGS`
  on the pane, and `check_action` greys out `IDLE_ACTIONS` while busy and `run` while `can_run()` is false. Dialogs
  subclass `Dialog` (OK/Cancel via `action_ok`/`action_cancel`); `MessageScreen` and `PathScreen` (no file picker in
  a terminal) are shared.
- Textual gotchas seen here: message handlers (`on_mount`, `on_button_pressed`, ...) run for every class in the MRO,
  so base and subclass handlers both run; `App.query()` searches the screen on top, so the app queries
  `main_screen`; focusing a widget in a hidden tab switches to that tab, so panes don't focus themselves on mount.
- Sub-apps follow a two-step flow: a check/preview (`c`) that changes nothing and returns a plan, then an apply step
  (`d`) after the user confirms. Errors in one item (feed, game, mod) are logged and reported, not fatal to the run.
- User data locations come only from `vaultsync/paths.py`: `HOME` (`vault-sync-data` next to the program, or
  `VAULT_SYNC_HOME`) with per-sub-app subfolders, and `LOG_DIR`. `migrate_system_folders()` moves the data of
  versions before 2.0 from the per-OS folders once (at start, if `HOME` doesn't exist), never replacing files.
- GOG: gogrepoc (git submodule) keeps token and manifest in `HOME/gog`. Files to delete are moved to `!orphaned`
  first and only deleted after all downloads succeed; on failure, cancel or a crash they are moved back
  (`Engine.recover` runs at the next start). `organize.py` flattens game folders before gogrepoc runs and sorts them
  into `Installer/` and `Extras/<category>/` afterwards, because gogrepoc only sees files directly in a game folder.
- Self-update: `vaultsync/updater.py` (no UI) reads the latest GitHub release of `Grimgorg/backup-tool`, picks the
  asset by the file names the Build workflow gives them (`vault-sync-<os>-<arch>-<version>`; pre-2.0 window builds
  used `VaultSync-...`, so they never install a console build), and replaces the running build by renaming it to
  `<name>.old` and moving the new one in; `cleanup_previous()` removes the `.old` at the next start. After an
  install the app exits with `('restart', target)` and `main()` runs the new build in the same terminal
  (`restart_in_terminal`). Only frozen builds with a plain `x.y.z` version check, so keep asset names and the
  version format in sync with `build.yml`.
- TTS: logic ported from the stand-alone `tts-backup` CLI; the zip format (with `manifest.json`) is shared with it, so
  keep it compatible. Assets are cached under TTS's own naming (URL with non-alphanumerics removed). `arkham.py` handles
  the Arkham Horror SCE mod's in-game downloads (saved as Saved Objects per mod version).

## Versions and releases

- `VERSION` holds `major.minor`. `version.py` numbers releases from the existing `v*` tags (next patch number);
  non-release builds get `<next>-dev+<sha>`, and running from source shows `<VERSION>-dev`.
- `build.py` makes one console executable (`vault-sync`, `vault-sync.exe`) with `--collect-submodules` for Textual and
  Rich (they import widgets and Unicode tables lazily). It writes `vaultsync/_version.py` (git-ignored) only for the duration of the PyInstaller build; the app
  reads `__version__` from it, falling back to the source version.
- `.github/workflows/build.yml`: every push to `main` is tested, built for all three OSes, smoke-tested (`--version`
  must match, `--check-https`, `--gogrepoc -h`, `--check-tui`) and **published as a GitHub release with a new tag**.
  So every commit pushed to `main` is a release; land changes through PRs. Bump `VERSION` for a new minor/major.
