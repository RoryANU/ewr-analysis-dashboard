"""Keep data/reference/parameter_sheet.csv identical to the MDBA EWR tool's copy on GitHub.

    python3 src/sync_parameter_sheet.py            check: exit 1 if the local copy differs from upstream (exit 0, with a warning, if offline)
    python3 src/sync_parameter_sheet.py --update   overwrite the local copy with upstream, then rebuild the dashboard

Used by the `parameter-sheet-sync` pre-push hook and tests/test_parameter_sheet_sync.py.
"""

import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

UPSTREAM_URL = "https://raw.githubusercontent.com/MDBAuth/EWR_tool/main/py_ewr/parameter_metadata/parameter_sheet.csv"
LOCAL = Path(__file__).resolve().parent.parent / "data" / "reference" / "parameter_sheet.csv"


def normalise(raw: bytes) -> bytes:
    """Line endings and a missing final newline do not count as differences."""
    return raw.replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"


def fetch_upstream(timeout: float = 30) -> bytes:
    with urllib.request.urlopen(UPSTREAM_URL, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        return normalise(resp.read())


def local_copy() -> bytes:
    return normalise(LOCAL.read_bytes())


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:12]


def main(argv: list[str]) -> int:
    try:
        upstream = fetch_upstream()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"parameter sheet: could not reach GitHub ({e}); not checked", file=sys.stderr)
        return 0
    if "--update" in argv:
        LOCAL.write_bytes(upstream)
        print(f"parameter sheet updated ({digest(upstream)}); now run: python3 src/build_dashboard.py")
        return 0
    if not LOCAL.exists() or local_copy() != upstream:
        print(
            f"parameter sheet is out of date with {UPSTREAM_URL}\n"
            f"  local {digest(local_copy()) if LOCAL.exists() else 'missing'}  upstream {digest(upstream)}\n"
            "  fix: python3 src/sync_parameter_sheet.py --update && python3 src/build_dashboard.py",
            file=sys.stderr,
        )
        return 1
    print(f"parameter sheet matches upstream ({digest(upstream)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
