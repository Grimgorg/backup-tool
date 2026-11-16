"""Works out Vault Sync's version number.

The VERSION file holds the major.minor version (e.g. "1.0"). Every push to main is released with the next patch
number: 1.0.0, 1.0.1, 1.0.2 ... counted from the git tags (v1.0.0, v1.0.1, ...) of earlier releases. Changing
VERSION to e.g. "1.1" starts again at 1.1.0. Builds that are not releases (pull requests, local builds) get a
test version such as 1.0.3-dev+abc1234: the release they lead up to, plus the commit.

  python version.py             test version for this checkout
  python version.py --release   version of the next release (used by the Build workflow on main)
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(ROOT, 'VERSION')


def base_version():
    """major.minor from the VERSION file."""
    with open(VERSION_FILE, encoding='utf-8') as f:
        base = f.read().strip()
    if not re.fullmatch(r'\d+\.\d+', base):
        raise ValueError('VERSION must be "major.minor", e.g. 1.0 (found {!r})'.format(base))
    return base


def next_release(base, tags):
    """The next release version for base, given the existing tag names."""
    pattern = re.compile(r'v?{}\.(\d+)'.format(re.escape(base)))
    patches = [int(m.group(1)) for m in map(pattern.fullmatch, tags) if m]
    return '{}.{}'.format(base, max(patches) + 1 if patches else 0)


def _git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def compute(release=False):
    base = base_version()
    try:
        version = next_release(base, _git('tag', '--list').split())
        commit = _git('rev-parse', '--short=7', 'HEAD')
    except (OSError, subprocess.CalledProcessError):
        # No git (e.g. a source download): there are no tags to count from.
        return base + '-dev' if not release else sys.exit('git is needed to number a release')
    return version if release else '{}-dev+{}'.format(version, commit)


if __name__ == '__main__':
    print(compute(release='--release' in sys.argv[1:]))
