"""Reading the error log back, for the admin panel (``logging_setup`` writes it).

Users only ever see a reference ("ref 3f9c0a1b2d"); the details are in
``ERROR_LOG_FILE`` and its rotated copies (``errors.log.1`` … ``.N``, newest
first). This module finds an entry by its reference and lists the latest ones.
Read-only: nothing here writes, deletes or rotates the log.

An entry is a header line in ``logging_setup._FORMAT`` plus any lines after it
up to the next header (a stack trace).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

# "2026-11-19 10:15:02,123 ERROR ah_dashboard.errors: [ref 3f9c0a1b2d] GET /api/bonus: ..."
_HEAD = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d{3} ([A-Z]+) ([\w.]+): (.*)")
_REF = re.compile(r"\[ref ([0-9a-f]{10})\] ?")
# A reference somewhere in what the admin pasted ("ref 3f9c0a1b2d", "(reference 3F9C0A1B2D)").
_REF_IN_TEXT = re.compile(r"(?<![0-9a-f])([0-9a-f]{10})(?![0-9a-f])")

MAX_LINE = 2000            # characters kept per line (a huge upstream answer stays readable)
MAX_DETAIL_LINES = 200     # stack trace lines kept per entry
MAX_READ = 8 * 1024 * 1024  # bytes read per file at most (rotation keeps them far smaller)


@dataclass
class Entry:
    time: str            # server time, "YYYY-MM-DD HH:MM:SS"
    level: str           # WARNING, ERROR, ...
    source: str          # logger name without the package prefix
    message: str         # first line, without the "[ref …]" prefix
    ref: str | None
    detail: list[str] = field(default_factory=list)   # stack trace lines


def parse_ref(text: str) -> str | None:
    """The reference in what the admin typed or pasted, or None."""
    m = _REF_IN_TEXT.search((text or "").strip().lower())
    return m.group(1) if m else None


def log_files(path: str | None, backups: int) -> list[str]:
    """The log and its rotated copies that exist, newest first."""
    if not path:
        return []
    names = [path] + [f"{path}.{i}" for i in range(1, max(0, backups) + 1)]
    return [n for n in names if os.path.isfile(n)]


def _read_lines(name: str) -> list[str]:
    try:
        with open(name, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - MAX_READ))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > MAX_READ and lines:
        lines = lines[1:]          # started mid-line
    return lines


def _parse(lines: list[str]) -> list[Entry]:
    """Entries in file order (oldest first). Lines before the first header
    (only possible in a cut-off file) are skipped."""
    out: list[Entry] = []
    for line in lines:
        line = line[:MAX_LINE]
        m = _HEAD.match(line)
        if m:
            time, level, source, message = m.groups()
            ref_m = _REF.match(message)
            ref = ref_m.group(1) if ref_m else None
            if ref_m:
                message = message[ref_m.end():]
            if source.startswith("ah_dashboard."):
                source = source[len("ah_dashboard."):]
            out.append(Entry(time, level, source, message, ref))
        elif out and len(out[-1].detail) < MAX_DETAIL_LINES:
            out[-1].detail.append(line)
    return out


def entries(path: str | None, backups: int) -> Iterator[Entry]:
    """Every entry, newest first (one file at a time)."""
    for name in log_files(path, backups):
        yield from reversed(_parse(_read_lines(name)))


def recent(path: str | None, backups: int, limit: int = 50) -> list[Entry]:
    out: list[Entry] = []
    for entry in entries(path, backups):
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def find(path: str | None, backups: int, ref: str) -> Entry | None:
    """The entry with this reference (references are random, so at most one)."""
    return next((e for e in entries(path, backups) if e.ref == ref), None)
