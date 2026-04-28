from __future__ import annotations

from dataclasses import dataclass


PERFECT_WINDOW_MS = 50
GOOD_WINDOW_MS = 100


@dataclass(slots=True)
class JudgmentResult:
    judgment: str
    score: int
    offset_ms: int


def judge_timing(expected_time_ms: int, actual_time_ms: int) -> JudgmentResult:
    offset_ms = actual_time_ms - expected_time_ms
    abs_offset = abs(offset_ms)
    if abs_offset <= PERFECT_WINDOW_MS:
        return JudgmentResult("Perfect", 100, offset_ms)
    if abs_offset <= GOOD_WINDOW_MS:
        return JudgmentResult("Good", 70, offset_ms)
    return JudgmentResult("Miss", 0, offset_ms)