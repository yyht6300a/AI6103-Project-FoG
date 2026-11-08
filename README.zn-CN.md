# FoG 复现项目

[English](README.md) | [简体中文](README.zn-CN.md)

本项目复现论文 **Foresight-over-Graph: Reasoning Beyond Local Horizons for Knowledge Base Question Answering**（NeurIPS 2026）。

上游仓库（https://github.com/yhong7/FoG）尚未公开源代码，目前仅包含 README 和架构图。本仓库根据论文重建处理流程，准备复现 FoG 所需的数据、模型、检索和实验框架，并开展规划中讨论的阈值优化实验。

## 目录结构

```text
.
├── paper/                      # 论文及其架构图
├── docs/                       # 分析文档和提取的论文文本
├── config/                     # 运行配置（LLM、嵌入、阈值）
├── data/
│   ├── raw/                    # WebQSP 和 CWQ 数据集（提交样本，忽略完整数据）
│   ├── processed/              # 提取的 triples.tsv 和 entity2name.tsv
│   └── freebase/               # Freebase 转储文件或 Virtuoso 数据库（不提交）
├── src/
│   ├── data/                   # 数据集加载与预处理
│   ├── model/                  # 问题条件化三元组编码器、评分器、FSE、FFP
│   └── agent/                  # 记忆子图管理与 LLM 交互
├── experiments/                # 阈值策略和检索指标
├── scripts/                    # 数据集下载及辅助脚本
└── requirements.txt
```

## 数据

WebQSP 和 CWQ 分别从 HuggingFace 数据集 `rmanluo/RoG-webqsp` 和 `rmanluo/RoG-cwq` 获取。每条样本包含 FoG 所需的字段：`question`、`answer`、`q_entity`（带名称的源实体）、`a_entity`（答案实体）和 `graph`（Freebase 推理子图，以名称可读的 `[head, relation, tail]` 三元组列表表示）。

```bash
python scripts/download_data.py                 # 下载用于提交的小规模样本
python scripts/download_data.py --full          # 下载完整 parquet 文件
```

本仓库不提交完整的 Freebase 知识图谱。可按照上游 README 使用 Virtuoso 部署，或在最小复现中将 RoG 数据集的 `graph` 字段用作 Freebase 子图。

## 复现阶段

1. 数据准备：源实体、标注推理路径和 3 跳子图。
2. 嵌入层：使用冻结的 `Qwen3-Embedding-4B` 编码实体、关系和问题。
3. 问题条件化三元组编码器与前瞻评分器。
4. 前瞻子图扩展（FSE）：使用扩展阈值。
5. 前瞻反馈传播（FFP）：由远及近传递消息。
6. 评分器训练：使用标注路径监督和局部负采样。
7. 使用 LLM 管理记忆子图。
8. 推理循环与评估（Hit、Hit@1、F1）。

## 待确定事项

复现过程中需要确定的选项均列在 `docs/` 下的分析文档中，包括源实体提取、MID 到文本的映射、标注路径转换、阈值尺度、FFP 深度、子问题 LLM、负采样过滤、完整 Freebase 或子集、自适应阈值初始值，以及 oracle 的定义。

### LLM 配置

将 [`env_example`](env_example) 复制为 `.env`，在本地填写 `LLM_API_KEY`。本地文件只保留 `LLM_MODEL_NAME`、`LLM_API_KEY` 和 `LLM_BASE_URL`；模型名和 URL 的非空值覆盖 [`config/config-dev.yml`](config/config-dev.yml) 中的默认值，留空时沿用 YAML。密钥仅从 `.env` 读取，该文件已被 Git 忽略。当前模型 `gpt-4o-mini` 和服务地址仅为暂定默认值，尚未确定或实测 API 服务。推理模型限于 `gpt-4o-mini`、`gpt-4` 和 `gpt-4o`。

超时和重试上限仅在 YAML 的 `llm.timeout` 和 `llm.max_retries` 中设置。前者为每次 HTTP 连接或读取操作的超时秒数，必须是大于零的有限数；后者为首次请求后的额外重试次数，必须是非负整数。旧 `.env` 中的 `LLM_TIMEOUT` 和 `LLM_MAX_RETRIES` 会被忽略。`.env` 支持单行赋值、引号、可选的 `export` 前缀和注释，不支持 shell 命令、多行值或变量插值。配置加载不读取或修改进程环境变量。HTTP 客户端禁用环境中的代理、CA 证书和凭据配置，显式传入模型、密钥、服务地址和超时，采用 OpenAI 兼容的 `/chat/completions` 协议；其他协议需要另行适配。

调用示例见 [English README](README.md#llm-configuration)。运行 `python -m unittest discover -s tests -v` 可执行离线检查，使用虚构密钥和模拟传输。待模型、服务及本地密钥确定后，仍需完成一次真实 API 调用。逐次请求的用量记录和记忆管理属于后续复现任务，当前配置入口尚未实现。

## 阈值优化实验

`experiments/` 包实现了规划中描述的固定阈值策略、自适应阈值策略和检索指标。验证集上的参数扫描见 `experiments/sweep.py`。
