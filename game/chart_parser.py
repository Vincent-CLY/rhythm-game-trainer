from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import random


@dataclass(slots=True)
class ChartNote:
    time_ms: int
    lane: int
    note_type: str
    duration_ms: int = 0
    pattern_name: str = ""
    pattern_instance: int = 1


@dataclass(slots=True)
class ChartPattern:
    name: str
    notes: list[ChartNote] = field(default_factory=list)


@dataclass(slots=True)
class ChartTrainingConfig:
    pattern_repeats: int = 4
    pattern_gap_ms: int = 400
    lead_in_ms: int = 1000
    final_round_random: bool = True
    random_seed: int | None = None


@dataclass(slots=True)
class ChartFile:
    bpm: int
    patterns: list[ChartPattern]
    training: ChartTrainingConfig | None = None


class ChartParserError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ChartParserError(message)


def load_chart(chart_path: Path) -> ChartFile:
    raw = json.loads(chart_path.read_text(encoding="utf-8"))
    _require(isinstance(raw, dict), "Chart root must be an object")
    bpm = int(raw.get("bpm", 120))
    training = _parse_training_config(raw.get("training"))
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
    return ChartFile(bpm=bpm, patterns=patterns, training=training)


def _parse_training_config(raw_training: object) -> ChartTrainingConfig | None:
    if raw_training is None:
        return None
    _require(isinstance(raw_training, dict), "training must be an object")
    training = ChartTrainingConfig(
        pattern_repeats=int(raw_training.get("pattern_repeats", 4)),
        pattern_gap_ms=int(raw_training.get("pattern_gap_ms", 400)),
        lead_in_ms=int(raw_training.get("lead_in_ms", 1000)),
        final_round_random=bool(raw_training.get("final_round_random", True)),
        random_seed=raw_training.get("random_seed"),
    )
    _require(training.pattern_repeats >= 0, "pattern_repeats must be non-negative")
    _require(training.pattern_gap_ms >= 0, "pattern_gap_ms must be non-negative")
    _require(training.lead_in_ms >= 0, "lead_in_ms must be non-negative")
    if training.random_seed is not None:
        training.random_seed = int(training.random_seed)
    return training


def build_note_sequence(chart: ChartFile) -> list[ChartNote]:
    if chart.training is None:
        return [note for pattern in chart.patterns for note in pattern.notes]
    training = chart.training
    rng = random.Random(training.random_seed)
    sequence: list[ChartNote] = []
    cursor_ms = training.lead_in_ms

    pattern_instances: dict[str, int] = {}

    def add_pattern(pattern: ChartPattern) -> int:
        if not pattern.notes:
            return 0
        min_time = min(note.time_ms for note in pattern.notes)
        max_time = max(note.time_ms for note in pattern.notes)
        duration = max(0, max_time - min_time)
        pattern_instances[pattern.name] = pattern_instances.get(pattern.name, 0) + 1
        instance_id = pattern_instances[pattern.name]
        for note in pattern.notes:
            sequence.append(
                ChartNote(
                    time_ms=note.time_ms - min_time + cursor_ms,
                    lane=note.lane,
                    note_type=note.note_type,
                    duration_ms=note.duration_ms,
                    pattern_name=pattern.name,
                    pattern_instance=instance_id,
                )
            )
        return duration

    for pattern in chart.patterns:
        for _ in range(training.pattern_repeats):
            duration = add_pattern(pattern)
            cursor_ms += duration + training.pattern_gap_ms

    final_round = list(chart.patterns)
    if training.final_round_random:
        rng.shuffle(final_round)
    for pattern in final_round:
        duration = add_pattern(pattern)
        cursor_ms += duration + training.pattern_gap_ms

    return sequence