#!/usr/bin/env bash
# Build libtk.dylib, the library TK's embedded version injects into Traktor,
# from a clean checkout: `make libtk` locally, and the release workflow
# (.github/workflows/release.yml) runs the same target.
#
#   1. downloads the pinned frida-gum devkit (macOS arm64) from frida's
#      GitHub releases and checks its sha256;
#   2. builds with traktor-native/src/build.sh against that devkit (not the
#      git-ignored traktor-native/vendor/ copy);
#   3. writes libtk.dylib.sha256 next to the library;
#   4. checks the result (scripts/check_libtk.py): ad-hoc signature, every
#      tk_* export, no private names.
#
# Everything lands in traktor-native/build/ (git-ignored).
#
# Signing: ad-hoc only (build.sh signs with `-s -`). Nothing in the embedded
# version carries the Developer ID (the User's ruling, 2026-11-25), so the
# signing variables the Makefile may have read from .env.local are dropped
# here, and the check fails on anything but an ad-hoc signature.
#
# Bumping frida: set FRIDA_VERSION and FRIDA_GUM_SHA256 below. The digest is
# on the release page, or: gh api repos/frida/frida/releases/tags/<version>
#   --jq '.assets[] | select(.name == "<asset>") | .digest'
set -euo pipefail
unset DEVELOPER_ID_APP NOTARY_APPLE_ID NOTARY_TEAM_ID NOTARY_PASSWORD CODESIGN_KEYCHAIN

FRIDA_VERSION="17.27.4"
FRIDA_GUM_SHA256="5fc78694714813b78e02d0884117880dda68ed713819415c3cb711b80a5b86bd"
ARCH="arm64"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/traktor-native/build"
ASSET="frida-gum-devkit-$FRIDA_VERSION-macos-$ARCH.tar.xz"
URL="https://github.com/frida/frida/releases/download/$FRIDA_VERSION/$ASSET"
DOWNLOADS="$OUT/downloads"
GUM="$OUT/frida-gum-$FRIDA_VERSION-$ARCH"

die() { echo "build_libtk: $*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || die "libtk is a macOS library; build it on a Mac"

sha256_of() { shasum -a 256 "$1" | cut -d' ' -f1; }

# A tarball already downloaded is reused only when its hash still matches.
fetch_devkit() {
    local tarball="$DOWNLOADS/$ASSET"
    mkdir -p "$DOWNLOADS"
    if [ ! -f "$tarball" ] || [ "$(sha256_of "$tarball")" != "$FRIDA_GUM_SHA256" ]; then
        echo "→ downloading $URL"
        rm -f "$tarball" "$tarball.part"
        curl -fsSL --retry 3 -o "$tarball.part" "$URL"
        mv "$tarball.part" "$tarball"
    fi
    local got
    got="$(sha256_of "$tarball")"
    [ "$got" = "$FRIDA_GUM_SHA256" ] || {
        rm -f "$tarball"
        die "sha256 mismatch for $ASSET: got $got, want $FRIDA_GUM_SHA256"
    }
    echo "→ $ASSET sha256 ok ($got)"
    # Fresh every time, and only the two files the build needs.
    rm -rf "$GUM"
    mkdir -p "$GUM"
    tar -xJf "$tarball" -C "$GUM" ./frida-gum.h ./libfrida-gum.a
}

fetch_devkit
TK_GUM_DIR="$GUM" bash "$ROOT/traktor-native/src/build.sh" "$ARCH"
(cd "$OUT" && shasum -a 256 libtk.dylib > libtk.dylib.sha256)
python3 -I "$ROOT/scripts/check_libtk.py" "$OUT/libtk.dylib" --devkit "$GUM/libfrida-gum.a"
echo "→ $OUT/libtk.dylib"
echo "→ $OUT/libtk.dylib.sha256"
