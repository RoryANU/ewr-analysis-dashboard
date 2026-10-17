#!/bin/sh
# Start the EWR analysis tool (macOS and Linux). Double-click start.command on a Mac, or run ./start.sh in a terminal.
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done

if [ -z "$PY" ]; then
  echo
  echo "Python 3.10 or newer was not found on this computer."
  echo "Install it from https://www.python.org/downloads/ and then start this again."
  echo
  printf "Press Enter to close this window."
  read -r _
  exit 1
fi

"$PY" run.py "$@"
status=$?
if [ "$status" -ne 0 ]; then
  echo
  printf "The tool stopped with a problem (see above). Press Enter to close this window."
  read -r _
fi
exit "$status"
