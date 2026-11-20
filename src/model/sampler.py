"""Per-question training regions for the foresight scorer (paper App. E, Eq 13-18).

For each training question:

* T_pos (Eq 13): the gold triples from member A's ``qa_<split>.jsonl.gz``, mapped to
  KG edge ids.
* E_seed (Eq 14): entities touched by T_pos, optionally unioned with an anchored set
  E_0. Here E_0 is the question's source entities.
* deg_pos(v) (Eq 15): number of positive triples that contain v.
* T_neg (Eq 16): for each seed v, kappa * deg_pos(v) triples sampled uniformly from
  N(v) \\ T_pos, deduplicated across seeds; kappa = 10.
* Generic-relation filter ("optionally ... before sampling"): member A's
  ``SubgraphConfig.relation_allowed``, the same rule used for inference-time
  expansion.
* Labels (Eq 18): 1 for T_pos, 0 for T_neg.

Project choices (not in the paper):

* Gold triples whose relation fails the filter (for example type constraints such
  as ``common.topic.notable_types``) are dropped from T_pos, because expansion never
  shows them to the scorer.
* Member A's gold path reaches only one of the answers. When
  ``augment_answer_paths`` is on, the same relation chain is walked from the source
  and every path that ends at any answer is added to T_pos. Without this, the edges
  towards the other answers would be sampled as negatives.
* Scorer questions never depend on gold paths. Optional sub-questions must be
  generated independently and supplied explicitly by the caller.
"""

from __future__ import annotations

import gzip
import json
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import numpy as np


@dataclass
class SamplerConfig:
    kappa: int = 10                      # paper App. E
    use_source_anchors: bool = True      # E_0 = source entities
    filter_generic_relations: bool = True
    augment_answer_paths: bool = True    # project choice, see module docstring
    max_path_frontier: int = 5000        # give up augmentation beyond this many nodes per hop
    seed: int = 0


@dataclass
class TrainingExample:
    qid: str
    question: str
    sub_questions: List[str]             # independent sub-questions (original question included)
    head_ids: np.ndarray                 # int64 [T]
    rel_ids: np.ndarray                  # int64 [T]
    tail_ids: np.ndarray                 # int64 [T]
    labels: np.ndarray                   # float32 [T]
    edge_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))

    @property
    def n_pos(self) -> int:
        return int(self.labels.sum())

    @property
    def n_neg(self) -> int:
        return int(len(self.labels) - self.labels.sum())


def iter_qa_records(processed_root: str | Path, dataset: str, split: str) -> Iterator[dict]:
    """Iterate member A's ``<dataset>/qa_<split>.jsonl.gz`` records."""
    path = Path(processed_root) / dataset / f"qa_{split}.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


class TrainingRegionBuilder:
    """Builds :class:`TrainingExample` objects from member A's records and KG index."""

    def __init__(self, kg, config: Optional[SamplerConfig] = None,
                 relation_filter: Optional[Callable[[str, int], bool]] = None) -> None:
        self.kg = kg
        self.config = config or SamplerConfig()
        if relation_filter is None and self.config.filter_generic_relations:
            from .kg_bridge import default_subgraph_config

            relation_filter = default_subgraph_config().relation_allowed
        rel_degree = kg.rel_degree
        if relation_filter is None:
            self.allowed_rel = np.ones(kg.n_relations, dtype=bool)
        else:
            self.allowed_rel = np.array(
                [relation_filter(r, int(rel_degree[i])) for i, r in enumerate(kg.relations)], dtype=bool)
        self.stats: Counter = Counter()

    # ----------------------------------------------------------------- helpers
    def _entity(self, name: str) -> Optional[int]:
        eid = self.kg.entity_id(name)
        return eid if eid is not None else self.kg.resolve_entity(name)

    def _edge_id(self, h: str, r: str, t: str) -> Optional[int]:
        hid, rid, tid = self.kg.entity_id(h), self.kg.relation_id(r), self.kg.entity_id(t)
        if hid is None or rid is None or tid is None:
            return None
        eids = self.kg.out_edges_with_rel(hid, rid)
        hit = eids[self.kg.tail[eids] == tid]
        return int(hit[0]) if len(hit) else None

    def _walk_chain(self, edges: Sequence[int], sources: Set[int]) -> Tuple[int, List[Tuple[int, int, bool]], bool]:
        """Follow the gold edges in order from the source side.

        Returns ``(start, steps, complete)`` where each step is
        ``(node_before_step, relation_id, forward)``.
        """
        h0, _, t0 = self.kg.edge(edges[0])
        start = h0 if h0 in sources else (t0 if t0 in sources else h0)
        x, steps = start, []
        for e in edges:
            h, r, t = self.kg.edge(e)
            if h == x:
                steps.append((x, r, True))
                x = t
            elif t == x:
                steps.append((x, r, False))
                x = h
            else:
                return start, steps, False
        return start, steps, True

    def _answer_paths(self, start: int, steps: Sequence[Tuple[int, int, bool]],
                      answers: Set[int]) -> Optional[Set[int]]:
        """Edges of every path from ``start`` that follows ``steps`` and ends at an answer."""
        frontier = {start}
        layers: List[List[Tuple[int, int, int]]] = []   # (edge, from, to) per step
        for _, rid, forward in steps:
            layer = []
            for v in frontier:
                if forward:
                    eids = self.kg.out_edges_with_rel(v, rid)
                    ends = self.kg.tail[eids]
                else:
                    eids = self.kg.in_edges_with_rel(v, rid)
                    ends = self.kg.head[eids]
                layer.extend(zip(eids.tolist(), [v] * len(eids), ends.tolist()))
            frontier = {to for _, _, to in layer}
            if len(frontier) > self.config.max_path_frontier:
                return None
            layers.append(layer)
        keep = frontier & answers
        kept: Set[int] = set()
        for layer in reversed(layers):
            prev = set()
            for e, frm, to in layer:
                if to in keep:
                    kept.add(e)
                    prev.add(frm)
            keep = prev
        return kept

    # ------------------------------------------------------------------- build
    def _gold_edges(self, record: dict, count: bool = True) -> Optional[List[int]]:
        """Gold triples as KG edge ids in path order (filtered), or None if none are usable."""
        gold = record.get("gold") or {}
        if not gold.get("matched"):
            if count:
                self.stats["skip_unmatched"] += 1
            return None
        pos_order: List[int] = []
        for h, r, t in gold.get("triples", []):
            e = self._edge_id(h, r, t)
            if e is None:
                if count:
                    self.stats["gold_triple_not_in_kg"] += 1
                continue
            if not self.allowed_rel[self.kg.rel[e]]:
                if count:
                    self.stats["gold_triple_filtered_relation"] += 1
                continue
            if e not in pos_order:
                pos_order.append(e)
        if not pos_order:
            if count:
                self.stats["skip_no_positive"] += 1
            return None
        return pos_order

    @staticmethod
    def _sources(record: dict) -> Set[int]:
        return {s["kg_id"] for s in record.get("source_entities", []) if s.get("kg_id") is not None}

    def sub_questions_for(self, record: dict, sub_questions: Sequence[str] = ()) -> List[str]:
        """Original question plus caller-supplied, gold-independent sub-questions.

        Deliberately does not inspect gold or infer provenance from record fields.
        """
        return list(dict.fromkeys([record["question"], *sub_questions]))

    def build(self, record: dict, epoch: int = 0,
              sub_questions: Sequence[str] = ()) -> Optional[TrainingExample]:
        """One question's training region, or None when it has no usable positive."""
        cfg, kg = self.config, self.kg
        pos_order = self._gold_edges(record)
        if pos_order is None:
            return None

        sources = self._sources(record)
        start, steps, complete = self._walk_chain(pos_order, sources)
        positives = set(pos_order)
        if cfg.augment_answer_paths and complete:
            answers = {a for a in (self._entity(n) for n in record.get("answers", [])) if a is not None}
            extra = self._answer_paths(start, steps, answers)
            if extra is None:
                self.stats["augment_aborted_frontier"] += 1
            else:
                added = extra - positives
                self.stats["augmented_positive_edges"] += len(added)
                self.stats["questions_augmented"] += bool(added)
                positives |= extra

        # Eq 14-16
        deg_pos: Counter = Counter()
        for e in positives:
            h, _, t = kg.edge(e)
            deg_pos[h] += 1
            if t != h:
                deg_pos[t] += 1
        seeds = set(deg_pos) | (sources if cfg.use_source_anchors else set())
        pos_arr = np.fromiter(positives, dtype=np.int64)
        rng = np.random.default_rng([cfg.seed, epoch, zlib.crc32(record["qid"].encode("utf-8"))])
        negatives: Set[int] = set()
        for v in sorted(seeds):
            k = cfg.kappa * deg_pos.get(v, 0)
            if k == 0:
                continue
            cand = np.unique(np.concatenate([kg.out_edges(v), kg.in_edges(v)]).astype(np.int64))
            cand = cand[self.allowed_rel[kg.rel[cand]]]
            cand = cand[~np.isin(cand, pos_arr)]
            if len(cand) > k:
                cand = rng.choice(cand, size=k, replace=False)
            negatives.update(cand.tolist())

        edges = np.array(sorted(positives) + sorted(negatives), dtype=np.int64)
        labels = np.zeros(len(edges), dtype=np.float32)
        labels[: len(positives)] = 1.0
        self.stats["questions"] += 1
        self.stats["positives"] += len(positives)
        self.stats["negatives"] += len(negatives)
        return TrainingExample(
            qid=record["qid"],
            question=record["question"],
            sub_questions=self.sub_questions_for(record, sub_questions),
            head_ids=kg.head[edges].astype(np.int64),
            rel_ids=kg.rel[edges].astype(np.int64),
            tail_ids=kg.tail[edges].astype(np.int64),
            labels=labels,
            edge_ids=edges,
        )
