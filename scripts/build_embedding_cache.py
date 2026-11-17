"""Build member B's embedding cache with the frozen Qwen3-Embedding-4B (needs a GPU in practice).

Encodes, aligned with member A's KG index ids:
  * every named entity (unnamed m./g. nodes are skipped and stay zero),
  * every relation,
  * the original questions (no gold-derived sub-questions) of the chosen datasets / splits.

Example (run from the repository root):
  python scripts/build_embedding_cache.py --out cache/embeddings_v2
  python scripts/build_embedding_cache.py --out cache/embeddings_v2 --parts queries --datasets webqsp

The entity pass saves progress after every chunk; rerunning continues where it stopped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.model.embedding import (DEFAULT_MODEL_NAME, QwenTextEncoder, build_entity_cache,  # noqa: E402
                                 build_query_cache, build_relation_cache, write_cache_meta,
                                 encoder_config, index_fingerprint, _require_match)
from src.model.kg_bridge import DEFAULT_INDEX_DIR, DEFAULT_PROCESSED_ROOT, load_kg  # noqa: E402
from src.model.sampler import iter_qa_records  # noqa: E402

SPLITS = {"webqsp": ["train", "test"], "cwq": ["train", "test"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="cache/embeddings_v2")
    ap.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    ap.add_argument("--processed-root", default=str(DEFAULT_PROCESSED_ROOT))
    ap.add_argument("--parts", nargs="+", default=["entities", "relations", "queries"],
                    choices=["entities", "relations", "queries"])
    ap.add_argument("--datasets", nargs="+", default=["webqsp", "cwq"], choices=sorted(SPLITS))
    ap.add_argument("--model", default=DEFAULT_MODEL_NAME)
    ap.add_argument("--device", default=None, help="cuda / mps / cpu (default: auto)")
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--max-length", type=int, default=512)
    ap.add_argument("--truncate-dim", type=int, default=None,
                    help="optional MRL truncation; default keeps the native 2560 dims")
    ap.add_argument("--chunk-size", type=int, default=50_000)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    kg = load_kg(args.index_dir)
    print(f"KG: {kg.n_entities:,} entities, {kg.n_relations:,} relations ({time.time() - t0:.0f}s)")
    encoder = QwenTextEncoder(args.model, device=args.device, max_length=args.max_length,
                              batch_size=args.batch_size, truncate_dim=args.truncate_dim)
    print(f"encoder: {args.model} on {encoder.device}, dim {encoder.dim}")

    out = Path(args.out)
    if args.no_resume and set(args.parts) != {"entities", "relations", "queries"}:
        raise ValueError("--no-resume requires all parts; alternatively use a new cache directory")
    if not args.no_resume and (out / "meta.json").exists():
        _require_match(json.loads((out / "meta.json").read_text()), {
            **encoder_config(encoder), "entity_fingerprint": index_fingerprint(kg.entities),
            "relation_fingerprint": index_fingerprint(kg.relations)})
    if "entities" in args.parts:
        build_entity_cache(kg.entities, encoder, out, chunk_size=args.chunk_size, resume=not args.no_resume)
    if "relations" in args.parts:
        build_relation_cache(kg.relations, encoder, out, resume=not args.no_resume)
        print(f"[relation] {kg.n_relations:,} relations")
    if "queries" in args.parts:
        queries = []
        for ds in args.datasets:
            splits = [s for s in ("train", "dev", "val", "test")
                      if (Path(args.processed_root) / ds / f"qa_{s}.jsonl.gz").exists()]
            if not splits:
                raise FileNotFoundError(f"no processed QA splits for {ds}")
            for split in splits:
                for rec in iter_qa_records(args.processed_root, ds, split):
                    queries.append(rec["question"])
        n_new = build_query_cache(queries, encoder, out, resume=not args.no_resume)
        print(f"[query] {n_new:,} new queries encoded")
    write_cache_meta(out, encoder, kg.n_entities, kg.n_relations,
                     extra={"kg_n_triples": int(kg.n_triples), "index_dir": Path(args.index_dir).name})
    print(f"done in {time.time() - t0:.0f}s -> {out}")


if __name__ == "__main__":
    main()
