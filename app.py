# Vault Sync: a console app (vaultsync.tui) with a tab per sub-app (GOG library, podcasts, Tabletop Simulator). Each
# tab runs its own tasks, so a GOG sync and a podcast download can run at the same time.
# The program also runs gogrepoc itself when started with --gogrepoc (see vaultsync.gog.engine), and has modes for the
# Build workflow's smoke test: --version prints the version, --check-https tests a verified HTTPS connection and
# --check-tui starts the app without a terminal and exits.
import sys

from vaultsync.common import use_bundled_certificates

# Before anything connects: the builds can't find the system's certificates on every OS.
use_bundled_certificates()


def use_utf8_stdio():
    # Pipes and some consoles default to the system code page; gogrepoc's output and the password are UTF-8.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    sys.stdin.reconfigure(encoding='utf-8')


def main(argv):
    command = argv[0] if argv else None
    if command == '--version':
        use_utf8_stdio()
        from vaultsync import __version__
        print(__version__)
        return 0
    if command == '--check-https':
        # The build must reach GitHub over verified HTTPS (update check, downloads).
        use_utf8_stdio()
        import os
        import urllib.request
        try:
            urllib.request.urlopen('https://github.com', timeout=30).close()
        except OSError as e:
            print('HTTPS failed: {}'.format(e))
            return 1
        print('HTTPS ok (certificates: {})'.format(os.environ.get('SSL_CERT_FILE') or 'system'))
        return 0
    if command == '--gogrepoc':
        use_utf8_stdio()
        from vaultsync.gog import engine
        engine.run_gogrepoc(argv[1:])
        return 0
    from vaultsync.tui.app import main as run_app
    return run_app(smoke_test=command == '--check-tui')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
