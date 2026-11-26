#!/bin/bash
# Build libtk.dylib (Phase 1). Links the vendored frida-gum static devkit.
# Usage: src/build.sh [arm64|universal]   (default arm64 for local testing)
#
# frida-gum is used instead of Dobby: it relocates the target code page via
# mach_vm_remap and patches through a writable alias, so it works on the
# code-signed arm64 Traktor main binary where in-place __TEXT patching is
# blocked by macOS code-signing. Vendor the devkit per arch into
# vendor/frida-gum/: libfrida-gum.a (arm64) and, for universal,
# libfrida-gum-x86_64.a. TK_GUM_DIR points elsewhere: scripts/build_libtk.sh
# (`make libtk`, the release workflow) downloads the pinned devkit, checks
# its sha256 and builds with it from build/, so a clean checkout needs no
# vendored copy.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCHS="${1:-arm64}"
GUM="${TK_GUM_DIR:-$ROOT/vendor/frida-gum}"
OUT="$ROOT/build"; mkdir -p "$OUT"

# frida-gum static needs these system libs/frameworks on macOS.
GUMLIBS="-lresolv -lbsm -framework Foundation -framework AppKit -framework Security -framework IOKit -framework CoreFoundation -framework CoreAudio"
CXXFLAGS="-dynamiclib -std=c++17 -ObjC++ -fobjc-arc -I$GUM -O2 -Wall"

build_one() {  # $1=arch  $2=gum .a  $3=out dylib
  nice -n 19 clang++ -arch "$1" $CXXFLAGS -o "$3" "$ROOT/src/tk_inject.mm" \
    "$2" $GUMLIBS -I"$ROOT/src"
}

if [ "$ARCHS" = universal ]; then
  [ -f "$GUM/libfrida-gum-x86_64.a" ] || { echo "need $GUM/libfrida-gum-x86_64.a (download the macos-x86_64 devkit)"; exit 1; }
  build_one arm64  "$GUM/libfrida-gum.a"         "$OUT/libtk-arm64.dylib"
  build_one x86_64 "$GUM/libfrida-gum-x86_64.a"  "$OUT/libtk-x86_64.dylib"
  lipo -create "$OUT/libtk-arm64.dylib" "$OUT/libtk-x86_64.dylib" -output "$OUT/libtk.dylib"
  rm -f "$OUT/libtk-arm64.dylib" "$OUT/libtk-x86_64.dylib"
else
  build_one "$ARCHS" "$GUM/libfrida-gum.a" "$OUT/libtk.dylib"
fi
codesign --force -s - "$OUT/libtk.dylib"
echo "built: $OUT/libtk.dylib"; lipo -archs "$OUT/libtk.dylib"; codesign -dv "$OUT/libtk.dylib" 2>&1 | grep Signature
