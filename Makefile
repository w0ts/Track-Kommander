# Track Kommander — Makefile
# ─────────────────────────────────────────────────────────────────────────────
# Build/package split (mirrors the Cath-Axis convention):
#   make venv       uv sync — create/refresh .venv (runtime + build deps)
#   make lock       uv lock — refresh uv.lock from pyproject.toml
#   make run        run the app from source
#   make lint       ruff check + format --check
#   make typecheck  pyright on src/core (zero errors required)
#   make test       run the unit tests
#   make check      every gate, no build: lockfile, lint, types,
#                   suppression ratchet, tests — what the release workflows run
#   make libtk      macOS: traktor-native/build/libtk.dylib, the library TK
#                   runs inside Traktor with (ad-hoc signed, pinned frida-gum)
#   make release    macOS: the release disk image, "Track Kommander.dmg" (its
#                   installer patches the user's own Traktor on their Mac)
#   make clean      remove dist/, build/
#   make distclean  clean + remove .venv
#
# Cross-platform: macOS/Linux run native POSIX recipes. On Windows, GNU Make is
# invoked from cmd.exe / PowerShell where there are no POSIX tools (awk, sh, rm)
# on PATH, so every target delegates to make.ps1 — always runnable via
# powershell.exe. `make` works from cmd, PowerShell, or Git Bash on Windows;
# the behaviour is identical.
# ─────────────────────────────────────────────────────────────────────────────

APP_NAME       := Track Kommander
ENTRY          := src/TrackKommander.py
PYTHON_VERSION := 3.13
VENV           := .venv
DIST           := dist

.DEFAULT_GOAL := help
.PHONY: help venv lock libtk release site run lockcheck lint typecheck suppressions test check models clean distclean

# CLAP ONNX artefacts (gitignored, ~110 MB) — produced once by
# scripts/export_clap_onnx.py. `make models` generates them if missing; build
# depends on it so a fresh clone / CI runner self-provisions the model.
CLAP_ONNX := clap_model/clap_audio.onnx
CLAP_EMB  := clap_model/clap_text_embeddings.npz

ifeq ($(OS),Windows_NT)
# ══════════════════════════════ Windows ══════════════════════════════════════
# Thin dispatcher: hand each target to the PowerShell task runner. We avoid
# every POSIX construct here (no awk/sh/rm, no $(shell …)) because none of
# them exist when make is launched from a stock Windows shell. `OS` is set to
# "Windows_NT" by Windows itself, so this branch is also taken under Git Bash.
PS := powershell -NoProfile -ExecutionPolicy Bypass -File make.ps1

help:      ; @$(PS) help
venv:      ; @$(PS) venv
lock:      ; @$(PS) lock
run:       ; @$(PS) run
lockcheck: ; @$(PS) lockcheck
lint:      ; @$(PS) lint
typecheck: ; @$(PS) typecheck
suppressions: ; @$(PS) suppressions
test:      ; @$(PS) test
check:     ; @$(PS) check
models:    ; @$(PS) models
clean:     ; @$(PS) clean
distclean: ; @$(PS) distclean

else
# ══════════════════════════════ macOS / Linux ════════════════════════════════

VENV_PY        := $(VENV)/bin/python

# Project-local env: pick up DEVELOPER_ID_APP / NOTARY_* from .env.local
# without touching the user's shell profile. The file is gitignored.
# Lines use plain ``KEY=value`` (Make syntax); `export` propagates them
# into the subshell used by `scripts/_build_app.sh`.
ifneq ($(wildcard .env.local),)
    include .env.local
    export
endif

# Resolve which `uv` to use. Prefer one already on PATH (Homebrew-installed,
# user-installed, etc.). If not, fall back to a project-local bootstrap that
# we install on first run via the stdlib venv + pip — that way ``make run``
# works on a fresh clone with only python3 on PATH.
UV_SYSTEM := $(shell command -v uv 2>/dev/null)
ifeq ($(UV_SYSTEM),)
    UV := .uv-bootstrap/bin/uv
else
    UV := $(UV_SYSTEM)
endif


help:
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ── Environment (uv-managed) ─────────────────────────────────────────────────

# Bootstrap target — creates .uv-bootstrap/ with uv installed, ONLY when
# the system doesn't have a uv on PATH. Idempotent: once .uv-bootstrap/bin/uv
# exists, this rule is a no-op.
.uv-bootstrap/bin/uv:
	@echo "→ uv not on PATH; bootstrapping into .uv-bootstrap/ (one-time)"
	@python3 -m venv .uv-bootstrap
	@.uv-bootstrap/bin/pip install --quiet uv
	@echo "  uv $$(.uv-bootstrap/bin/uv --version | awk '{print $$2}') installed"

# Which extras the venv gets. The checks need only `dev`; a hosted Linux
# runner passes EXTRAS="--extra dev" so it does not download torch's CUDA
# wheels (several GB) for a test run.
EXTRAS ?= --extra build --extra dev

venv: $(UV) ## uv sync — create/refresh .venv (runtime + build + dev deps; EXTRAS= to narrow)
	$(UV) sync --python $(PYTHON_VERSION) $(EXTRAS)

lock: $(UV) ## uv lock — refresh uv.lock
	$(UV) lock --python $(PYTHON_VERSION)

# Stamp file the build target depends on — uv sync re-runs only when
# pyproject.toml changes since the last sync.
$(VENV_PY): pyproject.toml
	@$(MAKE) --no-print-directory venv

# ── Run / build ──────────────────────────────────────────────────────────────
# libtk.dylib, the library the embedded version injects into Traktor: pinned
# frida-gum devkit downloaded + sha256-checked, built, ad-hoc signed, checked.
# The same path the release workflow (release.yml) runs. Never the
# Developer ID: scripts/build_libtk.sh drops what .env.local put here.
libtk: ## build traktor-native/build/libtk.dylib (+ .sha256), ad-hoc signed
	scripts/build_libtk.sh

# The release disk image (macOS): TK's kit trimmed + smoke-tested, libtk, and the installer that
# makes "Traktor Pro 4 TK.app" on the user's Mac from their own Traktor. RELEASE_DMG= elsewhere.
RELEASE_DMG ?= $(DIST)/Track Kommander.dmg
release: libtk ## the release disk image → dist/Track Kommander.dmg
	bash traktor-native/re/make_release_dmg.sh "$(RELEASE_DMG)"

# The site GitHub Pages serves from this repo's root: index.html and its sibling pages, rendered
# from site/SITE.md (the one source; the pages are never edited by hand). Commit them after.
site: $(VENV_PY) ## render site/SITE.md into index.html and the other pages
	cp site/SITE.md SITE.md
	$(VENV_PY) scripts/public_site.py .

run: $(VENV_PY) ## run the app from source
	$(VENV_PY) $(ENTRY)

lint: $(VENV_PY) ## ruff check + format src, tests, scripts (zero errors required)
	$(VENV_PY) -m ruff check src tests scripts
	$(VENV_PY) -m ruff format --check src tests scripts

# What a release is checked against. The release workflow runs this before it
# builds, so an installer cannot be published over a failing test.
typecheck: $(VENV_PY) ## pyright on src/core (zero errors required)
	$(VENV_PY) -m pyright

suppressions: $(VENV_PY) ## no new noqa / type: ignore (ratchet in scripts/checks/suppressions.py)
	$(VENV_PY) scripts/checks/suppressions.py

lockcheck: $(UV) ## uv.lock matches pyproject.toml
	$(UV) lock --check

test: $(VENV_PY) ## run the unit tests
	$(VENV_PY) -m pytest -q -rs

check: lockcheck lint typecheck suppressions test ## every gate, no build (what the release workflows run)
	@echo "All checks passed."

# Ensure the CLAP artefacts (clap_audio.onnx + clap_text_embeddings.npz) exist.
# On a fresh checkout they're absent (gitignored, ~110 MB); the export script
# downloads laion/clap-htsat-fused and transforms it to ONNX. --ensure makes
# this a cheap no-op once the artefacts are present. Cross-platform (pure
# Python), so the same target works for the macOS and Windows builds.
models: $(VENV_PY) ## download + export the CLAP model if missing
	$(VENV_PY) scripts/export_clap_onnx.py --ensure


# ── Cleanup ──────────────────────────────────────────────────────────────────
clean: ## remove build artefacts
	rm -rf build $(DIST) "$(DIST).lock" dist-nuitka

distclean: clean ## clean + remove venv
	rm -rf $(VENV) .uv-bootstrap

endif
