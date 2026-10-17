"""One-command launcher for the EWR analysis dashboard (standard library only).

    python3 run.py              (Windows: py run.py)

Creates a private Python environment in .venv the first time, installs the packages in requirements.txt (again only when that file
changes), starts the local server on a free port and opens the dashboard in your default browser. Extra arguments go to the server:
a port number to prefer, or --no-browser.
"""

import hashlib
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
MIN_PYTHON = (3, 9)


class LaunchError(Exception):
    """A problem the user can fix; the message is shown as is."""


def venv_python(venv_dir=VENV, windows=None):
    windows = sys.platform == "win32" if windows is None else windows
    return Path(venv_dir) / ("Scripts/python.exe" if windows else "bin/python")


def requirements_digest(path=REQUIREMENTS):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def needs_install(venv_dir=VENV, requirements=REQUIREMENTS):
    """True unless the packages were installed from the current requirements file."""
    stamp = Path(venv_dir) / ".requirements-installed"
    try:
        return stamp.read_text().strip() != requirements_digest(requirements)
    except OSError:
        return True


def mark_installed(venv_dir=VENV, requirements=REQUIREMENTS):
    (Path(venv_dir) / ".requirements-installed").write_text(
        requirements_digest(requirements)
    )


def check_python(version=None):
    version = tuple(sys.version_info[:2]) if version is None else tuple(version)
    if version < MIN_PYTHON:
        raise LaunchError(
            f"This tool needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer, but this is Python {version[0]}.{version[1]}.\n"
            "Install the latest Python from https://www.python.org/downloads/ and run this again."
        )


def interpreter_works(py):
    try:
        return (
            subprocess.call(
                [str(py), "-c", "import sys"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            == 0
        )
    except OSError:
        return False


def ensure_venv(venv_dir=VENV):
    py = venv_python(venv_dir)
    if py.exists():
        if not interpreter_works(py):
            raise LaunchError(
                f"The Python environment in {venv_dir} does not work (it may have been copied from another computer "
                "or its Python was uninstalled).\nDelete the folder named .venv and start again; it is rebuilt automatically."
            )
        return py
    print(
        "First start: setting up a private Python environment (one-off, about a minute)..."
    )
    try:
        venv.create(venv_dir, with_pip=True)
    except Exception as e:
        raise LaunchError(
            f"Could not create the Python environment ({e}).\n"
            "On Ubuntu/Debian try: sudo apt install python3-venv   then run this again."
        ) from e
    if not py.exists():
        raise LaunchError(
            f"The Python environment was not created properly. Delete the folder {venv_dir} and try again."
        )
    return py


def install_requirements(py, venv_dir=VENV, requirements=REQUIREMENTS):
    if not needs_install(venv_dir, requirements):
        return
    print(
        "Installing the packages the tool needs (first start, or after an update; needs internet)..."
    )
    if (
        subprocess.call(
            [
                str(py),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(requirements),
            ]
        )
        != 0
    ):
        raise LaunchError(
            "Installing the required packages failed. Check your internet connection and run this again."
        )
    mark_installed(venv_dir, requirements)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        check_python()
        py = ensure_venv()
        install_requirements(py)
    except LaunchError as e:
        print(f"\nCannot start the EWR tool:\n{e}", file=sys.stderr)
        return 1
    try:
        return subprocess.call([str(py), str(ROOT / "src" / "server.py"), *args])
    except KeyboardInterrupt:
        return 0
    except OSError as e:
        print(
            f"\nCannot start the EWR tool:\nCould not run the Python environment ({e}).\n"
            "Delete the folder named .venv and start again.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
