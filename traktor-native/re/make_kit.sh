#!/bin/bash
# Build the Track Kommander KIT: what TK inside Traktor needs on a Mac that has no TK checkout —
# Python 3.13 (uv's standalone build), TK's runtime packages and TK's source, in the repo's own
# layout, because libtk and TK look for exactly these paths under TK_REPO:
#
#   <kit>/python/                               the interpreter (libtk: TK_PYTHONHOME)
#   <kit>/.venv/lib/python3.13/site-packages    TK's locked runtime packages (no dev, no build)
#   <kit>/src/  traktor-native/re/embed_tk.py  assets/ Traktor/ licenses/ clap_model/ pyproject.toml
#   <kit>/KIT-VERSION                           "<TK version> <commit>[+local]"; libtk's marker
#
# Then trimmed (trim_kit.py: ~1.7 GB -> ~0.6 GB) and smoke-tested (smoke_kit.py).
#
# No venv interpreter, no pyvenv.cfg: libtk puts site-packages on sys.path itself, and worker
# processes run <kit>/python/bin/python3.13 with the parent's sys.path ("spawn"). So the kit holds
# no absolute path and works wherever it is copied, sealed inside an app (the release installer, re/dmg/).
#
# Usage: make_kit.sh <out-dir> [<name>]   -> <out-dir>/<name> (default "Track Kommander Kit")
# TK_REPO_OVERRIDE: build from another checkout (default: this script's repo).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_MINOR="3.13"

build_kit() {
  local out_dir="${1:?usage: make_kit.sh <out-dir> [<name>]}"
  local name="${2:-Track Kommander Kit}"
  local repo="${TK_REPO_OVERRIDE:-$(cd "$HERE/../.." && pwd)}"
  mkdir -p "$out_dir"
  local kit; kit="$(cd "$out_dir" && pwd)/$name"
  if [ -e "$kit" ]; then
    # Only ever our own earlier build (it carries the marker).
    [ -f "$kit/KIT-VERSION" ] || { echo "$kit exists and is not a kit: not touched" >&2; exit 1; }
    rm -rf "$kit"
  fi
  mkdir -p "$kit"

  # The interpreter TK's .venv was made from (uv's python-build-standalone: relocatable).
  local base
  base="$("$repo/.venv/bin/python" -c 'import sys; print(sys.base_prefix)')"
  echo "[1/4] python: $base"
  ditto "$base" "$kit/python"
  rm -rf "$kit/python/lib/python$PY_MINOR/site-packages/"*

  echo "[2/4] TK's runtime packages (uv.lock, no dev, no build extra)"
  ( cd "$repo" && UV_PROJECT_ENVIRONMENT="$kit/.venv" uv sync --frozen --no-dev \
      --no-install-project --python "$kit/python/bin/python$PY_MINOR" --quiet )
  # Only the packages are used: uv's interpreter links, scripts and pyvenv.cfg carry this Mac's
  # absolute paths.
  rm -rf "$kit/.venv/bin" "$kit/.venv/pyvenv.cfg" "$kit/.venv/.lock" "$kit/.venv/CACHEDIR.TAG" \
    "$kit/.venv/.gitignore"

  echo "[3/4] TK's source and data"
  ( cd "$repo" && rsync -a --exclude '__pycache__' --exclude '.DS_Store' \
      src assets Traktor licenses pyproject.toml LICENSE THIRD_PARTY.md "$kit/" )
  mkdir -p "$kit/traktor-native/re" "$kit/clap_model"
  cp "$repo/traktor-native/re/embed_tk.py" "$kit/traktor-native/re/"
  cp "$repo/clap_model/clap_audio.onnx" "$repo/clap_model/clap_text_embeddings.npz" "$kit/clap_model/"

  echo "[4/5] trim to what TK loads (trim_kit.py: PySide6, tests, tkinter...)"
  printf '%s\n' "trimming" > "$kit/KIT-VERSION"  # trim_kit.py only touches a kit
  python3 "$HERE/trim_kit.py" "$kit"

  echo "[5/5] marker + check (the way libtk starts it)"
  local version; version="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$repo/pyproject.toml")"
  local commit; commit="$(git -C "$repo" rev-parse --short HEAD)"
  # "+local": built with uncommitted changes, so the commit alone does not say what is inside.
  [ -z "$(git -C "$repo" status --porcelain -- src traktor-native/re assets Traktor pyproject.toml uv.lock)" ] \
    || commit="$commit+local"
  printf '%s %s\n' "$version" "$commit" > "$kit/KIT-VERSION"
  ( cd / && PYTHONHOME="$kit/python" PYTHONDONTWRITEBYTECODE=1 "$kit/python/bin/python$PY_MINOR" -c \
    'import sys; k = sys.argv[1]; sys.path[:0] = [k + "/.venv/lib/python3.13/site-packages", k + "/src"]; import PySide6, numpy, onnxruntime, av, core.runtime; print("kit python ok:", sys.prefix)' \
    "$kit" )
  # The smoke test: Qt (AVFoundation media), librosa, CLAP, every TK module. Caches go to a
  # scratch folder, never into the kit (libtk does the same: ~/Library/Caches/Track Kommander).
  local cache; cache="$(mktemp -d "${TMPDIR:-/tmp}/tk-kit-smoke.XXXXXX")"
  ( cd / && PYTHONHOME="$kit/python" PYTHONDONTWRITEBYTECODE=1 NUMBA_CACHE_DIR="$cache" \
      QT_MEDIA_BACKEND=darwin "$kit/python/bin/python$PY_MINOR" "$HERE/smoke_kit.py" "$kit" ) \
    2>&1 | grep -v '^qt\.' | tee "$cache/smoke.txt"
  rm -rf "$cache"
  du -sh "$kit"
  echo "built: $kit ($(cat "$kit/KIT-VERSION"))"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    -*|"") echo "usage: make_kit.sh <out-dir> [<name>]" >&2; exit 2 ;;
    *) build_kit "$@" ;;
  esac
fi
