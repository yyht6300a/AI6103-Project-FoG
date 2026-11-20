# 成员 A 交付报告：数据与知识图谱

> 对应》4.1「数据与知识图谱准备」与八人分工表中成员 A 的四项关键任务：
> **加载 WebQSP/CWQ、抽取 Freebase 3 跳子图、mid→名字映射、反推黄金路径**。
>
> 全部数字可用 `python scripts/*.py` 在本机复跑；统计明细在 `reports/stats/`。

---

## 0. 一页纸结论

| 问题 | 结论 |
|---|---|
| 数据够不够跑通复现？ | 够。WebQSP 4,737 题、CWQ 31,158 题（含官方无答案 test），Freebase 子图 826 万条三元组 |
| 源实体能不能定位？ | WebQSP 99.9% 落在 KG；CWQ 100%（官方无标注，靠链接 + 黄金链校验） |
| 黄金路径能不能反推？ | WebQSP test 92.9%、CWQ dev 83.3% 对齐成功；失败主要由"子图本身不含该路径"导致 |
| 黄金边会不会被 3 跳展开漏掉？ | 全量 WebQSP test 上黄金边召回 94.1%、CWQ dev 89.3%（`node_degree_max=20000, max_edges=40000`），这就是下游 Hit 的硬上限 |
| 交付了给别人用的东西吗？ | 有。`docs/DATA_CONTRACT.md`（字段级契约）+ 4 个可直接用的接口 |
| 最大的坑是什么？ | CWQ 官方 test **没有答案**，必须用 dev 当 test；`sexpr` 字段也不存在 |

---

## 1. 任务边界与交付物

《复现计划》给成员 A 的交付物是三项：**数据集加载脚本、Freebase 子图抽取脚本、gold 路径三元组文件**。
实际做下来还多出两项必需件，一并交付：

| # | 交付物 | 文件 | 状态 |
|---|---|---|---|
| 1 | 数据集加载脚本 | `fog/datasets/loader.py`、`scripts/download_data.py` | ✅ 官方 split 全量 |
| 2 | Freebase 子图抽取脚本 | `fog/kg/tsv_index.py`、`fog/subgraph.py`、`scripts/build_kg_index.py` | ✅ 3 跳 BFS + 朝内定向 |
| 3 | gold 路径三元组文件 | `data/processed/<ds>/gold_<split>.jsonl.gz` | ✅ 名字空间、可直接当正样本 |
| 4 | mid→名字映射（计划外） | `data/interim/mid2name.json`（40,109 条） | ✅ 决策点 2 |
| 5 | 下游数据契约（计划外） | `docs/DATA_CONTRACT.md` | ✅ B/C/D/E 对接用 |
| 6 | 单元 + 集成测试 | `tests/`（45 个用例） | ✅ 含真实数据冒烟 |

---

## 2. 做法

### 2.1 数据来源的选择（决策点 8）

全量 Freebase 需要 Virtuoso + 100 GB 级内存，本环境不具备。按计划的"最小复现"口径，用
**RoG 预处理子图的并集**代替全量 Freebase：

| 项 | 数值 |
|---|---:|
| WebQSP 子图三元组 | 3,791,303 |
| CWQ 子图三元组 | 7,207,791 |
| 合并去重 | **8,258,891**（重复 2,740,203） |
| 实体 / 关系 | 2,574,875 / 7,042 |
| 索引体积 | 256 MiB |

这不是"缩小版玩具图"：8.26 M 条真实 Freebase 三元组已经足够支撑 3 跳展开，
且与 ToG/RoG 系列工作使用的是同一套名字空间，换全量 Freebase 时只需换数据源
（`fog/kg/virtuoso.py` 已给出 SPARQL 物化接口）

### 2.2 KG 索引（性能工程）

3 跳展开要求低延迟邻接查询。把 TSV 建成 **CSR 索引**（出边/入边各一套），
另外按 `(relation, head)` 排序建了**关系定向索引**，用两次 `searchsorted` 就能回答
"实体 v 沿关系 r 有哪些边"

### 2.3 源实体（决策点 1）

* **WebQSP**：官方给了 `TopicEntityName`，直接用；若该名字不在 KG 中，退化到链接。
* **CWQ**：官方 JSON 里**没有** `topicEntityMid`。做法是
  1. 问题文本对 257 万实体词表做**最长匹配**（含所有格 `'s` 剥离）；
  2. 用 `mid2name` 校验"SPARQL 常量名"与"提及"是否一致（例如常量 `m.060c4` 的名字是`President`，提及 `The President` 直接判不一致）；
  3. 最后用"这条黄金关系链能不能走到答案"做硬校验。

### 2.4 黄金路径（决策点 3）

两步：

1. **mid 空间求关系链**：解析 SPARQL 图模式（剔除 `FILTER(...)` / `EXISTS{...}`），求"源常量 → SELECT 变量"的最短路径，得到带方向的关系步序列。WebQSP 另有官方`InferentialChain`，优先采用
2. **名字空间对齐**：按关系链在 KG 上逐跳游走，**只接受终点命中答案**的路径。方向不一致时允许反向（记 `relaxed`）；仍失败则退化为"关系集受限的最短路"（`relaxed_order`）

```
示例（WebQSP test）
Q: what is the name of justin bieber brother?
标注链: people.person.sibling_s → people.sibling_relationship.sibling
对齐结果: [Justin Bieber, people.person.sibling_s, m.0gxnnwp]
          [m.0gxnnwp, people.sibling_relationship.sibling, Jaxon Bieber]      ✅
```

### 2.5 子图展开（决策点 7/8）

分层 BFS（L=3）→ 诱导边集 → 关系过滤 → 确定性截断 → **朝内定向**。

* `dist`：到源实体的最短跳数；C 的"深度调度 τ_e(l)"直接用；
* `src→dst`：把每条边重定向为"远 → 近"，**FFP 不用自己算方向**；
* `gold`：该边是否在黄金路径上（训练正样本标记）；
* 确定性：同一问题、同一配置，输出逐字节相同（截断按 `(距离, 关系频次)` 排序）

---

## 3. 校准实验（最具信息量部分）

### 3.1 决策点 7 的"度数 1000"到底指什么

计划建议"度数大于 1000 过滤"。实测后可以把这句话钉死：

* **按关系频次卡 1000** → 7042 个关系只剩 6345 个、三元组只剩 **6.70%**，黄金边召回 **5.8%**。等价于把图扔掉
* **按实体度数卡 1000** → 只影响 614 个枢纽实体，但会把"黄金路径穿过枢纽"的题一起剪掉

所以拆成三个旋钮：`node_degree_max`（实体侧）、`rel_degree_max`（关系频次侧）、
`rel_blocklist`（元数据关系黑名单）。

### 3.2 网格校准（WebQSP test 等距抽样 200 题，180 题可对齐）

| 旋钮 | 取值 | 黄金边召回 | 全召回(≥100%) | 平均边数 | 单题耗时 |
|---|---|---:|---:|---:|---:|
| `max_edges` | 10,000 | 85.5% | 77.8% | 9,227 | 0.23 s |
| | 20,000 | 88.4% | 83.9% | 18,403 | 0.24 s |
| | 40,000 | 90.1% | 87.2% | 36,523 | 0.28 s |
| | 80,000 | 90.9% | 88.9% | 72,039 | 0.36 s |
| `node_degree_max` | 2,000 | 80.1% | 76.7% | 16,057 | 0.19 s |
| | 5,000 | 88.4% | 83.9% | 18,403 | 0.24 s |
| | 10,000 | 91.8% | 86.7% | 19,180 | 0.26 s |
| 组合 | (10k, 20k) | 91.8% | 86.7% | 19,180 | 0.26 s |
| | (10k, 40k) | 93.4% | 90.0% | 38,079 | 0.30 s |
| | **(20k, 40k)** | **95.1%** | **91.7%** | **38,746** | **0.35 s** |
| | (10k, 60k) | 94.0% | 91.1% | 56,883 | 0.43 s |

**最终取值**：`node_degree_max=20000`、`max_edges=40000`、`rel_degree_max=200000`、
`max_nodes=20000`。相比计划建议的"度数 1000"（按实体侧解释时召回仅 ~70%），黄金边召回提到 ~95%，平均边数只有 3.9 万——**召回和体积不是同向增长的**，这正是要做网格的原因

> 有意思的反直觉现象：`node_degree_max` 从 5000 提到 20000 召回涨 6.7 个百分点，
> 再往上"完全不限制"反而下降——因为 20k 节点预算会被枢纽的一跳邻居吃光，
> 2–3 跳的有用节点挤不进来。

### 3.3 召回的天花板在哪

把 `max_edges` 提到 80k、`node_degree_max` 提到 20k 后，抽样召回在 95% 附近收敛；
**全量 test 上的实际值是 94.1%**（98% 的题会撞到 40k 边上限）。
剩下这 6% 主要不是参数问题，而是两类硬损失：
（a）子图本身不含这条边（RoG 检索子图的并集不是全量 Freebase）；
（b）源实体的 1 跳邻居里出现枢纽节点，2 跳候选瞬间超过 `max_nodes`，
截断按边 id 顺序进行，黄金路径节点被挤出（`subgraph_stats.node_cap_hit > 0` 即为此情形）。

> **一个顺带发现**："名字空间合并同名实体"会造出大量自环边
> （例如 `(James K. Polk, common.image.appears_in_topic_gallery, James K. Polk)`——
> 实体与它的图片画廊同名）。这些自环在 40k 边预算里占了可观份额，过滤掉之后
> 黄金边召回从 90.0% 回升到 **94.1%**。"清洗一条假边比调一个参数更值"的典型例子。

---

## 4. 结果：对齐率与失败归因

（全量数字，由 `python scripts/summarize_stats.py` 生成，明细见 `reports/A_统计汇总.md`）

| 数据集 | 题数 | 对齐成功 | 源实体在 KG | 答案在 KG | 平均黄金三元组 | 子图黄金边召回 |
|---|---:|---:|---:|---:|---:|---:|
| WebQSP train | 3,098 | **93.6%** | 99.9% | 96.2% | 1.31 | 93.1% |
| WebQSP test | 1,639 | **92.9%** | 99.9% | 95.7% | 1.28 | **94.1%** |
| CWQ train | 27,639 | **79.9%** | 100% | 99.7% | 1.60 | 91.6% |
| CWQ test(dev) | 3,519 | **83.3%** | 100% | 99.9% | 1.67 | 89.3% |

（300 题抽样上的对齐率略高：WebQSP 93.3%、CWQ 85.7%，因为抽样取的是文件头部、
CWQ 尾部题目更长。失败原因分布见下）

失败归因的四种桶（`scripts/analyze_alignment.py`）：

1. `answers_not_in_kg`：答案实体在子图里一个都没有 → 无从校验（WebQSP 2.7%）；
2. `chain_no_answer`：关系链走不到答案 → 子图缺边 / 中间实体名字缺失；
3. `source_not_in_kg`：源实体（含链接回退）不在 KG；
4. `no_answers`：数据集本身没有答案（WebQSP 37 条 + CWQ 官方 test）。

**这四类里只有第 2 类能通过改代码改善**——加上"多起点 × 多链"兜底后，300 题抽样上 WebQSP 91.7% → 93.3%、CWQ 84.0% → 85.7%；其余三类都是数据来源决定的，再改代码也没用

---

## 5. 与计划的对照：三个待决策点的落地

| 待决策点 | 计划建议 | 实际落地 | 依据 |
|---|---|---|---|
| 1 源实体抽取 | 用数据集自带话题实体或根实体 | WebQSP 用标注；CWQ 用"最长匹配 + 名字校验 + 链校验" | CWQ 官方 JSON 无 `topicEntityMid` |
| 2 mid→文本映射 | `type.object.name`，无名用 mid | 从标注自建 40,109 条映射；CVT 天然是 mid | 避免 30 GB 转储；链两端名字标注里都有 |
| 3 黄金路径 | 由 SPARQL / S-expression 反推 | 统一走 SPARQL 图模式 + 名字空间求解 + 答案校验 | CWQ 无 `sexpr` 字段；sexpr 解析器仍实现并单测 |
| 7 度数过滤 | 度数 > 1000 过滤 | 拆成实体侧 20000 / 关系侧 200000 / 黑名单 | 见 3.1、3.2 的实测 |
| 8 KG 规模 | 先源实体 3 跳子图 | RoG 子图并集（8.26 M 条） | 全量需 Virtuoso + 100 GB 内存 |

细节见 `docs/DECISIONS.md`

---

## 6. 已知限制

1. **子图召回有上限**：黄金边召回率 94.1%（WebQSP test）/ 89.3%（CWQ dev）。上游丢的边，下游再强也找不回来。其中一部分是 `max_nodes` 被枢纽邻居挤爆导致的（见 3.3），可通过加大 `max_nodes` 或按`node_cap_hit` 筛题重建来回收
2. **对齐率不是 100%**：WebQSP 92.9%、CWQ 83.3%。未对齐的题**没有正样本**，训练时必须跳过，否则会被当成"全负样本"污染损失
3. **名字空间会合并同名实体**（RoG 式预处理的固有代价，ToG/RoG 全系一致）
4. **CWQ 的 test 是官方 dev**（3,519 题），官方 test（3,531 题）无答案、只能提交
5. **CWQ 源实体是推断出来的**，不是标注；链接错误的概率低但不为零
6. **子图统计在训练集上采样**（WebQSP 前 1,200 / CWQ 前 1,500），dev/test 全量

---

## 7. 复现步骤与耗时

| 步骤 | 命令 | 耗时 | 产物 |
|---|---|---|---|
| 下载 | `python scripts/download_data.py` | ~3 min（800 MB） | `data/raw/` |
| 建索引 | `python scripts/build_kg_index.py` | ~50 s | `data/processed/kg/index_all`（256 MiB） |
| 数据生成 | `python scripts/prepare_dataset.py --dataset all` | ~55 min（WebQSP 17 + CWQ 38） | `data/processed/{webqsp,cwq}/` |
| 自检 | `python scripts/validate_outputs.py` | ~2 min | `reports/stats/validation.json` |
| 单测 | `python -m unittest discover -s tests -t .` | ~12 s | — |

入口脚本都是幂等的：重复跑会覆盖同名产物，不改动 `data/raw/`

---

## 8. 给下游的三条叮嘱

1. **B**：`gold.triples` 里的字符串直接用 `source_entities[].name` / 三元组里的实体名，
   不要去查 Freebase mid——名字空间与子图完全一致。
2. **C**：`edges[].src/dst` 已经是"远→近"的朝内方向，`nodes[].dist` 是跳数；
   τ_e 剪枝后的 `G_xpd` 才是论文里的展开子图，你拿到的候选池是它的**输入**。
3. **E**：`gold.matched == false` 的题必须从训练/评测里剔除；`gold_<split>.jsonl.gz`
   要按 `len(answers) > 0` 再过滤一次（WebQSP 有 37 条无答案题）。

---

## 附录：产物清单与体积

见 `data/processed/` 与 `docs/DATA_CONTRACT.md` 第 1 节；`reports/A_统计汇总.md`
是本文所有表格的可复算版本。
