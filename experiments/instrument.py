"""Logging for the two threshold decision points."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Candidate:
    triple: tuple[str, str, str]
    score: float
    depth: int
    frontier_entity: str
    degree: int
    is_gold: Optional[bool] = None


@dataclass
class DecisionRecord:
    stage: str  # "expansion" | "potential"
    question_id: str
    iteration: int
    candidates: list[Candidate]
    kept: list[bool]
    policy_name: str = "fixed"


class DecisionLogger:
    """Append decision records to a JSONL file."""

    def __init__(self, path: str):
        self.path = path
        self._buf: list[DecisionRecord] = []

    def log(self, record: DecisionRecord) -> None:
        self._buf.append(record)

    def flush(self) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            for r in self._buf:
                f.write(json.dumps(r, default=lambda o: o.__dict__, ensure_ascii=False) + "\n")
        self._buf.clear()

