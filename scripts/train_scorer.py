"""Train the foresight triple scorer on one dataset (paper App. E settings by default).

Needs member A's KG index and processed QA files, and the embedding cache from
``scripts/build_embedding_cache.py``. Example (run from the repository root):

  python scripts/train_scorer.py --dataset webqsp --cache cache/embeddings_v2 --out-dir checkpoints/scorer_webqsp

Writes ``epoch<k>.pt`` and ``metrics.jsonl`` (train loss and edge-level eval
metrics per epoch) into ``--out-dir``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.model.embedding import EmbeddingStore, QwenTextEncoder  # noqa: E402
from src.model.kg_bridge import DEFAULT_INDEX_DIR, DEFAULT_PROCESSED_ROOT, load_kg  # noqa: E402
from src.model.sampler import SamplerConfig, TrainingRegionBuilder, iter_qa_records  # noqa: E402
from src.model.scorer import ForesightTripleScorer, ScorerConfig  # noqa: E402
from src.model.training import TrainConfig, split_qa_records, train  # noqa: E402


def build_examples(builder, records, limit=None):
    examples = []
    for rec in records:
        if limit is not None and len(examples) >= limit:
            break
        ex = builder.build(rec)
        if ex is not None:
            examples.append(ex)
    return examples


def load_training_records(processed_root, dataset, train_split="train", eval_split=None, seed=0):
    if eval_split is None:
        eval_split = next((s for s in ("dev", "val") if s != train_split and
                           (Path(processed_root) / dataset / f"qa_{s}.jsonl.gz").exists()), None)
    records = iter_qa_records(processed_root, dataset, train_split)
    validation = (iter_qa_records(processed_root, dataset, eval_split)
                  if eval_split is not None else None)
    train_records, eval_records = split_qa_records(records, validation, seed)
    return train_records, eval_records, eval_split or "train_qid_holdout_10pct"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, choices=["webqsp", "cwq"])
    ap.add_argument("--cache", default="cache/embeddings_v2")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    ap.add_argument("--processed-root", default=str(DEFAULT_PROCESSED_ROOT))
    ap.add_argument("--train-split", default="train")
    ap.add_argument("--eval-split", default=None,
                    help="explicit evaluation split; default: dev, val, or 10%% train qid holdout")
    ap.add_argument("--limit-train", type=int, default=None)
    ap.add_argument("--limit-eval", type=int, default=None)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    # paper App. E
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--weight-decay", type=float, default=1e-3)
    ap.add_argument("--dropout", type=float, default=0.1)
    ap.add_argument("--kappa", type=int, default=10)
    # not given in the paper
    ap.add_argument("--batch-questions", type=int, default=16)
    ap.add_argument("--max-triples-per-forward", type=int, default=64,
                    help="maximum triples per forward/backward; accumulate within each question batch")
    ap.add_argument("--num-heads", type=int, default=8)
    ap.add_argument("--num-self-blocks", type=int, default=1)
    ap.add_argument("--num-cross-blocks", type=int, default=1)
    ap.add_argument("--no-augment-answer-paths", action="store_true")
    ap.add_argument("--no-amp", action="store_true")
    ap.add_argument("--online-query-encoder", action="store_true",
                    help="load Qwen3 to encode queries missing from the cache")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    if args.max_triples_per_forward < 1:
        ap.error("--max-triples-per-forward must be positive")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    kg = load_kg(args.index_dir)
    encoder = QwenTextEncoder() if args.online_query_encoder else None
    store = EmbeddingStore.load(args.cache, query_encoder=encoder)
    store.validate_index(kg)

    sampler_cfg = SamplerConfig(kappa=args.kappa, seed=args.seed,
                                augment_answer_paths=not args.no_augment_answer_paths)
    builder = TrainingRegionBuilder(kg, sampler_cfg)
    train_records, eval_records, eval_source = load_training_records(
        args.processed_root, args.dataset, args.train_split, args.eval_split, args.seed)
    print(f"validation source: {eval_source}")
    train_ex = build_examples(builder, train_records, args.limit_train)
    train_stats = dict(builder.stats)
    builder.stats.clear()
    eval_ex = build_examples(builder, eval_records, args.limit_eval)
    if not eval_ex:
        raise ValueError("validation contains no usable examples")
    (out_dir / "split.json").write_text(json.dumps({
        "seed": args.seed, "validation_source": eval_source,
        "train_qids": sorted({r["qid"] for r in train_records}),
        "eval_qids": sorted({r["qid"] for r in eval_records})}, indent=2))
    print(f"train {len(train_ex):,} questions, eval {len(eval_ex):,} ({time.time() - t0:.0f}s)")
    (out_dir / "sampler_stats.json").write_text(json.dumps(
        {"train": train_stats, "eval": dict(builder.stats)}, indent=2))

    if encoder is None:
        missing = {t for ex in train_ex + eval_ex for t in [ex.question, *ex.sub_questions]
                   if not store.has_query(t)}
        if missing:
            raise SystemExit(f"{len(missing):,} queries are missing from the cache; rerun "
                             f"build_embedding_cache.py --parts queries --datasets {args.dataset}, "
                             f"or pass --online-query-encoder")

    torch.manual_seed(args.seed)
    model = ForesightTripleScorer(ScorerConfig(
        emb_dim=store.dim, num_heads=args.num_heads, num_self_blocks=args.num_self_blocks,
        num_cross_blocks=args.num_cross_blocks, dropout=args.dropout))
    print(f"scorer: {model.num_parameters():,} trainable parameters, d = {model.d}")
    train_cfg = TrainConfig(epochs=args.epochs, lr=args.lr, weight_decay=args.weight_decay,
                            batch_questions=args.batch_questions,
                            max_triples_per_forward=args.max_triples_per_forward,
                            amp=not args.no_amp, seed=args.seed)

    def on_epoch_end(epoch, metrics, optimizer=None):
        model.save(out_dir / f"epoch{epoch}.pt", extra={"dataset": args.dataset, "epoch": epoch,
                                                                 "training_mode": "initial_zero_state",
                                                                 "validation_source": eval_source})
        with open(out_dir / "metrics.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(metrics) + "\n")

    train(model, train_ex, store, torch.device(args.device), train_cfg, eval_ex, on_epoch_end)
    print(f"done in {time.time() - t0:.0f}s -> {out_dir}")


if __name__ == "__main__":
    main()
