# 数据核查报告（成员 A · 第 0 步）

> 目的：在写任何代码之前，先确认"拿到的数据到底是什么形状"，避免后面拿错误的假设去调參數。
> 所有数字由 `python scripts/dataset_report.py` 重新生成（几秒跑完），
> 明细在 `reports/stats/dataset_overview.json`。

---

## 1. 数据来源与真实性核验

| 数据 | 来源 | 许可 | 校验 |
|---|---|---|---|
| WebQSP train/test | 官方发布（`WebQSP.zip`）；本次经 HF 镜像 `YF0808/CWQ` 取得 | CC-BY 4.0 | 题数 3,098 / 1,639，与官方一致（合计 4,737） |
| CWQ train / dev / test | 官方 Dropbox 发布链（`tau-nlp` compwebq） | Apache-2.0 | 题数 27,639 / 3,519 / 3,531，与论文口径一致 |
| Freebase 子图（WebQSP） | `camazlucas/Freebase-WebQSP-CWQ-Subgraph` 的 `webqsp_subgraph.tsv` | CC-BY 2.5（源自 Freebase RDF） | 3,791,303 行 = 260,208,520 B |
| Freebase 子图（CWQ） | 同上的 `cwq_subgraph.tsv` | 同上 | 7,207,791 行 = 471,465,645 B |

> 子图数据集本身是 RoG（Reasoning on Graphs）预处理结果的**反序列化版本**：
> 官方 RoG 把每条问题的检索子图序列化成 JSON 字段，该数据集把全部子图并成一张
> 名字空间三元组表。合并去重后即我们使用的 KG 子集。

---

## 2. 标注规模与结构

| split | 题数 | 无答案 | 平均答案数 | 多答案占比 | SPARQL 解析失败 |
|---|---:|---:|---:|---:|---:|
| WebQSP train | 3,098 | 26 | 10.15 | 高 | 0 |
| WebQSP test | 1,639 | 11 | 8.36 | 高 | 0 |
| CWQ train | 27,639 | 0 | 2.19 | — | 0 |
| CWQ dev（作为 test） | 3,519 | 0 | 2.28 | — | 0 |
| CWQ 官方 test | 3,531 | **3,531** | 0 | — | 0 |

**结论 1：CWQ 官方 test 没有任何答案。** 这不是下载出错，而是官方设计（test 标签仅用于leaderboard 提交）。因此本复现按文献惯例把**官方 dev 当作 test**（3,519 题），官方 test 单独导出成 `qa_official_test_no_answer.jsonl.gz` 供最后提交用。
这一点必须写进最终论文的"实验设置"，否则数字对不上。

**结论 2：WebQSP 有 26 + 11 条无答案题。** 这些题在训练/评测时必须剔除，
否则 `F1` 的分母会出现 0/0。A 的产物里它们 `answers=[]`、`gold.matched=false`，
E 用 `len(answers) > 0` 过滤即可。

---

## 3. 字段差异

| 需要的字段 | WebQSP | CWQ |
|---|---|---|
| 问题 | `RawQuestion` | `question`（另有 `machine_question`） |
| 源实体 | ✅ `Parses[i].TopicEntityName` + `TopicEntityMid` | ❌ **完全没有** |
| 关系链 | ✅ `Parses[i].InferentialChain`（官方给定，2–3 跳） | ❌ 只能从 `sparql` 反推（**没有** `sexpr` 字段） |
| 约束 | ✅ `Parses[i].Constraints[]`（带 `EntityName`） | 隐含在 `sparql` 的旁支三元组里 |
| 答案 | `Answers[].EntityName` + `AnswerArgument` | `answers[].answer` + `answer_id` |
| 解析质量 | `AnnotatorComment.ParseQuality`（**3,098/3,098 全是 Complete**） | 无该字段 |

**结论 3：CWQ 的源实体必须自己抽。** 这是待决策点 1 里"用数据集自带话题实体"
这一建议在 CWQ 上**无法照抄**的直接原因

**结论 4：《复现计划》写的"CWQ S-expression 反推"在本份官方 JSON 上不可用**——
字段集是 `ID / answers / composition_answer / compositionality_type / created /
machine_question / question / sparql / webqsp_ID / webqsp_question`，没有 `sexpr`。
A 的做法：两个数据集统一走 `sparql` 图模式解析；S-expression 解析器仍然实现并单测
（`fog/gold/sexpr.py`），拿到带 sexpr 的版本可直接切换。

---

## 4. CWQ 题目类型分布

| compositionality_type | 题数（train） |
|---|---:|
| composition | 13,064 |
| conjunction | 11,609 |
| comparative | 1,531 |
| superlative | 1,435 |

`conjunction`（合取）占 42%，这类题在 SPARQL 里有多条并行约束分支——
A 的关系链取的是"源常量 → SELECT 变量"的**最短路径**，其余分支作为 `constraints`
（角色 `constraint`）保留，供 B 决定是否纳入正样本。

---

## 5. 子图（KG 子集）规模核查

| 项目 | 数值 |
|---|---:|
| WebQSP 子图三元组 | 3,791,303 |
| CWQ 子图三元组 | 7,207,791 |
| 合并去重后 | **8,258,891**（重复 2,740,203） |
| 实体数 | 2,574,875 |
| 关系数 | 7,042 |
| 最大出度 / 入度 | 2,186 / **134,182** |
| 索引体积 | 256 MiB |

**结论 5：实体度数是长尾的**（p50=2、p90=8、p99=67、p99.9=365、p99.99=2,033），
所以"枢纽节点"只有极少数（>1000 度的仅 614 个），但它们贡献了大量边。
这直接决定了决策点 7 的取值（见 `docs/DECISIONS.md`）。

**结论 6：关系频次分布极不均匀**——`common.topic.notable_types`（387,630）、
`freebase.valuenotation.is_reviewed`（216,663）、`common.topic.notable_for`（193,656）
这些**不是证据**的关系占了前几名。它们必须进黑名单，否则会污染负样本与候选池。

---

## 6. 名字空间的两个坑（已在下游契约里标注）

1. **同名合并**：RoG 用 `type.object.name` 把 mid 换成名字，不同 mid 同名会合并。
   本子图里 `m.04_nfz0` 这类没有名字的节点保留 mid——所以"名字空间"其实是
   "英文名优先、mid 兜底"的混合空间。
2. **大小写**：标注里的 `EntityName` 与子图里的名字大小写偶有差异（`Justin Bieber`
   vs `justin bieber`）。A 的索引查找做了大小写不敏感回退（`resolve_entity`）。

---

## 7. 核查结论（对后续工作的三条硬约束）

1. **CWQ 必须自抽源实体**，且要用黄金链做校验，否则会引入静默错误。
2. **CWQ 的 test 是 dev**，论文里的 CWQ 数字必须按 3,519 题复现。
3. **无答案题（WebQSP 37 条）必须过滤**，`official_test` 只能提交不能评测。
