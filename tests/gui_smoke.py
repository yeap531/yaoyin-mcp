"""Run on a desktop, or with xvfb-run on Linux. No API or private files."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui import smoke_test


if __name__ == '__main__':
    try:
        smoke_test()
    except BaseException:
        print('GUI smoke failed', file=sys.stderr)
        sys.exit(1)
    print('GUI smoke passed')
