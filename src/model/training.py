"""Training loop pieces for the foresight scorer (paper Sec 3.2 and App. E, Eq 19-20).

Paper settings: BCE on sigma(s_t), summed over a question's training triples
(Eq 19) and averaged over the questions in a mini-batch (Eq 20); AdamW with
lr 1e-5, betas (0.9, 0.999), weight decay 1e-3, no warmup or decay; dropout 0.1;
5 epochs; frozen embedding encoder.

Default training fits the initial edge scorer with zero entity states and the
original question. Optional state_provider supplies real FFP states without
detaching their graph. Joint training must also optimize C's parameters. Zero
states do not train state-input weights; a singleton Q_sub does not train f_attn.
"""

from __future__ import annotations

import hashlib
import math
import random
import time
from dataclasses import asdict, dataclass, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn.functional as F

from .sampler import TrainingExample
from .scorer import TAU_E, TAU_P, TripleBatch, pack_sub_questions, sub_question_set


@dataclass
class TrainConfig:
    epochs: int = 5                  # paper App. E
    lr: float = 1e-5                 # paper App. E
    betas: Tuple[float, float] = (0.9, 0.999)
    weight_decay: float = 1e-3       # paper App. E
    batch_questions: int = 16        # not given in the paper
    amp: bool = True                 # bfloat16 autocast on CUDA only
    seed: int = 0
    max_triples_per_forward: int = 64


def collate(examples: Sequence[TrainingExample], store):
    """Turn training examples into a scorer batch.

    Returns ``(batch, q_sub, q_sub_mask, labels)``.
    """
    q_index = np.concatenate([np.full(len(ex.labels), i, dtype=np.int64) for i, ex in enumerate(examples)])
    head = np.concatenate([ex.head_ids for ex in examples])
    rel = np.concatenate([ex.rel_ids for ex in examples])
    tail = np.concatenate([ex.tail_ids for ex in examples])
    q_ori = store.encode_questions([ex.question for ex in examples])
    per_question = []
    for ex in examples:
        texts = sub_question_set(ex.question, ex.sub_questions)
        per_question.append(store.encode_questions(texts))
    q_sub, mask = pack_sub_questions(per_question)
    batch = TripleBatch.from_ids(store, head, rel, tail, q_index, q_ori)
    labels = torch.from_numpy(np.concatenate([ex.labels for ex in examples]))
    return batch, q_sub, mask, labels


def _triple_microbatches(examples, max_triples_per_forward):
    """Slice CPU ID/label arrays before embedding lookup, preserving every triple.

    Each yielded example contains only its current slice. Question indices are
    local to that micro-batch; loss normalization uses the *original* batch size.
    """
    if max_triples_per_forward < 1:
        raise ValueError("max_triples_per_forward must be positive")
    offsets = np.cumsum([0] + [len(ex.labels) for ex in examples])
    for start in range(0, int(offsets[-1]), max_triples_per_forward):
        end = start + max_triples_per_forward
        micro = []
        for ex, offset in zip(examples, offsets[:-1]):
            lo, hi = max(0, start - int(offset)), min(len(ex.labels), end - int(offset))
            if lo < hi:
                micro.append(replace(ex, **{
                    name: getattr(ex, name)[lo:hi]
                    for name in ("head_ids", "rel_ids", "tail_ids", "labels", "edge_ids")
                }))
        yield micro


def _score(model, examples, batch, q_sub, mask, state_provider):
    # Provider receives sliced examples in current micro-batch order and its device batch.
    # Return h_s/h_o [N, d] on batch device; never detach real FFP states.
    h_s, h_o = (None, None) if state_provider is None else state_provider(examples, batch)
    return model(batch, q_sub, mask, h_s=h_s, h_o=h_o)


def split_qa_records(records, validation_records=None, seed=0):
    """Split raw records by qid before sampling; independent splits must be disjoint."""
    records = list(records)
    if validation_records is not None:
        validation_records = list(validation_records)
        if {r["qid"] for r in records} & {r["qid"] for r in validation_records}:
            raise ValueError("train and validation question IDs overlap")
        return records, validation_records
    ids = sorted({r["qid"] for r in records}, key=lambda q:
                 (hashlib.sha256(f"{seed}:{q}".encode()).digest(), q))
    if len(ids) < 2:
        raise ValueError("need at least two question IDs for train/validation split")
    held = set(ids[:min(len(ids) - 1, max(1, math.ceil(len(ids) * 0.1)))])
    return ([r for r in records if r["qid"] not in held],
            [r for r in records if r["qid"] in held])


def question_bce_loss(logits: torch.Tensor, labels: torch.Tensor, q_index: torch.Tensor,
                      n_questions: int) -> torch.Tensor:
    """Eq 19-20: per-question sum of triple BCE, averaged over the questions."""
    per_triple = F.binary_cross_entropy_with_logits(logits.float(), labels.float(), reduction="none")
    per_question = torch.zeros(n_questions, device=logits.device).index_add_(0, q_index, per_triple)
    return per_question.mean()


def roc_auc(labels: np.ndarray, scores: np.ndarray) -> float:
    """Area under the ROC curve (ties get average ranks); NaN if one class is missing."""
    labels = np.asarray(labels).astype(bool)
    scores = np.asarray(scores, dtype=np.float64)
    n_pos = int(labels.sum())
    n_neg = int(len(labels) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    _, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    avg_rank = np.cumsum(counts) - (counts - 1) / 2.0
    ranks = avg_rank[inverse]
    return float((ranks[labels].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def threshold_metrics(labels: np.ndarray, probs: np.ndarray, tau: float) -> Dict[str, float]:
    labels = np.asarray(labels).astype(bool)
    kept = np.asarray(probs) > tau
    return {
        "recall": float(kept[labels].mean()) if labels.any() else float("nan"),
        "kept_frac": float(kept.mean()) if len(kept) else float("nan"),
        "neg_kept_frac": float(kept[~labels].mean()) if (~labels).any() else float("nan"),
    }


def _autocast(device: torch.device, enabled: bool):
    if enabled and device.type == "cuda":
        return torch.autocast("cuda", dtype=torch.bfloat16)
    return torch.autocast(device.type, enabled=False)


@torch.no_grad()
def evaluate(model, examples: Sequence[TrainingExample], store, device: torch.device,
             batch_questions: int = 16, amp: bool = True, state_provider=None,
             max_triples_per_forward: int = 64) -> Dict[str, float]:
    """Edge-level metrics on a set of training regions (tau on the probability scale)."""
    model.eval()
    all_labels, all_probs, losses = [], [], []
    for start in range(0, len(examples), batch_questions):
        chunk = examples[start:start + batch_questions]
        for micro in _triple_microbatches(chunk, max_triples_per_forward):
            batch, q_sub, mask, labels = collate(micro, store)
            batch, q_sub, mask, labels = batch.to(device), q_sub.to(device), mask.to(device), labels.to(device)
            with _autocast(device, amp):
                logits = _score(model, micro, batch, q_sub, mask, state_provider).float()
            losses.append(question_bce_loss(logits, labels, batch.q_index, len(chunk)).item() * len(chunk))
            all_labels.append(labels.cpu().numpy())
            all_probs.append(torch.sigmoid(logits).cpu().numpy())
            del batch, q_sub, mask, labels, logits
    if not all_labels:
        return {}
    labels = np.concatenate(all_labels)
    probs = np.concatenate(all_probs)
    out = {"loss": float(sum(losses) / len(examples)), "auc": roc_auc(labels, probs),
           "n_questions": len(examples), "n_triples": int(len(labels)), "n_pos": int(labels.sum())}
    for name, tau in (("tau_e", TAU_E), ("tau_p", TAU_P)):
        for k, v in threshold_metrics(labels, probs, tau).items():
            out[f"{name}_{k}"] = v
    return out


def train(model, train_examples: List[TrainingExample], store, device: torch.device,
          config: Optional[TrainConfig] = None, eval_examples: Optional[List[TrainingExample]] = None,
          on_epoch_end: Optional[Callable[[int, Dict], None]] = None,
          log: Callable[[str], None] = print, state_provider=None,
          extra_parameters=()) -> List[Dict]:
    """Train initial scorer, or accept real FFP states via state_provider(examples, batch).

    For joint optimization pass C's trainable parameters as extra_parameters.
    The provider owns C's train/eval mode and recomputes states for each micro-batch
    from its sliced examples. Do not reuse a consumed computation graph.
    Returns one metrics dict per epoch.
    """
    cfg = config or TrainConfig()
    if not train_examples:
        raise ValueError("no training examples")
    if eval_examples and {ex.qid for ex in train_examples} & {ex.qid for ex in eval_examples}:
        raise ValueError("train and validation question IDs overlap")
    rng = random.Random(cfg.seed)
    torch.manual_seed(cfg.seed)
    model.to(device)
    log("training mode: " + ("FFP states supplied" if state_provider else "initial scorer (zero states)"))
    optimizer = torch.optim.AdamW([*model.parameters(), *extra_parameters], lr=cfg.lr, betas=cfg.betas,
                                  weight_decay=cfg.weight_decay)
    history = []
    order = list(range(len(train_examples)))
    n_steps = math.ceil(len(order) / cfg.batch_questions)
    for epoch in range(1, cfg.epochs + 1):
        model.train()
        rng.shuffle(order)
        t0, total = time.time(), 0.0
        for step in range(n_steps):
            idx = order[step * cfg.batch_questions:(step + 1) * cfg.batch_questions]
            chunk = [train_examples[i] for i in idx]
            optimizer.zero_grad(set_to_none=True)
            for micro in _triple_microbatches(chunk, cfg.max_triples_per_forward):
                batch, q_sub, mask, labels = collate(micro, store)
                batch, q_sub, mask, labels = batch.to(device), q_sub.to(device), mask.to(device), labels.to(device)
                with _autocast(device, cfg.amp):
                    logits = _score(model, micro, batch, q_sub, mask, state_provider)
                # Sum triple BCE / original question count, never / micro size.
                loss = question_bce_loss(logits, labels, batch.q_index, len(chunk))
                loss.backward()
                total += loss.item()
                del batch, q_sub, mask, labels, logits, loss
            optimizer.step()
            if (step + 1) % 50 == 0 or step + 1 == n_steps:
                log(f"epoch {epoch} step {step + 1}/{n_steps} loss {total / (step + 1):.4f} "
                    f"({time.time() - t0:.0f}s)")
        metrics = {"epoch": epoch, "train_loss": total / n_steps, "seconds": time.time() - t0,
                   "config": asdict(cfg),
                   "training_mode": "ffp_states" if state_provider else "initial_zero_state"}
        if eval_examples:
            res = evaluate(model, eval_examples, store, device, cfg.batch_questions,
                           amp=cfg.amp, state_provider=state_provider,
                           max_triples_per_forward=cfg.max_triples_per_forward)
            metrics.update({f"eval_original_{k}": v for k, v in res.items()})
            log(f"epoch {epoch} eval auc {metrics.get('eval_original_auc', float('nan')):.4f} "
                f"recall@tau_e {metrics.get('eval_original_tau_e_recall', float('nan')):.3f} "
                f"recall@tau_p {metrics.get('eval_original_tau_p_recall', float('nan')):.3f}")
        history.append(metrics)
        if on_epoch_end is not None:
            on_epoch_end(epoch, metrics)
    return history
