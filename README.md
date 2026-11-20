# FoG 论文复现项目

本项目旨在复现论文 **Foresight-over-Graph: Reasoning Beyond Local Horizons for Knowledge Base Question Answering**（NeurIPS 2026）。


这是一个小组协作项目，主要实现以下模块：

- 知识图谱（Knowledge Graph）的数据准备与索引构建
- 冻结文本编码器生成 Embedding
- 问题引导的三元组评分器（Triple Scorer）
- 前瞻子图扩展（Foresight Subgraph Expansion，FSE）
- 前瞻反馈传播（Foresight Feedback Propagation，FFP）
- 实验评估与性能分析

各模块目前正在逐步集成。需要注意，本文档中提供的 Initial Scorer 实验结果并不代表完整 FoG 系统的端到端性能。

## 1. 项目目录结构

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

以下目录属于本地生成的数据或训练产物，不应作为源代码提交到 GitHub：

- `cache/`：Embedding 缓存
- `checkpoints/`：训练得到的模型权重
- 训练日志
- 大型 KG 数据集及索引文件

## 2. 环境配置与数据准备

首先，在项目根目录安装 Python 依赖：

```bash
pip install -r requirements.txt
```

### 2.1 知识图谱数据准备

成员 A 负责知识图谱的准备和索引构建。

请按照成员 A 提供的脚本及说明，下载、处理并构建 KG 和 QA 数据。

成员 B 的脚本默认使用以下目录：

```text
data_knowledge_graph/data/processed/
├── kg/index_all/              # KG 索引，包含实体和关系 ID
├── webqsp/qa_train.jsonl.gz   # WebQSP 训练数据
└── cwq/qa_train.jsonl.gz      # CWQ 训练数据
```

项目中还必须包含 `data_knowledge_graph/fog` Python 包。

相关默认路径定义在 `src/model/kg_bridge.py` 中。如果实际数据位置不同，可以通过以下参数指定：

- `--index-dir`：KG 索引目录
- `--processed-root`：处理后的数据根目录

**重要：Embedding Cache 必须与训练或推理时使用的 KG Index 保持一致。**

`EmbeddingStore.validate_index()` 可以用于检查缓存与索引是否兼容。

完整的 Embedding 生成和 Scorer 训练应在具有 GPU 的计算节点上运行，例如通过 NTU TC2 的 SLURM 作业提交，而不是直接在集群登录节点上执行。

## 3. 成员 B：Embedding 与 Triple Scorer

成员 B 负责冻结的 Qwen3 文本编码器、Embedding Cache、问题引导的三元组评分器，以及对应的训练和测试。

### 3.1 构建 Embedding Cache

我们使用冻结的 `Qwen/Qwen3-Embedding-4B` 模型，对以下内容进行编码：

- 有名称的实体（Named Entities）
- 关系（Relations）
- 问题（Questions）

模型原始 Embedding 维度为 **2560**。

对于 Freebase 中没有可用名称的 `m.` / `g.` 标识符，我们在实体缓存中保留零向量，并由 Scorer 使用一个共享的可训练向量表示这些节点。

构建完整 Embedding Cache：

```bash
python scripts/build_embedding_cache.py --out cache/embeddings_v2
```

如果已有兼容的缓存，可以只补充某个数据集的问题 Embedding：

```bash
python scripts/build_embedding_cache.py \
  --out cache/embeddings_v2 \
  --parts queries --datasets webqsp
```

缓存目录主要包含以下文件：

```text
cache/embeddings_v2/
├── entity_emb.npy
├── entity_is_mid.npy
├── relation_emb.npy
├── queries.npy
├── queries.json
├── meta.json
├── relation_meta.json
├── query_meta.json
└── entity_progress.json
```

实体 Embedding 的生成过程支持中断后继续运行。

目前 `build_embedding_cache.py` 和 `train_scorer.py` 使用的默认缓存路径均为：

```text
cache/embeddings_v2
```

### 3.2 训练 Initial Scorer

Scorer 的主要结构包括：

1. **Role Bias**：为三元组中不同角色添加角色偏置。
2. **Self-Attention**：建模三元组内部的特征交互。
3. **Cross-Attention**：建立问题与三元组之间的关联。
4. **Sub-question Attention Pooling**：聚合与当前三元组相关的子问题信息。
5. **MLP Scorer**：输出三元组的最终评分。

当前训练阶段使用：

- 原始问题作为问题输入
- 初始为零的实体状态
- 基于 Gold Path 的监督信号
- 局部负样本采样

**当前训练的是 Initial Scorer，并未运行完整的 FSE/FFP 迭代流程。**

#### WebQSP 训练

```bash
python scripts/train_scorer.py \
  --dataset webqsp \
  --cache cache/embeddings_v2 \
  --out-dir checkpoints/scorer_webqsp_v2
```

#### CWQ 训练

```bash
python scripts/train_scorer.py \
  --dataset cwq \
  --cache cache/embeddings_v2 \
  --out-dir checkpoints/scorer_cwq_microbatch \
  --max-triples-per-forward 64
```

CWQ 使用 Triple Micro-batching，将三元组分批送入模型，以降低 GPU 显存需求。

主要训练配置：

| 参数 | 设置 |
| --- | --- |
| Epochs | 5 |
| Optimizer | AdamW |
| Learning Rate | `1e-5` |
| Weight Decay | `1e-3` |
| Negative Sampling Ratio（κ） | 10 |
| CWQ Micro-batch Size | 64 triples |

如果数据集中不存在独立的 `dev` 或 `val` 划分，训练脚本会从训练集中按 Question ID 确定性地划分 10% 作为验证集。

训练集和验证集的问题 ID 不重叠。

**当前实验未使用官方测试集进行评估。**

每次训练会在 `--out-dir` 中保存：

```text
epoch<k>.pt
metrics.jsonl
split.json
sampler_stats.json
```

其中，Checkpoint 包含 Scorer 配置和模型权重。

### 3.3 分析验证集评分分布

我们提供了 `analyze_score_distribution.py`，用于进一步分析 Scorer 输出的概率分布和阈值表现。

例如：

```bash
python scripts/analyze_score_distribution.py \
  --dataset cwq \
  --checkpoint checkpoints/scorer_cwq_microbatch/epoch4.pt \
  --cache cache/embeddings_v2 \
  --out-dir analysis/cwq_epoch4_mid \
  --group-by-mid
```

脚本可以：

- 统计正负三元组的预测概率分布
- 分析不同概率阈值下的 Recall
- 与训练日志中的验证结果进行一致性比较
- 区分包含无名称节点的三元组与仅包含有名称节点的三元组

生成的主要分析文件：

```text
summary.json
histogram.csv
```

注意：该分析脚本需要原始训练目录中的 Checkpoint、`metrics.jsonl`、`split.json` 和 `sampler_stats.json`。

同时，还需要使用与原始训练一致的 KG、验证集划分及采样设置。

仅下载单个 `.pt` 文件不足以完整复现该分析。

## 4. 成员 B 的预训练模型与缓存

训练完成的 Scorer 权重和 Embedding Cache 不存放在 GitHub 仓库中，而是上传至 Hugging Face 私有模型仓库：

**[czzhuhu/FoG-B-Models](https://huggingface.co/czzhuhu/FoG-B-Models)**

目前提供以下文件：

| 内容 | Hugging Face 路径 | 实验结果 |
| --- | --- | --- |
| WebQSP Initial Scorer | `webqsp/epoch5.pt` | Validation AUC = 0.9335 |
| CWQ Initial Scorer | `cwq/epoch4.pt` | Validation AUC = 0.8986 |
| Embedding Cache | `cache/embeddings_v2/` | 实体、关系、问题 Embedding 及元数据 |

以上 AUC 均来自 Initial Scorer 在训练数据划分出的验证集上的评估。

**这些数值不代表完整 FoG 系统的端到端 KBQA F1、Hit@1，也不代表经过 FFP 后的最终阈值表现。**

### 4.1 下载模型与缓存

获得私有仓库访问权限的组员，可以先登录 Hugging Face：

```bash
hf auth login
```

然后在项目根目录运行：

```bash
hf download czzhuhu/FoG-B-Models --local-dir .
```

下载后，文件将位于：

```text
cache/embeddings_v2/
webqsp/epoch5.pt
cwq/epoch4.pt
```

Hugging Face 仓库目前设置为 Private，因此其他成员必须先获得访问权限。

请勿共享个人 Hugging Face Access Token。

模型权重和 Embedding Cache 均不应提交到 GitHub。

## 5. 与 FSE / FFP 的集成接口

Scorer 提供了分阶段调用接口，使后续 FFP 可以复用三元组编码结果，并根据更新后的实体状态重新计算评分。

基本调用示例：

```python
from src.model.embedding import EmbeddingStore
from src.model.scorer import ForesightTripleScorer, TripleBatch

store = EmbeddingStore.load("cache/embeddings_v2")

# 首先加载成员 A 的 KG，并检查与缓存是否匹配：
# store.validate_index(kg)

scorer = ForesightTripleScorer.load("webqsp/epoch5.pt").eval()

# 使用 TripleBatch.from_ids(...) 构建三元组批次，
# 同时准备问题向量。
# batch: TripleBatch
# q_sub: [B, M, d]
# q_sub_mask: [B, M]

z = scorer.encode(batch)  # [N, d]

logits = scorer.score(
    z,
    h_s=None,
    h_o=None,
    q_sub=q_sub,
    q_sub_mask=q_sub_mask,
    q_index=batch.q_index
)

probs = logits.sigmoid()
```

其中：

- `scorer.encode(batch)`：生成三元组表示。
- `scorer.score(...)`：根据三元组表示、实体状态和问题信息计算分数。
- `h_s=None` 和 `h_o=None`：表示使用初始零实体状态。
- FFP 可以传入更新后的 `h_s` 和 `h_o`，对三元组重新评分。
- `scorer.prob(...)`：可以直接返回概率。

阈值设置：

```text
tau_e = 0.1
tau_p = 0.7
```

这些阈值作用于 **Sigmoid 后的概率**，而不是未经转换的 Logits。

关于具体接口，请参考：

```text
src/model/scorer.py
src/model/mock_scorer.py
```

其中：

- 成员 A 负责 KG Index 的生成和相关数据处理。
- 成员 B 负责 Embedding Cache 和 Scorer。
- 成员 C 负责 FSE/FFP 的集成及后续推理流程。

## 6. 测试

在项目根目录、安装好依赖并具备成员 A 的 KG 工具包后，可以运行：

```bash
python -m unittest discover -s tests -p 'test_b_*.py'
```
测试主要覆盖成员 B 的 Embedding、Scorer、采样和训练相关逻辑。


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

项目还包含 `experiments/` 目录，用于阈值策略实验、过程记录及评估指标统计。

其他工作模块中的占位代码目前仍然保留，以便后续小组集成；这些占位模块不应被视为已经完成的完整 FoG 功能。
