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
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return AnalyticsResult(generated_files=[])

    all_rows: list[dict[str, str]] = []
    session_rows: list[tuple[Path, list[dict[str, str]]]] = []
    for session_file in session_files:
        with session_file.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
            if not rows:
                continue
            session_rows.append((session_file, rows))
            all_rows.extend(rows)

    if not all_rows:
        return AnalyticsResult(generated_files=[])

    generated_files: list[Path] = []
    generated_files.append(_plot_accuracy_by_pattern(plt, output_dir, all_rows))
    generated_files.append(_plot_perfect_trend(plt, output_dir, session_rows))
    generated_files.append(_plot_offset_histogram(plt, output_dir, all_rows))
    generated_files.append(_plot_bpm_vs_accuracy(plt, output_dir, session_rows))
    return AnalyticsResult(generated_files=[path for path in generated_files if path is not None])


def _parse_int(value: str | None, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except Exception:
        return default


def _plot_accuracy_by_pattern(plt, output_dir: Path, rows: list[dict[str, str]]) -> Path | None:
    totals: dict[str, int] = {}
    hits: dict[str, int] = {}
    for row in rows:
        pattern = row.get("pattern_name", "Unknown") or "Unknown"
        totals[pattern] = totals.get(pattern, 0) + 1
        if row.get("judgment") in {"Perfect", "Good"}:
            hits[pattern] = hits.get(pattern, 0) + 1

    if not totals:
        return None

    pattern_accuracy = {
        name: (hits.get(name, 0) / totals[name]) * 100.0
        for name in totals
    }
    items = sorted(pattern_accuracy.items(), key=lambda item: item[1])
    labels = [item[0] for item in items]
    values = [item[1] for item in items]

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(labels, values, color="#5aa9e6")
    ax.set_title("Accuracy by Pattern")
    ax.set_ylabel("Accuracy %")
    ax.set_ylim(0, 100)
    ax.tick_params(axis="x", labelrotation=30)
    fig.tight_layout()
    output_path = output_dir / "accuracy_by_pattern.png"
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def _plot_perfect_trend(plt, output_dir: Path, session_rows: list[tuple[Path, list[dict[str, str]]]]) -> Path | None:
    if not session_rows:
        return None
    session_stats: list[tuple[float, float]] = []
    for session_file, rows in session_rows:
        total = len(rows)
        if total == 0:
            continue
        perfect = sum(1 for row in rows if row.get("judgment") == "Perfect")
        perfect_rate = (perfect / total) * 100.0
        session_stats.append((session_file.stat().st_mtime, perfect_rate))

    if not session_stats:
        return None
    session_stats.sort(key=lambda item: item[0])
    rates = [item[1] for item in session_stats]
    indices = list(range(1, len(rates) + 1))

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(indices, rates, marker="o", color="#f28e2b")
    ax.set_title("Perfect % Trend")
    ax.set_xlabel("Session")
    ax.set_ylabel("Perfect %")
    ax.set_ylim(0, 100)
    ax.grid(True, axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    output_path = output_dir / "perfect_trend.png"
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def _plot_offset_histogram(plt, output_dir: Path, rows: list[dict[str, str]]) -> Path | None:
    offsets: list[int] = []
    for row in rows:
        offsets.append(_parse_int(row.get("offset_ms"), 0))

    if not offsets:
        return None

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.hist(offsets, bins=31, color="#59a14f", edgecolor="white")
    ax.set_title("Offset Distribution")
    ax.set_xlabel("Offset (ms)")
    ax.set_ylabel("Count")
    ax.axvline(0, color="#333333", linewidth=1)
    fig.tight_layout()
    output_path = output_dir / "offset_histogram.png"
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def _plot_bpm_vs_accuracy(plt, output_dir: Path, session_rows: list[tuple[Path, list[dict[str, str]]]]) -> Path | None:
    if not session_rows:
        return None
    bpms: list[int] = []
    accuracies: list[float] = []
    for _, rows in session_rows:
        total = len(rows)
        if total == 0:
            continue
        hits = sum(1 for row in rows if row.get("judgment") in {"Perfect", "Good"})
        accuracy = (hits / total) * 100.0
        bpm = _parse_int(rows[0].get("bpm"), 0)
        bpms.append(bpm)
        accuracies.append(accuracy)

    if not bpms:
        return None

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.scatter(bpms, accuracies, color="#af52de", alpha=0.8)
    ax.set_title("BPM vs Accuracy")
    ax.set_xlabel("BPM")
    ax.set_ylabel("Accuracy %")
    ax.set_ylim(0, 100)
    ax.grid(True, axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    output_path = output_dir / "bpm_vs_accuracy.png"
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path