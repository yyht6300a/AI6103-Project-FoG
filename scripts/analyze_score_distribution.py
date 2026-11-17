"""Analyze an Initial Scorer on the validation set saved beside epoch*.pt.

Keep split.json, sampler_stats.json and metrics.jsonl beside the checkpoint.
Sampler settings were not persisted by train_scorer.py: --kappa and
--no-augment-answer-paths must match that training invocation (defaults match
training defaults). No encoder is loaded. CUDA inference uses BF16 autocast.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.train_scorer import build_examples, load_training_records  # noqa: E402
from src.model.embedding import EmbeddingStore  # noqa: E402
from src.model.kg_bridge import DEFAULT_INDEX_DIR, DEFAULT_PROCESSED_ROOT, load_kg  # noqa: E402
from src.model.sampler import SamplerConfig, TrainingRegionBuilder, iter_qa_records  # noqa: E402
from src.model.scorer import ForesightTripleScorer, TAU_E, TAU_P  # noqa: E402
from src.model.training import (  # noqa: E402
    _autocast, _triple_microbatches, collate, question_bce_loss, roc_auc, threshold_metrics,
)


def validation_examples(args):
    """Reproduce validation regions using the training run's saved split and counts."""
    run_dir = Path(args.checkpoint).parent
    match = re.fullmatch(r"epoch(\d+)", Path(args.checkpoint).stem)
    if match is None:
        raise ValueError("checkpoint must be named epoch<k>.pt")
    epoch = int(match[1])
    rows = [json.loads(line) for line in (run_dir / "metrics.jsonl").read_text().splitlines()
            if line.strip()]
    reference = next(row for row in reversed(rows) if row["epoch"] == epoch)
    if reference.get("training_mode", "initial_zero_state") != "initial_zero_state":
        raise ValueError("this script analyzes Initial Scorer checkpoints only")
    split = json.loads((run_dir / "split.json").read_text())
    if args.seed != split["seed"] or args.seed != reference["config"]["seed"]:
        raise ValueError("--seed must match split.json and the training epoch configuration")
    if "test" in args.train_split.lower():
        raise ValueError("--train-split cannot select Test Set data")
    source = split["validation_source"]
    if source not in ("dev", "val", "train_qid_holdout_10pct"):
        raise ValueError(f"expected validation split, refusing source {source!r} (no Test Set)")
    if set(split["train_qids"]) & set(split["eval_qids"]):
        raise ValueError("saved train and validation question IDs overlap")

    if source == "train_qid_holdout_10pct":
        # Saved IDs remain authoritative even if a dev/val file was added later.
        wanted = set(split["eval_qids"])
        records = [rec for rec in iter_qa_records(args.processed_root, args.dataset, args.train_split)
                   if rec["qid"] in wanted]
    else:
        _, records, _ = load_training_records(args.processed_root, args.dataset,
                                              args.train_split, source, args.seed)
    if {rec["qid"] for rec in records} != set(split["eval_qids"]):
        raise ValueError("validation question IDs do not match the saved training split")

    kg = load_kg(args.index_dir)
    store = EmbeddingStore.load(args.cache)
    store.validate_index(kg)
    sampler = SamplerConfig(seed=args.seed, kappa=args.kappa,
                            augment_answer_paths=not args.no_augment_answer_paths)
    builder = TrainingRegionBuilder(kg, sampler)
    # Training saves all split IDs, even when --limit-eval restricts usable examples.
    examples = build_examples(builder, records)
    if len(examples) > int(reference["eval_original_n_questions"]):
        builder.stats.clear()
        examples = build_examples(builder, records, limit=int(reference["eval_original_n_questions"]))
    expected = (run_dir / "sampler_stats.json")
    expected = json.loads(expected.read_text())["eval"]
    if dict(builder.stats) != expected:
        raise ValueError("validation sampler statistics differ; check dataset, KG, --kappa and "
                         "--no-augment-answer-paths against the training run")
    counts = {"n_questions": len(examples), "n_triples": sum(len(ex.labels) for ex in examples),
              "n_pos": sum(ex.n_pos for ex in examples)}
    if not examples or any(value != reference[f"eval_original_{key}"] for key, value in counts.items()):
        raise ValueError("validation counts differ from metrics.jsonl")
    return examples, store, reference, sampler, source, epoch


@torch.no_grad()
def predict(model, examples, store, device, max_triples_per_forward, group_by_mid=False):
    """Question batches of one; keep only labels/probabilities on CPU between forwards."""
    model.eval()
    all_labels, all_probs = [], []
    all_contains_mid = []
    loss_sum = 0.0
    for ex in examples:
        for micro in _triple_microbatches([ex], max_triples_per_forward):
            batch, q_sub, mask, labels = collate(micro, store)
            batch, q_sub, mask, labels = batch.to(device), q_sub.to(device), mask.to(device), labels.to(device)
            with _autocast(device, True):
                logits = model(batch, q_sub, mask).float()
            loss_sum += question_bce_loss(logits, labels, batch.q_index, 1).item()
            all_labels.append(labels.cpu().numpy())
            all_probs.append(torch.sigmoid(logits).cpu().numpy())
            if group_by_mid:
                all_contains_mid.append((batch.head_is_mid | batch.tail_is_mid).cpu().numpy())
            del batch, q_sub, mask, labels, logits
    result = (np.concatenate(all_labels), np.concatenate(all_probs), loss_sum / len(examples))
    return (*result, np.concatenate(all_contains_mid)) if group_by_mid else result


def probability_stats(probs):
    if not len(probs):
        return {"count": 0, **{key: None for key in
                ("mean", "median", "p90", "p95", "p99", "min", "max", "above_0.1", "above_0.7")}}
    quantiles = np.percentile(probs, [50, 90, 95, 99])
    return {"count": int(len(probs)), "mean": float(np.mean(probs, dtype=np.float64)),
            **dict(zip(("median", "p90", "p95", "p99"), map(float, quantiles))),
            "min": float(probs.min()), "max": float(probs.max()),
            "above_0.1": float((probs > TAU_E).mean()),
            "above_0.7": float((probs > TAU_P).mean())}


def mid_group_stats(labels, probs, contains_mid):
    """Complementary masks partition the aligned per-triple arrays exactly once."""
    groups = {}
    for name, mask in (("named_only", ~contains_mid), ("contains_mid", contains_mid)):
        group_labels, group_probs = labels[mask], probs[mask]
        n, n_pos = len(group_labels), int(group_labels.sum())
        groups[name] = {
            "n_triples": n, "n_positive": n_pos, "n_negative": n - n_pos,
            "positive_ratio": n_pos / n if n else float("nan"),
            "auc": roc_auc(group_labels, group_probs),
            "tau_e_recall": threshold_metrics(group_labels, group_probs, TAU_E)["recall"],
            "tau_p_recall": threshold_metrics(group_labels, group_probs, TAU_P)["recall"],
            "positive": probability_stats(group_probs[group_labels == 1]),
            "negative": probability_stats(group_probs[group_labels == 0]),
        }
    return groups


def write_histogram(path, positives, negatives):
    # Percentages are within each class. Bins are [left, right), last bin includes 1.
    edges = np.linspace(0.0, 1.0, 21)
    pos_counts, _ = np.histogram(positives, bins=edges)
    neg_counts, _ = np.histogram(negatives, bins=edges)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["bin_left", "bin_right", "positive_count", "positive_percent",
                         "negative_count", "negative_percent"])
        for left, right, pos, neg in zip(edges[:-1], edges[1:], pos_counts, neg_counts):
            writer.writerow([f"{left:.2f}", f"{right:.2f}", int(pos),
                             100.0 * pos / len(positives) if len(positives) else 0.0, int(neg),
                             100.0 * neg / len(negatives) if len(negatives) else 0.0])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True, choices=["webqsp", "cwq"])
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--max-triples-per-forward", type=int, default=64)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--group-by-mid", action="store_true",
                    help="compare named-only triples with triples containing a MID endpoint")
    ap.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    ap.add_argument("--processed-root", default=str(DEFAULT_PROCESSED_ROOT))
    ap.add_argument("--train-split", default="train")
    ap.add_argument("--kappa", type=int, default=10, help="must match training; not saved in checkpoint")
    ap.add_argument("--no-augment-answer-paths", action="store_true", help="must match training")
    args = ap.parse_args()
    if args.max_triples_per_forward < 1:
        ap.error("--max-triples-per-forward must be positive")
    torch.manual_seed(args.seed)
    examples, store, reference, sampler, source, epoch = validation_examples(args)
    device = torch.device(args.device)
    model = ForesightTripleScorer.load(args.checkpoint).to(device)
    predictions = predict(model, examples, store, device, args.max_triples_per_forward,
                          group_by_mid=args.group_by_mid)
    labels, probs, loss = predictions[:3]
    positive, negative = probs[labels == 1], probs[labels == 0]
    metrics = {"loss": loss, "auc": roc_auc(labels, probs)}
    for name, tau in (("tau_e", TAU_E), ("tau_p", TAU_P)):
        metrics.update({f"{name}_{key}": value for key, value in threshold_metrics(labels, probs, tau).items()})
    comparison = {}
    for key, value in metrics.items():
        recorded = reference[f"eval_original_{key}"]
        comparison[key] = {"recorded": recorded, "analyzed": value, "delta": value - recorded,
                           "close": bool(np.isclose(value, recorded, rtol=1e-3, atol=1e-4, equal_nan=True))}
    summary = {"dataset": args.dataset, "checkpoint": str(args.checkpoint), "epoch": epoch,
               "validation_source": source, "seed": args.seed, "sampler_config": asdict(sampler),
               "sampler_config_source": "CLI settings matching training; verified aggregate sampler statistics",
               "batch_questions": 1, "max_triples_per_forward": args.max_triples_per_forward,
               "bf16_autocast": device.type == "cuda", "n_questions": len(examples),
               "n_triples": len(labels), "positive": probability_stats(positive),
               "negative": probability_stats(negative), "metrics": metrics,
               "epoch_comparison": comparison,
               "histogram": {"bin_width": 0.05, "interval": "[left,right), final bin includes 1",
                             "percentage_denominator": "each class count", "percent_scale": 100},
               "threshold_comparison": "strictly greater than threshold"}
    if args.group_by_mid:
        summary["groups"] = mid_group_stats(labels, probs, predictions[3])
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_histogram(out / "histogram.csv", positive, negative)
    print(f"{args.dataset} epoch {epoch}: {len(examples)} validation questions, {len(labels)} triples")
    for key in ("auc", "tau_e_recall", "tau_p_recall"):
        print(f"{key}: {metrics[key]:.6f} (metrics.jsonl: {comparison[key]['recorded']:.6f}, "
              f"delta {comparison[key]['delta']:+.3g})")
    for name in ("positive", "negative"):
        print(f"{name}: " + json.dumps(summary[name]))
    if args.group_by_mid:
        print(f"{'group':<14} {'triples':>9} {'positive':>9} {'negative':>9} "
              f"{'pos_ratio':>10} {'AUC':>9} {'R@0.1':>9} {'R@0.7':>9}")
        for name, group in summary["groups"].items():
            print(f"{name:<14} {group['n_triples']:>9} {group['n_positive']:>9} "
                  f"{group['n_negative']:>9} {group['positive_ratio']:>10.4f} "
                  f"{group['auc']:>9.4f} {group['tau_e_recall']:>9.4f} {group['tau_p_recall']:>9.4f}")
    if not all(item["close"] for item in comparison.values()):
        print("WARNING: metrics differ from the recorded epoch; inspect epoch_comparison "
              "and confirm original sampler settings, AMP and data/cache versions.")
    print(f"saved {out / 'summary.json'} and {out / 'histogram.csv'}")


if __name__ == "__main__":
    main()
