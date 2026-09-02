"""One JSONL line per turn.

Without this, none of the thresholds in config.toml can be tuned -- you would
be guessing. Written to SUNDAY_HOME/logs, one file per day, keyed by trace_id.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sunday import config


class TurnLog:
    """Collects the numbers for one turn, writes once at the end."""

    def __init__(self, trace_id: str, modality: str, log_dir: Path | None = None) -> None:
        self.dir = log_dir or config.LOG_DIR
        self.started = time.perf_counter()
        self.first_token_at: float | None = None
        self.record: dict[str, Any] = {
            "trace_id": trace_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "modality": modality,
        }

    def mark_first_token(self) -> None:
        if self.first_token_at is None:
            self.first_token_at = time.perf_counter()

    def set(self, **values: Any) -> None:
        self.record.update(values)

    def _ms(self, start: float, end: float) -> int:
        return int((end - start) * 1000)

    def write(self) -> dict[str, Any]:
        now = time.perf_counter()
        if self.first_token_at is not None:
            self.record["ttft_ms"] = self._ms(self.started, self.first_token_at)
        self.record["total_ms"] = self._ms(self.started, now)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            day = datetime.now().strftime("%Y-%m-%d")
            with (self.dir / f"{day}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(self.record, ensure_ascii=False) + "\n")
        except OSError:
            # Logging must never be the reason a turn fails.
            pass
        return self.record
