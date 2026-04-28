from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json


@dataclass(slots=True)
class ChartNote:
    time_ms: int
    lane: int
    note_type: str
    duration_ms: int = 0
    pattern_name: str = ""


@dataclass(slots=True)
class ChartPattern:
    name: str
    notes: list[ChartNote] = field(default_factory=list)


@dataclass(slots=True)
class ChartFile:
    bpm: int
    patterns: list[ChartPattern]


class ChartParserError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ChartParserError(message)


def load_chart(chart_path: Path) -> ChartFile:
    raw = json.loads(chart_path.read_text(encoding="utf-8"))
    _require(isinstance(raw, dict), "Chart root must be an object")
    bpm = int(raw.get("bpm", 120))
    raw_patterns = raw.get("patterns", [])
    _require(isinstance(raw_patterns, list) and raw_patterns, "Chart must define patterns")
    patterns: list[ChartPattern] = []
    for raw_pattern in raw_patterns:
        _require(isinstance(raw_pattern, dict), "Each pattern must be an object")
        name = str(raw_pattern.get("name", ""))
        _require(bool(name), "Pattern name is required")
        raw_notes = raw_pattern.get("notes", [])
        _require(isinstance(raw_notes, list), f"Pattern {name} notes must be a list")
        notes: list[ChartNote] = []
        for raw_note in raw_notes:
            _require(isinstance(raw_note, dict), f"Pattern {name} contains invalid note data")
            note = ChartNote(
                time_ms=int(raw_note["time_ms"]),
                lane=int(raw_note["lane"]),
                note_type=str(raw_note.get("note_type", "TAP")).upper(),
                duration_ms=int(raw_note.get("duration_ms", 0)),
                pattern_name=name,
            )
            _require(1 <= note.lane <= 4, "Lane must be between 1 and 4")
            notes.append(note)
        patterns.append(ChartPattern(name=name, notes=notes))
    return ChartFile(bpm=bpm, patterns=patterns)