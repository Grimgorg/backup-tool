# Sorts the flat per-game folders written by gogrepoc into subfolders, and flattens them back.
# gogrepoc only finds files directly in <savedir>/<game>/, so the GOG engine flattens before running it
# and sorts again afterwards. Moves stay on the same drive, so both directions are instant renames.
# Usage:
#   python -m vaultsync.gog.organize sort <savedir> [-dryrun]
#   python -m vaultsync.gog.organize flatten <savedir> [-dryrun]
import ast
import os
import re
import sys
from collections import defaultdict

# Set by vaultsync.gog.engine to the manifest in the app data folder.
MANIFEST = 'gog-manifest.dat'
INSTALLER_DIR = 'Installer'
EXTRAS_DIR = 'Extras'
MANAGED_DIRS = (INSTALLER_DIR, EXTRAS_DIR)
SKIP_GAME_DIRS = ('!downloading', '!orphaned')
# Where messages go; GoG Sync replaces it to collect them in its log.
output = print

EXTRA_CATEGORIES = {
    'game add-ons': 'Add-ons',
    'audio': 'Soundtracks',
    'artworks': 'Artwork',
    'wallpapers': 'Artwork',
    'avatars': 'Artwork',
    'manuals': 'Manuals',
    'guides & reference': 'Guides',
    'video': 'Videos',
}
OTHER_CATEGORY = 'Other'

# "... Part3", "... part 3", "... (Part 3 of 4)", "... (Teil 3 von 24)"
PART_SUFFIX = re.compile(r'\s*(\(\s*(part|teil)\s*\d+\s*(of|von)\s*\d+\s*\)|\bpart\s*\d+)\s*$', re.IGNORECASE)
INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*]')


def load_manifest():
    with open(MANIFEST, encoding='utf-8') as f:
        text = f.read()
    if text.startswith('#'):
        text = text.split('\n', 1)[1]
    return ast.literal_eval(text)


def safe_dirname(name):
    return INVALID_PATH_CHARS.sub('', name).strip().rstrip('.').strip()


def target_dirs(game):
    """Maps each file name of a game to its relative target directory."""
    targets = {}
    for key in ('downloads', 'galaxyDownloads', 'sharedDownloads'):
        for d in game.get(key) or []:
            if d.get('name'):
                targets.setdefault(d['name'], INSTALLER_DIR)

    # Group extras into multi-part sets by their description without the part suffix.
    sets = defaultdict(list)
    for e in game.get('extras') or []:
        if not e.get('name') or e['name'] in targets:
            continue
        category = EXTRA_CATEGORIES.get((e.get('type') or '').lower(), OTHER_CATEGORY)
        set_name = safe_dirname(PART_SUFFIX.sub('', e.get('desc') or ''))
        sets[(category, set_name)].append(e['name'])
    for (category, set_name), names in sets.items():
        folder = os.path.join(EXTRAS_DIR, category)
        if len(names) > 1 and set_name:
            folder = os.path.join(folder, set_name)
        for name in names:
            targets.setdefault(name, folder)
    return targets


def move(src, dst, game_path, dryrun):
    output('  {}: {} -> {}'.format(os.path.basename(game_path), os.path.relpath(src, game_path), os.path.relpath(dst, game_path)))
    if not dryrun:
        make_dirs(game_path, os.path.relpath(os.path.dirname(dst), game_path))
        os.rename(src, dst)


def make_dirs(base, rel_dir):
    # Not os.makedirs: on the SMB share a directory removed moments ago (by flatten) can still be
    # reported as existing by the client's directory cache, so makedirs skips creating it and the
    # child fails with "path not found". An explicit mkdir per level always reaches the server.
    path = base
    for part in rel_dir.split(os.sep):
        path = os.path.join(path, part)
        try:
            os.mkdir(path)
        except FileExistsError:
            pass


def game_dirs(savedir):
    for name in sorted(os.listdir(savedir)):
        path = os.path.join(savedir, name)
        if os.path.isdir(path) and name not in SKIP_GAME_DIRS:
            yield name, path


def cmd_sort(savedir, dryrun):
    games = {g.get('folder_name') or g['title']: g for g in load_manifest()}
    moved = 0
    for name, path in game_dirs(savedir):
        game = games.get(name)
        if game is None:
            continue
        for file_name, rel_dir in sorted(target_dirs(game).items()):
            src = os.path.join(path, file_name)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(path, rel_dir, file_name)
            if os.path.exists(dst):
                output('  WARNING: {} already exists, leaving {} in place'.format(dst, src))
                continue
            move(src, dst, path, dryrun)
            moved += 1
    output('sort: {} file(s) {}moved'.format(moved, 'would be ' if dryrun else ''))


def cmd_flatten(savedir, dryrun):
    moved = 0
    for name, path in game_dirs(savedir):
        for managed in MANAGED_DIRS:
            managed_path = os.path.join(path, managed)
            if not os.path.isdir(managed_path):
                continue
            for root, _, files in os.walk(managed_path):
                for file_name in sorted(files):
                    src = os.path.join(root, file_name)
                    dst = os.path.join(path, file_name)
                    if os.path.exists(dst):
                        output('  WARNING: {} already exists, leaving {} in place'.format(dst, src))
                        continue
                    move(src, dst, path, dryrun)
                    moved += 1
            if not dryrun:
                # Remove the now empty managed directories, deepest first.
                for root, dirs, files in os.walk(managed_path, topdown=False):
                    if not os.listdir(root):
                        os.rmdir(root)
    output('flatten: {} file(s) {}moved'.format(moved, 'would be ' if dryrun else ''))


def main(argv):
    from vaultsync.gog import engine  # noqa: F401  (points MANIFEST at the app data folder)
    args = [a for a in argv if a != '-dryrun']
    dryrun = len(args) != len(argv)
    if len(args) != 2 or args[0] not in ('sort', 'flatten'):
        sys.exit('usage: organize.py sort|flatten <savedir> [-dryrun]')
    command, savedir = args
    if not os.path.isdir(savedir):
        sys.exit('not a directory: ' + savedir)
    if command == 'sort':
        cmd_sort(savedir, dryrun)
    else:
        cmd_flatten(savedir, dryrun)


if __name__ == '__main__':
    main(sys.argv[1:])
