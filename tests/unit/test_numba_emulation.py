"""The compiled app's stand-ins for numba's vectorize / guvectorize / stencil.

In the Nuitka bundle numba cannot compile; on a fresh install (no numba
cache) librosa failed to import and no track got signal features. The
stand-ins must give exactly what compiled numba gives. The emulated side
runs in a subprocess: the decorators have to be replaced before librosa is
imported, and this process imported it already.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from core import numba_emulation as emu

SRC = Path(__file__).resolve().parents[2] / "src"

_PROBE = """
import json, os, sys
sys.path.insert(0, {src!r})
if os.environ.get("EMU") == "1":
    os.environ["NUMBA_DISABLE_JIT"] = "1"
    from core.numba_emulation import install; install()
import numpy as np, librosa
rng = np.random.default_rng(0)
x = rng.standard_normal(3000).astype(np.float32)
x2 = rng.standard_normal((3, 300))
env = np.abs(rng.standard_normal(1500)).astype(np.float32)
out = dict(
    localmax=librosa.util.localmax(x2, axis=-1),
    localmin=librosa.util.localmin(x2, axis=-1),
    zc=librosa.zero_crossings(x),
    peaks=librosa.util.peak_pick(env, pre_max=3, post_max=3, pre_avg=3,
                                 post_avg=5, delta=0.5, wait=10),
    onsets=librosa.onset.onset_detect(onset_envelope=env, sr=22050, hop_length=512),
    abs2=librosa.util.abs2(np.fft.rfft(x)),
)
print(json.dumps({{k: np.asarray(v).astype(float).tolist() for k, v in out.items()}}))
"""


def _run(emulated: bool) -> dict:
    env = dict(os.environ, EMU="1" if emulated else "0")
    done = subprocess.run(
        [sys.executable, "-c", _PROBE.format(src=str(SRC))],
        capture_output=True,
        text=True,
        env=env,
        timeout=240,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.slow
def test_librosa_gives_the_same_answers_emulated() -> None:
    pytest.importorskip("librosa")
    real, emulated = _run(False), _run(True)
    for name in real:
        assert np.allclose(real[name], emulated[name]), name


# ── The stand-ins on their own ──────────────────────────────────────────────


def test_guvectorize_loops_leading_dims_and_fills_the_given_output() -> None:
    @emu._guvectorize(["void(float64[:], bool_[:])"], "(n)->(n)")
    def positive(x, y):
        y[:] = x > 0

    x = np.array([[1.0, -1.0, 2.0], [-3.0, 4.0, -5.0]])
    out = np.zeros(x.shape, dtype=bool)
    positive(x, out)
    assert out.tolist() == [[True, False, True], [False, True, False]]


def test_guvectorize_allocates_an_output_when_none_is_given() -> None:
    @emu._guvectorize(["void(float64[:], float64, bool_[:])"], "(n),()->(n)")
    def above(x, t, y):
        y[:] = x > t

    assert above(np.array([1.0, 5.0, 3.0]), 2.0).tolist() == [False, True, True]


def test_vectorize_runs_on_whole_arrays() -> None:
    @emu._vectorize(["float64(complex128)"])
    def abs2(z):
        return z.real**2 + z.imag**2

    assert abs2(np.array([3 + 4j, 1j])).tolist() == [25.0, 1.0]


def test_vectorize_falls_back_for_a_branching_kernel() -> None:
    @emu._vectorize(["float64(float64)"])
    def clip(v):
        return v if v > 0 else 0.0

    assert clip(np.array([-1.0, 2.0])).tolist() == [0.0, 2.0]


def _both(kernel, *args):
    """The kernel through compiled numba and through the stand-in."""
    numba = pytest.importorskip("numba")
    return numba.stencil(kernel)(*args), emu._stencil(kernel)(*args)


def test_stencil_constant_border_matches_numba() -> None:
    def localmax(x):
        return (x[0] > x[-1]) & (x[0] >= x[1])

    real, emulated = _both(localmax, np.array([0.0, 3.0, 1.0, 4.0, 2.0]))
    assert emulated.tolist() == real.tolist() == [False, True, False, True, False]


def test_stencil_with_a_branch_and_a_scalar_matches_numba() -> None:
    def crosses(x, t):
        a, b = x[-1], x[0]
        if -t <= a <= t:
            a = 0.0
        return (a > 0) != (b > 0)

    real, emulated = _both(crosses, np.array([1.0, -1.0, 0.05, 2.0, -0.02]), 0.1)
    assert emulated.tolist() == real.tolist()
