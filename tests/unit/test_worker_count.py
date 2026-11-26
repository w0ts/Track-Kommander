"""How many tracks a scan analyses at once.

The count was capped at six whatever the machine, for a reason that only
holds on a GPU: there the CLAP forward runs one at a time, and more threads
queue behind it. On the CPU path every stage runs in parallel, and the cap
left a 16-core machine analysing at under two thirds of what it could.

Measured on that machine, the whole analysis, real tracks (tracks a second,
peak memory):

    threads    4–8 min tracks      longest tracks in the library
       6       3.08    4.5 GB       1.97     9.2 GB
       8       3.79    5.8 GB       2.31    11.1 GB
      10       4.44    6.5 GB       2.78    12.8 GB
      12       4.91    7.3 GB       2.78    14.1 GB

The same runs gave what a thread costs in memory: 490–560 MB on ordinary
tracks and 630–740 MB on long ones. The budget was 250 MB, so the memory
limit never limited anything; raising the cap without correcting it would
have put a 16 GB machine into swap.
"""

from __future__ import annotations

import pytest

import workers.analysis as wa

GB = 1024**3


@pytest.fixture
def machine(monkeypatch):
    """A machine with the given cores and free memory."""

    def _set(cores: int, free_gb: float) -> None:
        monkeypatch.setattr(wa.os, "cpu_count", lambda: cores)
        monkeypatch.setattr(wa, "_available_ram_bytes", lambda: int(free_gb * GB))

    return _set


def test_a_large_machine_on_the_cpu_path_uses_more_than_six(machine) -> None:
    machine(cores=16, free_gb=32)
    assert wa._autodetect_n_workers(gpu=False) == wa.CPU_PATH_CAP
    assert wa.CPU_PATH_CAP > wa.GPU_PATH_CAP


def test_a_gpu_keeps_the_lower_cap(machine) -> None:
    """One forward at a time there; threads beyond the cap only queue."""
    machine(cores=16, free_gb=32)
    assert wa._autodetect_n_workers(gpu=True) == wa.GPU_PATH_CAP


def test_memory_limits_the_count(machine) -> None:
    machine(cores=16, free_gb=4)
    chosen = wa._autodetect_n_workers(gpu=False)
    assert chosen == int(4 * GB // wa._WORKER_RAM_BUDGET_BYTES)
    assert chosen < wa.CPU_PATH_CAP


def test_the_budget_is_what_a_thread_was_measured_to_use() -> None:
    assert 700_000_000 <= wa._WORKER_RAM_BUDGET_BYTES <= 800_000_000


def test_a_quarter_of_the_cores_is_left_for_traktor(machine) -> None:
    machine(cores=8, free_gb=64)
    assert wa._autodetect_n_workers(gpu=False) == 6


@pytest.mark.parametrize(("cores", "free_gb"), [(1, 64), (2, 0.1), (16, 0)])
def test_there_is_always_one(machine, cores: int, free_gb: float) -> None:
    machine(cores, free_gb)
    assert wa._autodetect_n_workers(gpu=False) >= 1
    assert wa._autodetect_n_workers(gpu=True) >= 1
