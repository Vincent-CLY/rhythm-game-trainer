from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import csv


CSV_COLUMNS = [
    "session_id",
    "timestamp",
    "pattern_name",
    "note_type",
    "zone",
    "expected_time",
    "actual_time",
    "offset_ms",
    "judgment",
    "bpm",
    "combo",
]


@dataclass
class SessionRecorder:
    output_dir: Path = Path("data/sessions")
    session_id: str = field(default_factory=lambda: uuid4().hex)
    _rows: list[dict[str, object]] = field(default_factory=list)

    @property
    def rows(self) -> list[dict[str, object]]:
        return list(self._rows)

    def record(self, **row: object) -> None:
        self._rows.append(row)

    def save(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{self.session_id}.csv"
        with output_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for row in self._rows:
                writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})
        return output_path

    def session_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()