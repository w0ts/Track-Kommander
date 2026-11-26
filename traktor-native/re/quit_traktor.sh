#!/bin/bash
# Quit the patched Traktor the way Cmd+Q does, so it SAVES its collection first, and wait for it.
#
# Never pkill / SIGTERM / SIGKILL it: Traktor writes collection.nml only on a real quit. Killed, it
# loses every collection change made since its last save (2026-11-24: ten pkill restarts dropped a
# test hotcue and the grids aligned with ALIGN).
#
# Usage: quit_traktor.sh   (exit 0 = Traktor quit, or was not running; 1 = still running after 90 s)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PY="$REPO/.venv/bin/python"

# The full copy (TraktorCopy.app) or the slim one (Traktor Pro 4 TK.app, make_slim_app.sh).
PID="$(pgrep -f '(TraktorCopy|Traktor Pro 4 TK)\.app/Contents/MacOS/Traktor Pro 4$' | head -1 || true)"
if [ -z "$PID" ]; then
  echo "Traktor (patched) is not running"
  exit 0
fi
NML="$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); from core.traktor import find_traktor_nml; print(find_traktor_nml() or "")' "$REPO/src")"
BEFORE="$( [ -n "$NML" ] && stat -f %m "$NML" || echo 0)"

# NSRunningApplication.terminate = the Quit Apple event (what Cmd+Q and the Dock's Quit send).
"$PY" -c 'import sys
from AppKit import NSRunningApplication
app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(sys.argv[1]))
sys.exit(0 if app is not None and app.terminate() else 1)' "$PID" || { echo "could not ask Traktor (pid $PID) to quit" >&2; exit 1; }

for _ in $(seq 1 90); do
  kill -0 "$PID" 2>/dev/null || break
  sleep 1
done
if kill -0 "$PID" 2>/dev/null; then
  echo "Traktor (pid $PID) is still running after 90 s (a dialog waiting?): NOT killed" >&2
  exit 1
fi
AFTER="$( [ -n "$NML" ] && stat -f %m "$NML" || echo 0)"
if [ "$AFTER" != "$BEFORE" ]; then
  echo "Traktor quit; collection saved ($(date -r "$AFTER" '+%H:%M:%S'))"
else
  echo "Traktor quit; collection.nml unchanged (nothing to save, or it did not save)"
fi
