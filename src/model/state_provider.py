# -*- coding: utf-8 -*-
"""FFP state provider: bridge member B's flat training batches to member C's subgraph FFP.

B's ``training.train`` calls ``state_provider(examples, batch)`` and expects
``(h_s, h_o)`` shaped ``[N, d]`` aligned with the flattened batch triples, with
gradients preserved. This module, per question, builds member A's subgraph, runs
FFP far-to-near message passing, and maps the resulting node states back onto
the batch triples through the KG entity id (``gid``).

Two usage notes inherited from member B's design:

* The provider is called inside the training autocast context, so ``h`` follows
  ``z``'s dtype (bfloat16 on CUDA). The returned states therefore match the
  dtype ``scorer.score`` will see, avoiding mixed-dtype ``torch.cat``.
* FFP tensors are recomputed on every call (never cached across micro-batches),
  because a consumed computation graph must not be reused by ``loss.backward``.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

import torch

from .ffp import ForesightFeedbackPropagation, ffp_node_states
from .fse import subgraph_to_batch
from .kg_bridge import default_subgraph_config, ensure_fog_importable


def build_subgraph_from_record(kg, record: dict, config=None):
    """Build member A's ``Subgraph`` for one qa record, or ``None`` without a source."""
    ensure_fog_importable()
    from fog.schema import Triple
    from fog.subgraph import build_subgraph

    cfg = config or default_subgraph_config()
    seed_names = [s.get("name") for s in record.get("source_entities", []) if s.get("name")]
    if not seed_names:
        return None
    gold = [Triple(t[0], t[1], t[2]) for t in (record.get("gold") or {}).get("triples", [])]
    answers = [a for a in (record.get("answers") or []) if a]
    return build_subgraph(kg, record.get("qid", ""), seed_names, gold, answers, cfg)


def make_subgraph_loader(kg, records: Dict[str, dict], config=None) -> Callable[[str], object]:
    """Return a cached ``qid -> Subgraph | None`` loader."""
    cache: Dict[str, object] = {}

    def loader(qid: str):
        if qid in cache:
            return cache[qid]
        record = records.get(qid)
        subgraph = build_subgraph_from_record(kg, record, config) if record else None
        cache[qid] = subgraph
        return subgraph

    return loader


class FfpStateProvider:
    """``state_provider`` for member B's ``train`` / ``evaluate``."""

    def __init__(self, scorer, ffp: ForesightFeedbackPropagation, store, kg,
                 subgraph_loader: Callable[[str], object], device):
        self.scorer = scorer
        self.ffp = ffp
        self.store = store
        self.kg = kg
        self.subgraph_loader = subgraph_loader
        self.device = device
        self._gid_maps: Dict[str, Dict[int, int]] = {}

    def _gid_map(self, qid: str, subgraph) -> Dict[int, int]:
        mapping = self._gid_maps.get(qid)
        if mapping is None:
            mapping = {node.gid: i for i, node in enumerate(subgraph.nodes)}
            self._gid_maps[qid] = mapping
        return mapping

    def __call__(self, examples, batch):
        device = batch.head_emb.device
        dim = batch.head_emb.shape[-1]
        ref_dtype = batch.head_emb.dtype
        parts = []  # (h_s_slice | None, h_o_slice | None, n_triples)

        for ex in examples:
            n = int(len(ex.head_ids))
            subgraph = self.subgraph_loader(ex.qid)
            if subgraph is None or not getattr(subgraph, "nodes", None):
                parts.append((None, None, n))
                continue

            q_ori = self.store.encode_questions([ex.question]).to(device)
            sub_batch = subgraph_to_batch(subgraph, self.store, self.kg, q_ori).to(device)
            z = self.scorer.encode(sub_batch)                     # gradients flow to scorer
            h = ffp_node_states(self.ffp, z, subgraph, device)    # gradients flow to FFP
            ref_dtype = h.dtype

            gid_to_idx = self._gid_map(ex.qid, subgraph)
            hs = torch.zeros(n, dim, device=device, dtype=h.dtype)
            ho = torch.zeros_like(hs)
            head_ids = ex.head_ids[:n]
            tail_ids = ex.tail_ids[:n]
            for i in range(n):
                hi = gid_to_idx.get(int(head_ids[i]))
                ti = gid_to_idx.get(int(tail_ids[i]))
                if hi is not None:
                    hs[i] = h[hi]
                if ti is not None:
                    ho[i] = h[ti]
            parts.append((hs, ho, n))

        h_s = torch.cat([
            p[0] if p[0] is not None
            else torch.zeros(p[2], dim, device=device, dtype=ref_dtype)
            for p in parts
        ], dim=0)
        h_o = torch.cat([
            p[1] if p[1] is not None
            else torch.zeros(p[2], dim, device=device, dtype=ref_dtype)
            for p in parts
        ], dim=0)
        return h_s, h_o


def train_with_ffp(scorer, ffp: ForesightFeedbackPropagation, train_examples, store, kg,
                   subgraph_loader, device, config=None, eval_examples=None,
                   on_epoch_end=None, log=print, start_epoch: int = 1,
                   optimizer_state=None):
    """Wire member B's ``train`` to member C's FFP: joint scorer + FFP optimization."""
    from .training import train

    provider = FfpStateProvider(scorer, ffp, store, kg, subgraph_loader, device)
    return train(
        scorer, train_examples, store, device,
        config=config, eval_examples=eval_examples, on_epoch_end=on_epoch_end, log=log,
        state_provider=provider, extra_parameters=list(ffp.parameters()),
        start_epoch=start_epoch, optimizer_state=optimizer_state,
    )

