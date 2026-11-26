#!/bin/bash
# Build "Traktor Pro 4 TK.app": the patched Traktor as a SLIM bundle beside the original
# (User, 2026-11-24). Only our own files are in it — Traktor's executable (Apple Silicon slice,
# re-signed ad-hoc with the injection entitlements, loading libtk by itself) and its Info.plist —
# while Frameworks, PlugIns and Resources are links to the installed app's folders (~120 MB
# instead of ~490). Mac-only. Never touches the installed app (read only).
#
# Usage: make_slim_app.sh <out-dir>   (e.g. <repo>/worktrees/native-scratch/slim)
# The DEVELOPMENT app: it runs TK from this checkout (tk-embed.conf names it). The release is made
# on the user's Mac by re/dmg/Install Track Kommander.command (make_release_dmg.sh), with TK's kit
# inside the app.
# TK_REPO_OVERRIDE points the build (its .venv, libtk and the installer generator) at another
# TraktorKontrol checkout, so it can run from a worktree that has no .venv of its own.
# The base Traktor version is recorded in Contents/SharedSupport/TK-base-version (apps built
# before 2026-11-25: Contents/TK-base-version): when the installed Traktor
# is updated, its folders change under our older executable, so the launcher refuses to start
# until this is run again (and the dylib's addresses are mapped for the new build).
set -euo pipefail

SRC="/Applications/Native Instruments/Traktor Pro 4/Traktor Pro 4.app"
OUT_DIR="${1:?usage: make_slim_app.sh <out-dir>}"
[ $# -eq 1 ] || { echo "usage: make_slim_app.sh <out-dir>" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${TK_REPO_OVERRIDE:-$(cd "$HERE/../.." && pwd)}"
APP="$OUT_DIR/Traktor Pro 4 TK.app"
EXE="Contents/MacOS/Traktor Pro 4"

[ -d "$SRC" ] || { echo "no installed Traktor at $SRC" >&2; exit 1; }
mkdir -p "$OUT_DIR"
if [ -e "$APP" ]; then
  # Only ever our own build: its Resources is a link to the installed app's (the original's never
  # is), even when an earlier build stopped half way.
  [ -L "$APP/Contents/Resources" ] || { echo "$APP exists and is not ours: not touched" >&2; exit 1; }
  rm -rf "$APP"
fi

echo "[1/5] bundle skeleton, linked to the installed app's folders"
mkdir -p "$APP/Contents/MacOS"
for d in Frameworks PlugIns Resources; do
  ln -s "$SRC/Contents/$d" "$APP/Contents/$d"
done
cp "$SRC/Contents/Info.plist" "$APP/Contents/Info.plist"
[ -f "$SRC/Contents/PkgInfo" ] && cp "$SRC/Contents/PkgInfo" "$APP/Contents/PkgInfo"

echo "[2/5] executable: the Apple Silicon slice"
lipo -thin arm64 "$SRC/$EXE" -output "$APP/$EXE"
# Not NI's crashpad_handler: Traktor's crash reporter sends dumps to Native Instruments (Sentry), and
# a patched Traktor's dumps (libtk, the repo's paths) must not go there (User, 2026-11-24). In its
# place a stand-in that exits at once (crashpad_stub.c): with no file there, the child Traktor forks
# for it died at every launch and macOS wrote a crash report (2026-11-25).
clang -arch arm64 -mmacosx-version-min=12.0 -O2 -o "$APP/Contents/MacOS/crashpad_handler" "$HERE/crashpad_stub.c"
codesign --force -s - "$APP/Contents/MacOS/crashpad_handler"

echo "[3/5] libtk loaded by the executable itself (Finder / Dock launch: no DYLD_INSERT_LIBRARIES)"
codesign --remove-signature "$APP/$EXE"
"$REPO/.venv/bin/python" -I "$HERE/macho_add_dylib.py" "$APP/$EXE" "@executable_path/libtk.dylib"
# A link to the repo's build: a rebuild of libtk is what the app loads next time.
ln -s "$REPO/traktor-native/build/libtk.dylib" "$APP/Contents/MacOS/libtk.dylib"

echo "[4/5] TK's settings and the version marker, in SharedSupport/ (sealed by the signature)"
# Before signing, so the seal covers them: codesign refuses plain files directly in Contents/
# (it takes them for nested code), but seals them in a folder such as SharedSupport/.
mkdir -p "$APP/Contents/SharedSupport"
cat > "$APP/Contents/SharedSupport/tk-embed.conf" <<EOF
# Read by libtk at load (tk_inject.mm load_embed_conf); a variable already set wins.
TK_EMBED=$REPO/traktor-native/re/embed_tk.py
TK_REPO=$REPO
TK_PYTHONHOME=$("$REPO/.venv/bin/python" -c 'import sys; print(sys.base_prefix)')
TK_DYLIB_LOG=/tmp/tk_dylib.log
EOF
/usr/libexec/PlistBuddy -c "Print :CFBundleVersion" "$SRC/Contents/Info.plist" \
  > "$APP/Contents/SharedSupport/TK-base-version"

echo "[5/5] sign the executable (it seals the bundle: links, the libtk link, SharedSupport/)"
codesign --force --options runtime --timestamp=none \
  --entitlements "$HERE/ent_mod.plist" -s - "$APP/$EXE"

codesign -dv "$APP/$EXE" 2>&1 | grep -E "Signature|flags|Identifier=" || true
otool -L "$APP/$EXE" | grep libtk
du -sh "$APP/Contents/MacOS"
echo "built: $APP (base Traktor $(cat "$APP/Contents/SharedSupport/TK-base-version"))"
