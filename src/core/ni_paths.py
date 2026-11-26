"""Where Native Instruments keeps a user's Traktor libraries.

One definition of ``~/Documents/Native Instruments``, the folder holding a
``Traktor <version>`` directory per installed version (each with its
``collection.nml`` and ``History``). Import this rather than building the
path again.
"""

from __future__ import annotations

from pathlib import Path


def ni_documents_root() -> Path:
    """The folder holding Traktor's per-version library directories."""
    return Path.home() / "Documents" / "Native Instruments"
