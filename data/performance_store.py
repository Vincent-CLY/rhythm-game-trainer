from __future__ import annotations

from pathlib import Path
import json


HISTORY_PATH = Path("data/performance_history.json")


def load_history(path: Path = HISTORY_PATH) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def save_history(history: list[dict[str, object]], path: Path = HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")


def append_history(entry: dict[str, object], path: Path = HISTORY_PATH) -> list[dict[str, object]]:
    history = load_history(path)
    history.append(entry)
    save_history(history, path)
    return history
