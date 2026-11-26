"""What the shipped ``Traktor/D2/`` folder is allowed to contain.

The whole folder is bundled verbatim (``--include-data-dir=Traktor=Traktor``),
so anything left in it ships. Two things used to be in it that must not come
back:

* ``VALID_PATHS.txt`` — a dump of Traktor's internal ``AppProperty``
  namespace, extracted from the installed application. Reference material,
  but not ours to redistribute.

``D2.qml`` is back, deliberately, as the standalone mapping for the manual
route — it *replaces* Native Instruments' 10 KB file, so anyone with real
Kontrol D2 hardware loses the native controller while it is in place. Nothing
automatic may put it there: the Install button is additive and copies only
``Api/``. That is the invariant worth pinning, and it is what
:func:`test_install_never_copies_our_own_d2_qml` checks — shipping the file
is safe exactly as long as only a human can install it.

``INSTALL.txt`` is the manual fallback, and it is the one piece of
documentation that has to be *exactly* right: someone following it is editing
Traktor's own QML by hand. It tells them to add the same two lines
:mod:`core.d2_patch` writes, sentinel comment and all, so these tests fail if
the patch changes and the instructions do not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.d2_patch import MODULE_NAME, ORIGINALS_ZIP, PATCH_SENTINEL, apply

REPO = Path(__file__).resolve().parents[2]
D2 = REPO / "Traktor" / "D2"


@pytest.fixture(scope="module")
def install_txt() -> str:
    return (D2 / "INSTALL.txt").read_text(encoding="utf-8")


# ── What ships ─────────────────────────────────────────────────────────────


def test_install_never_copies_our_own_d2_qml(tmp_path) -> None:
    """The button is additive: Traktor keeps the mapping NI shipped.

    ``Traktor/D2/D2.qml`` is bundled for the manual route, and the whole
    folder ships verbatim, so the one thing that must never happen is the
    installer picking it up and overwriting Traktor's own file — that is how
    a real Kontrol D2 loses its controller.
    """

    ni = tmp_path / "Traktor.app" / "qml" / "CSI" / "D2"
    ni.mkdir(parents=True)
    original = 'import CSI 1.0\n\nMapping\n{\n  Wheel { name: "w" }\n}\n'
    (ni / "D2.qml").write_text(original)

    # Our package, shaped as it ships: the Api module, with the standalone
    # mapping sitting beside it.
    ours = tmp_path / "pkg" / "D2"
    (ours / "Api").mkdir(parents=True)
    (ours / "Api" / "ApiModule.qml").write_text("// ours\n")
    (ours / "D2.qml").write_text("// the standalone replacement\n")

    result = apply(ni, ours / "Api", tmp_path / "backup", "Traktor Pro 4")
    assert result.ok, result.error

    installed = (ni / "D2.qml").read_text()
    assert "the standalone replacement" not in installed
    assert 'Wheel { name: "w" }' in installed, "NI's mapping survived"
    assert PATCH_SENTINEL in installed


def test_the_standalone_mapping_does_not_carry_stale_module_names() -> None:
    """It was committed carrying the patch applied to itself: two imports,
    two ``ApiModule`` blocks, one naming a module that no longer exists."""
    qml = (D2 / "D2.qml").read_text(encoding="utf-8")
    assert qml.count('import "Api"') == 1
    assert qml.count("ApiModule {") == 1
    assert MODULE_NAME in qml
    assert "djmatch" not in qml.lower()


def test_traktors_property_namespace_is_not_in_the_package() -> None:
    assert not (D2 / "VALID_PATHS.txt").exists()


def test_the_api_module_is_still_there() -> None:
    """It is the only thing the package actually needs to carry."""
    assert (D2 / "Api" / "qmldir").is_file()
    assert (D2 / "Api" / "ApiModule.qml").is_file()


# ── The manual fallback stays in step with the patch ───────────────────────


def test_the_instructions_carry_the_sentinel(install_txt) -> None:
    """A hand-install without it works but reads as "Not installed", because
    that marker is how :func:`core.d2_patch.is_patched` recognises the patch —
    and how Restore finds the lines to take out again."""
    assert PATCH_SENTINEL in install_txt


def test_the_instructions_name_the_module_the_code_installs(install_txt) -> None:
    assert MODULE_NAME in install_txt


def test_the_instructions_do_not_tell_anyone_to_replace_the_folder(install_txt) -> None:
    """The old text did, and said D2 hardware would stop working as a result."""
    lowered = install_txt.lower()
    assert "replacing the existing d2 folder" not in lowered
    assert "will no longer function" not in lowered


def test_the_instructions_send_people_to_the_button_first(install_txt) -> None:
    """Editing a signed application bundle by hand is the fallback, not the
    procedure."""
    assert "Install into Traktor" in install_txt


def test_the_instructions_name_the_archive_the_app_writes(install_txt) -> None:
    """A hand-install should leave the same evidence behind: the originals,
    zipped in the folder that was changed, for anyone to open later."""
    assert ORIGINALS_ZIP in install_txt


def test_the_instructions_cover_the_controller_entry(install_txt) -> None:
    """Without a Kontrol D2 in Controller Manager, Traktor never loads the
    mapping — the one failure that leaves everything else looking correct."""
    assert "Controller Manager" in install_txt
