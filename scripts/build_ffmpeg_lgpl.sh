#!/usr/bin/env bash
# Build an audio-only, LGPL-only FFmpeg and a PyAV wheel linked against it.
#
# Why: the PyAV wheels on PyPI link libx264 and libx265 (GPL-2.0) into
# libavcodec, so shipping them binds the whole app to the GPL even though
# FFmpeg itself reports "LGPL version 3 or later". Track Kommander only ever
# *decodes audio* (core/audio_io.py) and reads container metadata
# (core/audio_meta.py), so an FFmpeg with every encoder, video codec and
# external library switched off does the same job and carries no GPL code.
#
# The build is LGPL-2.1-or-later: no --enable-gpl, no --enable-version3, no
# --enable-nonfree, and --disable-autodetect so nothing on the build machine
# (Homebrew's x264, say) gets linked in by accident. The script refuses to
# finish unless the built libavcodec reports an LGPL licence.
#
# Output:
#   build/ffmpeg-lgpl/prefix/   FFmpeg shared libraries + headers
#   build/ffmpeg-lgpl/wheels/   av-<ver>-...whl with those libraries inside
#
# Usage:
#   macOS    scripts/build_ffmpeg_lgpl.sh
#   Windows  scripts\build_ffmpeg_lgpl.bat   (sets up MSVC, then runs this
#            under MSYS2 bash; FFmpeg is compiled by MSVC, not MinGW, so it
#            links the same C runtime as Python and the Nuitka build)
#
# Bumping PyAV: set PYAV_VERSION, FFMPEG_VERSION (= av.ffmpeg_version_info of
# that release) and CYTHON_VERSION below; put the new tarball in vendor/source/
# and its sha256 here; update FFMPEG_VERSION in src/core/licensing.py and the
# tarball name in scripts/_build_app.{sh,ps1}; rebuild on macOS and WOTS;
# replace vendor/wheels/ and the paths in pyproject.toml; uv lock.
# tests/unit/test_licensing.py catches a version or tarball mismatch.
#
set -euo pipefail

FFMPEG_VERSION="${FFMPEG_VERSION:-8.0.1}"   # must match what PyAV was released against
PYAV_VERSION="${PYAV_VERSION:-17.0.1}"
PYTHON_VERSION="${PYTHON_VERSION:-3.13}"
# PyAV only bounds Cython to <4; 3.3.0 rejects its source ("'seek_func'
# redeclared" in av/container/pyio.py). 3.2.4 was current at PyAV 17.0.1's
# release. Bump together with PYAV_VERSION.
CYTHON_VERSION="${CYTHON_VERSION:-3.2.4}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-12.0}"

case "$(uname -s)" in
    MINGW*|MSYS*) WINDOWS=1 ;;
    *)            WINDOWS=0 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$ROOT/build/ffmpeg-lgpl"
SRC="$WORK/ffmpeg-$FFMPEG_VERSION"
PREFIX="$WORK/prefix"
WHEELS="$WORK/wheels"
JOBS="$(sysctl -n hw.ncpu 2>/dev/null || nproc)"

# Paths handed to native Windows programs (Python, MSVC) must be Windows paths.
native() { if (( WINDOWS )); then cygpath -w "$1"; else echo "$1"; fi; }

# ── What FFmpeg may contain ────────────────────────────────────────────────
# Formats a DJ library holds: MP3, AAC/ALAC in MP4/M4A, FLAC, Ogg Vorbis/Opus,
# WAV/W64/AIFF/CAF PCM, WavPack, APE, Matroska audio. Embedded cover art needs
# no image decoder — the demuxer hands its bytes over as an attached_pic
# packet, and Qt decodes the JPEG/PNG.
PCM="pcm_s8,pcm_u8,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be,pcm_s32le,pcm_s32be"
PCM="$PCM,pcm_f32le,pcm_f32be,pcm_f64le,pcm_f64be,pcm_alaw,pcm_mulaw"
DECODERS="mp3,mp3float,mp2,mp2float,aac,aac_fixed,aac_latm,alac,flac,vorbis,opus,wavpack,ape,$PCM"
DEMUXERS="mp3,aac,flac,ogg,mov,wav,w64,aiff,caf,matroska,wv,ape"
PARSERS="mpegaudio,aac,aac_latm,flac,vorbis,opus"
# PyAV's AudioResampler runs through a filter graph.
FILTERS="abuffer,abuffersink,aformat,aresample,anull,buffer,buffersink,format,null"

# The source is built from vendor/source/, not downloaded: the same tarball
# ships inside the app (licenses/source/) to meet the LGPL's corresponding-
# source obligation, so what is compiled and what is shipped cannot differ.
TARBALL="$ROOT/vendor/source/ffmpeg-$FFMPEG_VERSION.tar.xz"
TARBALL_SHA256="${TARBALL_SHA256:-05ee0b03119b45c0bdb4df654b96802e909e0a752f72e4fe3794f487229e5a41}"

fetch() {
    mkdir -p "$WORK"
    [[ -f "$TARBALL" ]] || {
        echo "missing $TARBALL — download https://ffmpeg.org/releases/$(basename "$TARBALL")" >&2
        exit 1
    }
    local got
    got="$( (shasum -a 256 "$TARBALL" 2>/dev/null || sha256sum "$TARBALL") | cut -d' ' -f1)"
    [[ "$got" == "$TARBALL_SHA256" ]] || { echo "sha256 mismatch for $TARBALL: $got" >&2; exit 1; }
    rm -rf "$SRC"
    tar -xf "$TARBALL" -C "$WORK"
}

build_ffmpeg() {
    local platform_flags=()
    if (( WINDOWS )); then
        # MSYS2's coreutils ship a /usr/bin/link that shadows MSVC's linker.
        PATH="$(dirname "$(command -v cl)"):$PATH"
        platform_flags=(--toolchain=msvc --target-os=win64 --arch=x86_64)
    fi
    rm -rf "$PREFIX"
    (
        cd "$SRC"
        make distclean >/dev/null 2>&1 || true
        ./configure \
            --prefix="$PREFIX" \
            "${platform_flags[@]}" \
            --enable-shared --disable-static --enable-pic \
            --disable-autodetect \
            --disable-programs --disable-doc --disable-network --disable-debug \
            --disable-everything \
            --enable-protocol=file \
            --enable-decoder="$DECODERS" \
            --enable-demuxer="$DEMUXERS" \
            --enable-parser="$PARSERS" \
            --enable-filter="$FILTERS" \
            --enable-swresample
        make -j"$JOBS"
        make install
    )
}

check_licence() {
    local lib=""
    for f in "$PREFIX"/lib/libavcodec.*.dylib "$PREFIX"/lib/libavcodec.so.* "$PREFIX"/bin/avcodec-*.dll; do
        [[ -f "$f" ]] && { lib="$f"; break; }
    done
    [[ -n "$lib" ]] || { echo "FFmpeg install incomplete: no libavcodec" >&2; exit 1; }
    local licence
    licence="$(uv run --no-project --python "$PYTHON_VERSION" python -c "import ctypes,sys; l=ctypes.CDLL(sys.argv[1]); l.avcodec_license.restype=ctypes.c_char_p; print(l.avcodec_license().decode())" "$(native "$lib")" | tr -d '\r')"
    echo "libavcodec licence: $licence"
    [[ "$licence" == LGPL* ]] || { echo "refusing: FFmpeg is not an LGPL build" >&2; exit 1; }
}

build_pyav() {
    rm -rf "$WHEELS" "$WORK/raw-wheels"
    mkdir -p "$WHEELS"
    local constraints="$WORK/build-constraints.txt"
    echo "cython==$CYTHON_VERSION" > "$constraints"
    export PIP_CONSTRAINT PIP_BUILD_CONSTRAINT
    PIP_CONSTRAINT="$(native "$constraints")"
    PIP_BUILD_CONSTRAINT="$PIP_CONSTRAINT"
    if (( WINDOWS )); then
        # PyAV's setup.py finds FFmpeg on Windows only through MSVC's own
        # INCLUDE / LIB search paths (import libraries land in bin/).
        INCLUDE="$(native "$PREFIX/include");$INCLUDE" \
        LIB="$(native "$PREFIX/lib");$(native "$PREFIX/bin");$LIB" \
        DISTUTILS_USE_SDK=1 \
            uvx --python "$PYTHON_VERSION" pip wheel "av==$PYAV_VERSION" \
                --no-binary av --no-deps --no-cache-dir -w "$(native "$WORK/raw-wheels")"
        # Copy the FFmpeg DLLs into the wheel, as the PyPI wheels do.
        uvx --python "$PYTHON_VERSION" --from delvewheel delvewheel repair \
            --add-path "$(native "$PREFIX/bin")" -w "$(native "$WHEELS")" \
            "$(native "$(ls "$WORK"/raw-wheels/av-*.whl)")"
    else
        PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig" \
            uvx --python "$PYTHON_VERSION" pip wheel "av==$PYAV_VERSION" \
                --no-binary av --no-deps --no-cache-dir -w "$WORK/raw-wheels"
        # Copy the FFmpeg dylibs into the wheel and point av's extensions at
        # them, as the PyPI wheels do — the app must not depend on $PREFIX.
        DYLD_LIBRARY_PATH="$PREFIX/lib" \
            uvx --python "$PYTHON_VERSION" --from delocate delocate-wheel \
                -w "$WHEELS" "$WORK"/raw-wheels/av-*.whl
    fi
    ls -1 "$WHEELS"
}

fetch
build_ffmpeg
check_licence
build_pyav
