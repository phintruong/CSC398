"""python -m fixtures build [OUT_DIR]   (default: fixtures/data)"""

import sys
from pathlib import Path

from .build import write
from .replay import DATA

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "build":
        print(__doc__)
        sys.exit(2)
    for p in write(Path(sys.argv[2]) if len(sys.argv) > 2 else DATA):
        print(p)
