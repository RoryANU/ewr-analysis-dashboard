"""Keep data/reference/parameter_sheet.csv identical to the MDBA EWR tool's copy on GitHub.

    python3 -m ewr_analysis.sync_parameter_sheet            check: exit 1 if the local copy differs from upstream (exit 0, with a warning, if offline)
    python3 -m ewr_analysis.sync_parameter_sheet --update   overwrite the local copy with upstream, then rebuild the dashboard

Used by the `parameter-sheet-sync` pre-push hook and tests/test_parameter_sheet_sync.py.
"""

import csv
import hashlib
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from ewr_analysis.paths import DATA_DIR

UPSTREAM_URL = "https://raw.githubusercontent.com/MDBAuth/EWR_tool/main/py_ewr/parameter_metadata/parameter_sheet.csv"
LOCAL = DATA_DIR / "reference" / "parameter_sheet.csv"


def normalise(raw: bytes) -> bytes:
    """Line endings and a missing final newline do not count as differences."""
    return raw.replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"


def fetch_upstream(timeout: float = 30) -> bytes:
    """Fetch the upstream parameter sheet and return bytes with normalised newlines."""
    with urllib.request.urlopen(UPSTREAM_URL, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        return normalise(resp.read())


# columns the build reads unconditionally (build_dashboard.py, ewr_index)
REQUIRED_COLUMNS = {"PlanningUnitName", "Code", "StartMonth", "EndMonth", "Multigauge", "FlowLevelVolume"}


def local_copy() -> bytes:
    """Read the local parameter sheet with normalised newlines."""
    return normalise(LOCAL.read_bytes())


def digest(raw: bytes) -> str:
    """Return the first 12 hexadecimal SHA-256 characters for a sheet copy."""
    return hashlib.sha256(raw).hexdigest()[:12]


def main(argv: list[str]) -> int:
    """Check or update the sheet; return 1 for a mismatch or invalid header, otherwise 0."""
    try:
        upstream = fetch_upstream()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"parameter sheet: could not reach GitHub ({e}); not checked", file=sys.stderr)
        return 0
    if "--update" in argv:
        header = next(csv.reader([upstream.split(b"\n", 1)[0].decode("utf-8-sig", errors="replace")]))
        if not REQUIRED_COLUMNS <= set(header):
            print("parameter sheet: invalid upstream header; local copy unchanged", file=sys.stderr)
            return 1
        _replace_local(upstream)
        print(f"parameter sheet updated ({digest(upstream)}); now run: python3 -m ewr_analysis.build_dashboard")
        return 0
    if not LOCAL.exists() or local_copy() != upstream:
        print(
            f"parameter sheet is out of date with {UPSTREAM_URL}\n"
            f"  local {digest(local_copy()) if LOCAL.exists() else 'missing'}  upstream {digest(upstream)}\n"
            "  fix: python3 -m ewr_analysis.sync_parameter_sheet --update && python3 -m ewr_analysis.build_dashboard",
            file=sys.stderr,
        )
        return 1
    print(f"parameter sheet matches upstream ({digest(upstream)})")
    return 0


def _replace_local(content: bytes) -> None:
    """Replace the local copy atomically (write a temporary file beside it, then rename), so a failure never leaves half a sheet."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=LOCAL.parent, delete=False) as temp:
            temporary = Path(temp.name)
            temp.write(content)
        temporary.replace(LOCAL)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
