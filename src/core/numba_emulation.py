"""Pure-NumPy stand-ins for numba's ``vectorize``, ``guvectorize`` and ``stencil``.

Why the compiled app needs them
-------------------------------

In the Nuitka bundle numba cannot compile anything: it reads a function's
bytecode, and Nuitka has replaced that bytecode with a stub that raises
``RuntimeError("Compiled function bytecode used")``. :mod:`core.frozen_runtime`
already sets ``NUMBA_DISABLE_JIT`` so ``@jit`` functions run as plain Python —
but that switch covers ``@jit`` alone. librosa also declares ``@guvectorize``
and ``@vectorize`` functions with signatures, which numba compiles **when the
module is imported**, and ``@stencil`` kernels, compiled on first call.

On a machine whose numba cache already holds those functions they load
without compiling, which is why the app worked where it had been run before.
On a fresh install ``import librosa`` itself raised, every track's signal,
melodic and structure features were skipped, and the log said only
"Compiled function bytecode used" (seen on a user's Mac, 2 536 tracks, 0
analysed).

What these do
-------------

Each decorator returns a plain-Python object with the calling convention
librosa uses:

* ``vectorize`` → the kernel applied to whole arrays (NumPy broadcasting),
  falling back to :class:`numpy.vectorize` for a kernel that cannot take an
  array.
* ``guvectorize`` → loops the kernel over the leading dimensions, allocating
  the output when the caller does not pass one — librosa passes it.
* ``stencil`` → evaluates the kernel once over whole shifted arrays, with the
  border (where a relative index falls outside) set to ``cval`` — numba's
  ``constant`` mode.

Slower than compiled numba, but these run on short 1-D arrays (one value per
frame); the heavy lifting in librosa is NumPy and SciPy either way.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

import numpy as np

log = logging.getLogger(__name__)

_INSTALLED = False


# ── vectorize ───────────────────────────────────────────────────────────────


def _vectorize(*args, **_kwargs):
    """``numba.vectorize`` without compilation."""

    def wrap(func: Callable) -> Callable:
        fallback = np.vectorize(func)

        def ufunc(*values):
            try:
                return func(*values)
            except Exception:
                return fallback(*values)

        ufunc.__name__ = getattr(func, "__name__", "vectorized")
        ufunc.__doc__ = func.__doc__
        return ufunc

    # @vectorize used bare, without a signature list.
    if len(args) == 1 and callable(args[0]) and not isinstance(args[0], (list, str)):
        return wrap(args[0])
    return wrap


# ── guvectorize ─────────────────────────────────────────────────────────────

_CORE = re.compile(r"\(([^)]*)\)")


def _parse_layout(layout: str) -> tuple[list[list[str]], list[list[str]]]:
    """``"(n),(),()->(n)"`` → input and output core dimension names."""
    ins, outs = layout.replace(" ", "").split("->")

    def dims(part: str) -> list[list[str]]:
        return [[d for d in m.split(",") if d] for m in _CORE.findall(part)]

    return dims(ins), dims(outs)


def _output_dtype(signatures) -> np.dtype:
    """The output element type named by the first signature, e.g. bool_[:]."""
    first = signatures[0] if signatures else ""
    last = str(first).rsplit(",", 1)[-1] if "," in str(first) else str(first)
    if "bool" in last:
        return np.dtype(bool)
    if "int" in last:
        return np.dtype(np.int64)
    return np.dtype(np.float64)


def _guvectorize(*args, **_kwargs):
    """``numba.guvectorize`` without compilation."""
    if len(args) == 1:
        signatures, layout = [], args[0]
    else:
        signatures, layout = args[0], args[1]
    if isinstance(signatures, str):
        signatures = [signatures]
    in_dims, out_dims = _parse_layout(layout)
    out_dtype = _output_dtype(signatures)

    def wrap(func: Callable) -> Callable:
        def gufunc(*values, **_kw):
            n_in = len(in_dims)
            inputs = [np.asarray(v) for v in values[:n_in]]
            outputs = list(values[n_in:])
            # Leading (loop) shape of each input: everything before its core dims.
            leading = [a.shape[: a.ndim - len(d)] for a, d in zip(inputs, in_dims, strict=True)]
            loop_shape = np.broadcast_shapes(*leading) if leading else ()
            sizes: dict[str, int] = {}
            for a, d in zip(inputs, in_dims, strict=True):
                for name, size in zip(d, a.shape[a.ndim - len(d) :], strict=True):
                    sizes.setdefault(name, size)
            if not outputs:
                outputs = [
                    np.zeros(loop_shape + tuple(sizes[n] for n in d), dtype=out_dtype)
                    for d in out_dims
                ]
            spread = [
                np.broadcast_to(a, loop_shape + a.shape[a.ndim - len(d) :])
                for a, d in zip(inputs, in_dims, strict=True)
            ]
            for idx in np.ndindex(*loop_shape):
                func(*[a[idx] for a in spread], *[o[idx] for o in outputs])
            return outputs[0] if len(outputs) == 1 else tuple(outputs)

        gufunc.__name__ = getattr(func, "__name__", "gufunc")
        gufunc.__doc__ = func.__doc__
        return gufunc

    return wrap


# ── stencil ─────────────────────────────────────────────────────────────────


class _Relative:
    """What a stencil kernel sees: ``x[k]`` is x shifted by k, as a whole array.

    Records every offset read, so the border those offsets reach past can be
    set to the constant afterwards.
    """

    def __init__(self, array: np.ndarray) -> None:
        self.array = array
        self.offsets: list[int] = []

    def __getitem__(self, k: int) -> np.ndarray:
        if not isinstance(k, (int, np.integer)):
            raise TypeError("emulated stencils support 1-D integer offsets only")
        self.offsets.append(int(k))
        # value at i is array[i + k]; wrapped entries are overwritten below.
        return np.roll(self.array, -int(k))


class _OutOfRange(Exception):
    """A relative index reached past the array: the element takes ``cval``."""


class _At:
    """What a stencil kernel sees in the per-element fallback: ``x[k]`` is
    the single value ``array[i + k]``."""

    def __init__(self, array: np.ndarray) -> None:
        self.array = array
        self.i = 0

    def __getitem__(self, k: int):
        j = self.i + int(k)
        if j < 0 or j >= len(self.array):
            raise _OutOfRange
        return self.array[j]


def _stencil_vectorized(kernel, arrays, cval):
    views = [_Relative(a) if np.ndim(a) else a for a in arrays]
    result = np.asarray(kernel(*views))
    offsets = [k for v in views if isinstance(v, _Relative) for k in v.offsets] or [0]
    lo, hi = min(offsets), max(offsets)
    out = result.copy()
    if hi > 0:
        out[len(out) - hi :] = cval
    if lo < 0:
        out[:-lo] = cval
    return out


def _stencil_per_element(kernel, arrays, cval):
    """For kernels that branch on values (``if x[0] > t``): slow but exact."""
    views = [_At(a) if np.ndim(a) else a for a in arrays]
    n = len(next(a for a in arrays if np.ndim(a)))
    at = [v for v in views if isinstance(v, _At)]
    first = None
    values = []
    for i in range(n):
        for v in at:
            v.i = i
        try:
            val = kernel(*views)
        except _OutOfRange:
            val = cval
        if first is None and val is not cval:
            first = val
        values.append(val)
    dtype = np.asarray(first).dtype if first is not None else np.asarray(cval).dtype
    return np.asarray(values, dtype=dtype)


def _stencil(func_or_mode="constant", **options):
    """``numba.stencil`` without compilation (1-D, constant mode).

    Tried over whole shifted arrays first; a kernel that branches on values
    cannot run that way and falls back to one element at a time.
    """
    cval = options.get("cval", 0)

    def wrap(kernel: Callable) -> Callable:
        def apply(*arrays, **_kw):
            arrays = [np.asarray(a) if np.ndim(a) else a for a in arrays]
            try:
                return _stencil_vectorized(kernel, arrays, cval)
            except (TypeError, ValueError):
                return _stencil_per_element(kernel, arrays, cval)

        apply.__name__ = getattr(kernel, "__name__", "stencil")
        return apply

    if callable(func_or_mode):
        return wrap(func_or_mode)
    return wrap


# ── Installing ──────────────────────────────────────────────────────────────


def install() -> None:
    """Point numba's decorators at the stand-ins. Before librosa is imported."""
    global _INSTALLED
    if _INSTALLED:
        return
    import sys

    # The package itself: core.frozen_runtime registers a stand-in
    # "librosa.core.fft" beforehand, which is not librosa being imported.
    if "librosa" in sys.modules:
        log.warning(
            "numba emulation installed after librosa was imported — "
            "functions it already declared keep numba's decorators"
        )
    import numba

    numba.vectorize = _vectorize
    numba.guvectorize = _guvectorize
    numba.stencil = _stencil
    try:
        import numba.np.ufunc as npufunc

        npufunc.vectorize = _vectorize
        npufunc.guvectorize = _guvectorize
    except ImportError:
        pass
    _INSTALLED = True
    log.info("numba vectorize/guvectorize/stencil emulated (no compilation)")
