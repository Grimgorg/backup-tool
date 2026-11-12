"""Small helpers shared by the sub-app engines (no UI imports, so child processes can use them too)."""
import json
import os


class Cancelled(Exception):
    """Raised in a worker when the user cancelled the running task."""


class SyncError(Exception):
    """A task failed in a way the user should be told about."""


def format_size(b):
    if b is None:
        return ''
    for unit, factor in (('GB', 1024 ** 3), ('MB', 1024 ** 2), ('KB', 1024)):
        if b >= factor:
            return '%.2f %s' % (b / factor, unit)
    return '%d B' % b


def write_atomic(path, data):
    """Writes text or bytes so that readers never see a half-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    if isinstance(data, bytes):
        with open(tmp, 'wb') as f:
            f.write(data)
    else:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(data)
    os.replace(tmp, path)


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, value):
    write_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')
