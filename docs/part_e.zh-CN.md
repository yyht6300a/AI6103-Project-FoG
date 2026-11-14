# 成员 E 模块（训练 + 评测 + 集成）

对应《FoG 复现计划》分工里的成员 E。当前先交付**不依赖 B/C 即可完成**的底座；
集成主循环与真正端到端训练要等 B（评分器）与 C（FSE/FFP）落地后接入。

## 已交付

| 文件 | 内容 |
| --- | --- |
| `experiments/e2e.py` | 端到端指标：Hit / Hit@1 / F1（token 级 F1） |
| `experiments/adapter.py` | 把 A 的 `qa_*.jsonl.gz`（+可选 `subgraphs/`）转成 `experiments.sweep` 需要的 `QuestionData` |
| `src/model/training.py` | κ=10 局部负采样 + BCE / AdamW 训练循环 + 权重保存/加载 |
| `scripts/train_scorer.py` | 训练入口；`--smoke` 可在无 KG 索引、无真评分器时冒烟 |
| `tests/test_e2e.py` 等 | 8 个单元测试，全绿 |

## 运行

```bash
# 单元测试
python -m unittest tests.test_e2e tests.test_adapter tests.test_training -v

# 训练循环冒烟（不需要 KG 索引 / 真评分器）
python scripts/train_scorer.py --smoke --epochs 3

# 读 A 的 gold 正样本 + 负采样（需要先由 A 的 scripts/run_all.py 重建 KG 索引）
python scripts/train_scorer.py --dataset webqsp --split train
```

## 接口约定

- 端到端指标输入是“排序后的预测答案”与 A 数据的 `answers`：
  `end_to_end_metrics(predicted, gold)`。
- 适配层入口：`load_questions(dataset, split, data_root, subgraph_root=None,
  score_fn=None)`；`QuestionData.run_expansion_decision(policy)` 与
  `experiments.sweep.evaluate` 对齐。
- 训练循环 `train_scorer(scorer, batches)` 对 batch 的要求：每个 batch 为
  `(inputs, labels)`，`scorer(*inputs)` 输出 logits，`labels` 为 0/1 浮点张量。

## 待 B/C 接入

- 负采样已实现，但真正训练需要 B 的 `src/model/scorer.py`（当前是
  `NotImplementedError`）和嵌入层把文本转成张量。
- 集成主循环（FSE→FFP→MSM→作答）需要在 C 的 FSE/FFP 落地后写，届时调用
  D 的 `decompose_question` 与 `manage_memory`。
