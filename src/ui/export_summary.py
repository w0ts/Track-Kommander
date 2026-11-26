"""What to say once an export is done.

The numbers — how many tracks the key holds, how many this run added,
updated, left alone or deleted — and anything that went wrong. Nothing
about how the export works; the person reading this just ran it.
"""

from __future__ import annotations


def _counts(report, deleted: int) -> str:
    parts = [
        (f"added {report.added}", report.added),
        (f"updated {report.replaced}", report.replaced),
        (f"unchanged {report.unchanged + report.kept}", report.unchanged + report.kept),
        (f"deleted {deleted}", deleted),
    ]
    named = [text for text, n in parts if n]
    if not named:
        return ""
    return (
        named[0][0].upper()
        + named[0][1:]
        + (", " + ", ".join(named[1:]) if len(named) > 1 else "")
        + "."
    )


#: How many names a list in the dialog runs to before it is cut short.
_NAMED = 10


def _no_waveform(names: list[str]) -> str:
    """The tracks that are on the key but will show a flat line."""
    n = len(names)
    said = (
        f"{n} tracks have no waveform — their audio could not be read:"
        if n != 1
        else "1 track has no waveform — its audio could not be read:"
    )
    listed = "\n".join(names[:_NAMED])
    more = f"\n… and {n - _NAMED} more" if n > _NAMED else ""
    return f"{said}\n{listed}{more}"


def summarise(outcome, *, playlists: int, dest_name: str, skipped: int = 0) -> tuple[str, str]:
    """(status line, dialog text) for a finished export."""
    report = outcome.report
    stopped = " (stopped early)" if outcome.stopped else ""
    status = f"{dest_name}: {report.on_key:,} tracks, {playlists} playlists.{stopped}"

    lines = [status]
    counts = _counts(report, outcome.orphans_removed)
    if counts:
        lines.append(counts)
    if outcome.orphans_found and not outcome.orphans_removed:
        lines.append(
            f"{outcome.orphans_found} files on the key are not in its "
            f"database ({outcome.orphans_bytes / 1e9:.2f} GB)."
        )
    if outcome.stopped:
        lines.append("Stopped early — run it again to add the rest.")
    if report.missing:
        lines.append(f"{len(report.missing)} missing from disk.")
    if skipped:
        lines.append(f"{skipped} playlist entries skipped.")
    if outcome.no_waveform:
        lines.append(_no_waveform(outcome.no_waveform))
    if report.errors:
        n = len(report.errors)
        lines.append(f"{n} problem{'s' if n != 1 else ''}:\n" + "\n".join(report.errors[:10]))
    return status, "\n\n".join(lines)
