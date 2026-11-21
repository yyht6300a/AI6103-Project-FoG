# 成员 E 模块（训练 + 评测 + 集成）

对应《FoG 复现计划》分工里的成员 E。评分器训练循环与负采样现已由成员 B 落地
（`src/model/training.py`、`src/model/sampler.py`、`scripts/train_scorer.py`），
本分支只保留 E 独有的部分：端到端指标、A 数据到实验扫描的适配层，以及配套测试。

## 已交付

| 文件 | 内容 |
| --- | --- |
| `experiments/e2e.py` | 端到端指标：Hit / Hit@1 / F1（token 级） |
| `experiments/adapter.py` | A 的 `qa_*.jsonl.gz`（+ 可选 `subgraphs/`）→ `QuestionData`，对接 `experiments.sweep` |
| `tests/test_e2e.py`、`test_adapter.py` | 单元测试 |

## 运行

```bash
python -m unittest tests.test_e2e tests.test_adapter -v
```

## 接口约定

- 端到端指标：`end_to_end_metrics(predicted, gold)`，`predicted` 为排序后答案、
  `gold` 为 A 数据的 `answers`。
- 适配层：`load_questions(dataset, split, data_root, subgraph_root=None, score_fn=None)`；
  `QuestionData.run_expansion_decision(policy)` 与 `experiments.sweep.evaluate` 对齐。

## 后续（依赖 B/C）

- 评分器训练与负采样使用 B 的 `scripts/train_scorer.py` / `src/model/training.py`。
- 集成主循环（FSE→FFP→MSM→作答）与全量消融评测，待 C 的 FSE/FFP 权重上传、
  以及可用 API 确定后接入；届时调用 D 的 `decompose_question` 与 `manage_memory`。
