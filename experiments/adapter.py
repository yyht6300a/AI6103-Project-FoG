"""Adapter from member A's processed artifacts to the experiments sweep.

Builds :class:`QuestionData` objects exposing the ``run_expansion_decision``
protocol expected by :mod:`experiments.sweep`, plus gold answers for the
end-to-end metrics. It reads only the JSON fields documented in
``data_knowledge_graph/docs/DATA_CONTRACT.md``; it does not import member A's
``fog`` internals.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .instrument import Candidate


ScoreFn = Callable[[tuple[str, str, str]], float]


@dataclass
class QuestionData:
    """One question plus its gold evidence and scored candidate pool."""

    qid: str
    question: str
    answers: list[str]
    answer_mids: list[str]
    gold_triples: set[tuple[str, str, str]]
    gold_paths: list[list[tuple[str, str, str]]]
    n_sub_questions: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    _score_fn: Optional[ScoreFn] = None

    def scored_candidates(self) -> list[Candidate]:
        """Return candidates, (re)applying ``score_fn`` when provided."""
        if self._score_fn is not None:
            for c in self.candidates:
                c.score = self._score_fn(c.triple)
        return self.candidates

    def run_expansion_decision(self, policy) -> tuple[list[Candidate], list[Candidate]]:
        """Apply an expansion policy and return ``(all, kept)``."""
        all_c = self.scored_candidates()
        flags = policy.expansion_keep(all_c)
        kept = [c for c, keep in zip(all_c, flags) if keep]
        return all_c, kept


def load_subgraph_candidates(subgraph_path: Path, score_fn: ScoreFn) -> list[Candidate]:
    """Load the un-pruned candidate pool from one ``subgraphs/<qid>.json.gz``."""
    with gzip.open(subgraph_path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    nodes = data["nodes"]
    edges = data["edges"]
    names = [n["name"] for n in nodes]

    degree: Counter[int] = Counter()
    for e in edges:
        degree[e["s"]] += 1
        degree[e["t"]] += 1

    candidates: list[Candidate] = []
    for e in edges:
        h, r, t = names[e["s"]], e["r"], names[e["t"]]
        depth = min(e["ds"], e["dt"])
        if e["ds"] == depth:
            frontier_eid, frontier = e["s"], names[e["s"]]
        else:
            frontier_eid, frontier = e["t"], names[e["t"]]
        candidates.append(Candidate(
            triple=(h, r, t),
            score=score_fn((h, r, t)),
            depth=depth,
            frontier_entity=frontier,
            degree=degree[frontier_eid],
            is_gold=bool(e.get("g")),
        ))
    return candidates


def load_questions(
    dataset: str,
    split: str,
    data_root: str | Path,
    subgraph_root: str | Path | None = None,
    score_fn: ScoreFn | None = None,
    limit: int | None = None,
) -> list[QuestionData]:
    """Load questions from ``data/processed/<dataset>/qa_<split>.jsonl.gz``.

    ``subgraph_root`` optionally points to ``data/processed/<dataset>/subgraphs``
    so the per-question candidate pools can be loaded when present. Without it,
    questions still carry gold evidence and answers, and ``candidates`` is empty.
    """
    data_root = Path(data_root)
    qa_path = data_root / dataset / f"qa_{split}.jsonl.gz"
    if not qa_path.exists():
        raise FileNotFoundError(f"Missing QA artifact: {qa_path}")

    sub_root = Path(subgraph_root) if subgraph_root else None
    questions: list[QuestionData] = []
    with gzip.open(qa_path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            gold_triples = {tuple(t) for t in rec["gold"]["triples"]}
            gold_paths = [[tuple(t) for t in rec["gold"]["triples"]]] if gold_triples else []
            candidates: list[Candidate] = []
            if sub_root is not None and score_fn is not None:
                sg_path = sub_root / split / f"{rec['qid']}.json.gz"
                if sg_path.exists():
                    candidates = load_subgraph_candidates(sg_path, score_fn)
            questions.append(QuestionData(
                qid=rec["qid"],
                question=rec["question"],
                answers=rec["answers"],
                answer_mids=rec.get("answer_mids", []),
                gold_triples=gold_triples,
                gold_paths=gold_paths,
                candidates=candidates,
                _score_fn=score_fn,
            ))
            if limit is not None and len(questions) >= limit:
                break
    return questions
