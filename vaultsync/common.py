"""Small helpers shared by the sub-app engines (no UI imports, so child processes can use them too)."""
import json
import os
import ssl


class Cancelled(Exception):
    """Raised in a worker when the user cancelled the running task."""


class SyncError(Exception):
    """A task failed in a way the user should be told about."""


def use_bundled_certificates():
    """Lets HTTPS verify servers when Python can't find the system's certificates, as in the macOS build.

    The bundled OpenSSL looks for certificates where Python was installed on the build machine, which doesn't exist
    on the user's computer, so every urllib HTTPS request would fail. Then SSL_CERT_FILE is pointed at certifi's
    certificate bundle, which the builds include. Where the system's certificates are found (Linux, and Windows,
    which also reads its own certificate store), nothing changes. Call it before the first HTTPS request.
    """
    if os.environ.get('SSL_CERT_FILE'):
        return
    paths = ssl.get_default_verify_paths()
    if (paths.cafile and os.path.isfile(paths.cafile)) or (paths.capath and os.path.isdir(paths.capath)
                                                         and os.listdir(paths.capath)):
        return
    try:
        import certifi
    except ImportError:
        return
    os.environ['SSL_CERT_FILE'] = certifi.where()


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
