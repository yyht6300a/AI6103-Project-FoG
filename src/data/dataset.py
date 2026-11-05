"""Load WebQSP / CWQ examples and convert them to FoG inputs.

Each example from the RoG datasets carries the fields FoG requires:
    question   - natural language question
    answer     - list of answer strings
    q_entity   - source entities (readable names)
    a_entity   - answer entities (readable names)
    graph      - Freebase reasoning subgraph: list of [head, relation, tail]

The ``graph`` field is the Freebase subgraph with readable names, which can be
used directly as the minimal-reproduction knowledge graph and as the source of
gold reasoning paths for scorer training.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Example:
    example_id: str
    question: str
    answers: list[str]
    source_entities: list[str]
    answer_entities: list[str]
    triples: list[list[str]]  # [head, relation, tail]
    raw: dict[str, Any] = field(default_factory=dict)


def _as_triples(graph: Any) -> list[list[str]]:
    if isinstance(graph, str):
        graph = json.loads(graph)
    return [list(t) for t in graph] if graph else []


def example_from_row(row: dict[str, Any]) -> Example:
    graph = _as_triples(row.get("graph"))
    return Example(
        example_id=str(row.get("id", "")),
        question=str(row.get("question", "")),
        answers=[str(a) for a in (row.get("answer") or [])],
        source_entities=[str(e) for e in (row.get("q_entity") or [])],
        answer_entities=[str(e) for e in (row.get("a_entity") or [])],
        triples=graph,
        raw=row,
    )


def load_examples(path: str | Path) -> list[Example]:
    """Load a JSON or JSONL sample produced by scripts/download_data.py."""
    path = Path(path)
    if path.suffix == ".jsonl":
        return [example_from_row(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        rows = []
        for split_rows in data.values():
            rows.extend(split_rows)
        return [example_from_row(r) for r in rows]
    return [example_from_row(r) for r in data]


def build_graph(examples: list[Example]) -> dict[str, list[tuple[str, str]]]:
    """Build forward and reverse adjacency from the example triples.

    The forward map keeps the original directed triples; the reverse map exists
    so layered BFS expansion can walk against the edge direction. FoG keeps the
    original (s, r, o) direction and only orients the expanded subgraph inward
    at FFP time.
    """
    adj_out: dict[str, list[tuple[str, str]]] = {}
    adj_in: dict[str, list[tuple[str, str]]] = {}
    for ex in examples:
        for head, relation, tail in ex.triples:
            adj_out.setdefault(head, []).append((relation, tail))
            adj_in.setdefault(tail, []).append((relation, head))
    return {"out": adj_out, "in": adj_in}

