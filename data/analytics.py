from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv


@dataclass(slots=True)
class AnalyticsResult:
    generated_files: list[Path]


def generate_analytics(session_dir: Path = Path("data/sessions"), output_dir: Path = Path("data/analytics")) -> AnalyticsResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    session_files = sorted(session_dir.glob("*.csv"))
    if not session_files:
        return AnalyticsResult(generated_files=[])
    try:  # pragma: no cover - optional plotting dependency
        import matplotlib.pyplot as plt
    except Exception:
        return AnalyticsResult(generated_files=[])

    rows: list[dict[str, str]] = []
    for session_file in session_files:
        with session_file.open(newline="", encoding="utf-8") as handle:
            rows.extend(list(csv.DictReader(handle)))

    generated_files: list[Path] = []
    if rows:
        generated_files.append(_write_placeholder_chart(plt, output_dir / "accuracy_by_pattern.png", "Accuracy by Pattern"))
        generated_files.append(_write_placeholder_chart(plt, output_dir / "perfect_trend.png", "Perfect Trend"))
        generated_files.append(_write_placeholder_chart(plt, output_dir / "offset_histogram.png", "Offset Histogram"))
        generated_files.append(_write_placeholder_chart(plt, output_dir / "bpm_vs_accuracy.png", "BPM vs Accuracy"))

    return AnalyticsResult(generated_files=generated_files)


def _write_placeholder_chart(plt, output_path: Path, title: str) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.set_title(title)
    ax.text(0.5, 0.5, "Analytics will be expanded in the next slice", ha="center", va="center")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path