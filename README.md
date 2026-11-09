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

### LLM configuration

Copy [`env_example`](env_example) to `.env` and fill in `LLM_API_KEY` locally. The local file contains only `LLM_MODEL_NAME`, `LLM_API_KEY` and `LLM_BASE_URL`. Nonempty model and URL values override defaults in [`config/config-dev.yml`](config/config-dev.yml); blank values retain those defaults. The key is read only from `.env`, which Git ignores. Shared YAML defaults remain `gpt-4o-mini` at `https://api.openai.com/v1`. For local development, `.env` may select `deepseek-flash` at `https://api.deepseek.com` with a DeepSeek key through the [official OpenAI-compatible API](https://api-docs.deepseek.com/). Member D must switch back to `gpt-4o-mini` with the OpenAI endpoint and key and validate the module before delivery. Set the model and URL together, or leave both blank for YAML defaults; failures do not trigger a provider fallback. The allowed models are `deepseek-flash`, `gpt-4o-mini`, `gpt-4`, and `gpt-4o`. The earlier OpenAI live check passed on 2026/11/08 with `gpt-4o-mini-2024-07-18`.

Set `llm.timeout` and `llm.max_retries` only in YAML. Timeout is a positive finite number of seconds per HTTP connect/read operation; max_retries is the non-negative number of additional attempts after the first request. Legacy `LLM_TIMEOUT` and `LLM_MAX_RETRIES` entries in `.env` are ignored. Dotenv files support single-line assignments, quotes, optional `export` prefixes and comments; shell commands, multiline values and variable interpolation are unsupported. Configuration loading neither reads nor modifies process environment variables. The HTTP client disables environment-derived proxies, CA bundles and credentials and passes the model, key, endpoint and timeout explicitly. It uses the OpenAI-compatible `/chat/completions` protocol; other protocols need a separate adapter.

```python
from src.agent.llm_config import load_llm_config
from src.agent.llm_client import LLMClient

client = LLMClient(load_llm_config())  # Validates before any request.
try:
    response = client.complete([{"role": "user", "content": "Reply with OK."}])
finally:
    client.close()
```

Run offline checks with `python -m unittest discover -s tests -v`. These use synthetic credentials, mocked transport and private temporary CSV files. Run `python -m src.agent.llm_smoke` for a paid live check using the effective configuration; it checks a short acknowledgement in memory and prints configuration and usage metadata without printing credentials or response content.

Every HTTP attempt, including failures and retries, appends a row to [`logs/llm_usage.csv`](logs/llm_usage.csv), using UTF-8 and the columns `timestamp, model, input_tokens, output_tokens, status, error_type, request_id`. The timestamp is the request start time in UTC with an explicit offset. Model uses the returned model when available, otherwise the requested model; token counts come from `usage.prompt_tokens` and `usage.completion_tokens`, and request ID comes from the `x-request-id` header, following the [OpenAI Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) and [request debugging reference](https://developers.openai.com/api/reference/overview#debugging-requests). Unknown counts and IDs stay blank; reported zero counts remain zero. Status is `success` or `error`; error type contains only the exception class. HTTP success with invalid JSON is an error; application-level validation after the client returns does not change the HTTP accounting row. Keys, prompts, triples, model replies and error messages are excluded. Keep real usage rows in Git for later bill reconciliation; usage alone does not establish billed cost. Clients check the CSV header and writability before sending; append failures propagate without retrying. Concurrent processes must serialize CSV writes externally. Memory management remains a subsequent reproduction task.

### Module reference

See [Member D modules](docs/part_d.md) for interfaces and usage examples.

## Threshold optimization experiments

The `experiments/` package implements the fixed and adaptive threshold policies and the retrieval metrics described in the plan. See `experiments/sweep.py` for the validation sweep.

