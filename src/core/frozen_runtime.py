"""Runtime shims required only in the compiled (Nuitka) app.

Two independent problems show up once librosa is imported from the frozen
bundle. Both are fixed here, and both are gated on ``is_frozen()`` by the
callers so development behaviour is unchanged.

1. ``librosa.core.fft`` is missing from the bundle entirely.
   The build excludes it from Nuitka compilation
   (``--nofollow-import-to=librosa.core.fft`` — Nuitka 4.0.8's optimiser
   chokes on its module-global ``__FFTLIB`` reassignment) and *assumes* the
   loose ``.py`` ships on disk for CPython's finder to pick up. It does not:
   the bundle's ``librosa/core/`` holds only data files, so ``import librosa``
   dies with::

       ModuleNotFoundError: No module named 'librosa.core.fft'
       (librosa/core/audio.py:19 → from .fft import get_fftlib)

   The module is trivial and stable (deprecated since 0.11): it just holds the
   active FFT backend, defaulting to ``scipy.fft``, behind ``get_fftlib`` /
   ``set_fftlib``. We register an equivalent module in ``sys.modules`` so the
   ``from .fft import get_fftlib`` lines across librosa resolve to it.

2. Numba's on-disk cache has no usable "locator" in the bundle.
   librosa JITs functions with ``@numba.jit(cache=True)`` /
   ``@numba.guvectorize(cache=True)`` (``_localmax`` & friends). Numba picks a
   cache locator from each function's source file, and every built-in locator
   bails when the ``.py`` isn't on disk (and ``sys.frozen`` is unset — which we
   must keep unset, else CPython's import finder is disabled). We append a
   fallback locator that always succeeds, keyed to the app executable. It is
   *appended*, so numba's normal locators still win in development.

Call :func:`apply_frozen_runtime_shims` once per process, before any librosa
import, in every process that runs analysis (the GUI process's analysis
threads, and each spawned hotcue worker process).
"""

from __future__ import annotations

import logging
import os
import sys
import types
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

_FFT_INSTALLED = False
_NUMBA_INSTALLED = False
_NUMBA_JIT_DISABLED = False
#: The numba config attribute that turns the JIT off (see disable_numba_jit).
_NUMBA_JIT_FLAG = "DISABLE_JIT"


def disable_numba_jit() -> None:
    """Force numba into pure-Python mode in the compiled (Nuitka) app.

    In the Nuitka bundle a numba ``@jit`` / ``@guvectorize`` / ``@stencil``
    function cannot be JIT-compiled: numba reads the function's bytecode to
    compile it, but Nuitka has replaced that bytecode with a stub that raises
    ``RuntimeError("Compiled function bytecode used")``. So every cache *miss*
    (i.e. any machine without a pre-built numba cache — every fresh install)
    makes librosa's signal-feature helpers raise, and the whole signal-feature
    block is skipped: tracks come back with no danceability / HFC / spectral
    centroid / dynamic-complexity / melodic values.

    Setting ``DISABLE_JIT`` makes numba skip compilation entirely and call the
    *original* Python implementation. In the bundle that implementation is the
    Nuitka-compiled C function, invoked directly (never via the stub bytecode),
    so the features compute correctly on any CPU — and with no native codegen
    there is also no AVX/ISA-mismatch crash risk. Our signal path is dominated
    by numpy/scipy, so the loss of JIT on the few small numba helpers is
    negligible.

    Both the env var (read by numba at import) and the live config flag (read
    per dispatch) are set, so this works whether or not numba is already
    imported. Idempotent; frozen-only (gated by the caller).
    """
    global _NUMBA_JIT_DISABLED
    if _NUMBA_JIT_DISABLED:
        return
    os.environ["NUMBA_DISABLE_JIT"] = "1"
    try:
        import numba

        # numba builds its config module at import time from the environment,
        # so the attribute is not declared anywhere a type checker can see.
        setattr(numba.config, _NUMBA_JIT_FLAG, True)
    except Exception as exc:  # pragma: no cover — numba should be importable
        log.warning("disable_numba_jit: could not set numba config: %s", exc)
    _NUMBA_JIT_DISABLED = True
    log.info("numba JIT disabled (frozen app) — librosa runs pure-Python helpers")


class _FftShim(types.ModuleType):
    """Stand-in module object carrying the two attributes librosa reads."""

    __all__: list[str]
    get_fftlib: Callable[[], Any]
    set_fftlib: Callable[..., None]


def ensure_librosa_core_fft() -> None:
    """Register a stand-in ``librosa.core.fft`` in ``sys.modules`` (idempotent).

    IMPORTANT: this does NO heavy importing. An earlier version attempted
    ``import librosa.core.fft`` first (to prefer the real module); in the Nuitka
    bundle that imports librosa's package init AND triggers Nuitka's
    excluded-module exception path on this worker thread, which segfaulted the
    process (SIGSEGV in tuple dealloc). So we only build a tiny module object
    here — pure Python, no imports — and defer the one real import (scipy.fft)
    into get_fftlib(), which runs later on the pool thread where librosa init is
    already known to work.

    The real module is genuinely absent from the bundle (excluded from Nuitka
    compilation), and this shim is only called when frozen, so there is nothing
    to "prefer" — the stand-in is functionally identical to librosa's own
    deprecated fft.py: a container exposing the scipy.fft backend via
    get_fftlib()/set_fftlib(), which is all librosa's internals read.
    """
    global _FFT_INSTALLED
    if _FFT_INSTALLED or "librosa.core.fft" in sys.modules:
        _FFT_INSTALLED = True
        return

    mod = _FftShim("librosa.core.fft")
    mod.__doc__ = "Shimmed librosa.core.fft (Nuitka bundle) — see core.frozen_runtime."
    mod.__package__ = "librosa.core"
    mod.__all__ = ["get_fftlib", "set_fftlib"]

    # Backend holder. Default (None) means "scipy.fft", imported lazily on first
    # use so nothing heavy is imported on this thread at shim time.
    _state: dict[str, Any] = {"lib": None}

    def get_fftlib():
        lib = _state["lib"]
        if lib is None:
            import scipy.fft as _sf  # lazy: runs on the caller's (pool) thread

            lib = _state["lib"] = _sf
        return lib

    def set_fftlib(lib=None):
        if lib is None:
            import scipy.fft as _sf

            lib = _sf
        _state["lib"] = lib

    mod.get_fftlib = get_fftlib
    mod.set_fftlib = set_fftlib

    sys.modules["librosa.core.fft"] = mod
    _FFT_INSTALLED = True
    log.info("librosa.core.fft shim registered (frozen app)")


def install_numba_fallback_cache_locator() -> None:
    """Append an always-succeeds numba cache locator (idempotent).

    Covers every numba cache path (``@jit``, ``@guvectorize``, gufunc wrapper,
    code libraries) — they all resolve a locator via
    ``CacheImpl._locator_classes``.
    """
    global _NUMBA_INSTALLED
    if _NUMBA_INSTALLED:
        return

    try:
        from numba.core import caching as _caching
    except Exception as exc:
        log.warning("numba cache-locator shim skipped: %s", exc)
        return

    base_locator = getattr(_caching, "_CacheLocator", None)
    cache_impl = getattr(_caching, "CacheImpl", None)
    app_dirs = getattr(_caching, "AppDirs", None)
    if base_locator is None or cache_impl is None or app_dirs is None:
        log.warning("numba cache-locator shim skipped: unexpected numba internals")
        return

    class _FrozenCacheLocator(base_locator):
        """User-wide cache locator that works without on-disk sources."""

        def __init__(self, py_func, py_file):
            self._py_file = py_file
            self._lineno = py_func.__code__.co_firstlineno
            cache_dir = app_dirs(appname="numba", appauthor=False).user_cache_dir
            self._cache_path = os.path.join(cache_dir, self.get_suitable_cache_subpath(py_file))

        def get_cache_path(self):
            return self._cache_path

        def get_source_stamp(self):
            # Sources aren't on disk; key the cache to the app binary so it
            # invalidates when the app is rebuilt/updated.
            st = os.stat(sys.executable)
            return st.st_mtime, st.st_size

        def get_disambiguator(self):
            return str(self._lineno)

        @classmethod
        def from_function(cls, py_func, py_file):
            self = cls(py_func, py_file)
            try:
                self.ensure_cache_path()
            except OSError:
                return None
            return self

    if _FrozenCacheLocator not in cache_impl._locator_classes:
        cache_impl._locator_classes = list(cache_impl._locator_classes) + [_FrozenCacheLocator]

    _NUMBA_INSTALLED = True
    log.info("numba fallback cache locator installed (frozen app)")


#: The sklearn modules librosa imports at the top of decompose.py and
#: segment.py (librosa 0.11).
_SKLEARN_MODULES = ("decomposition", "cluster", "feature_extraction", "neighbors")


def ensure_sklearn_decomposition_stub() -> None:
    """Satisfy librosa's sklearn imports without sklearn.

    The build leaves sklearn out (a large, otherwise dead dependency), but
    ``librosa.decompose`` and ``librosa.segment`` import it at the top, and
    ``librosa.effects`` imports ``decompose`` — so ``librosa.effects.hpss``
    (the melodic feature) and the bassline's HPSS raised ModuleNotFoundError.
    Only functions nothing here calls use sklearn (NMF, clustering,
    nearest neighbours); anything taken from the stand-ins raises when used.
    A real sklearn, where installed, is left alone.
    """
    try:
        import sklearn  # noqa: F401

        return
    except ImportError:
        pass
    import types

    def _not_bundled(*_a, **_k):
        raise RuntimeError("sklearn is not bundled with Track Kommander")

    class _Stub(types.ModuleType):
        def __getattr__(self, name: str):
            if name.startswith("__"):
                raise AttributeError(name)
            return _not_bundled

    pkg = _Stub("sklearn")
    pkg.__path__ = []
    sys.modules.setdefault("sklearn", pkg)
    for name in _SKLEARN_MODULES:
        sub = _Stub(f"sklearn.{name}")
        setattr(pkg, name, sub)
        sys.modules.setdefault(f"sklearn.{name}", sub)
    log.info("sklearn stand-ins registered (frozen app)")


def apply_frozen_runtime_shims() -> None:
    """Apply all frozen-bundle shims. Idempotent; call before importing librosa."""
    ensure_librosa_core_fft()
    ensure_sklearn_decomposition_stub()
    # Disable JIT first: in the bundle numba can't compile (Nuitka stub
    # bytecode), so we run librosa's helpers as pure Python. This makes the
    # cache locator below moot, but it is kept (a harmless no-op when JIT is
    # off) so nothing regresses if the disable path ever fails.
    disable_numba_jit()
    # DISABLE_JIT covers @jit only. librosa's @guvectorize / @vectorize are
    # compiled at import and its @stencil kernels on first call; without a
    # numba cache (every fresh install) that raised "Compiled function
    # bytecode used" and no track got signal features. See core.numba_emulation.
    from core.numba_emulation import install as _emulate_numba

    _emulate_numba()
    install_numba_fallback_cache_locator()
