# FoG Reproduction

[English](README.md) | [简体中文](README.zn-CN.md)

Reproduction project for **Foresight-over-Graph: Reasoning Beyond Local Horizons for Knowledge Base Question Answering** (NeurIPS 2026).

The upstream repository (https://github.com/yhong7/FoG) does not publish its source code yet; it only contains a README and an architecture figure. This repository rebuilds the pipeline from the paper and prepares the data, model, retrieval, and experiment scaffolding needed to reproduce FoG and to run the threshold-optimization experiments discussed during planning.

## Directory layout

```text
.
├── paper/                      # the paper and its architecture figure
├── docs/                       # analysis document and extracted paper text
├── config/                     # runtime configuration (LLM, embedding, thresholds)
├── data/
│   ├── raw/                    # WebQSP and CWQ datasets (samples committed, full data ignored)
│   ├── processed/              # extracted triples.tsv and entity2name.tsv
│   └── freebase/               # Freebase dumps or Virtuoso database (not committed)
├── src/
│   ├── data/                   # dataset loading and preprocessing
│   ├── model/                  # question-conditioned triple encoder, scorer, FSE, FFP
│   └── agent/                  # memory subgraph manager and LLM interaction
├── experiments/                # threshold policy and retrieval metrics
├── scripts/                    # dataset download and helper scripts
└── requirements.txt
```

## Data

WebQSP and CWQ are fetched from the HuggingFace datasets `rmanluo/RoG-webqsp` and `rmanluo/RoG-cwq`. Each example contains the fields FoG needs: `question`, `answer`, `q_entity` (source entities with names), `a_entity` (answer entities), and `graph` (the Freebase reasoning subgraph as a list of `[head, relation, tail]` triples with readable names).

```bash
python scripts/download_data.py                 # download a small committed sample
python scripts/download_data.py --full          # download complete parquet files
```

The full Freebase knowledge graph is intentionally not committed. Set it up with Virtuoso following the upstream README, or use the `graph` field in the RoG datasets as the Freebase subgraph for the minimal reproduction.

## Reproduction phases

1. Data preparation: source entities, gold reasoning paths, and the 3-hop subgraph.
2. Embedding layer: frozen `Qwen3-Embedding-4B` for entities, relations, and questions.
3. Question-conditioned triple encoder and foresight scorer.
4. Foresight subgraph expansion (FSE) with the expansion threshold.
5. Foresight feedback propagation (FFP) with far-to-near message passing.
6. Scorer training with gold-path supervision and local negative sampling.
7. Memory subgraph management with the LLM.
8. Inference loop and evaluation (Hit, Hit@1, F1).

## Decision points

All choices that must be made during reproduction (source-entity extraction, mid-to-text mapping, gold-path conversion, threshold scale, FFP depth, sub-question LLM, negative-sampling filter, full-Freebase vs subset, adaptive-threshold initial values, oracle definition) are listed in the analysis document under `docs/`.

## Threshold optimization experiments

The `experiments/` package implements the fixed and adaptive threshold policies and the retrieval metrics described in the plan. See `experiments/sweep.py` for the validation sweep.

