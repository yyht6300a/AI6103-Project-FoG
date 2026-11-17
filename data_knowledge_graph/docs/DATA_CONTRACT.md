# 成员 A 数据契约（v0.1）

> 面向成员 B / C / D / E。**只依赖本文档列出的字段，不要依赖 `fog` 包内部实现。**
> 所有产物都在 `data/processed/` 下，均可由 `scripts/` 里的脚本从原始数据一键重建

---

## 1. 产物清单

| 文件 | 内容 | 谁用 | 大小（WebQSP / CWQ） |
|---|---|---|---|
| `data/processed/kg/index_all/` | Freebase 子图离线索引（CSR） | B/C/E | 256 MiB |
| `data/processed/<ds>/qa_<split>.jsonl.gz` | 问题 + 源实体 + 黄金三元组 + 子图统计 | 全体 | ~2 MB / ~9 MB |
| `data/processed/<ds>/gold_<split>.jsonl.gz` | 只要黄金三元组（训练正样本） | B/E | ~1 MB / ~3 MB |
| `data/processed/<ds>/subgraphs/<split>/<qid>.json.gz` | 完整展开子图（默认只物化前 300 条） | C/D | 每条 20–500 KB |
| `data/processed/<ds>/stats_<split>.json` | 统计（覆盖率、平均规模、分布） | E | 小 |
| `data/interim/mid2name.json` | mid → 英文名（40,109 条） | B | 1.7 MiB |
| `data/raw/qa/*.json` | 官方标注原文 | — | 61 MB |
| `data/raw/kg/*.tsv` | Freebase 子图三元组（head \t relation \t tail） | — | 731 MB |

`<ds>` ∈ `webqsp` / `cwq`；`<split>` ∈ `webqsp:{train,test}`、`cwq:{train,test,official_test_no_answer}`

> **CWQ 的 test 用的是官方 dev（3,519 题）**：官方 test（3,531 题）不公开答案，
> 文献（RoG / ToG 等）统一以 dev 作为 test 报告，本复现沿用同一约定
> 官方 test 另存为 `cwq/qa_official_test_no_answer.jsonl.gz`，只用于最终提交

---

## 2. `qa_<split>.jsonl.gz` 字段

一行一条问题。示例（WebQSP test 第 0 题，节选）：

```json
{
  "qid": "WebQTest-0",
  "dataset": "webqsp",
  "split": "test",
  "question": "what is the name of justin bieber brother?",
  "answers": ["Jaxon Bieber"],
  "answer_mids": ["m.0gxnnwq"],
  "source_entities": [{
     "name": "Justin Bieber",     // 送进嵌入模型的字符串（名字空间）
     "mid": "m.06w2sn5",          // Freebase mid（可能为 null）
     "kg_id": 248,                // KG 索引里的实体 id（null 表示不在 KG）
     "method": "annotation",      // annotation | linking | sparql_constant | fallback
     "mention": null,             // 问题中的字面提及（linking 时非空）
     "in_kg": true
  }],
  "gold": {
    "steps": [                     // 标注里的关系链（mid 空间，原样保留）
      {"relation": "people.person.sibling_s", "head_mid": null, "tail_mid": null, "direction": "fwd"},
      {"relation": "people.sibling_relationship.sibling", "head_mid": null, "tail_mid": null, "direction": "fwd"}
    ],
    "triples": [                    // ★ 名字空间黄金三元组 = 训练正样本
      ["Justin Bieber", "people.person.sibling_s", "m.0gxnnwp"],
      ["m.0gxnnwp", "people.sibling_relationship.sibling", "Jaxon Bieber"]
    ],
    "constraints": [],              // 约束三元组（角色 = constraint），可选用
    "matched": true,                // 是否成功对齐到答案
    "match_method": "exact",        // exact | relaxed | relaxed_order | shortest_any | none
    "n_path_hops": 2,
    "errors": []
  },
  "subgraph_stats": {               // 子图规模统计；null 表示该题没算子图
    "n_nodes": 4312, "n_edges": 9107, "truncated": false,
    "layer_node_counts": {"0": 1, "1": 34, "2": 87, "3": 1067},
    "n_gold_edges": 2, "gold_recall_in_subgraph": 1.0,
    "n_lateral_edges": 195, "hub_skipped": 7,
    "rel_filtered_edges_bfs": 11208, "rel_filtered_edges_induced": 152590,
    "config": {"hops": 3, "node_degree_max": 5000, "rel_degree_max": 200000, "...": "..."}
  },
  "extra": {"parse_id": "WebQTest-0.P0", "parse_quality": "Complete", "topic_mid": "m.06w2sn5",
            "inferential_chain": ["people.person.sibling_s", "people.sibling_relationship.sibling"]}
}
```

### 关键语义

* **一切实体字符串都在"名字空间"**：有英文名用英文名，没有就用 mid 兜底
  （RoG 式子图的既有约定）。B 直接用 `name` 编码，C/D 直接用它当节点键
* `gold.triples` 是**已对齐到 KG** 的黄金三元组，方向与 KG 存储方向一致，
  所以 `gold.triples ⊆ 子图边` 应当成立（`gold_recall_in_subgraph == 1.0` 时成立）
* `gold.steps` 只在 WebQSP 上有值（官方 `InferentialChain`，mid 空间）
  **CWQ 的 `steps` 是空列表**——它的关系链是从 SPARQL 图模式派生的，已经体现在
  `gold.triples` 里（名字空间、顺序即路径顺序）。下游请只依赖 `gold.triples`
* `gold.matched == false` 的问题**没有正样本**，训练时必须跳过（E 要用它算覆盖率）
* `source_entities` 目前只有 1 个元素；接口保留为列表是为了以后多实体问题

---

## 3. `gold_<split>.jsonl.gz` 字段

训练只需要正样本时用这个文件，体积最小：

```json
{"qid": "WebQTest-0", "dataset": "webqsp", "split": "test",
 "source": ["Justin Bieber"], "answers": ["Jaxon Bieber"], "answer_mids": ["m.0gxnnwq"],
 "matched": true, "match_method": "exact",
 "triples": [["Justin Bieber","people.person.sibling_s","m.0gxnnwp"],
             ["m.0gxnnwp","people.sibling_relationship.sibling","Jaxon Bieber"]],
 "n_path_triples": 2}
```

> 负样本采样（κ=10 的一跳局部噪声）**不在本文件里**，由 B/E 在训练时按
> §7 的接口实时采（因为负样本要跟"当前 frontier"绑定，属于训练循环的一部分）
> A 已经把采样所需的两个结构（`gold` 与 `kg` 索引）都准备好了

---

## 4. 子图文件 `subgraphs/<split>/<qid>.json.gz`

```json
{"qid": "WebQTest-0",
 "nodes": [{"gid": 248, "name": "Justin Bieber", "dist": 0, "is_source": 1, "is_answer": 0}],
 "edges": [{"s": 0, "r": "people.person.sibling_s", "t": 12, "ds": 0, "dt": 1, "src": 12, "dst": 0, "g": 1}],
 "stats": {...}}
```

| 字段 | 含义 |
|---|---|
| `nodes[].gid` | KG 全局实体 id（可跨问题对齐向量缓存） |
| `nodes[].name` | 名字空间字符串（B 编码用） |
| `nodes[].dist` | 到最近源实体的跳数（0 = 源实体本身）；C 的深度调度 τ_e(l) 用它 |
| `nodes[].is_source` / `is_answer` | 1/0 标记 |
| `edges[].s / .r / .t` | 局部节点下标 + 关系名，即三元组 `(nodes[s], r, nodes[t])`，方向与 KG 一致 |
| `edges[].ds / .dt` | 两端到源实体的跳数 |
| `edges[].src / .dst` | **朝内定向**后的方向（远 → 近），FFP 直接按 `src→dst` 传消息 |
| `edges[].g` | 1 = 该边是黄金边（训练正样本） |

⚠️ 子图是**未按分数剪枝的候选池**（FSE 的输入）。C 要按 τ_e 打分剪枝后才得到论文里的
展开子图 `G_xpd`；`stats.gold_recall_in_subgraph` 就是这个候选池对黄金边的召回上限

---

## 5. KG 索引接口（B/C/E 直接用）

```python
from fog.kg.tsv_index import TripleIndex

kg = TripleIndex.load("data/processed/kg/index_all")   # 8,258,891 三元组，~2 s
kg.n_triples, kg.n_entities, kg.n_relations
kg.entity_id("Justin Bieber")        # -> 248（精确）
kg.resolve_entity("justin bieber")   # -> 248（大小写不敏感回退）
kg.edge_triple(eid)                  # -> Triple(h, r, t)
kg.out_edges(eid), kg.in_edges(eid)  # -> numpy int32 边 id 数组
kg.entity_degree, kg.rel_degree      # -> numpy 数组（缓存）
```

---

## 6. 子图构建接口（C/D 在自己循环里现场建）

```python
from fog.subgraph import SubgraphConfig, build_subgraph

cfg = SubgraphConfig(hops=3, node_degree_max=5000, rel_degree_max=200_000, max_edges=20_000)
sub = build_subgraph(kg, qid, ["Justin Bieber"], gold_triples, answers, cfg)
sub.nodes[0].name, sub.edges[0].src, sub.edges[0].r, sub.stats["gold_recall_in_subgraph"]
```

平均 0.25 s/题（WebQSP，20k 边上限），完全确定性（同输入必得同输出）
大批量训练时建议按 split 预先把 `(qid → 子图)` 物化到内存或磁盘

---

## 7. 负采样所需接口（B/E）

论文 §E 的负样本定义：以黄金路径上的实体为种子，取 **`κ·deg_pos(v)`** 条一跳邻边
（`N(v) \ T_pos`），`κ=10`，并过滤极高度数通用关系

A 提供两块材料，采样逻辑留给训练脚本（因为它和 batch 绑定）：

```python
from fog.naming import MidNameResolver               # 决策点 2 的映射
gold = [tuple(t) for t in record["gold"]["triples"]]
seeds = {x for t in gold for x in (t[0], t[2])}
# 每个 seed 的一跳邻域（出边 + 入边）→ 减去 T_pos → 按 κ 采样
for name in seeds:
    eid = kg.resolve_entity(name)
    cand = [kg.edge_triple(e) for e in list(kg.out_edges(eid)) + list(kg.in_edges(eid))]
```

关系度数过滤用 `kg.rel_degree[kg.relation_id(r)] <= SubgraphConfig().rel_degree_max`
（与展开阶段保持同一口径，避免"训练见过、推理见不到"的分布差）

---

## 8. 已知限制（务必知悉）

1. **名字空间会合并同名实体**：RoG 式预处理用 `type.object.name` 做 mid→名字，
   不同 mid 同名的实体会被合并（与 ToG/RoG 全系一致）
2. **子图是 RoG 检索子图的并集**，不是全量 Freebase：某条黄金边可能根本不在池子里
   全量 WebQSP test 上黄金边召回 **94.1%**、CWQ dev **89.3%**（`node_degree_max=20000, max_edges=40000`）
3. **黄金路径对齐率不是 100%**：WebQSP test 92.9%、CWQ dev 83.3%（失败原因见
   `reports/A_数据与知识图谱报告.md` 第 4 节）。未对齐的问题没有正样本
4. **CWQ 源实体没有官方标注**，由"问题提及 × KG 词表"的最长匹配 + 黄金链校验得到，
   链接错误的概率虽低但非零（B 若要复算嵌入，请直接用 `source_entities[].name`）
5. `subgraph_stats` 在训练集上按 `configs/*.json` 的 `subgraph_sample` **采样**，
   其余为 `null`；test/dev 是全量

6. ⚠️ **候选池可能被枢纽节点的邻居"淹没"**：当源实体的 1 跳邻居里出现高度数节点时，
   2 跳候选会瞬间超过 `max_nodes`，截断按边 id 顺序进行，**黄金路径上的节点可能挤不进来**
   `subgraph_stats.node_cap_hit > 0` 就说明发生了这种情况，`gold_recall_in_subgraph` 会如实反映损失（例：WebQTrn-0 的 `node_cap_hit=469171`，黄金边只剩 1/3）
   *对 C 的建议*：训练时按 `gold_recall_in_subgraph` 分层抽样，或对 `node_cap_hit > 0` 的题单独加大 `max_nodes` 重建；不要假设候选池一定包含黄金边
