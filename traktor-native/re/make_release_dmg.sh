#!/bin/bash
# Build the Track Kommander disk image for the public release (macOS, Apple Silicon):
#
#   Install Track Kommander.command   dmg/: makes "Traktor Pro 4 TK.app" ON THE USER'S MAC from
#   READ ME.txt                       their own Traktor (no Native Instruments file in the image)
#   Track Kommander Files/
#     tk/                             the kit (make_kit.sh: Python, TK's packages and source,
#                                     trimmed + smoke-tested); sealed inside the app at install
#     libtk.dylib                     traktor-native/build (make libtk)
#     crashpad_handler                crashpad_stub.c, ad hoc: Traktor's crash reporter never runs
#     entitlements.plist              ent_mod.plist
#     macho_add_dylib.py              thins Traktor's executable and adds libtk's load command
#     supported-builds.txt            the Traktor builds libtk is mapped for (hashes)
#
#     tk-addon/                       only with --addon: an edition with an add-on is ONE
#                                     installer, TK and the add-on (User, 2026-11-26)
#
# Ad hoc throughout, no Developer ID (User's ruling, 2026-11-25).
# Usage: make_release_dmg.sh <out.dmg> [--addon <tk-addon folder>] [--name <volume name>]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${TK_REPO_OVERRIDE:-$(cd "$HERE/../.." && pwd)}"
OUT_DMG="${1:?usage: make_release_dmg.sh <out.dmg> [--addon <dir>] [--name <volume name>]}"
shift
ADDON=""
VOLNAME="Track Kommander"
while [ $# -gt 0 ]; do
  case "$1" in
    --addon) ADDON="${2:?--addon needs a folder}"; shift 2 ;;
    --name) VOLNAME="${2:?--name needs a name}"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[ -z "$ADDON" ] || [ -f "$ADDON/manifest.json" ] || { echo "not an add-on (no manifest.json): $ADDON" >&2; exit 1; }
# Its folder may not exist yet (dist/ on a fresh checkout: hdiutil then fails, 2026-11-26).
mkdir -p "$(dirname "$OUT_DMG")"
OUT_DMG="$(cd "$(dirname "$OUT_DMG")" && pwd)/$(basename "$OUT_DMG")"
LIBTK="$REPO/traktor-native/build/libtk.dylib"
[ -f "$LIBTK" ] || { echo "no libtk: make libtk first" >&2; exit 1; }

STAGE="$REPO/worktrees/release-dmg/stage"
FILES="$STAGE/Track Kommander Files"
rm -rf "$STAGE"
mkdir -p "$FILES"

echo "▶ kit"
bash "$HERE/make_kit.sh" "$FILES" tk

echo "▶ libtk, crashpad stand-in, entitlements, patch tool, supported builds"
cp "$LIBTK" "$FILES/libtk.dylib"
codesign --force -s - "$FILES/libtk.dylib"
clang -arch arm64 -mmacosx-version-min=12.0 -O2 -o "$FILES/crashpad_handler" "$HERE/crashpad_stub.c"
codesign --force -s - "$FILES/crashpad_handler"
cp "$HERE/ent_mod.plist" "$FILES/entitlements.plist"
cp "$HERE/macho_add_dylib.py" "$HERE/dmg/supported-builds.txt" "$FILES/"
if [ -n "$ADDON" ]; then
  echo "▶ add-on: $ADDON"
  rsync -a --exclude '__pycache__' --exclude '.DS_Store' "$ADDON/" "$FILES/tk-addon/"
fi

cp "$HERE/dmg/Install Track Kommander.command" "$HERE/dmg/READ ME.txt" "$STAGE/"
chmod 755 "$STAGE/Install Track Kommander.command"
# The way in that macOS never blocks: a text file (no code, so no "Apple could not verify") with
# the one line to paste into Terminal, spelt with this image's own volume name. Double-clicking
# the .command from a downloaded image is refused, and Open Anyway did not get it to run
# (User, 2026-11-26).
cat > "$STAGE/Install - copy this line into Terminal.txt" <<EOF
To install Track Kommander, copy the line below, paste it into Terminal, press Return:

bash "/Volumes/$VOLNAME/Install Track Kommander.command"

Terminal is in Applications > Utilities (or press Cmd+Space, type Terminal, press Return).
It asks for your Mac password once (nothing shows while you type), then any question
comes up in Terminal too: answer y or n. More in "READ ME.txt".

If Terminal answers "No such file or directory": eject every other "$VOLNAME" disk
in Finder, open the downloaded disk image again, and paste the line again.
EOF
# Where the app ends up, one click away (User, 2026-11-26: "i dont know where the installer put
# the file"). The usual place; the installer also shows the app in Finder when it is done.
ln -s "/Applications/Native Instruments/Traktor Pro 4" "$STAGE/Traktor Pro 4 folder"

echo "▶ disk image (lzfse) → $OUT_DMG"
rm -f "$OUT_DMG"
for try in 1 2 3; do  # hdiutil sometimes answers "Resource busy" (2026-11-26); a retry works
  nice -n 19 hdiutil create -volname "$VOLNAME" -srcfolder "$STAGE" -ov -format ULFO "$OUT_DMG" >/dev/null && break
  [ "$try" -lt 3 ] || { echo "hdiutil failed 3 times" >&2; exit 1; }
  sleep 5
done
rm -rf "$STAGE"
ls -lh "$OUT_DMG"
echo "✓ $OUT_DMG"
