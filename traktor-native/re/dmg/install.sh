#!/bin/bash
# Install Track Kommander. In Terminal (the disk image's "Install - copy this line into
# Terminal.txt" holds the line; it lives in "Track Kommander Files", beside what it installs):
#   bash "/Volumes/Track Kommander/Track Kommander Files/install.sh"
#
# Makes "Traktor Pro 4 TK.app" on this Mac, from YOUR Traktor Pro 4, and puts it beside it:
#   - Traktor's executable, its Apple Silicon part only, told to load Track Kommander (libtk),
#   - Track Kommander itself inside the app (Python, its libraries, TK: "Track Kommander Files"),
#   - Traktor's own Frameworks, PlugIns and Resources through links (not copied),
#   - signed "ad hoc" for this Mac (no Developer ID: a changed Traktor cannot keep NI's signature).
# Your Traktor Pro 4 is never changed; the disk image holds no Native Instruments file.
# It asks for the Mac password ONCE, at the start (sudo: Traktor's folder is protected), then
# runs as administrator; every question is asked here in Terminal (User, 2026-11-26: macOS's
# dialogs and a password prompt half way were too many steps). An earlier copy goes to the Bin.
set -Eeuo pipefail  # -E: the ERR trap below also fires inside functions

DRY="${TK_INSTALL_DRY_RUN:-}"  # test hook (make_release_dmg.sh): build into this folder, no sudo

# Traktor running (the normal one or the TK copy: both are "Traktor Pro 4"): say so and wait for
# it to quit, then go on by itself (User, 2026-11-26: it stopped instead). First, before the
# password, so the message is the first thing seen.
wait_for_traktor() {
  pgrep -xq "Traktor Pro 4" || return 0
  echo "Traktor is running. Quit Traktor: the installer goes on by itself (Ctrl+C cancels)."
  while pgrep -xq "Traktor Pro 4"; do sleep 1; done
  echo "Traktor has quit."
}
[ -n "$DRY" ] || wait_for_traktor

if [ "$(id -u)" -ne 0 ] && [ -z "$DRY" ]; then
  echo "Track Kommander needs your Mac password once: Traktor's folder is protected."
  exec /usr/bin/sudo /bin/bash "$0" "$@"
fi

FILES="$(cd "$(dirname "$0")" && pwd)"  # "Track Kommander Files": this script's own folder
SLIM="Traktor Pro 4 TK.app"
TRAKTOR_NAME="Traktor Pro 4.app"
EXE_REL="Contents/MacOS/Traktor Pro 4"
USUAL_DIR="/Applications/Native Instruments/Traktor Pro 4"
STAMP="$(date +%Y-%m-%d-%H%M%S)"
# The person who ran it (sudo keeps their name): their Bin, and Traktor opens as them, never root.
USER_NAME="${SUDO_USER:-$(id -un)}"
USER_HOME="$(/usr/bin/dscl . -read "/Users/$USER_NAME" NFSHomeDirectory | awk '{print $2}')"

# Everything shown here also goes to a log, the user's own (2026-11-26: a run stopped half way
# and its Terminal window was closed: nothing told why).
LOG_DIR="$USER_HOME/Library/Logs/Track Kommander"
mkdir -p "$LOG_DIR" && chown "$USER_NAME" "$LOG_DIR" 2>/dev/null || true
LOG="$LOG_DIR/install.log"
exec > >(tee -a "$LOG") 2>&1
chown "$USER_NAME" "$LOG" 2>/dev/null || true
echo "=== $(date '+%Y-%m-%d %H:%M:%S') install, as $(id -un) for $USER_NAME"
trap 'echo; echo "STOPPED: line $LINENO failed: $BASH_COMMAND"; echo "(the log: $LOG)"' ERR

stop() {
  echo
  echo "STOPPED: $1"
  echo "(the log: $LOG)"
  exit 1
}
ask() {  # ask "<question>" -> 0 on y/yes (from the keyboard, also when piped)
  local answer=""
  printf "\n%s [y/N] " "$1"
  read -r answer < /dev/tty || true
  case "$answer" in [yY]|[yY][eE][sS]) return 0 ;; *) return 1 ;; esac
}
sha() { /usr/bin/shasum -a 256 "$1" | cut -d' ' -f1; }
ours() { [ -L "$1/Contents/Resources" ]; }  # a TK app: Resources links to Traktor's
unquarantine() {  # our own files and folders only: find never follows the links into Traktor
  find "$1" \( -type f -o -type d \) -exec /usr/bin/xattr -d com.apple.quarantine {} + 2>/dev/null || true
}
to_bin() {  # into the user's Bin, theirs again (never deleted)
  local target; target="$USER_HOME/.Trash/$(basename "$1" .app) $STAMP.app"
  mv "$1" "$target"
  chown -R "$USER_NAME" "$target" 2>/dev/null || true
}

echo "Track Kommander — installing from $FILES"

# ── What is in the disk image ─────────────────────────────────────────────────────────────────
for f in tk/KIT-VERSION libtk.dylib crashpad_handler entitlements.plist macho_add_dylib.py supported-builds.txt; do
  [ -e "$FILES/$f" ] || stop "\"Track Kommander Files/$f\" is missing. Run this installer from the Track Kommander disk image."
done

# ── This Mac ──────────────────────────────────────────────────────────────────────────────────
[ "$(uname -m)" = "arm64" ] || stop "Track Kommander needs an Apple Silicon Mac (M1 or later)."
[ -n "$DRY" ] || wait_for_traktor  # again: it may have been started while the password was typed

# Traktor Pro 4.app: Spotlight first, then the usual place; never a TK copy. The supported build
# is recognised by its executable's hash (supported-builds.txt).
traktor="" ; expected_thin="" ; build_name="" ; seen=""
candidates=()
while IFS= read -r found; do candidates+=("$found"); done < <(
  mdfind "kMDItemCFBundleIdentifier == 'com.native-instruments.Traktor'" 2>/dev/null || true)
candidates+=("$USUAL_DIR/$TRAKTOR_NAME")
for c in "${candidates[@]}"; do
  [ "$(basename "$c")" = "$TRAKTOR_NAME" ] && [ -f "$c/$EXE_REL" ] && ! ours "$c" || continue
  line="$(grep -m1 "^$(sha "$c/$EXE_REL") " "$FILES/supported-builds.txt" || true)"
  if [ -n "$line" ]; then
    if [ -z "$traktor" ] || [ "$c" = "$USUAL_DIR/$TRAKTOR_NAME" ]; then
      traktor="$c"; expected_thin="$(echo "$line" | cut -d' ' -f2)"; build_name="$(echo "$line" | cut -d' ' -f3-)"
    fi
  else
    seen="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$c/Contents/Info.plist" 2>/dev/null || echo "another version")"
  fi
done
supported="$(grep -v '^#' "$FILES/supported-builds.txt" | cut -d' ' -f3- | paste -sd, - | sed 's/,/, /g')"
if [ -z "$traktor" ]; then
  [ -n "$seen" ] && stop "This Track Kommander runs in $supported. Your Traktor is $seen."
  stop "Traktor Pro 4 was not found on this Mac ($supported needed)."
fi
traktor_dir="$(dirname "$traktor")"
dest="$traktor_dir/$SLIM"
echo "  $build_name: $traktor"

# ── Earlier copies: ask first; they go to the Bin, never deleted ──────────────────────────────
old_app=0
if [ -z "$DRY" ]; then
  if [ -e "$dest" ]; then
    ours "$dest" || stop "$dest exists and is not a Track Kommander app; not touched."
    ask "\"$SLIM\" is already installed. Replace it? (the old one goes to the Bin)" \
      || stop "Cancelled: nothing was changed."
    old_app=1
  fi
fi

# ── Make the app in a work folder ─────────────────────────────────────────────────────────────
work="$(mktemp -d "${TMPDIR:-/tmp}/tk-install.XXXXXX")"
incoming=""  # the new app while it is being put in place (below); gone if the run stops
trap 'rm -rf "$work"; [ -z "$incoming" ] || rm -rf "$incoming"' EXIT
app="$work/$SLIM"
exe="$app/$EXE_REL"
mkdir -p "$app/Contents/MacOS" "$app/Contents/SharedSupport"
for d in Frameworks PlugIns Resources; do ln -s "$traktor/Contents/$d" "$app/Contents/$d"; done
cp "$traktor/Contents/Info.plist" "$app/Contents/Info.plist"
[ -f "$traktor/Contents/PkgInfo" ] && cp "$traktor/Contents/PkgInfo" "$app/Contents/PkgInfo"

echo "  copying Track Kommander into the app (about 600 MB)…"
/usr/bin/ditto "$FILES/tk" "$app/Contents/SharedSupport/tk"
cp "$FILES/libtk.dylib" "$FILES/crashpad_handler" "$app/Contents/MacOS/"
# An edition with an add-on: TK and the add-on in one installer, the add-on beside TK.
if [ -f "$FILES/tk-addon/manifest.json" ]; then
  /usr/bin/ditto "$FILES/tk-addon" "$app/Contents/SharedSupport/tk-addon"
fi
unquarantine "$app"  # a downloaded image marks every file; macOS would refuse to load them
py="$app/Contents/SharedSupport/tk/python/bin/python3.13"

echo "  Traktor's executable: the Apple Silicon part, loading Track Kommander…"
# -I: none of the user's Python settings; -B: no bytecode written (the kit is sealed next).
"$py" -I -B "$FILES/macho_add_dylib.py" --thin-arm64 "$traktor/$EXE_REL" "$exe" >/dev/null
chmod 755 "$exe"
[ "$(sha "$exe")" = "$expected_thin" ] || stop "Traktor's executable is not what was expected (its Apple Silicon part differs). Nothing was installed."
/usr/bin/codesign --remove-signature "$exe"
"$py" -I -B "$FILES/macho_add_dylib.py" "$exe" "@executable_path/libtk.dylib" >/dev/null

# Settings, sealed by the signature (tk_inject.mm load_embed_conf), and the Traktor build marker.
cat > "$app/Contents/SharedSupport/tk-embed.conf" <<EOF
# Read by libtk at load; a variable already set wins.
TK_EMBED_LOG=~/Library/Logs/Track Kommander/embed.log
TK_DYLIB_LOG=/tmp/tk_dylib.log
EOF
/usr/libexec/PlistBuddy -c "Print :CFBundleVersion" "$traktor/Contents/Info.plist" \
  > "$app/Contents/SharedSupport/TK-base-version"

echo "  signing it for this Mac (ad hoc)…"
/usr/bin/codesign --force --options runtime --timestamp=none \
  --entitlements "$FILES/entitlements.plist" -s - "$exe" \
  || stop "Signing failed (see above). Nothing was installed."

if [ -n "$DRY" ]; then
  /usr/bin/ditto "$app" "$DRY/$SLIM"
  echo "dry run: built $DRY/$SLIM"
  exit 0
fi

# ── Into Traktor's folder: the new app first, under another name; only then the old one goes ──
echo "  installing…"
incoming="$traktor_dir/.$SLIM.installing"
rm -rf "$incoming"
/usr/bin/ditto "$app" "$incoming" || stop "Copying the app into $traktor_dir failed (see above). Nothing was changed."
if [ "$old_app" -eq 1 ]; then to_bin "$dest"; fi
mv "$incoming" "$dest"
incoming=""
[ -x "$dest/$EXE_REL" ] || stop "The app is not where it should be (or cannot run): $dest"
echo "      $dest"

echo
echo "Installed. Opening \"Traktor Pro 4 TK\" now: Track Kommander appears inside Traktor after a few"
echo "seconds. Next time, open \"Traktor Pro 4 TK\" yourself; it is next to Traktor Pro 4."
echo "(A Finder window shows it. The disk image has a shortcut to this folder: \"Traktor Pro 4 folder\".)"
/usr/bin/sudo -u "$USER_NAME" /usr/bin/open -R "$dest" || true  # show it in Finder
/usr/bin/sudo -u "$USER_NAME" /usr/bin/open "$dest" || echo "Could not open it: open \"$dest\" yourself."
