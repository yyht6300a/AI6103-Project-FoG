# FoG 复现 —— 数据与知识图谱

> 论文：*Foresight-over-Graph: Reasoning Beyond Local Horizons for Knowledge Base Question Answering*
> （NeurIPS 2026，天津大学 / 北京交通大学）
> 本目录负责《FoG复现计划》分工里的**成员 A**：数据加载、Freebase 子图抽取、
> mid→名字映射、黄金推理路径反推，以及给 B/C/D/E 的数据契约。

---

## 1. 这个包做什么

```
官方标注 (WebQSP / CWQ)                 Freebase 子图 (RoG 预处理子图并集)
        │                                        │
        ├─ 源实体抽取 (决策点 1)                  ├─ 建 CSR 索引 (8.26M 三元组)
        ├─ mid→名字映射 (决策点 2)                │
        └─ 黄金路径反推 (决策点 3) ───────────────┘
                        │
                        ▼
        qa_<split>.jsonl.gz  +  gold_<split>.jsonl.gz  +  子图统计
                        │
        ┌───────────────┼────────────────┬──────────────────┐
        ▼               ▼                ▼                  ▼
   B 嵌入+评分器    C FSE/FFP        D MSM+LLM           E 训练/评测
```
---

## 2. 30 秒跑通

```bash
# 0) 依赖：Python ≥ 3.9 + numpy
pip install numpy

# 全流程（下载 → 建索引 → 生成 → 自检 → 汇总）
python scripts/run_all.py

# 或者分步：
python scripts/download_data.py                    # 1) 原始数据，约 800 MB
python scripts/build_kg_index.py                   # 2) KG 索引，约 1 分钟 / 256 MB
python scripts/prepare_dataset.py --dataset all    # 3) 数据产物
python scripts/validate_outputs.py                 # 4) 产物自检
python -m unittest discover -s tests -t .          # 5) 单元 + 集成测试
python scripts/summarize_stats.py --out reports/A_统计汇总.md   # 6) 统计汇总
```

只想快速看一眼效果：

```bash
python scripts/demo_one_question.py                       # 单题全链路演示
python scripts/prepare_dataset.py --dataset webqsp --limit 200   # 小样本
```

---

## 3. 交付物

| 计划里的交付物 | 本仓库对应 | 说明 |
|---|---|---|
| 数据集加载脚本 | `fog/datasets/loader.py`、`scripts/download_data.py` | WebQSP/CWQ 官方 split，字段差异已抹平 |
| Freebase 子图抽取脚本 | `fog/kg/tsv_index.py`、`fog/subgraph.py`、`scripts/build_kg_index.py` | 3 跳分层 BFS + 朝内定向 + 关系/枢纽过滤 |
| gold 路径三元组文件 | `data/processed/<ds>/gold_<split>.jsonl.gz` | 名字空间、可直接当训练正样本 |
| （计划外的必要件）mid→名字映射 | `data/interim/mid2name.json`（40,109 条） | 决策点 2 的落地 |
| （计划外的必要件）数据契约 | `docs/DATA_CONTRACT.md` | B/C/D/E 的对接文档 |

---

## 4. 目录结构

```
fog/
├── schema.py            数据契约的 dataclass（JSONL 字段名的唯一来源）
├── utils.py             JSON/JSONL 读写、进度、日志
├── naming.py            mid↔名字映射（决策点 2）、实体链接（决策点 1）
├── subgraph.py          3 跳分层 BFS + 朝内定向（决策点 7/8 的落地）
├── pipeline.py          一条问题的完整处理链 + 统计
├── kg/
│   ├── tsv_index.py     名字空间三元组索引（CSR + (实体,关系) 定向查边）
│   └── virtuoso.py      全量 Freebase 的 SPARQL 物化接口（论文级复现用）
├── datasets/loader.py   WebQSP / CWQ 加载器
└── gold/
    ├── sparql.py        SPARQL 图模式 → 关系链（决策点 3 主路径）
    ├── sexpr.py         CWQ S-expression → 关系链（备选路径）
    └── matcher.py       关系链 → KG 名字空间黄金三元组
scripts/                  download / build_kg_index / prepare_dataset / analyze_* / validate
tests/                    40 个单元测试（合成小图 + 真实案例回归）
docs/                     DATA_CONTRACT.md（给下游）、DECISIONS.md（决策点落地）
reports/                  A_数据核查.md、A_数据与知识图谱报告.md、stats/*.json
```

---

## 5. 给下游的最小用法

```python
# B：拿源实体与黄金三元组做训练/嵌入
import gzip, json
rec = json.loads(gzip.open("data/processed/webqsp/gold_test.jsonl.gz", "rt").readline())

# C：建子图（带 dist / src→dst 朝内定向 / gold 标记）
from fog.kg.tsv_index import TripleIndex
from fog.subgraph import SubgraphConfig, build_subgraph
kg = TripleIndex.load("data/processed/kg/index_all")
sub = build_subgraph(kg, "qid", ["Justin Bieber"], gold_triples, ["Jaxon Bieber"],
                     SubgraphConfig(hops=3))
```

完整字段说明见 [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md)。

---

## 6. 关键数字（ `reports/` ）

| 指标 | WebQSP test | CWQ test(dev) |
|---|---|---|
| 官方 split | train 3,098 / test 1,639 | train 27,639 / dev 3,519 / test 3,531（无答案） |
| 源实体落在 KG 中 | 99.9% | 100% |
| 黄金路径对齐成功 | 92.9% | 83.3% |
| 答案实体落在 KG 中 | 95.7% | 99.9% |
| 黄金边在 3 跳候选池中的召回 | 94.1% | 89.3% |
| 子图规模（均值，节点/边） | 19,696 / 35,868 | 18,309 / 37,547 |

KG：8,258,891 条三元组 / 2,574,875 实体 / 7,042 关系，索引 256 MiB

---

## 7. 已知限制

1. 子图是 **RoG 预处理检索子图的并集**，不是全量 Freebase：黄金边召回率 94.1%（WebQSP test）/ 89.3%（CWQ dev），这是下游 Hit 的硬上限
2. **名字空间会合并同名实体**（与 ToG/RoG 全系一致）
3. **CWQ 无官方源实体标注**，靠链接 + 黄金链校验得到
4. 训练集的 `subgraph_stats` 按 `configs/*.json` 里的 `subgraph_sample` 采样

细节与失败归因见 [`reports/A_数据与知识图谱报告.md`](reports/A_数据与知识图谱报告.md)。
