"""The compiled app ships without sklearn, but librosa imports it.

``librosa.decompose`` and ``librosa.segment`` import sklearn at the top;
``librosa.effects.hpss`` — the melodic feature and the bassline — pulls both
in. The stand-ins must let that import succeed and HPSS run, with sklearn
absent. Run in a subprocess, with sklearn made unimportable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

_PROBE = f"""
import sys
sys.path.insert(0, {str(SRC)!r})

class _NoSklearn:
    def find_spec(self, name, path=None, target=None):
        if name == "sklearn" or name.startswith("sklearn."):
            raise ImportError("sklearn hidden for the test")
        return None
sys.meta_path.insert(0, _NoSklearn())

from core.frozen_runtime import ensure_sklearn_decomposition_stub
ensure_sklearn_decomposition_stub()
import numpy as np, librosa
y = np.random.default_rng(0).standard_normal(22050 * 2).astype(np.float32)
h, p = librosa.effects.hpss(y)
assert h.shape == y.shape
import sklearn.decomposition
try:
    sklearn.decomposition.NMF(n_components=2)
except RuntimeError as exc:
    print("NMF refused:", exc)
print("OK")
"""


def test_hpss_runs_without_sklearn() -> None:
    pytest.importorskip("librosa")
    done = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=240
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert "NMF refused" in done.stdout and done.stdout.strip().endswith("OK")
