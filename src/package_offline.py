"""Package the already-built laptop artifact; no launcher or Python runtime is shipped."""
import argparse
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    parser.add_argument("zip", type=Path)
    args = parser.parse_args()
    args.zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(args.html, args.html.name)
    print(f"HTML: {args.html.stat().st_size:,} bytes; ZIP: {args.zip.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
