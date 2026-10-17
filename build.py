"""Build the dashboard: python build.py  ->  index.html

index.html is one file: double-click it to open; no server or Python is needed to view
it. Runs py_ewr on the scenarios in scenarios.json and embeds everything in the page.
Options: python build.py --help
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from build_dashboard import main  # noqa: E402

if __name__ == "__main__":
    main()
