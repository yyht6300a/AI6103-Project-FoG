# 成员 D 模块

[English](part_d.md) | [简体中文](part_d.zh-CN.md)

本文介绍成员 D 已实现的模块及其接口。配置和测试命令见 [README](../README.zn-CN.md#llm-配置)。潜力子图过滤、问题级记忆快照、LLM 引导的记忆替换、永久拒绝记录及展开排除已实现。成员 D 使用受控检索打分输入和真实 LLM 调用，证明模块正常运行且输出合理。C/E 系统集成由成员 E 负责，不属于成员 D 的交付范围，也不是模块验收的前置条件。下文历史记录中的“集成未验证”仅说明覆盖范围，不新增成员 D 待办。

## LLM 配置与请求

[`llm_config.py`](../src/agent/llm_config.py) 将私有 `.env` 中的字面值与共享 YAML 默认值合并，并校验最终配置。[`llm_client.py`](../src/agent/llm_client.py) 提供 `LLMClient.complete(messages)`，支持兼容 OpenAI 的 Chat Completions 接口。请求显式接收配置，不继承进程配置。调用方负责管理和关闭客户端；超时、连接失败、HTTP $429$ 和 HTTP $5xx$ 按客户端的有限重试策略处理。

## 真实验收入口

TODO 2.3.1 提供 [`scripts/validate_part_d.py`](../scripts/validate_part_d.py)：通过 `load_llm_config` 加载私有 `.env` 和共享 YAML，创建真实 `LLMClient`，直接调用公开的 `decompose_question` 和 `manage_memory`，并在 `finally` 中关闭客户端。运行时 HTTP 和回复均为真实内容，仅用受控用例代替 C/E 检索打分；不修复输出、不增加业务重试，严格潜在阈值固定为 $0.7$。

在仓库根目录使用项目 Python 环境运行：

```text
python scripts/validate_part_d.py --list-cases
python scripts/validate_part_d.py --suite single
python scripts/validate_part_d.py --suite multi
python scripts/validate_part_d.py --suite removal
python scripts/validate_part_d.py --suite ranking
python scripts/validate_part_d.py --suite conditional --list-cases
python scripts/validate_part_d.py --suite conditional
python scripts/validate_part_d.py --suite all
```

`--list-cases` 免费预览，不读取私有配置；其余命令产生真实 API 用量。记忆轮次默认使用 `evidence_decision`：先抽取需求并由代码求值，再选择证据和作答；多答案追加独立排序，零或单答案跳过排序。每事务正常 $2$ 次、保守最多 $3$ 次逻辑调用。`single` 的 $4$ 个用例（含一次分解）最多 $10$ 次，`multi` 的 $4$ 轮最多 $12$ 次，`removal` 的 $3$ 轮最多 $9$ 次，`ranking` 的 $2$ 个用例最多 $6$ 次；默认 `all` 仍为上述 $13$ 个用例，最多 $37$ 次。按当前额外重试 $2$ 次，HTTP 上限分别为 $30$、$36$、$27$、$18$、$111$ 次。可用 `--prompt-variant paper` 或 `--prompt-variant retrieval_questions` 显式运行原基线或旧增强版，其 `all` 上限仍为 $25$ 次逻辑调用、$75$ 次 HTTP 尝试；不发生自动回退。报告区分用例数、逻辑调用数和 HTTP 上限，任一阶段异常即停。

独立 `conditional` 组不加入 `all`：使用原完整条件题，Silver／Bronze 各执行独立完整图和从空状态开始的三轮链，共 $8$ 次事务，正常 $16$ 次、保守最多 $24$ 次逻辑调用及 $72$ 次 HTTP 尝试。链首轮提交两条出生地，次轮真实继承快照且仅补出版社，末轮以 $0.95$ 重输全部事实；逐轮检查完整记忆、答案、全部追问、永久拒绝和提交前过滤，失败即停，不靠 `publish` 关键词判断通过。报告保留成功事务各阶段的请求、schema 和原始回复，供人工审阅抽取语义；自动检查通过仍不等于语义审阅完成。默认协议的支持范围见[记忆管理提示词](#记忆管理提示词)；原有两跳用例也保留为回归，不能预先认定新默认能通过其真实验收。

每次付费运行写入唯一的私有 `tmp/tmp_part_d_validation_<UTC timestamp>.json`，包含用例预期、受控三元组及对齐分数、实际查询与有效输出、提交图、前后状态、异常类名和状态规则检查；不含密钥、失败的原始回复及异常消息。实际请求仍追加到 `logs/llm_usage.csv`，实际模型和用量元数据在 TODO 2.3.6 核对；私有报告用于人工审阅，不自动提交。

退出码 $0$ 仅表示解析与状态检查通过，语义审阅仍为待办。人工核对检索问题有效性、证据充分性、答案依据和排序、澄清质量。多轮观察项按实际选择记录保留、移除、拒绝、恢复与隔离；`false` 表示未观察到该行为，对应真实验收项可能需要完善用例后才能通过，不强迫模型选择。[语义质量审阅](#语义质量审阅)记录多答案覆盖及未解决问题。最终验收须满足 TODO 2.3.5 的 GPT-4o-mini 和匹配 OpenAI 配置要求。入口准备完成不代表 TODO 2.3.2 至 2.3.6 或 C/E 集成完成。

`tests/test_part_d_validation.py` 离线验证免费预览、完整调用上限、公开解析及隔离用量记录、异常即停、旧状态保持、客户端关闭和敏感错误过滤；模拟传输仅存在于测试中。TODO 2.3.1 未发起付费请求。

### 单轮真实验收结果

2026/11/09 使用未变更的私有 DeepSeek 配置运行 `python scripts/validate_part_d.py --suite single`，完成 TODO 2.3.2。全部 $4$ 次逻辑调用返回 `deepseek-flash`，各仅一次 HTTP 请求，无手工修改回复、绕过解析、输出修复或重试。问题分解返回“Who is the author of The Glass Orchard?”和“In which city was the author of The Glass Orchard born?”。第二个问题仍引用尚未解析的作者，其检索有效性留待 TODO 2.3.4 综合审阅。

| 用例 | 通过校验的输出与状态 |
| --- | --- |
| 证据充足 | 选中作者及出生地两条证据；回答 `Linden City`；无后续问题；两条证据均进入展开排除集合 |
| 证据不足 | 保留作者关系；答案为空；追问 `Which city was Mira Vale born in?`；作者关系进入展开排除集合 |
| 潜在图与历史记忆均为空 | 选择、答案、记忆及排除集合均为空；追问 `Who is the author of The Glass Orchard?` |

三次 MSM 均保持原快照不变，无新增永久拒绝，记忆、拒绝及排除集合符合状态规则。[`logs/llm_usage.csv`](../logs/llm_usage.csv) 新增的 $4$ 行始于 2026/11/09 00:31:25（UTC+08:00），均成功，共报告输入 $860$、输出 $851$ tokens；服务未提供请求 ID，继续留空。完整输出与状态保存在私有 `tmp/tmp_part_d_validation_20261108T163125709480Z.json`，报告自动生成的语义审阅标记仍为待办。本组不覆盖拒绝状态转换或多答案排序；TODO 2.3.3 至 2.3.6（包括综合语义审阅及最终 GPT-4o-mini 验收）和 C/E 集成仍待完成。

### 多轮真实验收结果

2026/11/09 使用未变更的私有 DeepSeek 配置完成 TODO 2.3.3。运行 `python scripts/validate_part_d.py --suite multi` 执行原有 $4$ 次调用，再运行 `python scripts/validate_part_d.py --suite removal` 执行补充 $3$ 次调用。全部返回 `deepseek-flash`，通过严格解析和状态检查，每次仅一次 HTTP 请求，无手工修改回复或绕过校验。报告记录各轮实际传递的上轮问题与记忆，检索及对齐分数仍为受控输入。

| 轮次 | 实际选择与状态转换 |
| --- | --- |
| `multi.partial` | 保留 `AUTHOR`；已提交但未选中的 `DISTRACTOR` 永久拒绝；分数 $0.7$ 的 `BIRTH` 未提交且保留检索资格；追问 `What city was Mira Vale born in?` |
| `multi.recovery` | 使用上述后续问题；保留 `AUTHOR`，选入分数升至 $0.95$ 的 `BIRTH`，回答 `Linden City`；已拒绝干扰项未提交 |
| `multi.exclusion_probe` | 保留两条路径证据与答案 `Linden City`；高分干扰项仍被屏蔽；未观察到旧记忆移除 |
| `multi.independent` | 从空记忆和空拒绝集合开始，选入同一 `DISTRACTOR` 并回答 `Nolan Reed`，不继承先前问题的拒绝状态 |
| `removal.unresolved` | 保留可能有用的 Mira Vale 出生地证据，答案为空；追问是否由 Silver Press 出版，以及 Nolan Reed 的出生地 |
| `removal.resolved` | 使用上述问题及 Bronze Press 出版社、Nolan Reed 出生地证据；选入两条新证据并回答 `Cedar City`，移除旧 Mira Vale 出生地证据并永久拒绝 |
| `removal.exclusion_probe` | 旧证据即使以 $0.95$ 高分重新输入仍未提交；拒绝持续保留，当前两条分支证据与答案 `Cedar City` 保持 |

全部 $7$ 次旧快照不变，记忆、拒绝与展开排除规则均通过。原报告的 `multi_observations.old_memory_removal` 如实保持 `false`，补充报告的全部 $4$ 个 `removal_observations` 均为 `true`。返回空问题列表不终止强制排除探测。条件用例假设出版社为单值，以不同出版社确定否则分支；这属于受控用例语义，不能证明开放世界图谱中的一般否定事实。综合语义质量、多答案排序及 GPT-4o-mini 验收仍留待 TODO 2.3.4 和 2.3.5，C/E 集成仍未验证。

[`logs/llm_usage.csv`](../logs/llm_usage.csv) 新增 $7$ 行，两组分别始于 2026/11/09 00:35:53 和 00:37:47（UTC+08:00），共报告输入 $1866$、输出 $3259$ tokens，全部成功；服务未提供请求 ID，继续留空。私有报告为 `tmp/tmp_part_d_validation_20261108T163553245365Z.json` 和 `tmp/tmp_part_d_validation_20261108T163747907687Z.json`，自动语义审阅标记仍为待办。未变更生产模块或私有配置；扩展入口离线检查覆盖调用上限、真实解析、问题传递、移除及持续屏蔽，全部 $89$ 项离线测试通过。完整用量汇总与交接仍由 TODO 2.3.6 处理。

### 语义质量审阅

2026/11/09 已完成 TODO 2.3.4 审阅并记录问题，不代表整体语义验收通过。核对上述三份私有报告的全部 $11$ 次真实输出，并在私有配置不变时运行 `python scripts/validate_part_d.py --suite ranking`，新增 $2$ 次真实 `deepseek-flash` 调用，均经生产严格解析。新报告为 `tmp/tmp_part_d_validation_20261108T164441286155Z.json`。原报告保留自动生成的 `semantic_review: pending` 标记；本节为人工审阅结论，不改写模型输出。

| 维度或用例 | 实际输出审阅 |
| --- | --- |
| 初始分解 | 作者查询有用；出生地查询仍依赖尚未解析的作者，重复原问题依赖。虽可独立理解，却未形成明确实体的出生地查询；真实检索召回尚未验证。后续 MSM 已正确追问 `Mira Vale`。 |
| 证据充足及多轮恢复、探测 | 保留书籍、作者、城市路径的两条必要边，支持 `Linden City`；无关作者或书籍未进入答案。保留必要路径、拒绝无关书籍符合用例语义。 |
| 证据不足、空图及首轮隐藏 | 不编造出生地；已有作者证据被保留，追问缺失作者或具名作者的出生地，可引导下一轮检索。 |
| 独立问题 | 所选作者关系支持 `Nolan Reed`，未被另一问题的拒绝状态干扰。 |
| 条件分支 | 首轮保留可能有用的出生地，不作答并追问出版社及另一出生地；后续 `Cedar City` 仅在用例的单出版社假设下成立。开放世界图谱中，另一出版社不能单独排除 Silver Press，解析器无法发现该假设。移除证据仅在用例内合理，不证明一般否定推理。 |
| `ranking.equal_support` | 保留两条直接出生地证据，回答 `["Linden City", "Cedar City"]`，不追问。两答案均有依据；支持相等时两种顺序均合理，不能据此证明严格排序。 |
| `ranking.unequal_support` | 保留 `confirmed_birthplace -> Linden City` 和 `reported_possible_birthplace -> Cedar City`，排除 `lived_in -> Harbor City`，但仅回答 `["Cedar City"]` 且无追问。实体归属校验通过，较强确认候选却未返回；预期两候选均返回且确认项优先，不同支持强度的排序仍未验证。 |

不同支持强度用例将确认及可能出生地均视为候选，确认关系提供较强定性支持。模型也可能将“candidate”理解为排除已确认事实，回复未解释原因。因此当时记录为未解决的完整性与措辞问题，不推断模型内部原因，也不直接认定发生排序倒置。检索分数均相等，不传达置信度差异；提示词会排序图行，单次运行不能证明顺序稳健性或概率校准。该观察不足以支持修改论文提示词或增加生产启发式规则。后续排序实现、验收结果及关闭决定见[独立答案排序](#独立答案排序)。

新增两次调用均保持旧快照、通过状态规则，无重试、回复改写或解析绕过。未发生真实传输或解析失败，不刻意制造付费故障；异常定位和非空旧状态保持继续沿用 TODO 2.2 及入口离线故障测试。[`logs/llm_usage.csv`](../logs/llm_usage.csv) 新增两条成功记录，始于 2026/11/09 00:44:41（UTC+08:00），共输入 $542$、输出 $1813$ tokens，请求 ID 继续留空。全部 $89$ 项离线测试通过，扩展入口断言覆盖多答案顺序、独立状态和无关居住地拒绝。TODO 2.3.5 须使用 GPT-4o-mini 运行 single、multi、removal、ranking 用例并解决语义问题后，才能进入 2.4 交付；2.3.6 用量汇总和真实 C/E 集成仍待完成。

#### 候选措辞的后续排查

2026/11/09 新增 $3$ 次真实 `deepseek-flash` 对照，仅改变问题措辞，三元组、分数、图序列化及图 8 提示词均相同，各次从空状态开始。私有入口 `python tmp/tmp_diagnose_ranking.py` 捕获实际合成提示词及生产解析前的回复正文白名单字段，证据为 `tmp/tmp_ranking_diagnosis.json`；两份临时文件不提交 Git，上文历史报告保持原样。

| 问题对照 | 模型实际输出 |
| --- | --- |
| 重复原“candidate birthplaces”问题 | 只选可能出生地、只答 `Cedar City`；与此前不同，本次确认出生地证据也未保留 |
| 明确包含确认及可能出生地 | 保留两条出生地证据，回答 `["Linden City", "Cedar City"]`，排除居住地 |
| 同一明确包含问题，再指定确认项优先 | 选择及答案顺序与上一对照相同 |

三次原始答案数组均与解析后完全一致，结束原因均为 `stop`，旧状态保持为空。代码核查确认输入均通过阈值且进入请求，`CASES` 中的预期仅写入报告，解析器及报告生成均不截断答案。本次重跑的单答案因此来自模型输出。小规模对照支持问题范围歧义为主要解释：“candidate”可能被理解为排除已确认事实。但选择本身也有波动，不能证明模型内部原因或统计因果效应。实体归属校验无法发现语义漏答，符合其职责。原问题重跑省略的确认出生地会按既定规则永久拒绝，说明语义选择可能影响后续检索，即使状态规则完全通过。

维护入口的 `RANKING_QUESTION` 已改为仅明确包含确认及可能出生地，不附加确认项优先指令，不改变论文提示词；第二个对照已经通过公开 MSM 验证该完全相同的问题。原异常保留为用例措辞问题证据，尚不能认定为排序缺陷。未附加排序指令时观察到一次确认项优先，但提交图排序后确认关系也在首行，顺序稳健性及 GPT 质量仍未验证。新增 $3$ 次成功请求，无重试，共输入 $878$、输出 $1423$ tokens，始于 2026/11/09 00:55:51（UTC+08:00），请求 ID 留空。TODO 2.3.5 仍须使用 GPT-4o-mini 验证澄清后的用例，交付关卡保持不变。

### GPT-4o-mini 验收尝试

2026/11/09 已使用用户配置的私有 OpenAI 设置执行 TODO 2.3.5，请求前确认实际模型为 `gpt-4o-mini`、地址为 `https://api.openai.com/v1`。交付关卡**未通过**。未修改生产提示词、解析器、拒绝规则或私有配置。下列报告保留实际输出，自动语义标记仍为待审，本节记录人工审阅结论。

| 命令及私有报告 | 结果 |
| --- | --- |
| `python scripts/validate_part_d.py --suite all`；`tmp/tmp_part_d_validation_20261108T170609405988Z.json` | 开始 $11$ 次调用，在 `removal.exclusion_probe` 因 `ValueError` 停止，尚未运行排序；此前 $10$ 次通过严格解析及状态检查 |
| `python scripts/validate_part_d.py --suite removal`；`tmp/tmp_part_d_validation_20261108T170726337215Z.json` | 明确条件问题后，$3$ 次回复均通过解析和状态检查，但语义及旧记忆移除覆盖未通过 |
| `python scripts/validate_part_d.py --suite ranking`；`tmp/tmp_part_d_validation_20261108T170732865045Z.json` | $2$ 次均通过解析、状态及用例语义审阅 |

单轮证据充足时保留两条路径证据并回答 `Linden City`；证据不足时保留作者关系、不作答，追问 `Where was Mira Vale born?`。空图不作答，但三个问题包含偏离目标的 `What is The Glass Orchard about?` 及泛泛索要出生地信息。初始分解为 `Who is the author of The Glass Orchard?` 和 `In which city was this author born?`，第二问依赖上下文，未满足论文要求的独立可理解子问题；真实检索有效性仍未验证。

基础多轮四例通过：阈值隐藏证据保留资格，升至 $0.95$ 后恢复；有用历史记忆保留；已提交的无关三元组永久拒绝且持续排除；独立问题可选入同一三元组。该组实际选择未出现旧记忆移除。

原条件用例在没有出版社证据时提前回答 `Linden City`；补充 Bronze Press 和 Nolan Reed 出生地后，选择为空，全部三条已提交证据永久拒绝，转而追问是否由 Silver Press 出版。因此下一次强制探测实际提交空图，并因 `ValueError` 被严格解析拒绝。失败前的空记忆及三条拒绝状态保持不变。报告按设计不保存失败原文和异常文本，无法据此还原具体哪条校验失败；HTTP 用量仍记录成功。

维护入口的 `BRANCH_QUESTION` 已明确书籍只有一个出版社，且出版社未知时应先澄清。该调整将原有用例前提提供给模型，消除此前开放世界歧义，不提供目标答案，不修改图 8。复测仍提前回答 `Linden City`；出版社证据到齐后保留全部三条证据、不作答，连续追问出版社。旧快照和状态规则均通过，却未观察到旧记忆移除及其永久屏蔽。因此措辞澄清**没有解决** GPT 的条件推理问题。两版实际报告均保留，结构通过不代表语义验收通过。

排序两例均回答 `["Linden City", "Cedar City"]`。等支持用例保留两条出生地证据；不同支持强度用例保留确认及可能出生地，省略并永久拒绝居住地，未增加排序指令即返回确认项优先。本次澄清用例未复现此前漏掉确认答案的问题，但图排序也将确认关系置前，不能证明排序稳健性或概率校准。

全部 $16$ 次实际 HTTP 请求返回 `gpt-4o-mini-2024-07-18`，无传输重试、手工修复或绕过解析。[`logs/llm_usage.csv`](../logs/llm_usage.csv) 新增 $16$ 行，共输入 $3523$、输出 $772$ tokens，HTTP 均成功且请求 ID 非空；原运行末次的应用层校验失败不改变 HTTP 记账。论文提示词和严格校验保持原样。TODO 2.3.5 保持未勾选，2.4 仍不得交付。条件语义问题的后续工作见[当前修改计划](#修改计划分离条件决策与记忆发布)；2.3.6 的完整历史用量汇总及 C/E 集成仍为独立待办。

## 逐次请求用量记录

[`llm_usage.py`](../src/agent/llm_usage.py) 为每次实际 HTTP 尝试追加一行 UTF-8 CSV 记录，包括重试和失败。元数据包含请求开始时间、模型、返回的 token 数、请求结果、异常类名和请求 ID。未知值留空，不记录凭据及请求、响应内容。后续应用层校验失败不会修改已成功的 HTTP 用量记录。配置和用量记录测试见 `tests/test_llm_configuration.py` 和 `tests/test_llm_usage.py`。

## 初始问题分解

2026/11/09 使用未变更的 GPT-4o-mini/OpenAI 配置运行 `python scripts/validate_part_d.py --suite reference_diagnostic`，完成 $3$ 次真实调用：原版仍输出 `In which city was this author born?`，增强版输出 `In which city was the author of The Glass Orchard born?`；两版第一问均为 `Who is the author of The Glass Orchard?`。向 MSM 提交受控作者关系后，模型保留该关系，答案为空并追问 `Where was Mira Vale born?`，旧状态和状态规则通过。本用例的上下文指代问题已修正；单次观察不能保证所有问题均合格。该显式诊断不加入 `all`，可用 `--suite reference_diagnostic --list-cases` 免费预览；切换默认决策流程后，当前保守上限为 $5$ 次逻辑调用、$15$ 次 HTTP 尝试，以上实际调用数仍为历史记录。

实际均返回 `gpt-4o-mini-2024-07-18`，无重试，新增 $3$ 条成功用量记录，共输入 $569$、输出 $87$ tokens。私有报告为 `tmp/tmp_part_d_validation_20261108T174923559860Z.json`，保留原始有效输出及自动待审标记，本段为人工审阅结果。仓库尚无真实 C/E 检索器，诊断记录的初始查询未实际执行检索，作者关系为受控输入；逐个子问题的真实召回验证仍待 C/E 集成，不能据此宣布 TODO 2.3.5 整体通过。图 8 未修改，其他语义问题仍待处理。

[`question_decomposition.py`](../src/agent/question_decomposition.py) 实现 FoG 第 3.1 节和附录 C 图 7（论文第 21 页）中的初始 $Q_{\mathrm{sub}}$。默认 `prompt_variant="explicit_references"` 在图 7 基础上增加独立指代要求，属于实现增强：每个问题按单独检索书写，写全已知实体名及未知实体的关系描述，不假定上一问已有答案，不编造实体名。`decompose_question` 和 `build_decomposition_prompt` 均接受该仅限关键字的参数；传入 `prompt_variant="paper"` 可使用保留论文措辞、仅校正指令编号的原版。两版共用原有解析、重试及记账路径。

```python
from src.agent.llm_client import LLMClient
from src.agent.llm_config import load_llm_config
from src.agent.question_decomposition import decompose_question

client = LLMClient(load_llm_config())
try:
    sub_questions = decompose_question(
        "Which man leads the country that uses Libya, Libya, Libya as its national anthem?",
        client,
    )
finally:
    client.close()
```

此示例会发起付费请求。`decompose_question(question, client)` 返回有序的 `list[str]`，供检索和评分使用，不创建检索或记忆状态。`build_decomposition_prompt(question)` 和 `parse_sub_questions(content)` 也可独立使用，无需调用 API。

原问题必须是非空白字符串，并原样插入提示词。响应必须正常完成（`finish_reason: stop`）、未被拒绝，且仅包含一个非空 JSON 数组，数组元素均为非空白字符串。Markdown 代码围栏、额外文字和格式错误的列表均被拒绝。字符串、顺序和重复项原样保留。图 7 未限制子问题数量；图 8 中一至三个子问题的限制适用于后续记忆管理。这些检查只校验结构，不保证语义正确。

输入或输出无效时抛出 `ValueError`，校验消息不暴露其文本。传输和用量记录错误由 `LLMClient` 向上传递；输出校验不增加重试、回退或替代问题。

`tests/test_question_decomposition.py` 覆盖提示词约束、输入原样插入、JSON 校验、响应封装、拒绝响应、截断、传输失败、请求载荷、重试及用量记录。测试中的所有 HTTP 调用均使用模拟实现。

## 潜力子图构建

[`potential_subgraph.py`](../src/agent/potential_subgraph.py) 实现 FoG 第 3.3 节公式 10：$G^{\mathrm{pot}} = \{t \in G^{\mathrm{xpd}} \mid s_t > \tau_p\}$。传入成员 C 在反馈传播后得到的精炼分数，并与扩展三元组逐项对齐。默认阈值为 $0.7$，与 `config/config-dev.yml` 中的 `potential_threshold` 一致；调用方若从 YAML 加载配置，须显式传入该值。

```python
from src.agent.potential_subgraph import build_potential_subgraph

triples = [["Country A", "led_by", "Person B"], ["Country A", "anthem", "Song C"]]
refined_scores = [0.7, 0.85]
potential = build_potential_subgraph(triples, refined_scores)
# [("Country A", "anthem", "Song C")]
```

`build_potential_subgraph(triples, scores, potential_threshold=0.7)` 接收对齐的序列，返回新的 `list[tuple[str, str, str]]`。每个三元组按头实体、关系、尾实体的顺序包含三个非空白字符串，保留其字符和方向。重复且满足条件的三元组仅保留一次，按首次满足条件的出现顺序排列。输入保持不变。允许空输入和空结果。分数等于阈值时不纳入结果，比较不使用容差。

分数和阈值必须是有限实数标量，且不能是布尔值。函数直接比较分数，不执行 sigmoid、归一化或截断，也不假定分数在 $[0, 1]$ 范围内。分数尺度由成员 C 负责；论文训练时使用的 sigmoid 不意味着此处应对公式 10 额外变换。对于一维 PyTorch 分数张量，调用方须显式使用 `scores.detach().cpu().tolist()` 转换；多维分数列表无法通过标量校验。本地成员 C 模块仍是占位实现，后续流水线适配仍需集成。

三元组格式错误、数值无效或长度不匹配时抛出 `ValueError`，低于阈值的无效行也会报错。校验错误不包含三元组文本。过滤不调用 API，也不维护记忆或排除状态：某轮被过滤的三元组可在后续调用中满足条件。永久拒绝由后续 LLM 记忆管理步骤负责。

`tests/test_potential_subgraph.py` 检查严格浮点边界、YAML 默认值一致性、自定义分数尺度、图中三元组的身份与顺序、输入保持不变、空结果、后续轮次重新纳入及无效输入。所有检查均离线运行。

## 问题级持久记忆

[`memory_state.py`](../src/agent/memory_state.py) 实现 FoG 第 3.3 节的状态基础。`QuestionMemory(original_question)` 将 $G^{\mathrm{mem}}$ 和永久拒绝集合初始化为空。每次问题运行均须创建独立实例，即使问题文本相同；在该次运行的各轮推理之间保留快照。此处持久指运行内跨轮保留，不包含磁盘存储或全局问题缓存。

```python
from src.agent.memory_state import QuestionMemory

first = QuestionMemory("Who leads Country A?")
second = QuestionMemory("Who leads Country B?")
assert first.memory_triplets == frozenset()
assert first.rejected_triplets == frozenset()
assert second.memory_triplets == frozenset()
```

`original_question` 必须是非空白字符串，并原样保存。构造器的可选参数 `memory_triplets` 和 `rejected_triplets` 接收由头实体、关系、尾实体序列组成的可迭代对象，均复制为 `frozenset[tuple[str, str, str]]`。完全相同的三元组去重，保留字符串和方向，不保证遍历顺序。每个三元组必须包含恰好三个非空白字符串。输入无效或记忆与拒绝集合有交集时抛出 `ValueError`，错误消息不包含问题或三元组文本。字段和内部元素均不可变；后续修改传入列表不会影响快照或其他问题。

下述更新编排器先完整校验 LLM 结果，再构造新快照，并继承已有永久拒绝集合。保留旧快照可支持失败时维持原状态。此容器保存显式状态并提供下述展开过滤接口，不执行证据选择、拒绝判定或 LLM 调用。阈值过滤不会填充任何集合。

`tests/test_memory_state.py` 验证空集初始化、不同问题及同文问题运行之间的隔离、不可变且脱离输入引用的快照、图身份保持、无效输入不改写旧状态，以及阈值过滤不产生拒绝状态。所有检查均离线运行。

## 记忆管理提示词

[`memory_prompt.py`](../src/agent/memory_prompt.py) 提供 `build_memory_prompt(question, memory_triplets, potential_triplets)`，对应 FoG 第 3.3 节和附录 C 图 8（论文第 21 页）。模板保留图中措辞、高召回选择原则、仅依据证据作答、三元组和实体原样复制约束、证据不足时生成一至三个澄清问题，以及包含三个字段的 JSON 输出示例。

`update_memory` 和公开 `manage_memory` 默认采用 `prompt_variant="evidence_decision"`。首阶段通过严格 schema 抽取题型、单关系相等条件及按查询实体分组的单跳需求；代码用完整提交图求值条件，并通过本轮事实编号和端点派生方向、匹配全部证据，得到必留集合及缺口。最终阶段使用基础严格 schema，必须保留必要证据；条件未知禁答，条件已定且无缺口须作答并停止追问。有缺口时由代码生成固定的开放值查询，模型须原样选择；集合外、重复或遗漏未知条件查询均在发布前失败。任何阶段失败均不发布快照、不补选、不补问或业务重试；成功后仍按最终选择计算永久拒绝，多答案继续独立排序。

这是工程增强，受控 GPT-4o 用例通过不构成一般语义保证。当前协议仅支持非条件单跳需求和单关系相等条件，提示要求模型把多跳、聚合及复杂要求标记为不支持，这类记录以 `decision_unsupported` 终止；缺失声明的反向精确命中也会保守拒绝，可能拒绝合法的另一方向问题。原有多跳真实验收在新默认下尚未通过，TODO 2.3.5 保持开放；不得把切换默认写成该交付关卡完成。固定查询只保证与派生缺口一致，真实 C/E 召回由成员 E 集成验证。

`build_memory_prompt` 是单次选择提示的构造器，仍默认 `retrieval_questions`；它不是完整默认事务入口。显式 `prompt_variant="paper"` 保留图 8 原版，`retrieval_questions` 保留旧检索增强版：围绕缺失关系、已知具名实体生成追问，避免重复查询已知事实。两种对照均保留原解析、永久拒绝和传输重试规则；未知变体请求前抛出 `ValueError`，默认流程不会遇错回退到对照版。

显式传入 `prompt_variant="answer_ranking"` 可在检索增强版上追加支持强弱比较、展示顺序无关及答案完整性指令，不改变默认值或增加模型阶段。该单次调用实验变体未通过筛查，保留作对照；正式排序增强使用下文的[独立答案排序](#独立答案排序)，无需选择该变体。

```python
from src.agent.memory_prompt import build_memory_prompt
from src.agent.memory_state import QuestionMemory

state = QuestionMemory("Who leads the country with Song C?", [
    ("Country A", "anthem", "Song C"),
])
potential = [("Country A", "led_by", "Person B")]
prompt = build_memory_prompt(state.original_question, state.memory_triplets, potential)
```

原问题须为非空白字符串，原样插入。两个图均接收由头实体、关系、尾实体序列组成的可迭代对象，每个三元组恰含三个非空白字符串；支持空图、无序快照和生成器。图 8 仅有一个 `Knowledge` 字段，此处传入历史记忆与潜在三元组的并集，按完全相等去重，再按元组字典序排序，使展示稳定。JSON 数组序列化保留 Unicode，解码后引号、换行、反斜杠和所有原始字符串均保持不变。图中行排序不改变三元组内部位置。取并集和序列化方式属于复现选择，论文未规定输入顺序或序列化编码。

输入格式无效时抛出 `ValueError`，不暴露输入文本。可变输入容器和记忆快照保持不变；一次性迭代器会被消耗。此函数只构造文本，不调用 API、不校验模型输出，也不更新记忆或拒绝状态。调用方负责提供符合条件的潜在子图；构造器不会隐式读取拒绝集合或检索结果。下述结构化输出解析器强制校验字段类型及输入范围；证据语义校验仍待实现。

`tests/test_memory_prompt.py` 检查论文指令和 JSON 示例、图并集与稳定排序、原字符串和方向保持、空图、无效输入处理，以及与现有记忆和潜在子图模块的组合。所有检查均离线运行。

## 结构化记忆输出

[`memory_response.py`](../src/agent/memory_response.py) 提供 `parse_memory_response(content, memory_triplets, potential_triplets)`，对应 FoG 第 3.3 节公式 11 和附录 C 图 8。传入助手回复文本，以及本次提示词实际提交的历史记忆和潜在子图。解析器要求完整文本仅含一个标准 JSON 对象，恰有三个必需字段，字段值均为数组。

```python
from src.agent.memory_response import parse_memory_response

memory = [("Country A", "anthem", "Song C")]
potential = [("Country A", "led_by", "Person B")]
content = '''{
  "selected_triplets": [["Country A", "led_by", "Person B"]],
  "predict_answer": ["Person B"],
  "subsequent_question": []
}'''
result = parse_memory_response(content, memory, potential)
assert result.predict_answer == ("Person B",)
```

`selected_triplets` 的每项必须是恰含三个非空白字符串的数组，与本次输入图并集中的某个三元组完全一致，包括头实体、关系、尾实体的位置和字符。`predict_answer` 的每项必须是非空白字符串，与选中三元组中的头实体或尾实体原串完全一致；仅出现在未选中输入中的实体、仅作为关系出现的字符串、子串、大小写变化和空白归一化均被拒绝。空选择必须返回空答案。`subsequent_question` 至多包含 $3$ 个非空白字符串，答案为空时不得为空，详见下文澄清约定。图 8 的全空对象仍作为提示词中的格式示例，但不能作为通过校验的完整回复。

试探性作答（TODO 2.1.8）沿用 Figure 8：LLM 仅依据选中三元组作答，并按证据支持的可能性排列多个答案。解析器保留返回排名、重复项和实体原串，支持选中的多跳路径及历史与新增证据，不按图顺序、字母或三元组数量重排。选中实体归属是必要约束，不能证明实体回答了问题或排名正确；语义蕴含、证据充分性及支持程度仍由 LLM 判断。本项不增加模型调用或启发式验证器。

返回不可变的 `MemoryResponse`，其 `selected_triplets`、`predict_answer` 和 `subsequent_question` 字段均为元组，三元组行也为元组。保留模型顺序、答案排名、重复项和所有原字符串。应使用解析函数完成校验；直接构造数据容器不会校验图成员关系。输入图遵循提示词构造器的可迭代对象约定。一次性迭代器会被消耗，若要同时构造提示词和解析回复，调用方须先将其物化为可复用容器。

图或输出无效时抛出 `ValueError`，错误消息不含原始内容。缺少、多余或重复字段、额外文字、代码围栏、尾随值、非标准 JSON 数值、数组元素格式错误，以及越界三元组或实体均被拒绝，不自动修复。拒绝重复键和限制精确字段集合属于避免歧义的解析选择。解析器不调用 API、不增加重试，也不改写记忆或永久拒绝状态。下述编排器负责响应封装校验和状态更新；状态快照提供展开排除接口。

`tests/test_memory_response.py` 覆盖原字符串、顺序、不可变性、空字段、子问题数量边界、严格 JSON 和字段错误、图和实体范围、阈值隐藏及拒绝证据、提示词组合和失败时状态保持。所有检查均离线运行。

### 精确匹配约束

TODO 2.1.10 沿用图 8，禁止外部知识、编造及三元组改写、创建、字段重排、合并和拆分。解析器按解码后的完整头实体、关系、尾实体元组校验其是否属于本次输入并集。字段排列、跨行重组、路径压缩、名称拆分或合并、大小写或空白修改及 Unicode 归一化均被拒绝，除非变换后的三元组本身也在本次输入中；代码不修复输出。等价 JSON 转义解码为相同字符串，允许通过。`selected_triplets` 中完整行的顺序可以改变，每行内部字段位置必须保持一致。

答案须精确匹配选中三元组的头尾实体串，详见上文。这些校验能拒绝编造的三元组和越界实体，但不能检测模型借助外部知识返回合法输入实体的情况；仅依据证据推理仍由提示词约束。澄清问题是自然语言检索查询，不受三元组成员关系限制。无效选择或答案在发布前报错，保留旧记忆与永久拒绝集合，不增加业务重试。回归测试覆盖所有非恒等字段排列、改写与合并拆分、等价 JSON 转义、选中行顺序及编排器状态保持。

## LLM 引导的记忆替换

[`memory_update.py`](../src/agent/memory_update.py) 提供 `update_memory(state, potential_triplets, client)`，对应 FoG 第 3.3 节公式 11 至 12。通过图 8 提交原问题、历史记忆与合格新证据的精确并集。高召回原则由提示词表达：除非 LLM 确信无关，否则保留三元组；这不代表已测得召回率保证。

```python
from src.agent.memory_update import update_memory

# state is a QuestionMemory; potential contains eligible triples.
# client is a caller-owned LLMClient. This call incurs API usage.
updated_state, response = update_memory(state, potential, client)
state = updated_state  # Publish only after the whole call succeeds.
```

返回新的 `QuestionMemory` 与解析后的 `MemoryResponse`。新记忆严格等于 `response.selected_triplets` 的集合，不与旧记忆取并集。旧证据再次入选时可跨轮保留，被省略时移除，空选择可清空记忆。回复对象仍保留模型顺序、重复项、答案排名及子问题；记忆保存无序去重图。原问题与已有永久拒绝集合保持不变。

潜在输入沿用提示词的图格式，支持空图和生成器。编排器先物化一次，确保构造提示词和解析回复使用同一份证据。状态或图格式无效、潜在三元组与已有永久拒绝集合重叠时，在请求前抛出 `ValueError`；调用方须提供合格证据。完整校验通过后，已提交但未选中的三元组加入永久拒绝集合，规则见下文探索效率。

仅正常完成（`finish_reason: stop`）、未拒绝且三个输出字段全部有效的回复才生成新快照。无效响应封装、非法 JSON、越界选择或答案（包括仅存在于被省略输入中的答案）抛出 `ValueError`；传输和用量记录错误向上传递。所有路径均不修改输入快照。调用方负责客户端生命周期，并仅在成功后保存返回状态。传输重试和逐次用量记录仍由 `LLMClient` 负责；输出校验不增加重试或回退。试探性答案校验及语义边界见上文。

`tests/test_memory_update.py` 覆盖受控多轮保留、移除及清空记忆、生成器与原字符串、有序回复、已有拒绝状态保持、无效输入拦截、完整输出校验、拒绝及截断回复、失败时状态保持、问题隔离，以及模拟 HTTP 重试和用量记录集成。所有检查均离线运行。

### 受控多轮验证

TODO 2.2.1 由 `tests/test_memory_rounds.py` 通过 `manage_memory` 和真实 `LLMClient` 验证，仅模拟 HTTP 适配器。五轮固定输入依次累积 $1$、$2$、$3$ 条证据，在没有新候选时移除一条旧证据，最后通过空选择清空记忆。逐轮核对实际提交证据并集、原问题保持、精确选择后的记忆、有序答案及后续子问题、新快照独立性和全部历史快照未变；同时确认恰有 $5$ 条成功用量记录，每轮仅一次 HTTP 尝试。使用项目环境运行 `python -m unittest discover -s tests -p test_memory_rounds.py -v`。本项验证受控状态转换，不产生付费请求；真实模型选择质量与 C/E 流水线集成仍未验证。

### 受控拒绝与恢复验证

TODO 2.2.2 由 `tests/test_rejection_rounds.py` 通过公开 `manage_memory` 和真实 `LLMClient` 解析链路验证，仅模拟 HTTP 传输。默认及自定义阈值下各运行 $6$ 轮：低于和等于阈值的三元组连续两轮未提交，随后分数超过原阈值时成功入选；已提交但未选中的三元组即使反复以高分输入仍被屏蔽。后续移除历史记忆会累积永久拒绝，清空记忆及再执行一轮全部候选被屏蔽的调用也不会重置拒绝集合。逐轮独立核对实际提示词证据并集、精确记忆、累计拒绝与展开排除集合、可检索候选、有序输出及全部历史快照未变；每个阈值恰有 $6$ 条成功模拟用量记录。使用项目环境运行 `python -m unittest discover -s tests -p test_rejection_rounds.py -v`。本项不产生付费请求；真实模型质量和 C/E 集成仍未验证。

### 受控问题隔离验证

TODO 2.2.3 由 `tests/test_question_isolation.py` 通过公开 `manage_memory` 和同一个真实 `LLMClient` 验证，仅模拟 HTTP 传输。不同问题文本及相同文本的独立运行各执行 $9$ 次交错调用：两次运行对重叠证据作出不同选择，分别累积拒绝，并在另一运行更新状态后继续处理；两者已有非空记忆和屏蔽集合后，新建第三次运行，确认其状态从空集开始，仍可选中旧运行已拒绝的三元组。每次调用核对实际提交证据并集、原问题、有序输出、所有运行的记忆、拒绝与展开排除集合、可检索候选，以及全部历史快照未变；每个场景恰有 $9$ 条成功模拟用量记录。使用项目环境运行 `python -m unittest discover -s tests -p test_question_isolation.py -v`。隔离要求调用方为独立运行分别保留快照；本项未验证并发执行、真实 C/E 接线或模型质量，不产生付费请求。

## 探索效率

TODO 2.1.11 实现 FoG 第 3.3 节的两类排除。`state.excluded_triplets` 返回当前记忆与累计永久拒绝集合的不可变并集。调用方应在每次检索与展开边界、打分和继续展开之前调用 `state.filter_expansion_triplets(candidates)`。返回有序三元组列表，按首次出现顺序保留合格行，精确去重并保留原字符串。所有候选行均须通过格式校验，包括被排除的行；生成器只消耗一次，不修改状态及输入容器。排除按完整三元组身份判断，共享实体或反转边方向不会自动导致排除。

```python
# candidates are triples returned by caller-owned retrieval.
eligible = state.filter_expansion_triplets(candidates)
# Score eligible only; scores must stay positionally aligned with eligible.
potential = build_potential_subgraph(eligible, refined_scores)
state, response = update_memory(state, potential, client)
excluded_for_next_round = state.excluded_triplets
```

调用方须在每步展开应用该接口；本模块不实现检索器，也不修改成员 C 的打分逻辑。已吸收记忆不再重复展开，但每轮仍从 `state.memory_triplets` 提交到记忆管理提示词。潜在图若含当前记忆，提示词会去重；若含永久拒绝三元组，`update_memory` 在请求前报错，防止遗漏上游过滤后重新引入。

对完整有效的 LLM 回复，设实际提交并集为 $U = G^{\mathrm{mem}} \cup G^{\mathrm{pot}}$，选中集合为 $S$，则新记忆为 $S$，永久拒绝集合更新为 $R' = R \cup (U \setminus S)$。新增证据和历史记忆只要已提交但未选中，均永久屏蔽。将全部省略输入映射为“显式拒绝”是用户确认的复现假设，论文未明确规定该映射。格式错误、拒答、未完成及失败调用均不产生新拒绝。永久拒绝在本次问题运行内累积，记忆清空后仍保留；即使问题文本相同，不同运行也独立维护屏蔽状态。仅因阈值未达标而未提交的三元组不属于本次 $U$，不会因此被拒绝，后续仍可检索恢复。

`tests/test_exploration_efficiency.py` 覆盖候选顺序与精确身份、格式校验、多轮屏蔽累积、历史记忆继续入提示词、拒绝输入拦截、阈值隐藏证据恢复、失败状态保持及问题隔离。既有更新测试也验证旧记忆移除和空选择产生永久拒绝。检查均离线进行；实际检索循环接线仍留待集成。

## 阈值隐藏证据的容错

TODO 2.1.12 通过已有潜在子图、展开过滤及记忆更新接口实现。重打分结果 $s_t \leq \tau_p$ 的三元组不进入本轮潜在图；若它也不在历史记忆中，就不会提交给 LLM，也不会产生永久拒绝。暂时隐藏不创建额外状态或持久候选黑名单。历史记忆无论当前检索分数如何仍会提交给 LLM，若在有效选择中被省略，则按上文规则永久拒绝。

下一轮将 `response.subsequent_question` 交给检索，应用当前状态的展开排除集合，重新获取按位置对齐的重打分结果，再调用 `build_potential_subgraph`。此前隐藏的证据即使连续多轮低分，仍保持候选资格；新分数严格超过同一阈值后可进入潜在图，再由 LLM 选入记忆。恢复依赖重新检索和重打分，不会自动插回、降低阈值或重试上一轮 LLM 请求。已提交且被拒绝的三元组即使后续检索可能赋予更高分，也仍被排除。

空潜在图合法：记忆管理仍提交历史记忆；历史记忆也为空时，提交空 Knowledge 数组。答案未确定时，回复须按解析器约定提供澄清子问题。`tests/test_fault_tolerance.py` 验证子问题驱动的重新检索与打分、默认及自定义阈值下的低分和相等边界、初始空图后的恢复、连续多轮隐藏、历史证据保留及永久拒绝对照，并逐轮核对实际提示词中的证据。测试属于成员 D 接口的受控离线串联验证，未验证真实 C/E 检索打分接线或真实模型质量。

## 独立答案排序

论文已要求多答案按证据支持程度排序，因此排序是模块的默认功能。公开 [`manage_memory`](../src/agent/memory_management.py) 在默认的证据决策、最终选择与作答之后，对不少于两个已有答案调用 [`answer_ranking.py`](../src/agent/answer_ranking.py)，仅根据原问题、实际选中三元组和实际已有答案按支持强弱排序。为提高正确率，排序及展示顺序的生成提取为一次单独的 LLM 调用。零或单答案自动跳过排序。

接口不提供关闭排序的业务开关；`prompt_variant` 选择排序之前的决策／选择流程，即使指定 `paper` 也仍对多个答案执行独立排序。旧名 `manage_memory_ranked` 是 `manage_memory` 的兼容别名。底层 `update_memory` 负责排序前的决策、选择、解析和状态转换，不作为完整 MSM 接口替代。

这是偏离论文单次 MSM 联合输出的实现增强。排序提示词沿用冻结原型全文，未继续调优；不传检索分数、金标准或未选中证据，不使用城市顺序、关系权重表或确定性后处理。第二阶段必须正常完成、未拒答，返回仅含 `predict_answer` 的标准 JSON；候选原串及多重集必须完全相同，禁止增删、改名或去重。只允许改变答案顺序，不改变选中证据、后续问题、记忆或永久拒绝，不能补救第一阶段漏答案或漏路径。

所有阶段共用调用方持有的 `LLMClient`，每次请求及传输重试独立记入 `logs/llm_usage.csv`。默认多答案每轮共 $3$ 次逻辑调用，最多 `3 * (max_retries + 1)` 次 HTTP 尝试，当前最多 $9$ 次；零或单答案为 $2$ 次逻辑调用、最多 $6$ 次 HTTP 尝试。显式单次选择变体相应少一次逻辑调用。任一阶段传输、记账或校验失败均不返回部分 `MSMResult`，只有整体成功后才发布 `result.state`。客户端生命周期仍由调用方管理。

2026/11/09 完成 $14$ 次两阶段筛查，最终全部通过；随后完成 $70$ 次独立真实运行、$136$ 次 GPT-4o-mini 请求，无异常或重试。直接关系两种强弱分配的全部六种排列及三次重复共 $36/36$ 排名正确，等支持 $6/6$、单答案 $2/2$ 通过；多跳排序由第一阶段 $12/24$ 提高至 $23/24$，其中另有 $7/24$ 漏选书籍到作者的必要边，完整路径保留为 $17/24$。空图还出现一次依赖未解析作者的追问。多跳排序失败的第二阶段提示词与本轮四次成功调用逐字相同，存在输出波动；路径漏选及澄清问题属于当前模型和提示设计下的语义质量限制，不认定为已消除，也不将全部失败归因为随机波动。

用户已接受上述工程效果与剩余模型语义限制，决定停止调优、完成共享接口并关闭排序排查项；原“全部排列及重复零失败”标准不再作为本项关闭条件。这不表示任意图谱输入均排序正确，也不改变既有永久拒绝规则。其他条件推理、正确移除旧记忆及 TODO 2.3.5 的整体交付关卡仍单独待办。

默认接口经已有全部 $70$ 次真实运行的原始回复免费重放，核对 $136$ 个阶段的提示词、答案原串、选择、问题及前后状态，与私有原型结果完全一致；重放验证迁移一致性，不是新增真实质量验收。`tests/test_answer_ranking.py` 覆盖默认排序、兼容别名、各提示变体、自定义阈值、跳过排序、重复及原串保持、证据隔离、严格多重集、两阶段原子失败和真实客户端模拟 HTTP 重试记账；`tests/test_part_d_validation.py` 核对全部轮次接线及 $25$ 次逻辑调用上限，受控预期回复实际执行 $15$ 次。全部 $100$ 项离线测试通过，默认化没有新增 API 用量或修改私有配置。

完整独立报告保留在私有 `tmp/tmp_ranking_holdout_20261108T191825684477Z.json`，审计汇总为 `tmp/tmp_ranking_holdout_live_audit.json`；历史提示词对照与筛查报告继续保留在私有 `tmp/` 和 Git 历史中。独立运行的用量已核对：MSM 输入 $32415$、输出 $5709$ tokens，排序输入 $14492$、输出 $882$ tokens，对应共享日志第 $163$ 至 $298$ 行共 $136$ 条成功记录。按运行前清单每百万输入 $0.15$、输出 $0.60$ 美元的价格口径，额外排序估算 $0.002703$ 美元、两阶段合计 $0.01099065$ 美元，实际账单待核对。

## MSM 重打分轮次接口

[`memory_management.py`](../src/agent/memory_management.py) 提供 `manage_memory(state, triples, scores, client, potential_threshold=0.7)`，完成 TODO 2.1.13。成员 C 传入反馈传播后按位置对齐的展开三元组和重打分分数；成员 E 管理问题快照、检索循环和客户端生命周期。输入格式、有限数值校验、原字符串保持、严格阈值及张量显式转换沿用上文潜在子图约定。若从 YAML 加载阈值，应显式传入；接口不读取文件或配置。

```python
from src.agent.memory_management import manage_memory
from src.agent.memory_state import QuestionMemory

state = QuestionMemory(original_question)  # Once per independent question run.
# Repeat these steps in E's loop; retrieval and C's scorer are caller supplied.
candidates = state.filter_expansion_triplets(retrieve(sub_questions))
scores = refine_scores(candidates)  # Aligned 1-D Python scalar sequence.
result = manage_memory(state, candidates, scores, client, potential_threshold=0.7)
state = result.state  # Publish only when the entire call succeeds.
sub_questions = result.response.subsequent_question
```

示例中的检索和打分函数表示调用方依赖，并非已实现函数。调用入口会产生 LLM 用量，空输入或空潜在图也会请求。`manage_memory` 默认包含上述答案排序阶段，调用方无需选择其他入口或传排序开关；参数及 `MSMResult` 返回类型保持不变。传输重试和用量记录仍由 `LLMClient` 负责。

| 返回字段 | C/E 用途 |
| --- | --- |
| `result.state.memory_triplets` | 更新后的不可变证据图 |
| `result.response.predict_answer` | 按模型排名保留的试探性答案元组 |
| `result.response.subsequent_question` | 下一轮有序检索问题 |
| `result.state.excluded_triplets` | 当前记忆与永久拒绝并集；每步展开均须排除 |
| `result.state.rejected_triplets` | 问题级永久屏蔽集合 |
| `result.response.selected_triplets` | 保留模型顺序及重复项的有效选择 |

`MSMResult` 是冻结容器，包含 `state: QuestionMemory` 和 `response: MemoryResponse`，复用现有不可变约定，不重复维护状态。过滤前校验全部三元组和分数，包括已有记忆、永久拒绝及低分行。先按对齐行比较阈值，再精确去重：同一三元组任一出现位置超过阈值即可入选潜在图。入口防御性移除新潜在输入中的已有记忆和永久拒绝；历史记忆仍独立提交，即使当前未检索到或分数较低。底层 `update_memory` 则对含永久拒绝的潜在输入直接报错。E 仍须在检索和每步展开之前过滤，以免浪费展开及打分；对已打分列表过滤时，必须同步处理对应分数，不能破坏对齐。

完整输出校验通过后才返回新结果。输入或输出无效抛出 `ValueError`，传输和记账错误原样传递；旧快照不被修改，不增加业务重试或服务回退，也不关闭客户端。E 在异常时保留旧状态，并负责停止条件和轮次限制；接口不根据试探性答案或空问题元组自动终止循环。永久省略判定和阈值暂时隐藏沿用上文规则。`tests/test_memory_management.py` 覆盖重复行分数对齐、已有屏蔽、全部输出字段、空图与自定义阈值、多轮恢复及移除、问题隔离和失败状态保持。真实 C/E 接线与模型质量仍待集成验证。

## LLM 异常与状态保持

TODO 2.1.14 复用现有 `LLMClient` 重试策略及 `manage_memory` / `update_memory` 的原子更新边界。每次请求采用配置的连接及读取超时；超时、连接失败、HTTP 429 和 HTTP 5xx 最多额外尝试 `max_retries` 次，等待时间依次为 $1$、$2$、$4$ 秒，此后最高 $8$ 秒。耗尽后原样传递 `requests.Timeout`、`requests.ConnectionError` 或 `requests.HTTPError`。其他 HTTP 错误、HTTP JSON 非法、回复外层结构或业务字段无效均直接报错，不增加业务重试、JSON 修复或服务回退。非法 JSON 和输出校验失败抛出 `ValueError`，包括选中三元组不属于实际提交并集、答案不精确匹配选中三元组头尾实体串。

任何失败均不返回 `MSMResult`，不修改输入快照的记忆、永久拒绝及展开排除状态；只有完整回复通过校验后，已提交但未选中的证据才产生拒绝。E 仅在成功后执行 `state = result.state`；异常时保留旧快照，自行决定停止或再次显式调用。状态保持不撤销 API 用量：每次传输尝试仍各记一行 CSV。HTTP JSON 非法记为错误；HTTP JSON 对象有效、但模型内容随后校验失败时，保留 HTTP 成功记录及返回的用量元数据。记账失败抛出 `OSError`，不自动重试。

`tests/test_memory_failures.py` 使用真实 HTTP 响应解析及重打分轮次入口，仅替换网络适配器和等待时钟。测试覆盖超时与限流在额外重试 $0$ 次和 $2$ 次时耗尽、超时及 429 后以相同证据恢复、逐次记账、外层与内容 JSON 非法、外层结构无效、编造及隐藏或已拒绝证据越界、未知及未选中历史实体作答、非空旧状态保持，以及校验失败后的再次显式调用成功。使用模拟密钥及私有临时 CSV，不产生付费请求，不验证真实模型质量或 E 的异常决策。

TODO 2.2.4 在同一测试文件补充 $6$ 个“成功、失败、恢复”连续场景：超时与 HTTP 429 重试耗尽、外层及内容 JSON 非法、编造三元组、未知答案实体。每个场景先通过 `manage_memory` 建立记忆和永久拒绝，再确认失败赋值后调用方仍持有同一快照，可检索候选不变。显式恢复调用可选入失败轮候选及重新达标的阈值隐藏证据，保留已有拒绝，仅在成功后将移除的历史证据加入拒绝。逐次核对完整请求及超时参数、重试上限、有序输出、历史快照不变，以及每次请求对应一条仅含元数据的 CSV 记录。使用项目环境运行 `python -m unittest discover -s tests -p test_memory_failures.py -v`。全部场景离线执行，真实模型验证仍为独立交付关卡。

## 后续子问题

TODO 2.1.9 对应 FoG 第 3.3 节公式 11 和附录 C：图 8 要求 LLM 在选中证据无法确定答案时生成 $1$ 至 $3$ 个澄清问题。`update_memory` 通过 `response.subsequent_question` 返回这些有序字符串元组，表示下一轮检索的 $Q'_{\mathrm{sub}}$。调用方应将其传给检索模块，无需重新执行初始问题分解；后续记忆管理仍使用 `state.original_question` 中的原问题。检索器和推理循环接线留待集成。

解析器将空 `predict_answer` 视为模型声明答案未确定，此时必须返回 $1$ 至 $3$ 个问题，输入图或选中图为空时也适用。有试探性答案时允许 $0$ 至 $3$ 个问题，因为部分答案仍可能需要补充证据，不强制两字段互斥。这是显式校验约定，不是语义充分性检验。缺失证据和问题措辞由 LLM 判断；代码不证明相关性、不按图大小推断充分性、不生成替代问题，也不额外调用模型。

子问题保留原字符串、空白、Unicode、顺序及重复项。它们是自然语言检索问题，无须完全匹配输入实体串或使用特定结尾标点。空白或非字符串项、超过 $3$ 个问题、无答案且无问题时，均在发布新快照前抛出 `ValueError`，保留旧记忆和永久拒绝状态，不增加业务重试。测试覆盖有无答案时的数量边界、原字符串保持、无效输出的状态保持，以及通过模拟检索传递子问题并在第二轮获得答案的受控流程。离线检查不代表已验证真实模型的问题质量。

2026/11/09 运行 `python scripts/validate_part_d.py --suite clarification_diagnostic`，通过公开 MSM 接口，对 `paper` 和默认 `retrieval_questions` 各检查空图、只有作者关系及完整路径，共 $6$ 次真实 GPT 调用。每次独立创建状态并使用相同的受控三元组和 $0.95$ 分数，严格阈值仍为 $0.7$。该组不属于默认 `all`；`--list-cases --suite clarification_diagnostic` 可免费预览，默认排序后，该组保守最多 $12$ 次逻辑调用、计入当前重试配置最多 $36$ 次 HTTP 尝试。

| 输入证据 | 论文原版 | 检索增强版 |
| --- | --- | --- |
| 空图 | 查询作者，但还追问出版年份及是否有作者出生地信息 | 仅问 `What is the name of the author of The Glass Orchard?`，无选择或答案 |
| 只有作者关系 | 保留作者关系，问 `Where was Mira Vale born?` | 保留作者关系，问 `What city was Mira Vale born in?`，不重复查作者，无答案 |
| 作者及出生地完整路径 | 保留两条证据，回答 `Linden City`，无追问 | 保留两条证据，回答 `Linden City`，无追问 |

全部通过严格解析，旧快照保持、状态规则成立，无新增永久拒绝。实际模型为 `gpt-4o-mini-2024-07-18`，无传输重试，新增 $6$ 条成功用量记录，共输入 $1862$、输出 $298$ tokens；未更改私有配置。私有报告为 `tmp/tmp_part_d_validation_20261108T180609850934Z.json`，自动语义标记仍为待审；上表是人工审阅结果。按成员 D 当前的“模拟数据与检索打分输入、真实 LLM 调用”验证范围，本次三种输入均通过，空图澄清偏题问题已解决，从问题列表删除。C/E 入口尚未实现，合并后的实际检索与整体运行效果由成员 E 验证，不作为本项关闭的前提；本次结果不代表真实召回收益或其他问题上的稳定性。其他条件推理问题及 TODO 2.3.5 继续待办。

---

## 问题排查与解决

以下记录 TODO 2.3.5 发现的问题及已完成实验，依据为 [GPT 验收记录](#gpt-4o-mini-验收尝试) 和当前代码。后续工作统一按[分离条件决策与记忆发布的修改计划](#修改计划分离条件决策与记忆发布)执行。涉及图 7、图 8 的提示词增强，需与论文原版分开对照。

### 条件未确定就作答，条件确定后却不作答

问题：不知道出版社时，模型直接回答 `Linden City`；给出 `Bronze Press` 和另一人的出生地后，却继续追问出版社。代码确认这些证据已提交，明确“只有一个出版社、未知时先澄清”也未解决问题。

出版社、回答人物与出生地的小问题对照，以及单次请求中的条件判断指令对照均已执行，结果见下文历史记录。后续按[分离条件决策与记忆发布的修改计划](#修改计划分离条件决策与记忆发布)直接修改完整 MSM 流程，不再重复这些独立诊断。不能仅凭答案实体出现在图中就判定推理正确。

### 漏选有用证据后，后续检索无法补救

用例：根据《The Glass Orchard》的出版社，决定回答谁的出生地。如果由 `Silver Press` 出版，回答 `Mira Vale` 的出生地；否则，回答 `Nolan Reed` 的出生地。出版社只是选择回答对象的条件，本题未规定两人是该书作者，也未规定两人与出版社有其他关系。当前维护版本还明确“书籍只有一个出版社，出版社未知时先询问，再作答”；原始失败版本尚未包含这两项说明。

证据分两轮提供，预期行为如下：

1. 第一轮只提供 `(Mira Vale, born_in, Linden City)`。此时出版社未知，尚不能确定应回答谁，因此应暂不作答、追问出版社，并保留这条出生地证据；如果后续查到出版社是 `Silver Press`，它就能用于回答。
2. 第二轮补充 `(The Glass Orchard, published_by, Bronze Press)` 和 `(Nolan Reed, born_in, Cedar City)`。在单出版社前提下，应走“否则”分支，保留出版社和 `Nolan Reed` 的出生地证据，回答 `Cedar City`；此时 `Mira Vale` 的出生地已与当前答案无关，可以移除。

问题：原条件用例第二轮返回空选择，出版社和两人的出生地全部被永久屏蔽，下一轮因此提交空图。这符合当前“已提交但未选中即拒绝”的规则，但会把一次语义漏选变成后续无法恢复的证据丢失；字段和实体校验发现不了这种错误。

#### 排查结果

2026/11/09 完成 $27$ 次真实 GPT-4o-mini 调用：事实小问题 $3$ 次、原始与当前条件问题的完整图对照各 $3$ 次、当前问题的默认与保留增强版各 $3$ 组连续三轮。全部经由公开 `manage_memory` 和生产严格解析；检索输入受控，分数均为 $0.95$，严格阈值保持 $0.7$。每轮只发布成功返回的状态，校验失败则保留旧状态，并继续预先安排的诊断轮次；没有业务重试、输出修复或解除屏蔽。第三轮强制高分重输三条证据，仅检查过滤行为，不代表实际检索器已执行模型的后续问题。

先验证提示词构造：同一原问题下，把 Mira 出生地放入历史记忆、另两条放入新检索，与把三条全部放入新检索，生成的完整提示词逐字相同。以下表现不能归因于证据的新旧来源。当前问题包含单出版社及未知时先询问两项说明；原始问题不包含这两项说明。两者都使用当前默认 `retrieval_questions` 提示词，因此原始问题对照不是对历史论文原版提示词的精确复跑。

| 对照 | 真实结果与状态 |
| --- | --- |
| 完整图下分别询问出版社、Mira 出生地、Nolan 出生地 | $3/3$ 正确回答 `Bronze Press`、`Linden City`、`Cedar City`，各保留对应事实。基本事实抽取在这三个对照中正确。 |
| 原始条件问题，三条证据一次提交，独立重复 $3$ 次 | $3/3$ 保留三条并回答 `Cedar City`，无新增拒绝。本轮未复现历史完整图空选择，不能据此否定此前失败。保留无关分支也不构成正确移除旧记忆的证据。 |
| 当前条件问题，同样三条证据，独立重复 $3$ 次 | 原始回复均保留三条，却全部不作答，仍询问已给出的出版社。仅 $1$ 次结构通过，另 $2$ 次因后续问题为嵌套数组而失败；失败不发布选择或拒绝状态。 |
| 当前问题，默认版三组连续轮次 | 首轮 $3/3$ 原始回复均选择 Mira 出生地、暂不作答并询问出版社，但后续问题均为嵌套数组，全部被拒绝，所以第二轮实际从空记忆继续。第二轮 $1/3$ 返回合法空选择并追问出版社，另外 $2$ 次选择两条新事实但格式失败。该合法空选择将出版社和 Nolan 出生地永久拒绝。第三轮高分重输时，这两条确实被过滤，只提交尚未成功进入记忆或拒绝集的 Mira 出生地；第三轮回复仍格式失败，既有拒绝保持。其余两组第三轮提交完整图，均格式失败。 |
| 仅增加“条件未知时保留可能有用的分支证据”，同样三组三轮 | 首轮 $3/3$ 原始回复仍选择 Mira 出生地，但均因嵌套问题数组失败，未形成可供下一轮继承的记忆。第二轮原始回复 $3/3$ 选择出版社和 Nolan 出生地，其中 $2$ 次结构通过、$1$ 次格式失败；第三轮原始回复均选择三条，其中 $2$ 次通过、$1$ 次格式失败。所有轮次仍不作答，并持续询问出版社；本组没有新增永久拒绝，但不能据此判定增强有效。 |

增强版只在 `### Real Input` 前增加一段选择指令：条件未知时保留仍可能支持任一分支的已有证据，不因暂时不能作答就丢弃；未增加条件判断步骤、修改问题、修改解析器或放宽拒绝规则。该指令只存在于私有诊断包装器中，未推广到生产提示词。两版第二轮实际提交的都是出版社和 Nolan 出生地；默认版出现一次合法空选择，增强版未出现，但每版仅 $3$ 次，且所有首轮均因格式失败未发布记忆，不能证明增强稳定改善选择，也没有验证它能跨轮保住首轮候选证据。

结论：事实小问题正确时，完整条件问题仍可能不使用已给出的事实，并出现合法空选择；永久拒绝机制会忠实地把这次语义漏选变成后续不可检索的证据丢失。本轮明确复现的是两条新证据被拒绝及高分重输无效，未复现历史三条全部拒绝、后续空图的完整路径。另有独立格式问题：$15/27$ 次回复的 `subsequent_question` 是嵌套数组，触发 `question_array`；其原始选择只能用于观察模型输出，不能计入成功记忆更新或永久拒绝。全部失败保持旧快照，说明严格校验和状态原子性按预期工作，但它们不能发现合法空选择的语义错误。

本项排查完成，问题尚未解决。不能把修正数组类型等同于修复条件推理或证据选择，也不能据本轮勾选正确旧记忆移除或 TODO 2.3.5。

实际返回模型均为 `gpt-4o-mini-2024-07-18`；$27$ 次逻辑调用各对应一次成功 HTTP 请求，无传输重试，均未触发多答案排序。HTTP 成功与上述业务校验失败分别统计。新增 [`logs/llm_usage.csv`](../logs/llm_usage.csv) 的 $27$ 行，共输入 $13023$、输出 $1616$ tokens；已逐条核对真实回复的模型及用量。私有入口为 `tmp/tmp_evidence_diagnosis.py`，完整提示词、成功 HTTP 原始回复、实际提交图及前后状态保存在 `tmp/tmp_evidence_diagnosis_20261109T034059678854Z.json`。`tmp/tmp_evidence_audit.py` 免费重放全部 $27$ 次事务，精确核对提示词、校验错误及状态，并生成 `tmp/tmp_evidence_audit.json`；$12$ 次业务通过、$15$ 次格式失败与日志一致。全部 $100$ 项离线回归通过，生产实现和私有 OpenAI 配置未改。

#### 字符串数组对照与跨轮保留复验

2026/11/09 执行字符串数组对照与跨轮保留复验，共 $24$ 次真实 GPT-4o-mini 调用，均通过公开 `manage_memory` 和原严格解析器。先将原默认提示词与仅明确 `subsequent_question` 为字符串数组的版本交替独立运行，各 $3$ 次；只有类型明确版全部通过后，才比较“仅明确类型”和“明确类型并保留可能有用的分支证据”，各 $3$ 组连续三轮。原问题、三元组、$0.95$ 分数和严格阈值 $0.7$ 不变；两种增强均只在私有包装器中注入，没有修改生产提示词、解析器、永久拒绝规则或私有 OpenAI 配置。

类型对照仅在 `### Real Input` 前加入以下指令，保留增强则继续使用上一轮的选择指令，不加入条件推理步骤：

```text
### Question Array Type (diagnostic clarification)
subsequent_question must be a JSON array of strings, for example
["Who published The Glass Orchard?"]. Never wrap a question in an inner array.
Use [] when there are no questions. Keep all existing semantic instructions.
```

| 对照 | 真实结果与状态 |
| --- | --- |
| 独立首轮，原默认提示词 | $3/3$ 返回嵌套问题数组，触发 `question_array`。原始选择包含 Mira 出生地，但没有发布记忆或拒绝状态。 |
| 独立首轮，仅明确数组类型 | $3/3$ 结构通过，保留 Mira 出生地、不作答，只问 `Who published The Glass Orchard?`，无新增拒绝。 |
| 连续三轮，仅明确类型 | $9/9$ 结构通过。三组首轮均成功建立 Mira 出生地记忆，第二轮均实际提交继承的出生地及两条新证据，并保留全部三条；第三轮仍保留全部三条。每轮都不作答，持续询问出版社，无新增拒绝。 |
| 连续三轮，明确类型并增强保留 | $9/9$ 结构通过，选择、答案、后续问题和状态与仅明确类型的对应轮次相同。首轮候选确实跨轮保住，但本轮没有显示保留增强的额外收益。 |

结论：明确字符串数组类型，在本次受控用例中消除了格式失败，使两版都能从真实成功的首轮快照继续，补上了上一轮缺失的跨轮保留验证。该结果不证明任意问题上的格式稳定性，也不能把未观察到漏选视为永久证据丢失问题已经解决。两版在条件确定后仍重复询问已给出的出版社，未回答 `Cedar City`，也未移除无关的 Mira 出生地；正确旧记忆移除和条件推理仍未通过。第三轮虽高分重新提供三条，但它们全部已在记忆中，没有本轮新拒绝可供验证；这是记忆过滤探测，不能再次计作“拒绝后无法恢复”的复现。没有解除屏蔽、手工恢复证据、修复回复或业务重试。

实际返回模型均为 `gpt-4o-mini-2024-07-18`；$24$ 次逻辑调用对应 $24$ 次成功 HTTP 请求，无传输重试，未触发多答案排序；其中 $21$ 次业务通过、$3$ 次格式失败。新增 [`logs/llm_usage.csv`](../logs/llm_usage.csv) 的 $24$ 行，共输入 $12870$、输出 $1387$ tokens，已逐条核对返回模型和用量。私有入口 `tmp/tmp_evidence_format.py`，运行命令为项目 Python 执行 `tmp/tmp_evidence_format.py`；完整提示词、原始成功 HTTP 回复、提交图、前后状态与核对结果保存在 `tmp/tmp_evidence_format_20261109T035108450775Z.json`。同一入口在付费阶段结束后免费重放全部 $24$ 次事务，精确匹配提示词、有效输出、错误和快照，并核对 CSV 用量；全部 $100$ 项离线回归通过。此次仅交付诊断结果及用量，未改生产实现。

#### 独立条件判断对照

2026/11/09 执行 $18$ 次独立 MSM 事务：固定上一轮的字符串数组指令，对未知出版社、`Silver Press`、`Bronze Press` 三种证据状态，各比较仅明确类型与另加条件判断指令，每版独立重复 $3$ 次，交替执行。每次均从空记忆和空拒绝集合开始；未知组提供两人的出生地，已知组再提供对应出版社。原问题、默认 `retrieval_questions` 提示词、$0.95$ 分数及严格阈值 $0.7$ 不变，不加入保留增强。私有包装器只在选择提示词的 `### Real Input` 前额外注入以下指令：

```text
### Conditional Evaluation (diagnostic instruction)
First determine the publisher from the supplied evidence, then evaluate the
condition in the original question. If the publisher is unknown, do not answer;
ask for the publisher. If it is known, use the birthplace for the applicable
branch to answer, and do not ask again for the known publisher.
```

| 证据状态 | 仅明确类型，独立重复 $3$ 次 | 再加条件判断，独立重复 $3$ 次 |
| --- | --- | --- |
| 出版社未知，仅两条出生地 | $3/3$ 保留两条，不作答，只问 `Who published The Glass Orchard?` | 同左，$3/3$ |
| 出版社为 `Silver Press` | $3/3$ 保留三条，回答 `Linden City`，不追问 | 同左，$3/3$ |
| 出版社为 `Bronze Press` | $3/3$ 保留三条，不作答，仍问 `Who published The Glass Orchard?` | 同左，$3/3$ |

全部 $18/18$ 通过严格解析，原快照不变，记忆、拒绝及展开排除状态符合规则。未知组的候选保留和澄清通过；已知组均保留出版社及正确分支需要的出生地，但同时保留无关分支。已知条件下答案及不重复追问仅 `Silver Press` 通过，`Bronze Press` 全部失败；每版综合预期行为通过 $6/9$，条件判断指令没有观察到额外收益。选择中出现出版社只能证明事实被保留，不能证明模型正确识别或使用了条件。输出未暴露内部判断步骤，因此本轮定位到两种已知出版社的行为差异，尚不能确定失败发生在出版社识别、否则分支判断还是回答决策。

独立对照未全部通过，问题保持开放；本轮未进入连续移除验证，未推广条件判断或保留增强。没有新增永久拒绝，全部事务彼此独立，本轮不覆盖跨轮恢复、正确旧记忆移除或拒绝后高分重输；不能因此关闭此前证据丢失问题或勾选 TODO 2.3.5。

实际返回均为 `gpt-4o-mini-2024-07-18`；$18$ 次事务各一次成功 HTTP 请求，无传输重试、业务重试、输出修复或多答案排序。新增 [`logs/llm_usage.csv`](../logs/llm_usage.csv) 共 $18$ 行，输入 $10044$、输出 $1215$ tokens。私有入口为 `tmp/tmp_evidence_condition.py`，运行命令为项目 Python 执行该文件；完整指令、请求、原始成功 HTTP 回复、提交图、前后状态及审计保存在 `tmp/tmp_evidence_condition_20261109T040052050573Z.json`。全部 $18$ 次事务免费精确重放通过，核对提示词、有效输出及快照，CSV 返回模型和逐次用量与原始回复一致；全部 $100$ 项离线回归通过。生产实现、严格解析器、永久拒绝规则和私有 OpenAI 配置均未变。

#### 出版社、回答人物与出生地隔离诊断

2026/11/09 执行 $18$ 次独立 MSM 事务。在 `Silver Press`、`Bronze Press` 两种完整图上，分别询问出版社、条件下应回答谁的出生地、对应具名人物的出生城市，各重复 $3$ 次。每次从空记忆和空拒绝集合开始；两图均包含相同的两条出生地事实，仅出版社不同。通过公开 `manage_memory`，保持默认 `retrieval_questions`、私有字符串数组类型说明、$0.95$ 分数及严格阈值 $0.7$；未添加条件判断或保留增强。出版社问题为 `Who published The Glass Orchard?`，出生地问题为 `In which city was Mira Vale born?` 或 `In which city was Nolan Reed born?`。人物问题逐字如下，两图使用同一问题：

```text
The Glass Orchard has exactly one publisher. If it was published by Silver Press, we need Mira Vale's birthplace; otherwise, we need Nolan Reed's birthplace. Whose birthplace should we answer with? Return only the person's name as the answer. If the publisher is unknown, ask for the publisher before answering.
```

| 小问题 | Silver 图，独立重复 $3$ 次 | Bronze 图，独立重复 $3$ 次 |
| --- | --- | --- |
| 出版社抽取 | $3/3$ 只保留出版社事实，回答 `Silver Press`，不追问 | $3/3$ 只保留出版社事实，回答 `Bronze Press`，不追问 |
| 条件下应回答的人物 | $0/3$ 符合预期；均保留三条，却回答 `Linden City` 而非 `Mira Vale`，不追问 | $0/3$ 符合预期；均保留三条、不作答，继续询问已给出的出版社，而非回答 `Nolan Reed` |
| 具名人物出生城市 | $3/3$ 只保留 Mira 出生地，回答 `Linden City`，不追问 | $3/3$ 只保留 Nolan 出生地，回答 `Cedar City`，不追问 |

全部 $18/18$ 通过严格解析及状态检查，旧快照保持不变；原始 HTTP 回复、有效输出、选择及追问已逐项审阅。出版社与城市抽取合计 $12/12$ 符合预期，条件人物问题 $0/6$。Silver 返回的城市属于图中实体且有事实支持，因此可以通过实体校验，却不满足人物名回答要求；这不直接等于分支选择错误。Bronze 在直接事实问题中正确抽取出版社和城市，在条件人物问题中仍重复询问出版社，说明本轮故障仍集中于条件问法下的回答决策，不能据此推断其内部比较步骤。人物问题虽然明确要求人物名，但仍包含两处出生地目标及未知时先询问的说明，措辞影响尚未隔离，不能把其失败完全归因于逻辑能力。

人物组全部保留三条，没有新增拒绝。事实组各只选择直接相关的一条，将其余两条永久拒绝：共 $12$ 个独立事务、$24$ 个问题内拒绝项。这些省略与各自的事实小问题相符，不是完整条件题中的必要证据漏选，也不是旧记忆移除；拒绝集合彼此隔离，没有传递给其他小问题。本轮未执行拒绝后重输、连续轮次或恢复，不能据此关闭证据漏选或正确旧记忆移除问题。生产实现、严格解析器、永久拒绝规则和私有 OpenAI 配置均未变，TODO 2.3.5 保持待办。

实际模型均为 `gpt-4o-mini-2024-07-18`，$18$ 次事务各一次成功 HTTP 请求，无传输或业务重试、输出修复或多答案排序。新增 [`logs/llm_usage.csv`](../logs/llm_usage.csv) 共 $18$ 行，输入 $9141$、输出 $890$ tokens，已逐条核对返回模型和用量。运行命令为项目 Python 执行私有入口 `tmp/tmp_evidence_probes.py`；完整问题、预期、请求、原始成功 HTTP 回复、提交图、前后状态及审计保存在 `tmp/tmp_evidence_probes_20261109T040910813692Z.json`。全部 $18$ 次事务精确免费重放通过，提示词、有效输出及快照与原运行一致；全部 $100$ 项离线回归通过。本次对照已完成，独立诊断关卡未通过，仅交付诊断记录和用量。

#### 人物目标措辞对照

2026/11/09 执行 $12$ 次独立 MSM 事务。在相同的 `Silver Press`、`Bronze Press` 完整图上，交替比较上一节的出生地人物问题与纯人物选择问题，每种图每版重复 $3$ 次。仅改变原问题措辞；沿用私有字符串数组说明、默认 `retrieval_questions`、$0.95$ 分数、严格阈值 $0.7$ 和公开 `manage_memory`，每次从空记忆及空拒绝集合开始。未增加条件判断、保留增强或正确分支提示。纯人物问题逐字如下：

```text
The Glass Orchard has exactly one publisher. Which person is selected by this rule: choose Mira Vale if The Glass Orchard was published by Silver Press; otherwise choose Nolan Reed? Answer with only the selected person's name.
```

| 问题与证据图 | 原始回复及严格事务结果 |
| --- | --- |
| 旧问题，Silver | $3/3$ 保留三条、回答城市 `Linden City`，无追问；结构通过，但人物目标错误，语义通过 $0/3$ |
| 纯人物问题，Silver | $3/3$ 原始回答均为 `Mira Vale`，无追问。其中 $2$ 次只选择出版社关系，因 `answer_membership` 失败；另 $1$ 次选择三条并成功发布。完整事务通过 $1/3$ |
| 旧问题，Bronze | $3/3$ 保留三条、不作答，重复询问已知出版社；结构通过，语义通过 $0/3$ |
| 纯人物问题，Bronze | $3/3$ 只选择出版社关系、不作答，重复询问已知出版社；结构通过，语义通过 $0/3$；各永久拒绝两条出生地事实 |

已逐项审阅全部原始成功 HTTP 回复、选择、答案和追问。严格事务 $10/12$ 通过，$2$ 次校验失败均保持空旧状态，没有发布原始选择或拒绝；所有旧快照及成功事务的记忆、拒绝和排除规则符合预期。两版完整语义通过分别为 $0/6$、$1/6$。纯人物措辞使 Silver 的原始答案从城市变为正确人物，但未解决 Bronze 的重复追问，也未形成可靠的可发布结果；不能将 $3$ 次原始正确人物回答记为 $3$ 次接口验收成功，亦不能推断内部条件比较步骤。

本轮暴露上一轮诊断预期的一处限制：人物出现在提交图中并不足以通过答案校验，生产解析器要求答案实体出现在本次 `selected_triplets` 中。纯人物题可依据问题中的规则及出版社选择人物，出生地关系本身不参与该人物判断；仅选择出版社却回答人物仍违反现有接口契约。本轮保留该失败，不放宽解析、不强制选择出生地、不将失败原始输出发布为记忆。Bronze 纯人物组新增的 $6$ 个问题内拒绝项仅涉及该人物小问题未选中的出生地，不直接证明完整城市条件题漏掉必要证据；没有跨问题继承、旧记忆移除或拒绝后重输。本次对照已完成，关卡未通过，原证据丢失问题与 TODO 2.3.5 保持开放。

实际模型均为 `gpt-4o-mini-2024-07-18`，$12$ 次事务各一次成功 HTTP 请求，无传输或业务重试、输出修复或多答案排序；HTTP 成功与上述应用校验失败分别统计。新增 [`logs/llm_usage.csv`](../logs/llm_usage.csv) 共 $12$ 行，输入 $6414$、输出 $691$ tokens，已逐条核对模型及用量。运行命令为项目 Python 执行私有入口 `tmp/tmp_evidence_wording.py`；完整问题、请求、原始回复、提交图、前后状态及审阅保存在 `tmp/tmp_evidence_wording_20261109T041641726832Z.json`。全部 $12$ 次事务精确免费重放通过，包括两次校验失败及原状态保持；全部 $100$ 项离线回归通过。生产实现、严格解析器、永久拒绝规则和私有 OpenAI 配置均未变。

#### otherwise 与显式不等于对照

2026/11/09 执行 $12$ 次独立 MSM 事务，交替比较纯人物题中的 `otherwise choose Nolan Reed` 与 `choose Nolan Reed if The Glass Orchard was not published by Silver Press`，两种完整出版社图、每图每版各重复 $3$ 次。仅改变上述问题文字，其余沿用私有字符串数组说明、默认 `retrieval_questions`、$0.95$ 分数、严格阈值 $0.7$ 和公开 `manage_memory`。每次从独立空状态开始，不添加提示词增强或正确分支提示。显式不等于版的完整问题如下：

```text
The Glass Orchard has exactly one publisher. Which person is selected by this rule: choose Mira Vale if The Glass Orchard was published by Silver Press; choose Nolan Reed if The Glass Orchard was not published by Silver Press? Answer with only the selected person's name.
```

| 图与问题版本 | 原始人物判断 | 严格接口及发布状态 |
| --- | --- | --- |
| Silver，otherwise | $3/3$ 回答 `Mira Vale`，无追问 | 全部只选出版社，$3/3$ 因 `answer_membership` 失败，空旧状态保持 |
| Silver，显式不等于 | 同上，$3/3$ 原始正确 | 同上，$3/3$ 校验失败，无状态发布 |
| Bronze，otherwise | $3/3$ 空答案，追问 `Who published The Glass Orchard?` | $3/3$ 结构通过，只保留出版社，各永久拒绝两条出生地 |
| Bronze，显式不等于 | $3/3$ 空答案，分别询问是否由 Silver 出版、是 Silver 还是其他出版社、由谁出版 | $3/3$ 结构通过，选择和拒绝与 otherwise 相同 |

原始人物判断加无追问，两版各通过 $3/6$；完整语义验收两版均为 $0/6$。严格事务合计 $6/12$ 通过，另 $6$ 次均为所选证据缺少答案实体而失败，没有发布原始选择或拒绝。所有旧快照保持不变，成功事务的记忆、拒绝及展开排除符合规则。全部原始回复已逐项审阅，没有错误类型的非空答案；Silver 的失败属于答案来源契约，不能记作人物判断错误。显式不等于没有改善 Bronze，仅改变了部分追问措辞；这不能证明内部究竟未完成出版社比较，还是比较后未采用其结果。

本轮再次确认纯人物题的诊断适配限制：人物选择可由问题规则和出版社推出，但人物并非出版社关系的头尾实体。该题能观察原始人物判断，无法在仅选出版社时满足现有答案契约；不应为此放宽生产校验或强塞与人物选择无关的出生地事实。Bronze 的 $6$ 次成功事务共发布 $12$ 个问题内出生地拒绝项，与独立人物小问题相符，未跨问题传递，不能视作完整城市题漏选必要证据或正确移除旧记忆的验证。本次对照已完成，未改善 Bronze，原证据丢失问题及 TODO 2.3.5 保持开放；生产实现、严格解析器、永久拒绝规则和私有 OpenAI 配置均未变。

实际模型均为 `gpt-4o-mini-2024-07-18`，$12$ 次事务各一次成功 HTTP 请求，无传输或业务重试、输出修复或多答案排序。新增 [`logs/llm_usage.csv`](../logs/llm_usage.csv) 共 $12$ 行，输入 $6348$、输出 $510$ tokens；HTTP 成功与应用校验失败分别记录。项目 Python 执行私有入口 `tmp/tmp_evidence_inequality.py`，完整问题、请求、原始回复、提交图、前后状态及审阅保存在 `tmp/tmp_evidence_inequality_20261109T042514950435Z.json`。全部 $12$ 次事务免费精确重放通过，包括 $6$ 次校验失败及空旧状态保持；逐次模型和用量与 CSV 一致，全部 $100$ 项离线回归通过。本轮仅交付诊断记录与用量。

#### 直接与条件出版社作答对照

2026/11/09 执行 $12$ 次独立 MSM 事务，在 Silver、Bronze 两张完整图上交替比较以下直接出版社题与条件出版社题，每图每版重复 $3$ 次。固定私有字符串数组说明、默认 `retrieval_questions`、全部 $0.95$ 分数、严格阈值 $0.7$ 和公开 `manage_memory`；每次从空记忆与空拒绝集合开始。未添加保留增强、条件判断指令或正确分支提示。

```text
Who published The Glass Orchard? Answer with only the publisher's name.

The Glass Orchard has exactly one publisher. If it was published by Silver Press, answer Silver Press; if it was not published by Silver Press, answer its actual publisher. Answer with only the publisher's name.
```

| 图与问题版本 | 原始输出与严格事务结果 |
| --- | --- |
| Silver，直接题 | $3/3$ 只选出版社事实，回答 `Silver Press`，无追问，完整事务通过 |
| Silver，条件题 | 同上，$3/3$ 完整通过 |
| Bronze，直接题 | $3/3$ 只选出版社事实，回答 `Bronze Press`，无追问，完整事务通过 |
| Bronze，条件题 | 同上，$3/3$ 完整通过 |

已逐项审阅全部原始回复、选择、答案、追问及发布状态。原始正确答案加无追问与完整语义验收均为 $12/12$，无空答案、错误类型答案、重复追问、`answer_membership` 或其他校验失败。答案均来自所选出版社事实，全部旧快照不变，记忆、拒绝及展开排除符合规则。每次永久拒绝两条无关出生地，合计 $24$ 个独立问题内拒绝项；这些拒绝与出版社小问题相符，没有跨问题继承，不构成完整城市题漏选必要证据或正确移除旧记忆的验证。

本轮直接题及条件题均通过，表明这两种问法下已有出版社事实可以正确作答；两题均可直接复制出版社，不能据此宣告相等／不等于比较或人物分支推理通过。上一轮 Bronze 人物题的空答案与重复追问仍未解决，纯人物题仅选出版社时与答案来源契约的冲突仍单独保留。本次对照已完成，原证据丢失问题及 TODO 2.3.5 保持开放。

实际模型均为 `gpt-4o-mini-2024-07-18`，$12$ 次事务各一次成功 HTTP 请求，无传输或业务重试、输出修复或多答案排序。新增 [`logs/llm_usage.csv`](../logs/llm_usage.csv) 共 $12$ 行，输入 $6126$、输出 $467$ tokens，逐次返回模型及用量与 CSV 一致。运行命令为项目 Python 执行私有入口 `tmp/tmp_evidence_publisher.py`；完整问题、请求、原始回复、提交图、前后状态及审阅保存在 `tmp/tmp_evidence_publisher_20261109T043304197599Z.json`。全部 $12$ 次事务免费精确重放通过，全部 $100$ 项离线回归通过。生产实现、严格解析器、永久拒绝规则及私有 OpenAI 配置均未变。

#### Silver 与 Bronze 正向条件交换对照

2026/11/09 执行 $12$ 次独立 MSM 事务，逐字使用以下 Silver 正向与 Bronze 正向人物题，每张完整图每版交替重复 $3$ 次。交换正向出版社及人物分支顺序，但两图的正确人物不变；固定私有字符串数组说明、默认 `retrieval_questions`、全部 $0.95$ 分数、严格阈值 $0.7$ 和公开 `manage_memory`，每次从独立空状态开始。未增加判断或保留增强。

```text
The Glass Orchard has exactly one publisher. Which person is selected by this rule: choose Mira Vale if The Glass Orchard was published by Silver Press; otherwise choose Nolan Reed? Answer with only the selected person's name.

The Glass Orchard has exactly one publisher. Which person is selected by this rule: choose Nolan Reed if The Glass Orchard was published by Bronze Press; otherwise choose Mira Vale? Answer with only the selected person's name.
```

| 证据图与问题版本 | 原始人物判断 | 严格接口与发布状态 |
| --- | --- | --- |
| Silver，Silver 正向 | $3/3$ 回答 `Mira Vale`，无追问 | 全部仅选出版社，因 `answer_membership` 失败，空旧状态保持 |
| Silver，Bronze 正向 | $3/3$ 空答案，重复追问已知出版社 | 全部结构通过，仅保留出版社，各永久拒绝两条出生地 |
| Bronze，Silver 正向 | $3/3$ 空答案，重复追问已知出版社 | 同上，全部结构通过，各永久拒绝两条出生地 |
| Bronze，Bronze 正向 | $3/3$ 回答 `Nolan Reed`，无追问 | 全部仅选出版社，因 `answer_membership` 失败，空旧状态保持 |

已逐项审阅全部原始成功 HTTP 回复、选择、答案、追问及状态。原始正确人物且无追问合计 $6/12$，两版各 $3/6$；严格事务 $6/12$ 通过，完整语义验收 $0/12$。无错误类型的非空答案，全部只选出版社。正向条件匹配组原始人物判断正确，但答案不在所选图内，$6$ 次均未发布选择或拒绝；不能将这些校验失败记作人物判断错误。otherwise 组发布 $12$ 个独立问题内出生地拒绝项，与该人物小问题无关证据的省略相符，未跨问题传递，不能视为完整城市题的必要证据漏选或正确旧记忆移除。全部旧快照保持不变，成功事务的记忆、拒绝与展开排除符合规则。

交换后，原始空答案与已知出版社追问随 otherwise 分支从 Bronze 转移至 Silver；Bronze 正向组能够回答 `Nolan Reed`。本样本呈现与分支位置相关的现象，不能据此判定内部比较过程或断言 otherwise 是唯一原因；本次同时改变正向出版社和分支顺序。人物题仅选出版社时的答案来源契约冲突仍独立存在，不放宽生产校验、不强塞出生地。原证据丢失问题、正确旧记忆移除及 TODO 2.3.5 保持开放。

此前提出的 $12$ 次 otherwise／双显式正向人物题对照于 2026/11/09 取消，未执行。

实际模型均为 `gpt-4o-mini-2024-07-18`，$12$ 次事务各一次成功 HTTP 请求，无传输或业务重试、输出修复或排序调用；HTTP 成功与应用校验失败分别记录。新增 [`logs/llm_usage.csv`](../logs/llm_usage.csv) 共 $12$ 行，输入 $6294$、输出 $495$ tokens，逐次返回模型及用量与 CSV 一致。项目 Python 执行私有入口 `tmp/tmp_evidence_branch.py`；完整问题、请求、原始回复、提交图、前后状态及审阅保存在 `tmp/tmp_evidence_branch_20261109T052243366663Z.json`。全部 $12$ 次事务免费精确重放通过，包括 $6$ 次校验失败及空旧状态保持；全部 $100$ 项离线回归通过。生产实现、严格解析器、永久拒绝规则和私有 OpenAI 配置均未变。本次对照已完成，仅交付诊断记录和用量。

#### 第 1 步结果：最小修复未通过

2026/11/09 运行私有入口 `tmp/tmp_evidence_minimal_fix.py`，执行最小修复对照的 $4$ 次独立 MSM 事务。逐字使用 `scripts/validate_part_d.py` 的完整 `BRANCH_QUESTION`，Silver、Bronze 图各比较一次基线与一个候选。两组均保留既有字符串数组说明、默认 `retrieval_questions`、完整三条输入、全部 $0.95$ 分数、严格阈值 $0.7$、独立空状态及默认多答案排序。候选只增加下面一项通用指令，不修改原问题、不注入本题人物或城市、不把 Bronze 改写为唯一否则取值，也不叠加先前的保留增强。通用指令同时要求依据已有事实作分支决策、选择条件事实及对应答案证据；原问题的单出版社前提仍由原问题提供。

```text
### Conditional Answer Evidence (candidate repair)
For a conditional question, use supplied facts to determine which branch applies,
including the otherwise branch when the stated condition is false. Select both
the facts establishing that decision and the facts supporting the requested
answer in that branch. If those facts are present, answer the original question
and ask no further questions; do not treat an established condition as unknown.
All existing evidence, entity, and output constraints still apply.
```

| 图与版本 | 实际答案及追问 | 选择、发布与判定 |
| --- | --- | --- |
| Silver，基线 | `Linden City`，无追问 | 保留全部三条，含出版社及对应出生地；严格事务、状态与本步语义标准通过 |
| Silver，候选 | 同基线 | 同基线，通过 |
| Bronze，基线 | 空答案，追问 `Who published The Glass Orchard?` | 保留全部三条并发布；严格事务及状态通过，已有出版社仍追问，语义失败 |
| Bronze，候选 | 同基线 | 同基线，语义失败 |

逐项审阅了 $4$ 份原始回复。基线与候选各通过 $1/2$ 个完整题用例，候选没有改善 Bronze。全部旧快照保持不变，发布后的记忆均为完整三条，永久拒绝集合均为空，展开排除均等于这三条记忆。严格解析与状态检查全部通过，没有 `answer_membership` 或其他应用校验错误；不能把接口成功记作条件题正确。本轮未出现漏选必要证据，但已有足够证据仍不回答，原漏选问题及正确移除旧记忆均未解决。

**决策**：不采用或推广该候选，本批付费调用已结束，未进入跨轮链。当前 `gpt-4o-mini-2024-07-18` 在这份通用候选提示及完整 Bronze 条件题上的作答未通过；这不证明所有提示均无效，也不证明模型内部的失败原因。TODO 2.3.5 继续开放。

实际模型均为 `gpt-4o-mini-2024-07-18`，共 $4$ 次逻辑调用、$4$ 次成功 HTTP 请求，无传输或业务重试、输出修复或排序请求；新增 [`logs/llm_usage.csv`](../logs/llm_usage.csv) $4$ 行，共输入 $2296$、输出 $276$ tokens，逐条返回模型及用量与 CSV 一致。完整请求、原始回复、候选文本、提交图、前后状态及判定保存在私有 `tmp/tmp_evidence_minimal_fix_20261109T053252585102Z.json`；本次新增 $4$ 次事务的免费精确重放全部通过。生产实现、严格解析器、永久拒绝语义和私有 OpenAI 配置均未变。本次只交付实验记录与真实用量，不重复运行无关离线回归。

#### 修改计划：分离条件决策与记忆发布

2026/11/09 根据上一节失败结果制定本计划。可以提出具体实现修改，但尚不能保证修复有效：本轮必要证据齐全，基线与候选仍都无法回答 Bronze，说明仅增加同一次请求中的通用指令未达到采用门槛；这不证明模型内部的失败原因。下一项改为在记忆选择前增加独立的结构化证据决策阶段，让最终作答显式使用其结果，并在永久拒绝发布前检查必要证据是否保留。本节取代此前尚未执行的后续安排，保留原完整题与跨轮验收门槛；本次仅制定计划，未实现、未新增 API 调用。

1. **实现一个完整候选，不再追加措辞对照**：新增内部证据决策模块，例如 `src/agent/evidence_decision.py`，并在 `memory_prompt.py`、`memory_update.py` 和 `memory_management.py` 接入候选 `evidence_decision` 流程。先作为可显式选择的候选运行，保持现有默认行为及 `paper` 对照，验收通过后再决定推广。沿用原问题逐字文本、历史记忆与达标新证据的完整并集，不先裁剪图，也不把问题改写成人物题或出版社题。一个 MSM 事务固定依次执行以下阶段，不在失败后追加纠错请求。

   | 阶段 | 输入与输出 | 实现约束 |
   | --- | --- | --- |
   | 证据决策 | 输入原问题和完整提交图；输出 `condition_status`、`condition_triplets`、`answer_targets`、`required_triplets`、`missing_relations` | 状态区分条件成立、不成立、未知及非条件题；条件证据、必留证据必须逐字来自提交图。回答对象可以逐字来自原问题，但不能作为最终答案绕过实体校验。未知时列出仍可能适用的回答对象，并保留已有的相关候选证据。 |
   | 记忆选择与作答 | 再次提供原问题、完整提交图和已校验的决策记录；输出现有三个字段 | 明确 `subsequent_question` 为字符串数组。最终选择必须包含 `required_triplets`；条件未知时不得输出答案。条件已定且所需事实齐全时，应回答原问题并停止追问；事实有缺口时仅询问缺失关系。 |
   | 默认答案排序 | 仅当最终候选答案多于一个时执行现有独立排序 | 沿用现有候选集合、顺序校验和失败行为，不关闭排序，不借排序修改证据或生成答案。 |

   证据决策须区分“有事实证明条件不成立”和“没有事实证明条件成立”。本题可结合原问题明确的单出版社前提及实际出版社事实判断条件，但不能仅凭缺少 `Silver Press` 三元组就走否则分支，也不能把其他出版社限定为 `Bronze Press`。不向任一阶段注入本题的正确人物、城市或分支结果，不编写依赖这些名称的条件判断代码。

2. **在发布前拦截阶段间矛盾，先完成离线实现检查**：为决策记录增加字段、类型和证据精确成员校验；最终输出继续经过原 `parse_memory_response`，再检查必留证据未漏选、未知条件未作答，以及声明没有事实缺口时未返回空答案或继续追问。不满足约定就使整个事务失败，保留原快照，不自动补选、补答或业务重试。这些检查只验证结构与阶段间一致性，不能证明决策语义正确，后者由真实受控验收判断。中间记录不发布记忆或拒绝；全部阶段成功后，仍按最终 `selected_triplets` 计算已提交而未选中的永久拒绝，既有屏蔽和严格阈值均不变。离线检查重点覆盖条件未知时保留两条候选出生地、条件确定后选择对应路径、必留证据漏选被拒、各阶段失败保持旧状态，以及多答案仍执行排序；运行受影响测试和完整回归后冻结候选，再进行付费验证。

3. **先用原完整题验收，只运行两次候选事务**：复用上一节已核对的基线和失败反例，不再付费重跑相同基线。通过公开 `manage_memory` 的候选流程，对 Silver、Bronze 完整图各运行一次，保持 `BRANCH_QUESTION`、全部 $0.95$ 分数、严格阈值 $0.7$、独立空状态及既有字符串数组说明。Silver 必须回答 `Linden City`，Bronze 必须回答 `Cedar City`，均无追问；决策记录须正确确定回答对象，最终选择须包含出版社与对应出生地，严格事务及状态检查通过。本步允许保留另一出生地，正确移除另在下一步验收。任一阶段失败、语义错误或仍追问已有出版社，就结束本批，不进入跨轮链、不推广候选；记录失败发生在证据决策还是最终作答，以及对应原始回复。不能把新的校验拦截记作问题已修复，也不自动续做小问题或措辞实验。

   **第 3 步失败后的补充：修正回答对象的分支绑定，再复验**（2026/11/09 制定并执行，复验失败；执行结果见本计划下方引用记录）。

   已核对私有报告 `tmp/tmp_evidence_decision_step3_20261109T061358161308Z.json` 的两阶段完整请求、原始回复及验收脚本。Silver 的 $12$ 项检查仅 `answer_targets` 失败：条件为真、必留路径、最终城市及拒绝状态均正确，不能将本次归因于出版社缺失、检索漏召回或最终答案错误，也不能据此判断尚未运行的 Bronze。具体缺口在 [`evidence_decision.py`](../src/agent/evidence_decision.py)：`DECISION_INSTRUCTIONS` 只将该字段定义为“所需事实涉及的对象”，明确要求条件未知时列出所有可能对象，却只对 `required_triplets` 说明条件确定后采用适用路径，没有明确收窄 `answer_targets`；解析仅检查字符串数组，`validate_decision_response` 也未检查对象与分支的对应关系。现有跨轮测试预先提供正确对象，未覆盖这次“条件已定但仍列两分支对象”的反例。这些是可确认的契约与校验缺口；模型究竟误解字段还是未遵守预期，单次输出无法证明。

   - **直接修改决策协议及消费代码**：在 `evidence_decision.py` 中，以按分支分组的 `targets_by_case` 替代模型直接生成的平铺 `answer_targets`，包含 `true`、`false`、`not_conditional` 三个字符串数组；模型从原问题及提交图识别各分支对象，不注入本题答案。代码按 `condition_status` 生成唯一的内部 `answer_targets`：`true`、`false`、`not_conditional` 分别取对应数组，`unknown` 取两个条件分支的去重并集。非条件题的两个条件数组为空，条件题的 `not_conditional` 数组为空。对象可以尚无事实证据，不能从必留三元组的主语反推，也不能用“数组只能有一个元素”代替分支绑定；一个适用分支可以有多个对象。提示同步明确各数组含义，分支表须通过字段、类型及对象来自原问题或提交图的校验，最终阶段只消费代码生成的当前对象；保留原问题、完整图、必留证据及全部既有发布门槛。该修改让“选择哪个分支的对象”由代码执行，不增加 LLM 调用阶段，也不在收到旧错误回复后临时删掉 `Nolan Reed`。分支表本身仍可能被模型填错，结构校验不能证明其语义正确。
   - **只补与此缺陷相关的离线检查**：在 `tests/test_evidence_decision.py` 覆盖条件成立／不成立时只传递相应对象、未知时传递两分支对象、单分支多对象及非条件题；检查最终提示实际使用派生结果，非法分支表在最终请求前失败且旧快照不变。保留本次原始记录作为旧缺陷证据，不能把旧协议回复被新版拒绝或模拟分支表通过记作真实修复。相关检查和完整回归通过后冻结一个修订候选，保留默认及 `paper` 对照。
   - **直接重做第 3 步的两图验收**：仍用当前 `gpt-4o-mini` 和原完整 Silver、Bronze 图，各至多一次修订候选事务；Silver 因决策协议已变需要重新验收，不重跑既有基线。要求原始分支表与原问题对应正确、派生对象正确，并继续满足第 3 步全部证据、答案和状态门槛；不能仅凭代码选中了某个数组或最终城市正确就通过。预算仍最多 $6$ 次逻辑调用、$18$ 次 HTTP 尝试，首个失败即停。只核对新记录中的首个错误环节：条件判断、分支对象归属、必留证据或最终作答，再针对该环节修改；不沿用同一版本重复付费，不另开人物／出版社小问题或措辞对照。两图均通过后才进入原第 $4$ 步。本次仅补充排查结论与方案，未修改生产代码、私有配置或发起 API 请求，TODO 2.3.5 继续开放。

   **补充复验失败后的方案：约束决策输出格式，同时修正字段语义**（2026/11/09 制定并执行；Bronze 失败，结果见下方引用记录）。

   已核对 `tmp/tmp_evidence_decision_step3_revised_20261109T063102138107Z.json`、对应验收入口及当前代码。本次实际停在 `evidence_decision / decision_completion`：模型返回了带围栏的 JSON，尚未执行对象及证据校验。正文中的问题句对象、出版社条件证据遗漏是另两项待解决的语义问题。当前 [`LLMClient.complete`](../src/agent/llm_client.py) 及 `_attempt` 只发送 `model`、`messages`，决策阶段仅靠提示要求裸 JSON；对象校验接受原问题的任意逐字子串，因此整句分支问题也能满足来源检查。条件证据必留已有 `decision_condition_retention` 拦截，无需重新实现。这些现有证据足以确定下一次修改范围，不再付费确认已观察到的错误。

   - **在请求端落实严格结构化输出**：为 `LLMClient.complete` 增加可选的仅限关键字参数 `response_format`，逐次传到 `_attempt` 的 HTTP 请求体；仅证据决策调用传入 `type="json_schema"`、`json_schema.strict=true` 及明确命名的 schema，省略参数的现有调用保持原请求体。schema 定义当前五字段、状态枚举、三分支字符串数组和三元组数组，各对象列全 `required` 并设置 `additionalProperties=false`；三元组长度及精确图成员仍由现有代码校验。当前 `gpt-4o-mini` 及实际返回的 `gpt-4o-mini-2024-07-18` 支持该接口，但结构化输出仍可能有语义错误，见 [OpenAI 官方文档](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)。沿用当前 Chat Completions HTTP 客户端、记账及有限传输重试，无需更换 SDK 或模型。保留原始正文的严格解析；拒绝、截断、不支持的 schema 或非法正文均终止事务，不剥围栏、不降级为普通文本、不追加纠错请求。
   - **同一次修改澄清两个语义缺口**：在 `DECISION_INSTRUCTIONS` 和 schema 描述中，将分支数组明确为“该分支待查询事实所属的实体名称”，例如查询出生地时填人物实体，而非问题句、关系名或待预测的城市；不写入本题人物、城市或分支答案。保留当前按状态派生对象的代码、多对象及对象尚无事实的支持；不以问号检测、字符串截取或从必留三元组主语反推实体代替语义判断。明确 `required_triplets` 同时包含全部 `condition_triplets` 和适用回答路径，继续由现有子集检查拒绝遗漏，不能自动并入漏掉的出版社。schema 的类型约束和来源检查都不能证明实体或分支归属正确，真实验收仍须逐项审阅；不能将“裸 JSON 已通过”算作这两个问题已解决。
   - **只验证本次改动触及的边界，再冻结候选**：离线检查真实客户端发送的请求体，确认仅决策阶段携带 schema、每次传输重试保留同一契约，默认流程、最终作答及排序请求保持原行为，失败仍正确记账。在公开 `manage_memory` 链路检查带围栏／拒绝／截断回复仍失败且不启动最终请求；复用已有条件证据遗漏、分支派生和状态不变检查，不重复建设。保留本次原始报告；模拟正确实体只能验证接线，不能证明模型已纠正问题句对象。受影响检查及完整离线回归通过后，冻结一个同时包含格式与语义修改的候选，不先单独付费测试 JSON 格式或补做模型连通性测试。
   - **直接重新验收原完整两图，失败后只处理实际错误环节**：更新私有记录／重放适配器，使其透传并保存 `response_format`，免费重放同时比对消息和请求选项；仍从公开 `manage_memory(..., prompt_variant="evidence_decision")` 执行。沿用原 `BRANCH_QUESTION`、Silver／Bronze 完整图、全部 $0.95$ 分数、严格阈值 $0.7$、独立空状态及当前 OpenAI 配置，各至多一次事务，最多 $6$ 次逻辑调用、$18$ 次 HTTP 尝试，首个失败即停。依次核对请求确实携带 schema、原始正文可严格解析、条件判断、分支实体名称及归属、条件与回答证据、最终答案和发布状态；格式通过后必须继续审查后续项目。若 API 拒绝 schema，修正请求契约；若仍输出问题句或错误分支对象，定位为实体／分支语义失败；若缺条件证据，定位为必留证据失败；若最终阶段失败，针对最终阶段修改。只依据新记录修改对应环节，不对同一冻结版本重复付费、不退回人物／出版社小问题或措辞对照。两图均满足原第 $3$ 步门槛，才执行第 $4$ 步；任何拦截本身均不算修复通过。

   本补充已实施并完成本批复验；候选继续显式选择，TODO 2.3.5 保持开放。第 $4$、$5$ 步的跨轮、移除和交付门槛继续适用。

> 2026/11/09 已执行“补充复验失败后的方案”：`LLMClient.complete` 新增仅限关键字的可选 `response_format`，逐次传到 HTTP 请求体；仅证据决策携带命名严格 schema，完整定义五字段、状态枚举和三分支数组，各对象列全 `required` 并禁止额外字段。提示和 schema 明确对象为待查询事实所属的实体名称，必留集合包含全部条件证据与适用路径。默认、`paper`、最终作答及排序请求不携带 schema；严格解析、精确成员、条件证据必留和原子发布规则保持原样，不剥围栏或修补回复。全部 $111$ 项离线测试通过，包含真实 HTTP adapter 请求体及重试契约、独立用量记账、仅决策携带 schema，以及围栏／拒绝／截断在最终请求前失败；字符、语法及差异检查通过。这些离线结果不证明语义正确。
>
> 冻结后以原 `BRANCH_QUESTION`、完整两图、全部 $0.95$ 分数、严格阈值 $0.7$ 和独立空状态，通过公开候选入口各执行一次。Silver 的 $14$ 项门槛全部通过：条件为 `true`，分支表分别为 `Mira Vale`、`Nolan Reed`，派生对象为 `Mira Vale`；条件证据与必留路径正确，答案为 `Linden City`，无追问。最终保留三条输入证据，暂无拒绝；本步允许保留另一出生地，不构成正确旧记忆移除通过。
>
> Bronze 在首阶段以 `evidence_decision / decision_membership` 失败，立即停止；未执行 Bronze 最终作答或任何跨轮链。请求已携带严格 schema，正文为可严格解析的裸 JSON，实际被拦截的是条件证据中不存在于提交图的 Silver 出版社三元组。原始回复如下：
>
> ```json
> {"condition_status":"unknown","condition_triplets":[["The Glass Orchard","published_by","Silver Press"]],"targets_by_case":{"true":["Mira Vale"],"false":["Nolan Reed"],"not_conditional":["The Glass Orchard"]},"required_triplets":[["The Glass Orchard","published_by","Silver Press"],["Mira Vale","born_in","Linden City"],["Nolan Reed","born_in","Cedar City"],["The Glass Orchard","published_by","Bronze Press"]],"missing_relations":["published_by"]}
> ```
>
> 人工审阅还发现：已有 Bronze 事实及单出版社前提时，状态仍为 `unknown`、仍声明缺少出版社；条件题的 `not_conditional` 数组非空。两个条件分支的人物名称及归属正确，未再出现整句问题对象，但不能据此认定整个决策通过。实际首个校验失败是虚构条件证据，后续分支约束未执行；Bronze 的记忆、拒绝和展开排除均保持空集。格式问题在本批得到通过，条件证据与条件判断语义仍未解决，不能将拦截算作修复成功。
>
> 私有入口 `tmp/tmp_evidence_decision_step3_structured.py`，原始报告 `tmp/tmp_evidence_decision_step3_structured_20261109T065752782736Z.json`；旧入口与历史报告保留。适配器透传并保存请求选项，全部 $3$ 次逻辑调用按消息与选项精确免费重放通过，回复与 CSV 用量逐次核对通过。实际模型均为 `gpt-4o-mini-2024-07-18`，新增 $3$ 次 HTTP 请求，无重试，输入 $2162$、输出 $255$ tokens；HTTP 均成功且请求 ID 非空，应用层失败不改写 HTTP 记账。私有配置未改。
>
> **不能推进第 4 步**：两图验收未全部通过，候选不推广，TODO 2.3.5 保持开放。本批停止，不重复付费运行同一候选。下一项须直接针对 Bronze 决策把待判断值误作已有条件证据、未使用实际出版社判断条件的环节提出代码修改，同时保留非条件分支互斥检查；不退回小问题或措辞对照，不放宽成员校验，也不自动修补事实。本轮不继续实施新的修复方案。

   **Bronze 决策失败后的方案：分离条件描述与事实求值**（2026/11/09 制定并执行，必留路径验收失败；结果见下方引用记录）。

   已核对上述严格结构化输出复验的执行记录、原始报告及当前实现。Bronze 的首个实际错误是把问题中的 `Silver Press` 比较值生成为已有条件事实，触发 `decision_membership`；原始回复还忽略实际 Bronze 证据，返回 `unknown` 并声明缺少出版社。格式已通过，成员校验正确拦截虚构事实，最终作答尚未执行，旧状态保持不变。两个条件分支的人物名称及归属已正确，本次直接修改条件与事实的混淆环节：LLM 提取条件和分支，代码从实际图中取值并判断条件，不再追加泛化提示或独立措辞实验。

   - **修改证据决策协议**：在 `evidence_decision.py` 中，让模型输出待判断条件的主体、关系、比较操作、比较值，以及原问题中支持唯一性的原文依据；同时输出各分支的回答对象、所需证据和缺失关系。比较值属于问题约束，不能作为已有事实。完整提交图按稳定顺序编号，模型仅通过本次有效编号引用证据，代码严格检查编号并还原原三元组；空图只能引用空集合。模型不再直接生成 `condition_status` 或条件证据三元组。同步调整 schema、解析及消费代码，保留条件／非条件分支的互斥检查、多对象及尚无事实的对象。仅约束证据编号不足以修复错误的 `unknown`，必须同时落实下述代码求值。
   - **由代码查询完整图并求值**：本次只支持当前用例涉及的单值关系等值判断，主体、关系和比较值均从本次输入提取，不写入本题书名、出版社、人物、城市或分支特判。代码按主体和关系查询完整提交图，不能只检查模型挑选的证据；实际值等于比较值时为 `true`，实际值不同且问题明确关系只有一个值时为 `false`。没有实际值，或只有不同值但缺少唯一性前提时，为 `unknown`；存在违反单值前提的冲突事实时，事务失败并保留旧状态。不支持的复杂条件明确报错，不猜测、不自动切换流程。唯一性依据须逐字来自原问题，但来源检查不能证明模型抽取正确，条件描述、前提及分支归属仍须在真实验收中审阅。
   - **按计算结果构造最终阶段输入**：代码生成条件状态、来自完整图的实际条件证据和适用回答对象；必留集合由实际条件证据与适用分支路径组成，条件未知时保留两分支已有候选证据。缺失关系随适用分支确定，并依据图中实际条件事实消除已经满足的条件缺口，不能在出版社已知时沿用“缺少出版社”的声明。最终阶段继续收到原问题和完整图，沿用必留证据检查、严格答案来源、失败原子性及最终未选中即永久拒绝的规则。上述派生是新协议的正常执行，不在错误回复后临时补选、修补事实或追加纠错请求；候选仍显式选择，默认及 `paper` 不变。每事务仍为两次逻辑调用，多答案时追加现有独立排序。
   - **只补本次修改需要的离线检查**：在 `tests/test_evidence_decision.py` 覆盖实际值匹配、不同值加唯一性、缺事实、缺唯一性、冲突事实及非法证据编号；检查实际条件证据始终来自完整图、派生结果确实进入最终提示，以及失败不启动最终请求、不发布记忆或拒绝状态。复用已有分支互斥、严格解析、排序、拒绝和跨轮检查，运行受影响检查及完整回归后冻结一个修订候选。不重建已有格式测试，不免费重放全部历史实验；只重放新增及受修改影响的记录。模拟正确条件描述只能验证代码求值和接线，不能证明真实模型已正确提取条件或证据路径。
   - **先验收已知失败的 Bronze，再检查 Silver**：本补充将第 $3$ 步的执行顺序改为 Bronze 在先，Silver 在后，其他语义和状态门槛不降低。通过公开 `manage_memory(..., prompt_variant="evidence_decision")` 使用原 `BRANCH_QUESTION`、完整图、全部 $0.95$ 分数、严格阈值 $0.7$、独立空状态及当前 `gpt-4o-mini` 配置，各至多一次事务，首个失败即停，最多 $6$ 次逻辑调用、$18$ 次 HTTP 尝试。Bronze 必须派生 `false`、对象 `Nolan Reed`、实际 Bronze 条件证据，保留该证据及对应出生地，回答 `Cedar City` 且无追问，必要证据未被拒绝；再验证 Silver 对应的 `true`、`Mira Vale`、Silver 条件证据及 `Linden City`。人工逐项审阅模型提取的条件、唯一性依据、分支及证据路径，不能只凭代码比较结果或最终城市正确通过。每批保存原始请求、请求选项、回复、派生过程和发布前后状态，免费精确重放并核对实际用量。两图均通过才进入原第 $4$ 步；失败只处理新记录中的首个错误环节，不重复付费运行同一候选，不退回人物题、出版社题或措辞对照，不自动更换模型。

   本方案已实施并完成本批复验，私有配置未改。第 $4$、$5$ 步的真实跨轮保留、正确移除、高分重输过滤及交付门槛继续适用；结构拦截或代码求值通过均不等于问题已解决，TODO 2.3.5 保持开放。

> 2026/11/09 已执行“Bronze 决策失败后的方案”：首阶段改为抽取条件描述与各分支对象、证据编号和缺失关系，严格 schema 不再让模型生成条件状态或条件事实。完整图稳定编号，代码验证引用并还原原三元组，从完整图查询主体与关系，结合逐字唯一性依据计算等值条件；不支持的条件、冲突单值事实及非法编号终止事务。代码按状态派生对象、实际条件证据及必留集合，未知时保留两分支引用的路径，并生成条件缺口；已知条件关系不再沿用为缺口。默认和 `paper`、严格解析、答案来源、最终选择的永久拒绝及多答案排序保持原行为。
>
> 新协议替换旧协议的模拟决策测试；全部 $106$ 项离线检查通过，覆盖匹配、不同值加唯一性、缺唯一性、空图、冲突值、非法编号、分支互斥、最终提示消费派生结果、格式与阶段失败原子性及非条件多答案排序。字符、Python 语法及差异检查通过。离线结果只证明执行边界，不证明真实抽取正确。
>
> 冻结候选后，以原完整 `BRANCH_QUESTION`、Bronze 三条证据、全部 $0.95$ 分数、严格阈值 $0.7$、独立空快照及未修改的 OpenAI 配置，通过公开 `manage_memory(..., prompt_variant="evidence_decision")` 运行一次。条件主体、关系、等值操作、比较值与唯一性原文均正确，两分支人物及归属正确；代码从实际 Bronze 事实派生 `false` 和对象 `Nolan Reed`，没有再虚构 Silver 事实或追问已知出版社。首阶段原始正文如下：
>
> ```json
> {"condition":{"kind":"equals","subject":"The Glass Orchard","relation":"published_by","operator":"eq","value":"Silver Press","uniqueness_quote":"The Glass Orchard has exactly one publisher."},"branches":{"true":{"targets":["Mira Vale"],"missing_relations":[],"evidence_ids":[]},"false":{"targets":["Nolan Reed"],"missing_relations":[],"evidence_ids":[]},"not_conditional":{"targets":[],"missing_relations":[],"evidence_ids":[]}}}
> ```
>
> **语义门槛未通过**：编号 $0$、$1$ 的出生地证据已在完整图中，但两分支 `evidence_ids` 均为空且没有声明缺口，导致派生 `required_triplets` 仅有实际 Bronze 出版社，遗漏适用出生地。这是首阶段回答路径抽取失败，非条件求值、格式、编号校验或最终作答失败；合法空数组通过结构检查不能证明路径完整。最终阶段恰好选择全部三条证据并回答 `Cedar City`、无追问，严格解析及阶段一致性通过，但不能据此将候选判为通过。返回记忆为完整三条证据，拒绝为空，展开排除为全部提交图，原空快照保持不变；本次没有必要证据被拒绝，也没有验证旧记忆移除。
>
> 首个失败即停，未运行 Silver、跨轮链或业务重试。本批共 $1$ 次事务、$2$ 次逻辑调用和 $2$ 次成功 HTTP 请求，无传输重试或排序，实际模型均为 `gpt-4o-mini-2024-07-18`。决策输入／输出 $614/99$ tokens，最终作答 $658/68$ tokens，合计 $1272/167$ tokens；用量 CSV 新增两行，始于 2026/11/09 15:17:50（UTC+08:00），请求 ID、模型和用量逐条核对通过。私有入口 `tmp/tmp_evidence_decision_step3_grounded.py`，完整原始请求、schema、回复、派生记录及前后快照见 `tmp/tmp_evidence_decision_step3_grounded_20261109T071750532158Z.json`；新增事务按消息及请求选项免费精确重放通过，入口和报告均已从磁盘复核。
>
> **不能推进第 4 步**：完整图首项仍未通过，Silver 尚未复验，候选不推广，本问题和 TODO 2.3.5 保持开放。本批停止，不重复付费运行同一候选。下一次须针对首阶段“已有回答路径却输出空证据编号且声称无缺口”的抽取与完整性契约提出修改；不能自动补选、放宽验收或把本次正确最终城市视为路径缺口已修复。条件求值在本次 Bronze 上通过，不证明其他问题上的抽取稳定性。

   **必留路径失败后的方案：描述事实需求，由代码确定证据和缺口**（2026/11/09 制定并执行，首阶段分支互斥校验失败；结果见下方引用记录）。

   已核对 `tmp/tmp_evidence_decision_step3_grounded_20261109T071750532158Z.json`、对应验收入口及当前 `evidence_decision.py`。本次 $15$ 项检查仅 `required_evidence` 失败：Bronze 条件求值、分支对象、最终答案及状态规则均通过，但必留集合只有出版社，没有 Nolan 出生地。`derive_decision` 仅校验证据编号有效，再合并模型给出的编号和缺口；没有从回答事实需求检查路径是否齐全。`validate_decision_response` 只能保护这个已经漏项的集合，因而最终漏选出生地仍可能被发布为永久拒绝。现有测试预填正确路径编号；空图测试又允许有对象却无回答证据、无回答缺口，未覆盖该完整性问题。这足以确定修改范围，无需再付费确认现象，也无需重做条件求值、格式或事实小问题实验。

   - **直接替换容易漏项的分支协议**：在 `evidence_decision.py` 的提示、严格 schema 和解析中，以 `fact_queries` 替代模型生成的 `evidence_ids`、`missing_relations`；每条需求描述 `target`、`relation`、`direction`，分别表示待查询对象、图关系和查询方向，不包含预测值或证据选择。保留 `targets` 及条件描述；条件题的两个分支都须描述事实需求，`not_conditional` 为空，非条件题采用相反约定。有效分支的对象和需求不能为空，每个对象都须被需求覆盖，查询对象必须属于该分支；不能用“证据数组非空”代替完整性约束，缺事实时仍须保留需求。本次只实现从对象沿指定关系查询的一跳事实需求，支持多个对象及一个需求匹配多个事实；无法表示的多跳、聚合或复杂约束明确失败，不扩建通用推理引擎。关系存在于图中时使用精确标识，不存在时仍描述所需关系，供缺口追问；不因无匹配就把需求删掉。模型仍须正确抽取对象、关系及方向，来源和类型校验不能证明语义正确。
   - **从完整图统一派生必留路径和缺口**：保留已实现的条件求值，按状态确定适用分支；对每条事实需求扫描完整提交图，按对象、关系及方向精确匹配，保留全部匹配三元组。匹配为空就生成带对象与方向的事实缺口，不接受模型自行声明“无缺口”；只在所有适用需求都有匹配时声明回答事实齐全。`required_triplets` 由实际条件证据与适用需求的匹配证据并集生成；条件未知时使用两分支需求的并集，并保留条件缺口。内部保留逐需求的匹配结果，最终提示接收派生证据和可定位到对象的缺口，避免同一关系下不同对象相互抵消。最终选择仍由原阶段生成，现有必留检查据此拒绝遗漏；不直接生成答案、不事后修改 `selected_triplets`。这是新协议的固定求值步骤，不是看到旧空数组后临时补选，也不根据人物名、城市名或验收预期硬编码证据。
   - **用最小离线检查覆盖本次漏洞**：更新 `tests/test_evidence_decision.py`，用有效需求验证完整图必留集合包含出版社与适用出生地，未知条件保留两分支已有事实，缺一人的出生地时只生成该人的缺口，空图不能再得到“无回答缺口”。覆盖多对象、同关系不同对象、查询方向及多匹配，防止简单按对象收集全部邻边而保留无关证据。通过公开 `manage_memory` 检查派生路径进入最终提示、最终漏选该出生地被拦截且旧快照不变；空需求、对象未覆盖和不支持的需求应在最终请求前失败。复用已有条件、严格解析、排序和永久拒绝检查，相关测试及完整回归通过后冻结候选。旧报告保留为缺陷证据；旧协议被新解析器拒绝、模拟查询通过，都不能单独记作真实修复。不重放全部历史实验，不增加模型连通性或独立格式测试。
   - **直接复验 Bronze，再验收 Silver 和原跨轮链**：沿用第 $3$ 步的公开入口、原 `BRANCH_QUESTION`、完整图、全部 $0.95$ 分数、严格阈值 $0.7$、独立空状态和当前 `gpt-4o-mini` 配置，Bronze 在先、Silver 在后，各至多一次事务，首个失败即停，最多 $6$ 次逻辑调用、$18$ 次 HTTP 尝试。逐项检查原始需求中的人物、`born_in` 及方向与原问题对应，派生必留集合包含实际出版社和对应出生地，且没有虚构缺口；继续满足原第 $3$ 步的答案、追问、选择及状态门槛，不能只看最终城市。本步仍允许最终保留另一出生地，两图均通过后立即进入原第 $4$ 步，验证真实保留、移除与拒绝后过滤；不另开小问题。保存并免费精确重放新增请求、schema、原始回复、逐需求匹配和前后状态，核对实际用量。
   - **失败只修改首次出错环节**：需求缺失、关系或方向错误，处理抽取协议；需求正确但证据或缺口派生错误，处理图匹配；派生路径正确却被最终漏选或无法作答，处理最终阶段；只有第 $4$ 步失败才针对真实移除或过滤问题修改。不沿用同一版本重复付费，不用放宽校验、解除屏蔽或自动补答绕过门槛，也不在本批切换模型。默认及 `paper` 流程、共享配置、私有 `.env` 和最终未选中即永久拒绝的规则保持现状。

   本方案已实施并完成本批复验。第 $4$、$5$ 步及原调用预算继续适用；候选不推广，TODO 2.3.5 保持开放。

> 2026/11/09 已执行“必留路径失败后的方案”：分支协议改为 `targets` 和 `fact_queries`，每条需求包含对象、精确关系及方向。代码要求有效分支对象与需求非空、每个对象均被需求覆盖，拒绝非法方向和复杂需求；从完整图匹配全部一跳事实，生成逐需求匹配记录、对象与方向明确的缺口，并与实际条件证据合成必留集合。未知条件保留两分支匹配证据及条件缺口；不生成答案或修改最终选择。默认及 `paper`、条件求值、严格解析、永久拒绝和排序保持原规则。
>
> 全部 $108$ 项离线回归通过，覆盖完整图必留路径、空图缺口、多对象同关系隔离、方向、多匹配和无关邻边排除，以及空需求、对象未覆盖、非法需求在最终请求前失败。公开入口验证派生路径进入最终提示，最终漏选出生地被拦截且非空旧快照不变。字符、Python 语法和差异检查通过；离线通过不证明真实需求抽取正确。
>
> 冻结候选后，仅执行 Bronze 原完整题一次：原 `BRANCH_QUESTION`、完整三条证据、全部 $0.95$ 分数、严格阈值 $0.7$、独立空状态及未修改的 OpenAI 配置。首阶段原始正文如下：
>
> ```json
> {"condition":{"kind":"none","subject":"","relation":"","operator":"","value":"","uniqueness_quote":""},"branches":{"true":{"targets":["Mira Vale"],"fact_queries":[{"target":"Mira Vale","relation":"born_in","direction":"outgoing"}]},"false":{"targets":["Nolan Reed"],"fact_queries":[{"target":"Nolan Reed","relation":"born_in","direction":"outgoing"}]},"not_conditional":{"targets":[],"fact_queries":[]}}}
> ```
>
> 首个实际失败为 `evidence_decision / decision_targets`：条件被抽取为 `none`，但 `true`、`false` 分支仍有对象和需求，违反非条件分支互斥约定。人工审阅另确认原题明确包含出版社条件，`none` 本身也是条件抽取语义错误；两分支人物、`born_in` 与 `outgoing` 需求正确，但未进入图匹配及最终作答，不能判定真实必留路径已修复。事务未发布，输入记忆、永久拒绝及展开排除均保持空集。首个失败即停，未执行 Silver、跨轮链、输出修复或业务重试。
>
> 本批仅 $1$ 次事务、$1$ 次逻辑调用及 $1$ 次成功 HTTP 请求，无传输重试或排序；模型 `gpt-4o-mini-2024-07-18`，输入／输出 $697/98$ tokens。用量 CSV 新增一行，始于 2026/11/09 15:35:37（UTC+08:00），模型、用量及请求 ID 已核对。私有入口 `tmp/tmp_evidence_decision_step3_queries.py`，完整原问题、请求选项、原始回复和前后状态保存在 `tmp/tmp_evidence_decision_step3_queries_20261109T073537922122Z.json`；新增事务免费精确请求重放通过，入口及报告已从磁盘复核。
>
> **不能推进第 4 步**：Bronze 首阶段未通过，Silver 和真实跨轮保留、移除、屏蔽仍未验收，本问题及 TODO 2.3.5 保持开放，候选不推广。本批停止，不重复付费运行同一候选。下一次须针对条件被误分类为非条件、与已抽取条件分支矛盾的首阶段协议提出修改；不能放宽互斥检查、自动猜测条件或用正确事实需求代替完整验收。

   **事实需求复验失败后的方案：绑定题型、条件与分支，直接修正抽取入口**（2026/11/09 制定，尚未实施）。

   已核对 `tmp/tmp_evidence_decision_step3_queries_20261109T073537922122Z.json`、对应私有入口、当前 [`evidence_decision.py`](../src/agent/evidence_decision.py) 和 [`tests/test_evidence_decision.py`](../tests/test_evidence_decision.py)。请求逐字包含原题、三条完整证据及严格 schema，回复正常结束、未拒绝、未截断，HTTP 成功；没有证据指向检索、阈值、传输或客户端丢字段。实际执行链是：模型输出 `kind="none"` 和全空条件字段 → `derive_decision` 暂定 `not_conditional` → 遍历到非空 `true` 分支，抛出 `decision_targets` → 最终请求与状态发布均未发生。诊断码表示首先发现了分支互斥矛盾，不表示人物目标错误；两分支人物、关系和方向均正确。此次未运行事实匹配，不能将旧的必留路径问题判为已修复，也不能归因于最终阶段漏选。

   **已确认的实现缺口**：`CONDITION_SCHEMA` 独立允许 `equals`、`none`、`unsupported`，`branches` 又无条件要求三个同形对象；schema 没有将题型与分支结构绑定，所以本次矛盾回复完全满足请求端的结构约束。互斥规则只写在提示及本地校验中，本地拒绝是正确防护，却无法促使模型生成正确条件。提示虽然定义 `none` 为非条件题，但未明确“原题有条件、只是条件不成立或证据不足”仍须抽取条件，也未区分决定回答对象的出版社条件与末句“未知时先追问”的操作要求。测试主要由 `description()` 预填正确条件；非法用例未纳入本次 `none` 加非空真假分支的原样反例，且非条件排序测试仍使用含 `If … otherwise …` 的 `QUESTION`，只能证明给定描述后的执行，不能证明题型抽取正确。

   **归因边界**：前一版 Bronze 曾正确抽取条件，本版改为事实需求后却输出 `none`，只能证明这次完整抽取退步，不能证明事实需求必然干扰条件理解，也不能确定模型混淆了“假”“未知”“非条件”中的哪一种。下一次同时消除已知的结构矛盾和语义歧义，不增加付费实验去猜模型内部原因。仅把本次回复拦得更早、改诊断码或继续强调“不要填错”，都不足以解决问题。

   - **将互斥规则写进生成结构**：集中修改 `evidence_decision.py` 的提示、`DECISION_RESPONSE_FORMAT`、解析及 `derive_decision` 的解码入口。外层保留普通对象，在单一 `description` 属性内用嵌套 `anyOf` 表示三种完整结构，每种以单值枚举固定 `kind`：`conditional_equals` 必须包含条件描述和 `true`、`false` 两个事实需求分支，不提供 `not_conditional`；`nonconditional` 只包含一个无条件事实需求分支，不提供条件字段或真假分支；`unsupported` 只记录无法支持的原题片段，并使事务明确失败。各对象列全必需字段、禁止额外字段；有效分支的对象、需求非空和覆盖约束继续由本地严格检查。这样在生成时排除“非条件类型却带真假分支”的组合，不再要求模型填写不适用的空占位。采用嵌套而非根级 `anyOf`，不使用不支持的 `if/then/else`；相关接口边界见 [OpenAI 官方结构化输出文档](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)。本地仍独立拒绝非法或混合结构，不从已有分支猜题型、不把旧 `none` 回复自动转成条件题。
   - **明确抽取语义，保留已工作的求值和事实匹配**：条件结构增加非空 `condition_quote`，逐字引用决定答案分支的条件原文；继续抽取主体、关系、等值比较值及独立的 `uniqueness_quote`，比较操作固定为 `eq`。提示和字段说明明确：题型由原问题决定，与本轮图中的实际值、条件真假或证据是否齐全无关；出版社未知时仍抽取同一条件，由代码产生 `unknown`，不能退化为非条件题；“未知时先追问”属于回答前置要求，不替代决定人物分支的条件。不注入本题名称、预期城市或条件结果。原文引用须非空且精确来自原题，但来源校验不证明其语义；合法的 `nonconditional` 仍可能是模型误分类，必须通过原题语义验收，不能声称 schema 已保证题型正确。新结构只解码为现有求值器需要的条件和分支，不改完整图取值、唯一性判断、逐需求匹配、缺口派生、最终必留检查或记忆发布规则，不增加 LLM 阶段或另建题型分类器。
   - **补真实反例及必要边界，再冻结一次候选**：在 `tests/test_evidence_decision.py` 保留本次原样矛盾回复作为非法输入，确认只启动首阶段、非空旧记忆及既有拒绝集合均不变；增加混合结构、缺条件原文和伪造原文的拒绝检查。用同一有效条件描述覆盖 Silver、Bronze 和缺出版社三种图，分别派生 `true`、`false`、`unknown`；核对完整图必留对应出生地、缺条件时保留两分支出生地及出版社缺口，并通过公开 `manage_memory` 检查最终提示消费结果。非条件排序用例改用真正无条件的原问题，继续验证多对象及独立排序。核对真实客户端请求体包含新的完整结构契约，复用现有传输重试、严格解析、需求覆盖、方向和失败原子性检查；不安装新验证框架，不重建格式或模型连通性测试。受影响检查和完整离线回归通过后冻结候选。旧回复被新版拒绝和模拟条件通过均不能记作真实抽取修复。
   - **直接复验原 Bronze、Silver，通过即进入第 4 步**：修订私有记录／重放入口以保存并消费新结构，沿用原 `BRANCH_QUESTION`、完整图、全部 $0.95$ 分数、严格阈值 $0.7$、独立空状态及当前 `gpt-4o-mini` OpenAI 配置。Bronze 在先、Silver 在后，各至多一次事务，首个失败即停；仍最多 $6$ 次逻辑调用、$18$ 次 HTTP 尝试，不单独付费测试题型或 JSON。人工先核对原始题型、条件原文、主体、关系、比较值、唯一性依据及两分支需求，再核对派生状态、对象、必留路径、缺口和最终输出。Bronze 须为 `false`、`Nolan Reed`、实际 Bronze 出版社和对应出生地、`Cedar City`；Silver 须为对应的 `true`、`Mira Vale`、Silver 证据和 `Linden City`，均无追问、必要证据未被拒绝，并满足原第 $3$ 步全部门槛。本步仍允许保留另一出生地；两图通过后直接执行原第 $4$ 步的两条真实跨轮链，不以单轮正确答案代替保留、移除及屏蔽验收。保存新增完整请求、schema、原始回复、派生过程和前后快照，只免费精确重放新增事务并核对实际用量。
   - **按首次错误决定下一项，不继续扩写协议碰运气**：API 拒绝新 schema 时只修契约；仍合法地误报非条件、抽错条件或分支时，记录为首阶段语义失败，本批停止，不能靠搬运分支、关键词特判或补填条件绕过；题型与条件正确后，才分别定位需求抽取、图匹配或最终选择的首个错误。对同一冻结候选不重复付费；若仍在完整题抽取上失败，下一项应依据已保存反例单独评估允许的推理模型，而非继续追加等价 schema、提示措辞或人物／出版社小问题。模型评估另定范围，不在本批自动切换；TODO 2.3.5 的 GPT-4o-mini 验证门槛继续保持。

   本次仅补充归因与方案，未修改生产代码、测试、私有配置或新增 API 用量；第 $4$、$5$ 步及原分批预算继续适用。候选仍不推广，本问题和 TODO 2.3.5 保持开放；下一项直接实施上述抽取协议修改和必要检查，再按门槛复验。

> 2026/11/09 已实施“事实需求复验失败后的方案”：首阶段改为外层 `description` 内嵌 `anyOf`，分别绑定 `conditional_equals` 的条件和真假分支、`nonconditional` 的单一分支、`unsupported` 的原文片段。条件增加逐字来源检查的非空 `condition_quote`，比较操作限定 `eq`；本地独立拒绝混合结构和旧格式，不推断、修复或搬运分支。解码后复用完整图求值、逐需求匹配、缺口派生和发布前检查。新协议不增加调用阶段，默认及 `paper` 行为、永久拒绝和私有 OpenAI 配置保持不变。
>
> 全部 $110$ 项离线测试通过，覆盖原始矛盾回复、混合结构、缺失／伪造条件原文、同一抽取在真假与缺条件图上的派生、公开最终提示消费、真正无条件题的独立排序，以及失败时非空旧记忆和既有拒绝不变。真实客户端请求体测试继续确认完整 schema 传递；字符、Python 语法及差异检查通过。技能附带扫描脚本因文件读取权限不可用，字符检查改用技能允许的 `rg` 模式完成；未安装框架。
>
> 冻结后先 Bronze、后 Silver，各执行一次完整题事务。Bronze 原文、条件主体／关系／比较值／唯一性和两分支需求均正确，派生 `false`、`Nolan Reed`、实际 Bronze 出版社及出生地，回答 `Cedar City`，无追问；必要证据未拒绝。Silver 正确派生 `true`、`Mira Vale`、Silver 出版社及出生地，回答 `Linden City`，无追问；但 `condition_quote` 为 `The Glass Orchard has exactly one publisher.`，引用了唯一性前提而非决定分支的 `If … Silver Press` 条件。来源合法不能证明语义正确，故 Silver 首阶段条件原文语义未通过，不能据单轮答案正确宣布第 $3$ 步通过。
>
> 执行偏差：私有入口的自动门槛只验证条件引用非空且来自原题，漏查其是否为分支条件，曾将两图标记为通过并提前启动 Silver 跨轮第 $1$ 轮。人工复核撤销该通过结论，不改写原始报告。额外一轮仅输入两条出生地：抽取保留正确人物与 `born_in` 需求，缺失的出版社关系描述为 `publisher`，代码派生 `unknown`、两条必留出生地及出版社缺口；最终原始回复为 `{"selected_triplets": [], "predict_answer": [], "subsequent_question": ["What is the publisher of The Glass Orchard?"]}`。生产一致性门槛以 `decision_consistency / decision_required_omitted` 拦截，旧空状态未变。该结果证明本轮最终选择仍漏掉必要证据，不证明真实移除或屏蔽通过；关系在未来图出现时能否对齐 `published_by` 也未验收。首个失败后未再请求，未补选或重试。
>
> 两份私有报告为 `tmp/tmp_evidence_decision_step3_tagged_20261109T074916123911Z.json` 和 `tmp/tmp_evidence_decision_chains_20261109T075012307756Z.json`，入口为同名前缀 `.py`。报告保存完整请求、schema、原始回复、派生结果和快照；自动检查结论须结合本段人工纠正阅读。新增事务免费精确请求重放及逐请求模型／用量核对通过。共 $3$ 次事务、$6$ 次逻辑调用和 $6$ 次成功 HTTP 请求，均为 `gpt-4o-mini-2024-07-18`，无传输重试或排序；完整图批输入／输出 $3152/352$ tokens，额外首轮 $1612/163$ tokens，合计 $4764/515$ tokens。实际用量已追加 `logs/llm_usage.csv`；私有文件已从磁盘复核，无配置变更。
>
> **不能推进下一步**：Silver 条件原文抽取仍有语义错误，本批停止，候选不推广。按本方案，下一项应依据已保存反例另定允许推理模型的评估范围，不能继续追加等价 schema／措辞或对同一冻结候选付费碰运气；本批不自动切换模型。最终选择在条件未知时漏选必要证据亦为独立待解决问题；两条真实跨轮链的保留、移除、持续屏蔽尚未通过，本问题和 TODO 2.3.5 继续开放。

   **题型绑定方案执行后的补充：修正验收放行，限定模型评估，先验证未知条件下的证据保留**（2026/11/09 制定并执行；前置事务最终回复格式失败，本批停止）。

   已逐项核对上述两份原始报告、`tmp/tmp_evidence_decision_step3_tagged.py`、`tmp/tmp_evidence_decision_chains.py`，以及当前抽取、图求值、最终提示和状态发布代码。本次应分别处理首阶段语义、最终选择与验收放行，不能把它们合称为条件推理失败，也不能因离线检查通过就继续推进。以下归因不需要新增 API 调用。

   | 环节 | 已确认的原因与影响 | 不能据此推断的内容 |
   | --- | --- | --- |
   | Silver 条件原文 | `condition_quote` 引用了单出版社前提；`decode_description` 只检查非空和逐字来源，随后移除该字段。实际求值使用另行提取的主体、关系、比较值和唯一性依据，因此仍能算出 `true` 并回答正确。现有 schema 没有保证引用与这些字段描述同一条件。 | 这是引用角色错误，不能说 Silver 已算错条件，也不能用正确城市证明完整抽取正确。 |
   | 未知条件下的最终选择 | 原始最终请求确实包含两条出生地、两个人物对象、两条 `required_triplets` 和出版社缺口，并明确要求未知时保留候选。模型仍返回空选择；`validate_decision_response` 在构造新快照、计算永久拒绝之前拦截。直接失败点是最终生成没有遵守已给出的必留约束。 | 本次没有丢失已发布记忆，也没有新增拒绝；不能归因于检索、阈值、图匹配漏项，或声称永久丢失已修复。为什么模型忽略约束，现有输出不能证明。 |
   | 缺事实时的关系标识 | 完整图抽出 `published_by`，无出版社图抽出 `publisher`。当前协议允许关系缺席时描述需求，求值器又逐轮重新抽取并精确匹配；该轮得到 `unknown` 符合现有输入。 | 这不是本轮漏选出生地的原因；若下一轮见到事实仍使用 `publisher`，才会造成出版社存在却匹配不到。无需现在新增别名表或强行替换关系。 |
   | 验收放行 | 私有脚本的 `extracted_condition` 复用了生产的来源检查，没有检查分支条件语义，故两图的自动 `passed` 均为真；跨轮入口自身也没有检查第 $3$ 步的人工结论。精确重放只能重现这个过宽判据。现有模拟检查预填条件及最终选择，验证的是接线和拦截，不能证明真实生成能力。 | 这是可修复的验收缺口；提前启动的一轮只能保留为失败证据，不能追认为已获准的跨轮验收。 |

   **执行顺序调整**：先修验收，再冻结现有生产协议，只评估一个允许模型。将已失败的未知条件首轮前置为筛选，通过后验收两张完整图，再续跑真实跨轮状态；该前置不表示原第 $3$ 步通过。这样先处理已确认的最终漏选，并复用成功状态，避免完整图通过后才再次发现同一阻塞。

   - **先免费修复验收入口，不继续改生成协议**：在私有验收脚本中分开记录结构／事务结果与人工语义结论；运行成功不再自动等于语义通过。针对固定原题，条件引用须确实表达“由指定出版社决定分支”的等值条件，唯一性引用须支持单出版社，两分支的对象、需求和方向逐项审阅；不强求引用等于某一个固定句长。这些固定预期仅属于验收器，不能进入生产提示或求值代码。用已保存 Silver 回复确认“来源通过、语义失败”，用已保存未知轮确认“两条必留已进入最终请求、最终空选、旧状态未变”；无需重放全部历史实验。后续付费阶段只在上一阶段语义明确通过后显式启动，禁止两个脚本自动串行放行；无人工结论时保持待审，不推定通过。
   - **限定一个模型候选，保留可归因性**：下一批仅评估计划允许的 `gpt-4o`，两阶段及可能的排序使用同一模型；冻结当前原问题、完整输入、schema、提示、图求值、严格解析和必留拦截。选择它是为了在现有严格结构化输出接口下直接检验换模型能否跨过两个已知错误，不预先断言效果优于 mini。[OpenAI 官方结构化输出说明](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)列出兼容的 GPT-4o 快照；[模型页](https://developers.openai.com/api/docs/models/gpt-4o)同时标记已弃用，实际账户可用性以首个正式事务为准，不额外发连通性请求。若不可用或契约不兼容，记录接口阻塞后停止，不降级 JSON、不顺带遍历 `gpt-4` 或其他模型。实施时仅在私有 `.env` 显式选取模型，保留 OpenAI 端点与匹配密钥，在 `finally` 中恢复原配置；共享 YAML 默认值保持原样，逐次保存实际返回模型。
   - **首批最多三个完整题事务，先过最接近损失点的反例**：第一项复用原跨轮首轮的输入，即空记忆、空拒绝集和两条出生地，原问题不变、分数均为 $0.95$、严格阈值为 $0.7$，通过公开 `manage_memory(..., prompt_variant="evidence_decision")` 实际运行两阶段。必须抽出正确分支条件及两分支需求，派生 `unknown`、两条必留出生地和仅出版社缺口，最终真实选择两条、无答案且只询问出版社，严格事务及语义均通过。关系缺席时可描述同一出版社需求，不因名称尚非 `published_by` 单独判失败，也不自动做别名映射。任一失败即停；特别是再次空选时直接记录最终阶段失败，不再修改首阶段 schema。成功则保留真实返回快照，待两图通过后作为 Silver 链的第一轮。随后 Bronze、Silver 完整图各一次、各从独立空状态开始，人工确认引用角色、条件主体／精确 `published_by`／比较值／唯一性、分支需求及第 $3$ 步全部状态、路径、答案门槛；两图均通过才续跑链路。首批最多 $9$ 次逻辑调用、$27$ 次 HTTP 尝试，不重跑 mini 基线，不补做人物、出版社或等价措辞实验。
   - **通过就续跑剩余五轮，把关系风险和真实移除一并验收**：Silver 第二轮继承前置事务的真实快照，仅补 Silver 出版社，随后第三轮高分重输完整图；Bronze 从独立空状态执行原第 $4$ 步三轮。第一轮的别名描述不必逐字继承，但出版社出现后必须抽取精确 `published_by`，从完整图找到条件证据并消除条件缺口；若仍为 `unknown`，对照本轮原始抽取与匹配记录，区分关系抽错和求值代码错误。继续逐轮核对适用出生地及出版社保留、无关旧出生地移除并永久拒绝、第三轮高分重输仍被过滤。只有两条链全部通过，才记作该模型下受控证据保留／移除问题通过；首轮成功但旧证据始终不移除仍为失败。不得用完整图事务的状态替代真实继承状态。剩余最多 $15$ 次逻辑调用、$45$ 次 HTTP 尝试；连同首批仍为 $8$ 次事务、最多 $24$ 次逻辑调用和 $72$ 次 HTTP 尝试，正常单答案路径共 $16$ 次逻辑调用。前置首轮只计一次，未经许可启动的历史失败轮不计入本批通过数。
   - **按首次失败收敛，不续开无边界实验**：抽取语义失败就保留该原始反例，最终漏选则同时保留正确派生结果和最终请求，图匹配或过滤失败则只检查相应确定性代码；只有后两者确认是代码缺陷时才直接修复并跑受影响检查。同一冻结模型与协议不重复付费，也不把“加强校验后成功阻止丢失”当作“模型已正确保留”。若 `gpt-4o` 也不能通过，本批结束并明确现有两阶段候选未达到门槛，不再自动扩写 schema、追加措辞或批量换模型。若它全部通过，结论仅为该模型及这些受控用例通过，不能关闭要求 `gpt-4o-mini` 的 TODO 2.3.5 或自动推广；恢复 mini 配置后，后续须有明确的 mini 修复候选并完成其真实验收，或另行取得交付模型／门槛变更授权。

   制定时仅补充归因、评估范围和验收顺序；实际执行结果如下。第 $5$ 步的交付门槛继续适用，本问题与 TODO 2.3.5 保持开放。

> 2026/11/09 已执行本补充。新增私有入口 `tmp/tmp_model_evaluation.py`，将事务结果与人工语义审阅分开，审阅默认待定且不自动启动下一阶段。免费核对两份历史反例：Silver 引用 `The Glass Orchard has exactly one publisher.` 虽来自原题，却是唯一性前提，不能作为分支条件通过；未知轮两条出生地均进入必留集合及最终请求，模型空选被拦截，旧状态不变。固定题的语义预期仅用于私有验收，不进入生产代码。本批保留旧报告，不改写历史自动通过结论。
>
> 冻结现有生产协议，通过公开候选入口执行唯一一个前置事务：原完整问题、独立空状态、两条出生地、全部 $0.95$ 分数、严格阈值 $0.7$。仅在私有 `.env` 临时选择 `gpt-4o`，端点和密钥保持原值；两阶段均实际返回 `gpt-4o-2024-08-06`。`finally` 已逐字节恢复原配置，确认回到 `gpt-4o-mini`；共享 YAML、提示、schema、求值器和解析器未改。
>
> 人工审阅首阶段通过：条件引用为 `If it was published by Silver Press`，唯一性引用为 `The Glass Orchard has exactly one publisher.`；主体、`published_by`、`eq`、`Silver Press` 正确，两分支分别为 `Mira Vale`、`Nolan Reed` 的出向 `born_in` 需求。代码派生 `unknown`、两条必留出生地和唯一的出版社缺口；最终请求确实包含这些结果。此次没有复现先前的条件引用角色错误。
>
> 最终阶段以 `json / invalid_json` 失败：原始正文有 JSON 代码围栏，严格解析拒绝，尚未执行最终选择的一致性与发布校验。原始回复如下（外层使用四反引号以展示模型实际围栏）：
>
> ````text
> ```json
> {
>   "selected_triplets": [["Mira Vale", "born_in", "Linden City"], ["Nolan Reed", "born_in", "Cedar City"]],
>   "predict_answer": [],
>   "subsequent_question": ["The Glass Orchard published_by ?"]
> }
> ```
> ````
>
> 正文内容包含两条候选、空答案和仅出版社查询，但格式不合约，不能把人工读出的正确内容计为端到端保留通过。未剥围栏、补选、重试或修改生产协议；记忆、拒绝和展开排除仍为空。未执行 Bronze／Silver 完整图，也未续跑剩余五轮。
>
> 私有报告 `tmp/tmp_model_evaluation_20261109T081642950982Z.json` 保存完整请求、schema、原始回复、需求匹配、快照及独立人工审阅；新增两次调用的消息及请求选项精确免费重放、逐请求实际模型与用量核对通过。共 $1$ 次事务、$2$ 次逻辑调用、$2$ 次成功 HTTP 请求，无重试或排序，输入 $1613$、输出 $181$ tokens；实际用量已追加 `logs/llm_usage.csv`。HTTP 成功不代表应用验收成功。
>
> **不能推进下一步**：前置事务未通过严格格式门槛，现有两阶段候选在本模型下仍未达到门槛，本批结束，不自动更换模型或继续付费。后续问题明确为最终生成阶段没有遵守裸 JSON 契约；须另行制定针对该阶段的修复方案，不能用此次首阶段正确或原始正文包含证据宣布问题已修复。两张完整图及真实跨轮保留、移除、持续屏蔽仍未验收；候选不推广，TODO 2.3.5 保持开放。

> 2026/11/09 用户授权现场排查、修复并继续推进后，已修复上述最终回复格式缺口：在 `memory_response.py` 定义三字段的命名严格 schema，`memory_update.py` 仅对 `evidence_decision` 最终请求传递 `response_format`。这将最终阶段从提示约束改为接口结构约束，依据 [OpenAI 官方结构化输出说明](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)。schema 约束字段及数组类型，三元组长度、精确成员、必留证据及答案来源仍由原解析与一致性校验执行；拒绝、截断、围栏或非法正文仍失败，不剥围栏或补选。默认、`paper` 和排序请求行为保持原样。
>
> 新增离线检查覆盖最终 schema 接线、旧围栏反例仍原子失败、遗漏必留项仍被拦截和其他提示变体请求不变；真实 HTTP adapter 的重试测试现覆盖决策与最终两个 schema，并逐次核对记账。全部 $111$ 项离线测试通过。这些结果仅确认接口与事务契约，不保证模型正确抽取事实需求。
>
> 冻结 `final_strict_schema_v1` 后，用 `gpt-4o` 按本补充顺序逐项运行并人工复核，私有入口为 `tmp/tmp_final_schema_validation.py`。每次调用只运行一个显式阶段，下一阶段要求此前报告有明确人工通过记录，不自动串行放行；首轮成功快照真实继承，完整图快照不替代跨轮状态。原问题、分数、阈值、完整输入、首阶段协议与求值器未改；每次均在 `finally` 中逐字节恢复原 mini 配置。
>
> | 事务 | 结果 |
> | --- | --- |
> | 未知条件前置／Silver 第 $1$ 轮 | 抽取和方向正确；保留两条出生地、无答案，仅查询出版社。格式、严格事务与人工语义通过。 |
> | Bronze 完整图 | 条件为 `false`，正确保留 Bronze 出版社及 Nolan 出生地，回答 `Cedar City`，无追问；通过。 |
> | Silver 完整图 | 条件为 `true`，正确保留 Silver 出版社及 Mira 出生地，回答 `Linden City`，无追问；通过，可以进入跨轮验收。 |
> | Silver 第 $2$ 轮 | 继承前置快照，仅补出版社；回答正确，移除旧 Nolan 出生地并永久拒绝；通过。 |
> | Silver 第 $3$ 轮 | 高分重输完整图，Nolan 出生地在提交前被过滤，正确路径、答案及拒绝保持；Silver 链通过。 |
> | Bronze 第 $1$ 轮 | 独立空状态，正确保留两条候选、无答案，仅查询出版社；通过。 |
> | Bronze 第 $2$ 轮 | 首阶段把两分支的 `born_in` 方向抽为 `incoming`；最终仅选出版社，空答并追问已有出版社，必要出生地被纳入返回快照的永久拒绝；语义失败，立即停止。 |
>
> Bronze 第 $2$ 轮的原始需求为 `{"true":{"targets":["Mira Vale"],"fact_queries":[{"target":"Mira Vale","relation":"born_in","direction":"incoming"}]},"false":{"targets":["Nolan Reed"],"fact_queries":[{"target":"Nolan Reed","relation":"born_in","direction":"incoming"}]}}`。提交图中的人物均在出生地三元组头部，正确需求应为 `outgoing`。条件主体、`published_by`、比较值、唯一性和实际 Bronze 事实正确，代码也正确求得 `false`；首个语义错误是需求方向，并非条件比较、关系别名或过滤错误。
>
> 错误方向导致匹配结果为空，派生必留集合仅有出版社，并虚报 Nolan 的入向 `born_in` 缺口。最终原始回复为 `{"selected_triplets":[["The Glass Orchard","published_by","Bronze Press"]],"predict_answer":[],"subsequent_question":["Who is the publisher of The Glass Orchard?"]}`。它满足当前结构与派生必留门槛，生产入口确实返回了拒绝两条出生地的新快照；不能写成“生产拦截、状态未发布”。原输入快照仍不可变，私有验收器判失败后未将该返回快照用于下一轮，未运行 Bronze 第 $3$ 轮。最终追问也未遵循已派生的出生地缺口。正确 JSON 不能解决这个语义与阶段契约缺口。
>
> 本批共 $7$ 次事务、$14$ 次逻辑调用及成功 HTTP 请求，无重试或排序；均为 `gpt-4o-2024-08-06`，输入 $11583$、输出 $1151$ tokens。全部最终正文均为裸 JSON，先前围栏阻塞在本批未复现。各事务的消息／schema 精确免费重放与逐请求用量核对通过，实际用量追加 `logs/llm_usage.csv`。私有报告前缀为 `tmp/tmp_final_schema_`，依序后缀为 `screen_20261109T082506306879Z.json`、`bronze_full_20261109T082519934869Z.json`、`silver_full_20261109T082534647370Z.json`、`silver_2_20261109T082551732787Z.json`、`silver_3_20261109T082605047002Z.json`、`bronze_1_20261109T082618854742Z.json`、`bronze_2_20261109T082635302736Z.json`；完整原始抽取、逐需求匹配、前后快照及人工结论均保留。
>
> **已推进至跨轮验收，但不能推进采用／交付**：格式缺口已现场修复，两个完整图及 Silver 链通过；Bronze 链在第 $2$ 轮失败，本批结束。剩余关键问题是模型能生成结构合法但方向错误的需求，使派生必留集合漏掉实际必要证据，现有一致性检查无法识别；最终阶段还可能重复查询已知事实。后续须针对需求方向与图证据的绑定、发布前语义矛盾提出修复，不能硬编码 `born_in` 方向或把自动翻转／补选记作模型通过。本候选不推广；GPT-4o 的局部通过也不替代 TODO 2.3.5 要求的 mini 真实验收，该关卡继续开放。

   **最终结构化输出修复后的补充：绑定事实端点，先修 Bronze 方向失配**（2026/11/09 制定并收窄方案，尚未实施）。

   已复核“题型绑定方案执行后的补充”及其后续现场修复记录，重点比对 `tmp/tmp_final_schema_bronze_full_20261109T082519934869Z.json`、`tmp/tmp_final_schema_bronze_1_20261109T082618854742Z.json`、`tmp/tmp_final_schema_bronze_2_20261109T082635302736Z.json` 和私有验收入口，并核对当前抽取、匹配、一致性检查和拒绝计算代码。本次只制定下一项修复，不运行付费请求，不把此前的格式修复、Silver 链通过或人工阻止续轮等同于证据丢失问题已经解决。

   **进度与本批范围**：上一冻结版本在 GPT-4o 下已通过原第 $3$ 步的两张完整图，实际阻塞位于第 $4$ 步 Bronze 第 $2$ 轮；修复后的候选仍须复验，旧版本通过不自动转移，GPT-4o-mini 交付门槛也未通过。下一项只修“需求方向与实际图端点脱节”，不同时建设追问枚举协议。本题方向正确后，Nolan 出生地应进入必留集合，出版社与出生地均齐全，`missing_relations` 应为空；现有检查已要求必须作答、不得追问。因此先用这一个修改验证整条故障链能否消失，避免同时改两个阶段而无法归因。

   **归因落点**：最新阻塞已从 JSON 格式转为“错误需求使必要证据脱离保护”。Bronze 第 $1$ 轮成功保留两条出生地，第 $2$ 轮提交图仍确实包含它们及新出版社；不存在此次检索未召回、阈值隐藏或历史记忆漏传。更直接的对照是：Bronze 完整图与 Bronze 第 $2$ 轮的首阶段 `messages`、`response_format` 逐字相同，实际模型同为 `gpt-4o-2024-08-06`，却分别生成 `outgoing` 和 `incoming`。因此不能将差异归因于首阶段的新旧证据来源或跨轮上下文；公开入口每轮重新抽取，先前的正确需求没有作为约束继承。相同请求出现不同抽取证明本批生成不稳定，但不足以说明模型内部为何反转方向，也不足以凭一次成功估计稳定性。

   | 失效环节 | 可确认的执行链与代码缺口 | 本次处理目标 |
   | --- | --- | --- |
   | 需求与事实脱节 | [`evidence_decision.py`](../src/agent/evidence_decision.py) 接受合法枚举 `incoming`，只校验目标归属、类型和需求覆盖，未要求有图中端点依据。`derive_decision` 因而只在三元组尾部查找人物；两条出生地中的人物实际都在头部。 | 已有事实的方向改由可校验的图绑定派生，避免让模型重复猜方向。 |
   | 空匹配被当作真实缺口 | 错误方向匹配为空后，代码直接生成 Nolan 的入向 `born_in` 缺口；必留集合因此只剩出版社。`required_triplets` 来自同一份错误需求，最终子集检查无法独立发现它漏掉了必要出生地。 | 区分真实未检索到、需求与现有图冲突、已找到事实，冲突不能继续派生缺口或发布。 |
   | 最终追问偏离决策 | 最终阶段只选出版社、空答，追问已有出版社。`validate_decision_response` 仅在无缺口时要求作答并停止追问；一旦存在任何缺口，就不核对追问是否对应该缺口。因此本轮同时绕过必留检查和追问约束。 | 本批先消除虚假缺口，复用现有无缺口检查；真实缺口下的追问约束保留为独立问题，不预先扩建协议。 |
   | 错误拒绝被合法返回 | [`memory_update.py`](../src/agent/memory_update.py) 按通过检查的最终选择计算省略项，两条出生地都进入新快照的永久拒绝集合；原快照不可变不代表新快照正确。私有人工门槛只阻止它进入下一轮，生产接口本身没有拦住。 | 在构造返回快照前阻断上述矛盾，仍保留成功事务的原永久拒绝规则。 |

   **直接实施一个修订候选，先免费验证，再复验原失败路径**：生产修改集中在 `evidence_decision.py` 的事实需求抽取、校验及匹配，配套修改 `tests/test_evidence_decision.py`，必要时只补固定诊断码映射。仅用于显式 `evidence_decision` 候选；保留现有题型绑定、条件求值、完整提交图、最终提示及三字段 schema、严格解析、多答案排序和永久拒绝规则。不改题目措辞、不新增关系别名、不更换模型、不缓存跨轮抽取结果来掩盖本轮错误。

   - **把已有事实的需求改为可验证的端点绑定**：集中修改 `evidence_decision.py` 的事实需求 schema、解码及图匹配。保留分支对象和所需关系，将需求分成 `observed` 与 `missing` 两种绑定。`observed` 引用当前完整图的稳定三元组编号，不再由模型填写方向；代码核对编号有效、关系逐字一致、目标确实位于相应事实的头部或尾部，再由该位置派生查询方向。编号只在本次请求内有效，不能跨轮复用。多个引用必须支持同一查询方向；自环或两侧均可充当目标且无法唯一确定角色时明确失败，不按编号顺序任选。由绑定得到查询后，仍扫描完整图收集同一目标、关系、方向的全部匹配事实，不能只保留模型引用的一条而漏掉其他合法答案。模型仍负责判断该关系是否满足原题；编号和端点校验只能排除结构性错绑，不能证明语义。
   - **缺失声明不得绕过图证据核对**：`missing` 只描述目标、关系和待检索方向，不携带预测答案；代码同时记录所声明方向及另一方向的精确命中。声明方向已有事实时拒绝“缺失”声明；仅另一方向命中时，作为需求／图绑定冲突终止事务，不自动反转方向、合并两侧事实或补选。两侧均无命中才允许作为本候选的缺口继续。这里的反向命中是保守的冲突信号，不证明原查询已满足；确实缺入向事实但存在同关系出向事实的合法问题也可能被拒绝，须将其列为候选支持范围限制，不能宣称实现了通用方向推理。先用这个边界保护已确认的损失路径，不为未发生的问题建设关系本体或别名系统。
   - **用最小离线对照确认修改作用，再冻结候选**：先用保存的 Bronze 第 $2$ 轮抽取和最终回复，经公开 `manage_memory` 免费复现旧版返回拒绝两条出生地的快照。只将方向改为正确值、保持原最终回复，确认 Nolan 出生地进入必留集合、`missing_relations` 为空，原必留检查因最终漏选而拦截；再分别以“选齐必要证据但仍空答”“选齐并作答但仍问出版社”核对现有无缺口检查，不新增追问协议。这些修改回复的对照只证明因果与接线，不计作模型通过。修订候选检查有效绑定可派生正确方向和完整必留集合，非法编号、关系／目标错绑、方向歧义及伪缺口在最终请求前失败；覆盖合法入向、出向、多匹配事实、两侧均无事实及保守拒绝边界。失败使用非空旧记忆及既有拒绝集合验证原子性；复用已有未知条件保留、旧证据移除、过滤和排序检查。仅适配首阶段 schema 的接线检查，运行受影响测试和完整离线回归后冻结，不另建诊断框架或重放全部历史批次。
   - **第一批只运行 Bronze 前两轮，直接判断是否修好损失路径**：沿用此次失败的 `gpt-4o`，原 `BRANCH_QUESTION`、$0.95$ 分数、严格阈值 $0.7$ 及公开候选入口不变；最小调整现有私有验收入口以保存绑定结果，沿用逐项人工审阅。先从独立空状态执行 Bronze 第 $1$ 轮，再仅补出版社执行第 $2$ 轮，必须真实继承首轮成功快照；这是故障前置回归，不提前认定修订版已通过原第 $3$、$4$ 步。首轮须保留两条候选、无答案、只问出版社；次轮须派生 `false`、正确 Nolan 出向需求及实际两条必留事实、无缺口，最终回答 `Cedar City`、无追问，仅移除并拒绝 Mira 出生地。必须整笔事务成功且人工语义通过；需求被拦截、最终被拦截或答案正确但必要证据已被拒绝都算失败。首个失败即停，不在本批顺手追加新的修复或付费请求。
   - **两轮通过才恢复原验收，复用本批成功状态**：用同一冻结版本完成 Bronze／Silver 两张独立完整图验收，逐项核对题型、分支条件引用、唯一性、对象、关系、绑定端点、必留路径、答案与拒绝。均通过后，从本批已通过的 Bronze 第 $2$ 轮快照执行高分重输第 $3$ 轮，再执行 Silver 三轮链。前置两轮计入 Bronze 链，不重新运行；旧版本成功快照及此前失败返回快照均不得续用。只有新版本两张完整图与两条链都通过，才记作当前受控问题修复通过，不能用测试数量或首阶段抽取正确代替最终行为验收。

   **失败后的处理边界**：绑定或条件抽取失败，就处理该首阶段实际错误；绑定及派生决策已正确，而最终仍漏选、空答或重复追问，则记录为最终阶段失败，不能反过来继续修改方向协议。现有门槛拦住这种回复意味着防护有效，不意味着任务完成。只有后续确实出现“存在真实缺口，最终却追问已有事实”的独立反例，才另定追问绑定的最小修复；本批取消原先预设的追问候选生成、动态 `enum` 及其专项测试。所有失败均保留原始记录，不补选、补答、翻转错误回复、解除屏蔽或业务重试，也不为同一版本追加样本碰运气。

   **分批预算**：第一批仅 $2$ 次 MSM 事务，正常 $4$ 次逻辑调用，保守最多 $6$ 次逻辑调用、$18$ 次 HTTP 尝试。通过后才执行余下 $6$ 次事务；全套合计仍最多 $8$ 次事务，正常 $16$ 次逻辑调用，保守最多 $24$ 次逻辑调用，按现有额外重试 $2$ 次最多 $72$ 次 HTTP 尝试。每项须经事务结果与独立人工语义审阅后才显式启动下一项；保存消息、schema、编号绑定、双向匹配、派生缺口、原始最终回复及返回前后快照，只免费重放新增事务和受影响反例，真实用量照常记账。执行时仅在私有 `.env` 选取当前 GPT-4o，保留 OpenAI 端点及匹配密钥，并在 `finally` 中逐字节恢复原配置；不改变共享默认值。

   本方案尚未执行，本次不修改生产代码、测试、私有 `.env` 或用量日志。GPT-4o 全部门槛通过后也只说明本候选通过这些受控用例；推广及交付仍须满足原第 $5$ 步，并恢复、核对 OpenAI 的 `gpt-4o-mini` 配置，单独完成 TODO 2.3.5 要求的真实验收，不能用 GPT-4o 结果替代。本问题和交付关卡继续开放。

> 2026/11/09 已执行“最终结构化输出修复后的补充”：事实需求改为 `observed`／`missing` 绑定。前者引用本轮排序图编号，检查编号、精确关系、目标端点及唯一方向，再扫描完整图保留全部匹配；后者记录声明方向，仅两侧均无事实才允许缺口。自环、混合方向、错绑及伪缺口在最终请求前失败。反向命中采用保守拒绝，可能拒绝合法的另一方向问题，不代表通用方向推理。默认流程、最终 schema、永久拒绝及共享配置不变。
>
> 免费对照经公开入口复现旧版 Bronze 第 2 轮拒绝两条出生地；只修方向、保持最终回复触发 `decision_required_omitted`；选齐但空答触发 `decision_answer_required`；选齐作答但仍追问触发 `decision_unnecessary_questions`。记录为 `tmp/tmp_endpoint_causal.json`，仅证明因果与接线。全部 $112$ 项离线回归通过，覆盖有效双向绑定、多匹配、空图、非法编号与端点、歧义、伪缺口及非空旧状态的失败原子性；字符、语法及差异检查通过。
>
> 冻结后 GPT-4o Bronze 第 1 轮保留两条出生地、无答案、只问出版社；第 2 轮真实继承首轮状态，仅补 Bronze 出版社，正确派生 `false`、Nolan 出向需求、出版社及出生地必留、无缺口，回答 `Cedar City`、无追问，仅移除并永久拒绝 Mira 出生地。人工审阅通过后推进独立 Bronze 完整图，也通过条件、绑定、答案及状态门槛。成功报告为 `tmp/tmp_endpoint_binding_bronze_1_20261109T085508419870Z.json`、`tmp/tmp_endpoint_binding_bronze_2_20261109T085519385957Z.json`、`tmp/tmp_endpoint_binding_bronze_full_20261109T085531169576Z.json`。
>
> Silver 完整图首阶段失败并立即停止：分支 `targets` 是正确人物，但 `fact_queries.target` 分别填为 `Linden City`、`Cedar City`，引用编号分别为 $0$、$1$。端点本身有效，却是答案城市，违反查询对象必须属于分支人物的约束；实际诊断为 `evidence_decision / decision_queries`。未调用最终阶段或发布快照，输入记忆、拒绝及展开排除保持空集。原始记录为 `tmp/tmp_endpoint_binding_silver_full_20261109T085543186636Z.json`。没有自动改回人物、补选或业务重试，Bronze 第 3 轮及 Silver 三轮链均未启动。
>
> 本批共 $4$ 次事务、$7$ 次逻辑调用及成功 HTTP 请求，无传输重试、无排序，均为 `gpt-4o-2024-08-06`，合计输入／输出 $6805/701$ tokens。各项免费精确请求重放及逐条用量核对通过，每轮均在 `finally` 中逐字节恢复私有配置。入口 `tmp/tmp_endpoint_binding_validation.py` 和报告已从磁盘复核。
>
> **不能继续剩余跨轮验收或推进采用／交付**：Bronze 原损失路径在本批通过，但 Silver 查询对象语义失败。下一项须针对首阶段将答案端点当作分支查询对象的实际反例提出修改；本批停止，不重复付费。候选仍需显式选择，本问题及 TODO 2.3.5 保持开放，GPT-4o-mini 交付验收未完成。

   **端点绑定复验失败后的补充：按查询实体组织需求，消除对象重复生成**（2026/11/09 制定并执行；完整图通过，跨轮首轮最终追问语义失败）。

> 2026/11/09 已执行本补充。修改前精确重放旧 Silver 请求及回复，仍为 `evidence_decision / decision_queries`，仅首阶段调用，空旧状态不变。线上分支改为 `requirements: [{entity, fact_queries: [{relation, binding}]}]`，移除重复生成的 `targets`／`target`；解码严格校验分组并按所属实体展开内部对象与查询，线上与内部结构分别检查，不修补旧回复。条件求值、端点绑定、完整图匹配及发布规则保持原约定。全部 $113$ 项离线测试通过，覆盖多实体／多关系传递、旧字段与空组拒绝、未知与非条件题、合法入向、错绑及伪缺口的原子失败；字符、语法及差异检查通过。模拟通过不证明实体角色语义正确。
>
> 冻结候选后，依次执行 Silver、Bronze 独立完整图及 Silver 跨轮首轮，共 $3$ 次事务。两张完整图的原始分组均正确填写 `Mira Vale`／`Nolan Reed`、`born_in` 及编号 $0$／$1$；派生状态分别为 `true`／`false`，适用人物的出向出生地与实际出版社均必留、无缺口，最终分别回答 `Linden City`／`Cedar City` 且无追问，必要证据未被拒绝。逐项人工复核通过后才启动下一事务；两图通过，因此已推进第 $4$ 步，未转用完整图快照作为跨轮输入。
>
> Silver 跨轮首轮从独立空状态仅提交两条出生地。首阶段原始分组、编号及派生 `unknown` 正确，必留两条出生地，唯一缺口为书籍的 `published_by`；最终保留两条事实、无答案且拒绝集合为空。但最终追问原文为 `["What is the city where The Glass Orchard was published by Silver Press?", "Was The Glass Orchard published by Silver Press? If so, in which city did Mira Vale's birth occur?"]`。第一问错把出版城市作为需求，第二问混入已知出生地；未满足只追问缺失关系的语义门槛。自动 `publisher_question` 检查仅匹配 `publish`，给出通过，人工已明确记录 `clarification_semantics=false`、不批准后续继承；不能把自动通过写成整轮通过。生产入口返回了保留两条必要事实的快照，但验收未通过，没有错误拒绝必要证据，也没有继承该快照或启动后续轮次。
>
> 私有入口 `tmp/tmp_grouped_requirements_validation.py`；报告依次为 `tmp/tmp_grouped_requirements_silver_full_20261109T091132655445Z.json`、`tmp/tmp_grouped_requirements_bronze_full_20261109T091143875522Z.json`、`tmp/tmp_grouped_requirements_screen_20261109T091157703849Z.json`。报告保存原始分组、规范化需求、端点匹配、派生结果、请求选项及前后状态；全部新增事务精确免费重放、用量逐次核对通过。实际 $6$ 次 HTTP 请求均为 `gpt-4o-2024-08-06`，无重试或排序，合计输入 $5911$、输出 $569$ tokens；实际用量已记入 `logs/llm_usage.csv`。每次事务后私有 `.env` 均逐字节恢复为原 mini 配置。
>
> **已推进第 4 步，但不能推进第 5 步或交付**：本批在 Silver 首轮最终追问语义失败后停止，未继续 Silver 后两轮或 Bronze 三轮链，候选仍需显式选择，TODO 2.3.5 保持开放。下一项应针对最终阶段在已校验唯一缺口下仍生成错误关系和重复已知事实的追问提出修复，同时强化验收器的追问语义复核；原始分组与内部求值正确，不继续重写首阶段、不重复付费运行同一候选。本批没有证明新版真实旧记忆移除及高分重输过滤，GPT-4o-mini 交付验收仍待完成。

   已逐项复核上述四份 `tmp/tmp_endpoint_binding_*.json` 报告、`tmp/tmp_endpoint_binding_validation.py`、当前 `evidence_decision.py`、记忆发布代码及相关测试。本补充接替上一方案的后续执行顺序：先修最新 Silver 首阶段反例，再用修订候选重做完整题与跨轮验收；不直接续跑旧候选的 Bronze 第 $3$ 轮。本次仅制定方案，不修改生产代码、测试、私有配置或用量，不发起 API 请求。

   **归因与证据边界**：端点绑定确实在本批 Bronze 前两轮及完整图中得到正确使用，第二轮也真实移除并拒绝了无关旧出生地；这只证明本批三次事务通过。Silver 是新的首阶段阻塞：格式、关系名和事实编号均有效，但同一份回复对“查询实体”给出了相互矛盾的两个表示。它被生产入口拦截，未进入最终作答，未返回新记忆；不能把此前 Bronze 的错误拒绝归到本次 Silver，也不能把正确拦截视为端到端修复。

   | 环节 | 本次可确认的事实 | 对下一项修改的含义 |
   | --- | --- | --- |
   | 输入与抽取 | Silver 完整图含两条出生地及实际 Silver 出版社。两分支 `targets` 分别正确填写人物，`fact_queries.target` 却分别填写 `Linden City`、`Cedar City`；对应 `born_in` 和编号 $0$、$1$ 正确。 | 不是召回、阈值、旧记忆传递或关系别名问题；错误在查询实体与待回答值的角色混用。 |
   | 端点绑定与实际拦截 | `resolve_query` 单独处理城市时，可以按同一三元组尾部合法派生 `incoming`；随后 `derive_decision` 检查查询对象属于分支人物时，触发 `decision_queries`。 | 编号与端点检查只证明三元组确实连接该实体，无法证明它是原题的查询对象。应保留此防护，修正上游表示，不能放宽为任一端点都可接受。 |
   | 协议缺口 | `BRANCH_SCHEMA` 要求独立生成 `targets` 和每条查询的 `target`，两者都是自由字符串；提示已要求查询对象属于分支，schema 却不能保证这种跨字段一致性，字段本身也没有角色描述。 | 同一个查询实体不应由模型填写两遍。直接改成按实体分组的需求，代码按所属组传递对象，消除这类自相矛盾的合法 JSON。 |
   | 对照与未知项 | Bronze 完整图与 Silver 完整图的首阶段请求选项相同，消息仅图中的出版社值不同，同为 `gpt-4o-2024-08-06`；前者正确，后者角色错配。 | 不支持归因于跨轮来源或请求格式变化；也不能凭这两个不同输入断言 Silver 值导致错误、模型能力不足或方向修复稳定。无需再付费定位已经暴露的字段矛盾。 |
   | 测试覆盖 | 现有正常夹具预先填入一致的人物对象；端点测试验证的是绑定合法性、歧义与伪缺口。 | 模拟回复通过只能证明接线与防护，不能证明真实模型会维持实体角色。新测试应覆盖表示调整及错误边界，真实修复必须回到同一 Silver 完整题。 |

   **只实现一个表示修订，不再增加推理阶段**：修改范围集中在 `evidence_decision.py` 的 `BRANCH_SCHEMA`、`DECISION_INSTRUCTIONS`、`decode_description` 及内外部结构衔接，配套调整现有测试与私有验收器的字段读取。保留当前条件描述与求值、`observed`／`missing` 绑定、完整图匹配、最终 schema 和发布检查。

   - **每个查询实体只生成一次**：分支的线上输出改为 `requirements: [{entity, fact_queries: [{relation, binding}]}]`，这是结构示意。移除线上 `targets` 和查询内的 `target`；每组 `entity` 表示原题要求查询其属性或关系的已知实体，`fact_queries` 仅描述该实体需要的事实，不再填写已检索到的答案端点。提示和 schema 的字段描述同时说明角色，采用通用表述，不注入本题人物、城市、分支答案或固定关系方向。条件题的两个分支及非条件题共用同一分组结构；继续支持一个分支多个实体、一个实体多个关系、尚无事实的实体及合法入向查询，不用单元素数组限制规避问题。
   - **解码时展开所属关系，不修补旧回复**：严格校验分组字段、非空实体、非空事实需求及现有来源约束，再由组内 `entity` 构造内部 `targets` 与每条查询的 `target`，交给现有 `resolve_query` 和完整图求值器。线上 schema 与内部规范化结构分别校验，不能继续拿新 `BRANCH_SCHEMA.required` 检查旧内部字段。各对象仍列全必填字段并禁止额外字段，旧格式或查询内再次带 `target` 均拒绝，不兼容猜测、不读取旧错误城市再改成人物。绑定编号、精确关系、唯一端点、全量同向匹配及双向伪缺口检查照旧；这些结构检查仍不能识别“整组把城市当查询实体”或分支归属错误，必须保留人工语义门槛，不能宣称对象语义已由代码证明。
   - **免费验证只覆盖本次改变的边界**：修改前，用保存的 Silver 请求和原始回复经公开入口精确重放，确认仍为 `evidence_decision / decision_queries`、只有首阶段调用且旧状态不变；保留原报告。新协议检查分组展开能正确传递实体、关系和原绑定，多实体／多关系不会串组，条件未知与非条件题仍派生正确内部对象；空组、旧字段、错绑及伪缺口在最终请求前失败。复用已有非空旧记忆和拒绝集合的原子性、必留遗漏及无缺口作答检查；不要重新建设状态测试或全量重放历史。更新模拟夹具不算模型通过。受影响检查及完整离线回归通过后，冻结一个候选。
   - **第一项付费验证只跑失败的 Silver 完整图**：最小适配现有私有入口，使其保存原始分组、规范化查询、端点匹配、派生决策和最终状态，并继续精确核对消息与 schema。沿用当前 GPT-4o、原 `BRANCH_QUESTION`、完整三条证据、全部 $0.95$ 分数、严格阈值 $0.7$、独立空快照及公开候选入口。人工逐项核对原始两分支的查询实体确为对应人物、关系及绑定正确，派生 `true`、Mira 出向需求、出版社与 Mira 出生地必留、无缺口；最终必须回答 `Linden City`、无追问、必要证据未被拒绝。仅解析成功、分组一致或最终城市正确均不足以通过。只运行一次，失败即停，不先付费重跑已通过的 Bronze，也不单独测试字段抽取或连通性。
   - **Silver 通过后，完成原门槛，沿用同一冻结版本**：先做 Bronze 独立完整图，满足原第 $3$ 步全部门槛，再按原第 $4$ 步执行两条真实三轮链。完整图允许保留无关出生地，跨轮第二轮必须移除无关旧事实且保住必要路径，第三轮必须证明高分重输仍被过滤。旧版本三次成功只作回归依据，不能转移到新协议；新链从空状态开始、逐轮继承本批成功快照，不能挪用完整图快照或此前失败状态。只免费重放本批新增事务与受影响反例，逐项人工通过后才显式启动下一事务。

   **预算与失败分流**：Silver 首项仅 $1$ 次 MSM 事务，正常 $2$ 次、保守最多 $3$ 次逻辑调用，按现有额外重试 $2$ 次最多 $9$ 次 HTTP 尝试；通过后才执行余下最多 $7$ 次事务。整批仍为两张完整图加两条三轮链，最多 $8$ 次事务，正常 $16$ 次、保守最多 $24$ 次逻辑调用及 $72$ 次 HTTP 尝试，不额外叠加上一方案的 Bronze 前置两轮。任一项失败即停止本批：原始组实体或分支错了，定位为首阶段语义失败；原始分组正确而内部对象或匹配错了，修解码／求值；决策和必留路径正确而最终漏选、空答或重复追问，修最终阶段，不继续重写首阶段。保留原文与快照，不补选、补答、解除屏蔽、业务重试或同版本追加样本碰运气。执行时延续上一方案的私有 GPT-4o 配置及逐字节恢复约束；GPT-4o-mini 的 TODO 2.3.5 交付验证仍须单独完成。

   **制定后的绕路审核**：本方案只处理已观察到的“同一实体重复生成后角色不一致”，先验证原失败事务，再完成被首阶段协议变化影响的完整题与跨轮门槛。已排除新增追问协议、动态枚举、关系本体／别名、跨轮抽取缓存、换模型、独立人物／出版社题、措辞对照及旧基线重跑；它们都不是消除本次字段矛盾的前提。没有扩大端点求值器，也没有追加第三个 LLM 阶段或付费格式探测。重做新版本的跨轮链是验证永久拒绝安全性的必要回归，旧通过不能替代；免费模拟仅证明实现，真实首项仍可能失败。方案审核通过，仅表示路径直接、范围与停止点明确，不表示问题已经修复或允许推广交付。

4. **两图均通过后，直接验证真实跨轮状态**：Silver、Bronze 各执行三轮，共 $6$ 次候选 MSM 事务。第一轮仅提供两条出生地，要求条件未知、无答案、追问出版社且保留两条候选；第二轮只补充该图出版社，继承第一轮真实返回状态，要求正确城市、无追问、保留出版社和对应出生地，并移除另一出生地；第三轮高分重输完整图，要求该移除项已被过滤、不再提交，正确路径和答案保持。任一轮失败即停止该批，不人工重置状态、补回必要证据或解除屏蔽。若答案正确但无关证据始终保留，则仅记作作答通过、移除未通过；不据此关闭本问题或 TODO 2.3.5，也不自动追加作者用例。必要证据进入永久拒绝集合即判失败。

   **第 4 步失败后的补充：将最终追问绑定到已求值的真实缺口**（2026/11/09 制定并执行，最终请求 HTTP 失败；结果见下方引用记录）。

   已核对本计划中按查询实体分组的最新执行记录、`tmp/tmp_grouped_requirements_validation.py` 及其调用的 `tmp/tmp_evidence_decision_chains.py`、三份 `tmp/tmp_grouped_requirements_*.json` 报告，并逐项比对当前提示、schema、解析、决策一致性和状态发布代码。本次阻塞是 Silver 跨轮首轮的最终追问，不是此前 Silver 完整图的对象错配或 Bronze 的方向错绑。两张完整图已通过；跨轮首轮虽然生产事务成功返回，人工语义验收失败，不能继承其快照。以下方案接替该失败后的下一项安排，本次只补充文档，不修改生产代码、测试、私有配置或用量，不发起 API 请求。

   **归因：正确缺口没有约束最终查询的含义**。

   | 环节 | 已确认的事实与代码依据 | 结论及修改边界 |
   | --- | --- | --- |
   | 输入、抽取与求值 | 首轮提交两条出生地，原始分组的人物、`born_in` 和编号正确。`derive_decision` 派生 `unknown`，必留两条出生地，`missing_relations` 仅为 `{target: "The Glass Orchard", relation: "published_by", direction: "outgoing", reason: "condition_unknown"}`。 | 出版社确实尚未提供，出生地已经齐全；不归因于召回、阈值、端点绑定或跨轮传递。保留首阶段协议与求值器。 |
   | 最终请求与原始回复 | [`build_decision_memory_prompt`](../src/agent/memory_prompt.py) 已把唯一缺口和两条已匹配出生地完整传给最终阶段，也已要求只问缺失关系；回复却问出版城市，并把“是否由 Silver 出版”与已知 Mira 出生地合成条件问题。 | 最终阶段未遵守已提供的查询边界；事实不是“模型没看到缺口”或“缺少字符串数组说明”。回复混用了原题的城市目标和出版社条件，但无法据此证明模型内部为何混用。继续添加同义提示没有解决依据。 |
   | 请求和发布校验 | [`MEMORY_RESPONSE_FORMAT`](../src/agent/memory_response.py) 对追问只约束字符串数组，解析器检查非空和至多三问。`validate_decision_response` 在 `unknown` 下只禁答；有缺口时不检查追问是否对应目标、关系和方向。必留集合已全选，因此这些检查全部通过。 | 严格 JSON 不能约束字符串含义。这是最终阶段到状态发布之间可确认的契约缺口；应在构造新快照前验证查询与真实缺口绑定，不能仅增加验收告警。 |
   | 实际状态影响 | 返回快照保留两条出生地、答案为空、拒绝集合为空，旧快照不变；人工审阅拒绝继承，后续轮次未启动。 | 本次没有永久拒绝必要证据，也未证明检索器真的无法召回；已确认的是待交给检索器的查询语义错误。不能把原子性通过或没有证据损失算作整轮通过。 |
   | 验收误报与覆盖缺口 | `tmp_evidence_decision_chains.run` 的 `publisher_question` 只要求任一追问含 `publish`，所以自动 `passed=true`；报告另记 `clarification_semantics=false`、人工不批准。现有离线测试主要覆盖正确模拟追问、未知禁答及无缺口禁问，没有用这两条原始错误追问检验发布边界。 | 自动“含关键词”不等于只问缺失出版社。补充本反例的生产拦截和验收核对，保留事务通过与人工语义通过两个独立结论。 |

   **只做一个最终阶段修订：缺口生成查询，模型负责选择，不再自由改写查询**。此前“先不建设追问绑定”的边界针对尚未观察到的风险；本次已出现真实缺口正确而追问错误的独立反例，满足其另定最小修复的条件。修改限于显式 `evidence_decision` 候选且 `missing_relations` 非空的路径，不新增 LLM 阶段，不重写实体分组、条件求值或图匹配。

   - **从现有缺口确定可检索的问题字符串**：在 `evidence_decision.py` 增加小型纯函数，只接收已派生的缺口，按 `(target, relation, direction)` 稳定去重并生成固定查询，不再让模型重新决定查询实体或关系。出向模板可固定为 `Which entity does "<target>" connect to through relation "<relation>"?`，入向模板为 `Which entity connects to "<target>" through relation "<relation>"?`；实体和关系按原串安全引用，方向明确，不填预测值。当前反例因此只允许询问 `The Glass Orchard` 的出向 `published_by` 值，不含 `Silver Press` 答案限定、出版城市或任何出生地。模板对所有关系共用，不建立出版社专用规则、关系别名表或自然语言改写器。保留独立字符串查询接口；这只保证与派生缺口一致，实际 C/E 召回仍由成员 E 集成验证。
   - **在生成端与发布前使用同一候选集合**：有缺口时，最终提示列出上述查询及对应缺口，要求 `subsequent_question` 逐字选用；由本次集合生成最终 schema 的字符串 `enum`，不修改共享的 `MEMORY_RESPONSE_FORMAT` 常量。原三字段和公开 `MemoryResponse` 不变，仍先严格解析，再本地检查追问非空、全部属于该集合、没有重复；条件未知时必须包含 `condition_unknown` 对应的查询。最多三问沿用既有上限，多缺口可以分轮检索，不把未问缺口算作已满足。非法追问以固定诊断码在 `decision_consistency` 阶段终止，不能先返回新快照再让验收器决定。无缺口时保留原提示、schema 和无缺口作答／禁问检查，不构造空 `enum`。不替换模型返回的错误字符串、不事后补问、不补选证据、不追加纠错请求；schema 被拒、回复拒绝或截断也按现有失败边界停止。
   - **免费验证直接围绕这条失败链**：修改前只精确重放保存的 Silver 首轮，确认旧生产入口接受原始错误追问、自动关键词通过而人工失败。修改后将同一首阶段及原始最终回复注入公开入口，要求新追问校验拒绝、没有返回可继承快照；因新最终请求已改变，这项属于旧反例在新边界下的检查，不能声称是新请求的精确历史重放。再用正确候选查询验证保留两条证据、无答案、无拒绝；单独将两条错误追问、正确问题夹带已知出生地问题、错误方向及空追问注入，均不得发布。用一个非空旧记忆和既有拒绝集合的用例复用失败原子性检查。小范围补齐入向／出向、同关系不同实体、稳定去重、多缺口及未知条件必问的检查，确认最终请求实际携带本次 `enum`、全局 schema 未被污染；沿用现有传输重试、严格解析及排序测试，不新建测试框架或全量重放历史。受影响测试和完整离线回归通过后冻结一个候选，模拟查询通过仍不算真实模型验收。
   - **验收器检查全部追问，不再靠任一关键词通过**：最小适配现有私有入口，保存缺口、允许查询集合、最终请求及 schema、原始回复、实际问题、前后状态。自动检查每条问题与允许集合逐字对应，当前首轮必须且仅有书籍出版社这一查询；人工核对实体、关系、方向、开放值查询及没有混入已知事实。`outcome.passed`、追问语义审阅与人工批准分别记录，缺少审阅也不能自动继承；不把 `publish`、正确城市或 JSON 合法作为语义通过的替代。人工还须审阅首阶段的原始分组与派生缺口，避免把“输出忠实于错误缺口”误认成修复。
   - **第一项付费事务就是原失败首轮，通过才继续原跨轮链**：沿用此次实际使用的 GPT-4o、原 `BRANCH_QUESTION`、两条出生地、全部 $0.95$ 分数、严格阈值 $0.7$、独立空快照及公开候选入口，只执行一次 Silver 首轮。要求首阶段和派生结果仍正确，最终保留两条事实、无答案、无拒绝，只返回上述出版社查询；生产事务与人工语义均通过后，才继承本批真实状态执行 Silver 第 $2$、$3$ 轮，再从另一独立空状态执行 Bronze 三轮。次轮必须正确作答、无追问且仅移除无关旧出生地；第三轮必须证明高分重输被永久过滤，必要路径和答案保持。不能续用上批首轮的失败快照，不能把完整图快照拿来跳过首轮。每一事务审阅通过才显式启动下一项，任一失败即停。

   **复用范围、预算与停止点**：两张已通过完整图的缺口均为空，本修订须保持该路径的请求消息、schema、解析及返回行为不变；用上述两份 `silver_full`、`bronze_full` 报告在公开入口免费精确重放验证后，沿用其完整图验收依据，不再付费重跑。若做不到这一点，先收窄修改并重新审核影响，不能未经说明扩大付费批次。本批只重建两条跨轮链，最多 $6$ 次 MSM 事务：首项 $1$ 次，正常 $2$ 次、保守最多 $3$ 次逻辑调用及 $9$ 次 HTTP 尝试；通过后余下最多 $5$ 次。全批正常 $12$ 次、保守最多 $18$ 次逻辑调用，按现有额外重试 $2$ 次最多 $54$ 次 HTTP 尝试；不另叠加两张完整图或独立格式、措辞、连通性探测。真实请求和用量按原入口记账，只免费精确重放本批新增请求及受影响反例。执行时仅在私有 `.env` 临时选择 GPT-4o，保留 OpenAI 端点及匹配密钥，在 `finally` 中逐字节恢复原配置；不改变共享默认值。

   原始分组或派生缺口错误，定位首阶段或求值环节；缺口正确而模板目标／方向错误，修查询生成；候选集合正确而 schema、回复或本地检查不一致，修最终接线及发布校验；追问已正确但证据选择、作答或移除失败，则只处理对应实际失败阶段。所有失败保留原始回复和状态，不放宽校验、不解除屏蔽、不在同一冻结版本上重复付费碰运气。只有两张完整图的未受影响路径重放通过、两条新跨轮链全部通过，才能讨论原第 $5$ 步；GPT-4o 结果不替代 TODO 2.3.5 的 GPT-4o-mini 交付验证，本问题和交付关卡继续开放。

   **制定后的绕路审核**：已把原先只靠自由文本提示的追问约束改为“已有缺口 → 固定查询集合 → 生成约束与本地校验”，直接消除本次出版城市、已知出生地混入的出口，且不增加模型调用。排除重写已正确首阶段、追加泛化提示／模型裁判、换模型、单独人物或出版社题、关系本体及跨轮缓存。完整图已通过且无缺口路径不变，用免费精确重放替代重复付费；跨轮首轮是实际失败点，排在所有新付费事务之前。两条新链用于验证修改后的真实状态继承、旧证据移除和永久过滤，属于尚未通过的必要验收。审核结论：方案未绕路；范围、首个验证点和失败停止点明确，但方案尚未实现，不能记作问题已修复。

   > 2026/11/09 已执行“第 4 步失败后的补充”：新增缺口到固定查询的纯函数，按实体、关系、方向稳定去重并安全引用原串；仅在显式候选且有缺口时追加允许查询提示并构造请求级字符串 `enum`，共享 schema 不变。最终严格解析后，本地拦截集合外、重复及遗漏未知条件查询的追问；空追问仍由原严格解析拒绝。无缺口路径保持原请求与返回行为，失败不发布快照，不补问或追加纠错调用。
   >
   > 修改前免费精确重放原 Silver 首轮，确认生产入口接受两条错误追问，旧关键词检查仍通过。修改后注入相同首阶段和原始最终回复，得到 `decision_consistency / decision_questions`，没有返回快照；由于最终请求已改变，这属于旧反例在新边界下的检查。两张历史 `silver_full`、`bronze_full` 的消息、schema、回复及结果均免费精确重放通过，沿用完整图验收依据，未付费重跑。全部 $115$ 项离线回归通过，包括方向、安全引用、实体区分、稳定去重、schema 隔离，以及正确查询发布和错误追问下非空旧记忆与既有拒绝集合保持不变。
   >
   > 冻结候选后仅启动 Silver 首轮：原 `BRANCH_QUESTION`、两条出生地、全部 $0.95$ 分数、严格阈值 $0.7$、独立空状态和公开入口。GPT-4o 首阶段正确提取两分支，派生状态为 `unknown`，必留集合为两条出生地，唯一缺口为书籍的出向 `published_by`；允许查询为 `Which entity does "The Glass Orchard" connect to through relation "published_by"?`。最终请求返回 `HTTPError`，没有最终回复或可继承快照，输入状态保持空集；已停止本批，Silver 后两轮和 Bronze 三轮均未执行。
   >
   > 本批 $1$ 次事务、$2$ 次逻辑调用、$2$ 次 HTTP 尝试，无额外重试或排序。2026/11/09 17:32:08（UTC+08:00）开始的首请求返回 `gpt-4o-2024-08-06`，报告输入／输出 $1145/132$ tokens；最终请求记为 `gpt-4o` 的 HTTP 错误，未知用量留空，不能当作零用量。实际两行已记入 `logs/llm_usage.csv`；私有 `.env` 已在 `finally` 中逐字节恢复。私有入口 `tmp/tmp_gap_queries_validation.py`，报告 `tmp/tmp_gap_queries_screen_20261109T093208283245Z.json`。原验收器在重放缺失最终回复时触发断言，随后通过 `tmp/tmp_gap_failure_audit.py` 免费核对两阶段请求及 schema 逐字一致、重放首阶段成功回复并重建已记录的 HTTP 异常类；这不代表重放了不存在的最终回复。报告保留人工批准为否。
   >
   > **不能推进第 5 步**：真实首轮仍未通过，候选保持显式选择，本问题和 TODO 2.3.5 继续开放。当前只定位到最终请求的 HTTP 边界；入口未保存 HTTP 状态码或服务端诊断，不能断言 schema 被拒或确定其他原因。下一次应先补充不含凭据、提示或回复正文的传输诊断，核对最终请求契约并针对实际错误修复，再另行冻结验证；不得在本版本上重复付费碰运气。尚缺两条新跨轮链全部通过，以及独立 GPT-4o-mini 交付验证。

   **最终请求 HTTP 失败后的补充：保留错误依据，按实际原因修复请求**（2026/11/09 制定，尚未执行）。

   已核对 `tmp/tmp_gap_queries_screen_20261109T093208283245Z.json`、上一版 `tmp/tmp_grouped_requirements_screen_20261109T091157703849Z.json`、两份完整图报告、验收入口及其记录／重放依赖，并比对 `llm_client.py`、`llm_usage.py`、`memory_update.py`、`evidence_decision.py` 和 `tests/test_gap_queries.py`。本补充接替上述 HTTP 失败后的安排。本次仅制定方案，不修改实现或私有配置，不新增 API 请求及用量；上一批失败状态不得继承。

   **归因：传输失败原因尚缺证据，错误记录和失败重放的缺陷已经确定**。

   | 环节 | 本次可确认的依据 | 结论与处理范围 |
   | --- | --- | --- |
   | 模型与首阶段 | 首请求实际返回 `gpt-4o-2024-08-06`，分组、`unknown`、两条必留出生地及唯一出版社缺口均正确；其消息和 schema 与上一版首轮逐字一致。 | 本轮没有首阶段反例，不重写分组、条件求值、图匹配或提示词，不因最终 HTTP 错误改换模型。 |
   | 最终请求变化 | 两份报告中，移除新 schema 的 `subsequent_question.items.enum` 后，请求选项完全相同；新提示以前一版最终提示为完整前缀，仅追加允许查询及选择约束。枚举只有 $1$ 个、$81$ 字符的查询。 | 排查集中在这份最终请求及实际服务端错误。变化相关不等于因果；不能把 `enum`、引号或动态 schema 直接认定为故障原因。 |
   | HTTP 边界与重试 | 最终调用记录只有 `error_type=HTTPError`，没有 `completion`；对应 CSV 有请求 ID，但没有状态码。客户端在 `raise_for_status()` 或重定向检查处可以抛出此异常；默认额外重试 $2$ 次只用于连接／超时、$429$ 和 $5xx$，本批最终请求实际只尝试一次。 | 已确认不是最终回复解析或追问语义校验失败，也没有新快照。按当前配置和客户端路径，更符合不重试的 HTTP 错误，但仍不能据此补写具体状态码，或断言鉴权、配额、schema 或服务端哪项出错。 |
   | 错误信息丢失 | `LLMClient` 原样抛出异常；终止的 `HTTPError.response` 通常仍携带状态、头及已读取的响应。私有 `RecordingClient.complete` 只保存异常类，`failure_diagnostics` 又仅识别 `MemoryValidationError`，因此报告为 `unclassified`。 | 先在现有私有记录器的异常出口提取有限诊断即可，不需要扩建共享日志、修改 CSV 列或更换 HTTP 客户端。历史文件未保存的状态和错误正文无法靠离线重放恢复。 |
   | 离线覆盖与二次异常 | `GapQueryTests` 明确使用忽略 schema 的模拟传输，只证明模板、请求构造与本地发布拦截。验收入口随后无条件重放，记录器读取缺失的 `completion`，再引发断言；补充审计仅重建了 `HTTPError()` 类。 | $115$ 项通过不证明服务端接受 schema；二次断言是验收器故障，不能替代原始 HTTP 原因。修复失败分支后再取证，不能编造缺失回复或历史服务端诊断。 |

   已查阅 [OpenAI Structured Outputs 官方文档](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)：该接口支持 `gpt-4o-2024-08-06` 和枚举，严格模式还要求遵守其 JSON Schema 子集。当前单个短枚举未显示数量或长度超限，根对象及必填字段约定也未因本次修改变化；这排除了“枚举一概不支持”的解释，仍不能证明这份具体请求已被接受。普通 JSON／JSON Schema 校验器及模拟回复也不能代替服务端契约验证。

   - **先补最小诊断与失败出口，暂不改业务协议**：修改现有私有 `RecordingClient`，在重新抛出终止异常前保存调用阶段、异常类、HTTP 状态、`x-request-id` 及经过白名单检查的服务端 `error.type`、`error.code`、`error.param`；字段缺失记为未知。读取响应须用 `error.response is not None`，不能用响应真假值，否则错误状态可能被误当成无响应。服务端 `error.message` 只在内存中识别已知的 schema 路径及规则，落盘为固定原因码；不保存任意消息、原始错误正文、请求头、密钥或异常字符串，未知文本明确记为未分类。沿用原有私有请求／成功回复留档，新增诊断不复制提示或回复正文。按成功／失败分别处理重放：成功才消费 `completion`；失败核对已记录请求和有限诊断、确认无发布，旧报告只重建已知异常类，不把重建当作复现 HTTP 原因。同步保证报告先保存原故障，清理／审计异常单列，配置恢复继续放在 `finally`。未知 tokens 不记作零；汇总区分已知用量和未知请求数，不改共享 CSV。
   - **免费检查只检验这次缺口**：用模拟 `requests.Response` 覆盖一个带 schema 错误字段的 $400$、缺失／非 JSON 错误体、无响应异常，以及 HTTP 错误响应的真假值陷阱；验证有限诊断、脱敏、原异常传播和无 `completion` 时的失败审计。复用既有失败原子性及用量测试，不重建故障框架。逐项核对保存的最终 schema 与官方子集；若发现确定违反的具体路径，直接修该路径并以它作为回归依据，跳过下项取证请求。现阶段没有这种确定证据，不先删除 `enum`，不安装新校验依赖，也不全量重放历史。
   - **仍无明确原因时，只取证原失败最终请求一次**：在诊断出口检查通过后，直接使用报告中第二次调用的原始 `messages` 和 `response_format`，通过现有 `LLMClient` 发出一次请求，显式标记为 `final_answer` 并接入上述诊断出口；保持 GPT-4o、OpenAI 端点、匹配密钥及其他负载不变，仅在私有诊断客户端将 `max_retries` 显式设为 $0$，不修改共享 YAML。无需重跑已经保存且正确的首阶段，也不另发连通性、简化 schema 或字段抽取请求。诊断最多 $1$ 次逻辑调用、$1$ 次 HTTP 尝试，使用真实用量账本和新的 `tmp/tmp_*` 报告，保存响应或上述有限错误依据后即停。它不调用状态发布、不算跨轮首轮通过，即使成功也不能继续继承旧失败报告；私有配置须在 `finally` 中逐字节恢复。此项是补齐已丢失原因的一次受控取证，不是同版本反复试到成功。
   - **按错误证据只修命中的一处**：若服务端明确指向 `response_format`，按其具体路径／规则修正请求级 schema；若确实无法使用该动态枚举，可明确冻结一个使用既有基础字符串 schema 的候选，同时保留允许查询提示和本地逐字集合、去重、未知条件必问校验，集合外追问仍在发布前失败。这是事先选择的契约修订，不是遇错自动降级，不以换成自由文本校验放行。若指向其他参数，只修该参数；若为认证／权限／模型访问或端点问题，只检查相应私有配置和访问条件，不输出密钥，不靠 schema 或模型切换掩盖。若为限流、额度或服务端故障，先处理对应外部条件，不立即追加请求。若返回成功，记录历史原因未知、当前原请求可被接受；保留原 schema，完成诊断／失败出口修订的离线检查和冻结后进入正式验收，不为凑一个业务修复而改协议，也不宣称历史 HTTP 故障已解决。若仍然失败且原因不明，保留请求 ID 和未知结论，停止付费，不做双版本 schema 碰运气对照。服务端成功但最终输出违反本地契约时，归入最终生成问题，保留严格拦截，另按原文定位。
   - **修订冻结后，第一项正式验证仍是原 Silver 首轮**：运行受影响检查及完整离线回归；以原错误追问仍被发布边界拒绝为必过项，确认必要证据、永久拒绝和失败原子性未放宽。两张无缺口完整图只免费精确重放消息、schema 和结果，若不一致先收窄修改，不扩大付费批次。之后通过公开候选入口，从独立空状态重做 Silver 首轮，要求原题、两条出生地、$0.95$ 分数和严格阈值 $0.7$ 不变，首阶段正确、唯一出版社查询、无答案、两条证据保留、无拒绝；逐项人工批准后才继续 Silver 后两轮及独立 Bronze 三轮。每轮继承本批真实成功快照，次轮必须正确移除无关旧事实，第三轮必须证明高分重输仍被过滤。任一 HTTP、结构、语义或状态失败即停止；不把诊断成功、正确城市或本地拦截通过代替完整门槛。

   **预算与采用边界**：一次诊断请求仅在免费检查仍无法定位时使用；正式验收仍最多 $6$ 次 MSM 事务，正常 $12$ 次、保守最多 $18$ 次逻辑调用及 $54$ 次 HTTP 尝试。连同可选诊断，合计正常最多 $13$ 次、保守最多 $19$ 次逻辑调用及 $55$ 次 HTTP 尝试，不叠加完整图、模型或措辞对照。诊断取证结束即收口；明确请求修订、修复外部条件，或已取证原请求成功后，完成离线检查及候选冻结才启动正式验收，仍失败且原因未知时不自动追加请求。两条链未全部通过前不进入第 $5$ 步，TODO 2.3.5 及独立 GPT-4o-mini 交付验证继续开放。

   **制定后的绕路审核**：已去掉“看到 HTTPError 就先改 schema”、重跑首阶段取证、增加通用可观测平台、全量历史重放及付费双版本试探。最小修改落在实际丢失错误的私有记录器和失败审计出口；唯一可选诊断直接发送已保存的失败负载，回答当前唯一缺失的“服务端为什么拒绝／失败”。后续实现只按实际错误展开，保留已正确的首阶段与发布保护。正式两条链用于补齐尚未通过的状态验收，完整图以免费重放复用，未额外扩大验收集。审核结论：未绕路；HTTP 根因尚未确定，取证、修复和验收各有停止点，方案完成不代表修复完成。

> 2026/11/09 已执行“最终请求 HTTP 失败后的补充”：私有 `RecordingClient` 在原异常重抛前记录阶段、异常类、HTTP 状态、符合格式的请求 ID，以及白名单内的 `error.type`、`error.code`、`error.param`；响应使用 `is not None` 判断。任意错误消息不落盘，只识别固定规则原因码，未命中记为 `unclassified`。失败重放仅重建允许的异常类，不读取缺失的 `completion`，不伪造历史 HTTP 原因。验收入口先保存原事务结果，再执行清理与审计，关闭异常单列；用量汇总区分已知 tokens 与未知请求数，不改 CSV 列。私有实现为 `tmp/tmp_http_diagnostics.py`、`tmp/tmp_http_probe.py` 及原记录器、验收入口。
>
> 免费检查覆盖带 schema 字段的 HTTP $400$、HTTP 错误响应的真假值陷阱、缺失／非 JSON 错误体、无响应异常、未知字段及敏感文本过滤、原异常对象传播、无回复失败重放；旧失败事务精确请求重放与原失败状态一致。全部 $115$ 项离线回归通过。逐项核对保存的最终 schema：根对象、必填字段、禁止额外字段、嵌套数组及单个短字符串枚举未发现明确违反[官方结构化输出子集](https://developers.openai.com/api/docs/guides/structured-outputs)的路径；这不证明服务端接受该具体请求，因此未预先删除枚举或放宽本地校验。
>
> 随后仅对旧报告第二次调用的原始 `messages` 与 `response_format` 发出一次 `final_answer` 取证请求，使用 `gpt-4o`、OpenAI 端点及原匹配密钥，私有客户端显式 `max_retries=0`。未重跑首阶段、未调用状态发布、未继承失败状态，也未增加连通性或双版本 schema 请求。结果仍为 `HTTPError`，本次确定状态为 HTTP $400$，`error.type=invalid_request_error`、`error.param=response_format`，`error.code` 未提供或未通过白名单，规则原因码为 `unclassified`，请求 ID 为 `req_f77fccff6c514284bd8f9db871a3354b`。这定位了当前请求的拒绝参数，仍不能还原历史失败原因或指出具体 schema 规则。
>
> 新报告为私有 `tmp/tmp_http_probe_20261109T101814224662Z.json`，已从磁盘复核；真实账本新增 $1$ 行错误记录，只有 $1$ 次逻辑调用、$1$ 次 HTTP 尝试，无重试，无成功回复。输入与输出 tokens 均未知，报告中的已知 token 小计为 $0$，未知用量请求数为 $1$，不能将小计当作实际零消耗。私有配置已在 `finally` 中逐字节恢复，生产协议、共享 YAML、严格解析及发布保护未修改。
>
> **不能推进正式跨轮验收或第 5 步**：取证已达到一次请求上限，已停止付费。剩余问题是服务端拒绝 `response_format` 的具体路径／规则仍未分类；当前证据不足以确定应修改哪个 schema 路径，也不足以断言动态枚举不可用。下一步须先从现有请求和服务端契约定位具体违规，或凭请求 ID 取得有限服务端诊断，再冻结针对性修订；不得追加同版请求或用双版本付费对照碰运气。Silver、Bronze 两条链及 TODO 2.3.5 的独立 GPT-4o-mini 交付验证继续开放，候选不推广。

   **HTTP 400 取证后的修订方案：复用已成功的基础 schema，保留查询发布门槛**（2026/11/09 制定并执行，两条跨轮链通过）。

> 2026/11/09 已执行本修订：最终请求统一复用 `MEMORY_RESPONSE_FORMAT`，删除动态查询枚举及其复制代码，保留基础严格 schema、允许查询提示及全部本地发布校验。错误追问仍原样拒绝，不修复输出、不自动退回其他 schema。全部 $115$ 项离线回归、字符与语法检查通过；两张历史完整图的消息、schema、回复和结果精确免费重放通过，历史错误追问注入新入口仍触发 `decision_questions`，此项是反例回归，不是新请求的历史精确重放。
>
> GPT-4o 正式执行 Silver、Bronze 各三轮，全部通过；每轮人工审阅原始抽取、派生决策、最终回复和状态后才继承快照。两组首轮均保留两条出生地、无答案或拒绝，原样返回唯一出版社查询；Bronze 首轮关系名为 `publisher`，当时无出版社事实，语义仍为所需出版社查询，第二轮见到事实后正确使用 `published_by`。Silver 后两轮回答 `Linden City`，Bronze 后两轮回答 `Cedar City`，均保留出版社与对应出生地、无追问；第二轮移除并永久拒绝另一出生地，第三轮以 $0.95$ 重输后仍在提交给 LLM 前被过滤。所有输入快照保持不变。
>
> 本批共 $6$ 次 MSM 事务、$12$ 次逻辑调用及 HTTP 尝试，无失败、重试或排序调用，实际模型均为 `gpt-4o-2024-08-06`；真实账本新增输入 $11850$、输出 $1100$ tokens，未知用量请求为 $0$。逐阶段用量核对及全部新增事务精确免费重放通过，私有配置每轮均在 `finally` 中逐字节恢复。私有报告为 `tmp/tmp_gap_queries_screen_20261109T102804686389Z.json`、`tmp/tmp_gap_queries_silver_2_20261109T102817270854Z.json`、`tmp/tmp_gap_queries_silver_3_20261109T102826548850Z.json`、`tmp/tmp_gap_queries_bronze_1_20261109T102836389484Z.json`、`tmp/tmp_gap_queries_bronze_2_20261109T102846241440Z.json`、`tmp/tmp_gap_queries_bronze_3_20261109T102856251258Z.json`，均已从磁盘复核。没有新增独立取证、连通性或双版本请求；基础 schema 已被本批接受，不能据此证明历史动态枚举的具体拒绝原因。
>
> **可以推进第 5 步**：未受影响的两张完整图及两条新跨轮链均通过。本轮完成本修订的实现与验收，候选仍通过 `prompt_variant="evidence_decision"` 显式选择；第 $5$ 步的默认流程采用、维护入口及成员 D 模块交付另行推进。本结论仅覆盖上述受控用例，不能推断开放问题集稳定性。TODO 2.3.5 的独立 GPT-4o-mini 交付验证仍待办。C/E 集成按本文开头的职责边界由成员 E 完成，不阻塞成员 D 交付。

   本补充依据上述执行记录、原失败报告 `tmp/tmp_gap_queries_screen_20261109T093208283245Z.json`、取证报告 `tmp/tmp_http_probe_20261109T101814224662Z.json`、此前 HTTP 成功的 `tmp/tmp_grouped_requirements_screen_20261109T091157703849Z.json`，以及当前请求、诊断和发布代码制定。本次只补充分析与方案，不修改实现或私有配置，不发起 API 请求。本补充接替“继续定位具体 schema 规则后才能修订”的后续安排；旧记录和失败结论保留。

   **深入归因：请求被拒已定位，继续等待精确规则不是推进修复的必要条件**。

   | 层次 | 已复核的证据 | 归因及边界 |
   | --- | --- | --- |
   | 直接阻塞 | 新取证为 HTTP $400$、`invalid_request_error`，`param=response_format`；只发送原失败的最终请求，无最终回复、解析或发布。 | 当前阻塞在服务端请求契约接受环节，不能归因于最终回复内容、永久拒绝计算或检索。新取证的状态不能倒填为历史请求的状态；它也不提供具体 schema 路径。 |
   | 变更范围 | 旧、新首阶段 `messages` 和选项逐字相同；失败最终提示保留旧提示的完整前缀。去掉 `properties.subsequent_question.items.enum` 后，最终请求选项与此前成功版完全相同；枚举仅含 $1$ 个、$81$ 字符的查询。取证消息与原失败消息逐字相同。 | 动态枚举是已确认的 schema 增量，也是优先移除的兼容性依赖；追加提示同时存在，且没有精确服务端原因，尚不足以证明枚举、引号或某条规则就是根因。此前成功只证明基础 schema 曾被接受，不保证新事务必然成功。 |
   | 业务事实与状态 | 原首阶段正确派生 `unknown`、两条必留出生地和唯一出版社缺口。最终 HTTP 失败后没有可继承快照，输入状态保持空集。 | 本轮没有必要证据被拒，也没有新的首阶段反例。修订应局限于最终请求，不重写抽取、求值、检索或记忆状态机；原本的追问语义问题仍须正式验收。 |
   | 诊断为什么仍未知 | `tmp/tmp_http_diagnostics.py` 只识别 `required`、枚举不支持、`additionalProperties` 三类消息模式，未保存其余消息；`code=null` 同时可能表示缺失或未通过白名单。 | `unclassified` 只说明本地分类器未命中，不能推出服务端没有具体解释。旧正文未保留，免费重放无法恢复；继续发送同一负载而不改变诊断能力，不能保证增加有效信息。已有失败重放、报告保全和未知用量修复可直接复用。 |
   | 可直接采用的修订 | `validate_decision_response` 已独立检查允许集合、去重及未知条件必问，并在新快照构造前执行；`tests/test_gap_queries.py` 故意使用忽略 schema 的传输验证这些拦截。 | 动态枚举约束生成，不是发布安全的唯一屏障。可以事先冻结基础严格 schema 加原查询提示、本地严格门槛的候选，取消服务端对动态枚举的依赖；不会放行错误追问，但模型仍可能生成错误并被拦截。 |

   重新查阅 [OpenAI 官方结构化输出说明](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)：枚举属于支持类型，严格模式使用其 JSON Schema 子集；结构约束不保证语义正确。当前证据仍不足以宣布该枚举违反官方规则。下面选择的是有历史成功依据、可保留原发布门槛的最小兼容性修订，不把“绕开尚未确诊的请求增量”写成“已经证明并修复服务端根因”，也不要求先证明动态枚举在所有场景不可用。

   - **先修最终请求的一处依赖**：在 `evidence_decision.py` 的 `decision_response_format` 及 `memory_update.py` 接线中，使显式候选的最终请求统一复用既有 `MEMORY_RESPONSE_FORMAT`，取消缺口路径上的动态字符串枚举；清理仅服务于该枚举的复制代码，函数是否保留以最小改动为准。继续发送 `type=json_schema`、`strict=true` 和原三字段定义，不改成普通文本或 JSON mode。保留缺口查询生成、最终提示中的允许集合，以及严格解析后的全部查询、必留证据、未知禁答和无缺口禁问检查；错误字符串原样拒绝，不替换或补问。修订在请求发出前固定，不在收到错误后自动退回另一 schema，不双发请求。
   - **免费检查只覆盖改变的边界，再冻结候选**：调整 `tests/test_gap_queries.py` 中动态枚举断言，确认有缺口、无缺口请求均携带基础严格 schema，允许查询仍进提示，共享常量未污染；复用既有错误追问、重复、未知条件必问及非空旧状态失败原子性检查。原始错误追问必须仍在发布前失败，正确出版社查询必须保留两条出生地且无答案、无拒绝。将已保存首阶段与最终错误回复注入新入口属于反例回归，不能冒称新请求的历史精确重放；保存的 HTTP 失败仅核对原请求和无发布，不伪造成功回复。两份无缺口完整图继续免费精确重放消息、schema、回复与结果，保持其验收依据；受影响测试及完整回归通过后冻结唯一候选。不新增通用 schema 校验器或扩大历史重放集。
   - **第一项真实验证直接重做 Silver 原失败首轮**：通过公开 `manage_memory(..., prompt_variant="evidence_decision")`，保持 GPT-4o、OpenAI 端点和匹配密钥、原 `BRANCH_QUESTION`、两条出生地、全部 $0.95$ 分数、严格阈值 $0.7$ 与独立空快照。首阶段重新真实执行，不能拿旧报告替代；最终须原样返回唯一允许的出版社查询，无答案，保留两条证据且无拒绝，原状态不变。逐项审阅抽取、派生缺口、原始最终回复和返回状态，通过后才继承本批快照，继续 Silver 后两轮及独立 Bronze 三轮；沿用第 $4$ 步的移除与高分重输过滤门槛。此前完整图不重复付费，取证成功或单轮作答成功均不能替代跨轮通过。
   - **失败就沿实际出口收口，不再开一轮通用诊断**：基础 schema 若仍被 HTTP 拒绝，先保存现有有限诊断和请求 ID，停止本批，不自动换模型、schema 或追加请求；已有结构化输出成功记录也不能推翻新失败。此时才针对实际 `param` 检查对应请求；若仍指向 `response_format` 且本地没有明确违规，以请求 ID 获取该次服务端具体路径／规则作为后续修订依据，不把等待诊断作为本次最小修订的前置条件，也不在本任务中发送支持消息。若 HTTP 通过但查询、证据、答案或状态不合格，保留生产拦截和原始回复，按实际失败阶段停止；不恢复枚举碰运气，不继续继承失败快照。诊断器若仍未分类，如实记为未知，不为凑原因猜测模型内部行为或追写历史字段。

   **预算与完成条件**：不新增独立取证、连通性或双版本请求。正式验收仍最多 $6$ 次 MSM 事务，正常 $12$ 次、保守最多 $18$ 次逻辑调用；沿用额外重试 $2$ 次时最多 $54$ 次 HTTP 尝试，HTTP $400$ 按现有客户端不重试。每轮通过才启动下一轮，沿用真实用量账本及已修复的私有报告／失败审计；未知 tokens 继续单列，执行时临时选择 GPT-4o 并在 `finally` 中逐字节恢复私有配置。两张完整图的未受影响路径重放及两条新跨轮链均通过，才允许进入第 $5$ 步；TODO 2.3.5 的独立 GPT-4o-mini 交付验证继续开放。

   **制定后的绕路审核**：上次补齐状态码、失败审计和用量未知处理有必要，但已经完成；若下一轮仍围绕同一负载追加取证或等待完整根因，主线会停在诊断上。本方案只撤去最终请求的动态枚举依赖，保留已经实现的查询发布保护，先免费验证边界，再将第一笔付费用在原失败首轮。精确服务端诊断只在基础 schema 也失败时成为后续依赖，不扩建日志平台、不换 SDK／模型、不重做已正确首阶段设计、不加人物或出版社小题。两条真实跨轮链直接覆盖尚未通过的记忆保留、移除和永久过滤，不属于绕路。审核结论：已去掉继续取证的前置绕路，方案完成；请求修订尚未实施，HTTP 接受性及业务语义尚未验证，不能宣布问题已解决。

5. **依据门槛采用或停止，并交付可复核结果**：只有完整题和两条跨轮链均通过，才将候选纳入默认流程，保留论文基线，更新维护的验收入口、中英文模块说明和调用预算，并完成回归及提交。结果只覆盖这些受控用例，不宣称开放问题集上的稳定性。若候选失败，本方案停止；下一次工作必须直接针对已定位的失败阶段提出代码修改，或单独评估更换允许的推理模型，不能沿用同一候选反复付费碰运气。模型更换不属于本批，不自动修改私有 `.env`；TODO 2.3.5 的 `gpt-4o-mini` 验证要求仍保持。

> 2026/11/09 第 5 步已完成默认采用及维护入口接线：依据上方已通过的两张完整图及两条 GPT-4o 三轮链，`manage_memory`（含兼容别名）和 `update_memory` 默认改为 `evidence_decision`，保留显式 `paper` 及旧增强变体。只切换默认选择，不改冻结的抽取、条件求值、端点绑定、固定查询、基础严格 schema、本地发布门槛、永久拒绝或独立排序。`build_memory_prompt` 仍是单次选择构造器，其默认值不代表完整事务。中英文模块说明已同步当前流程、支持范围及预算。
>
> 维护入口透传结构化输出参数，默认执行两次请求，多答案追加排序；`all` 保留原 $13$ 个用例，保守上限由 $25$ 调整为 $37$ 次逻辑调用、当前最多 $111$ 次 HTTP 尝试。独立 `conditional` 组复用原完整题，覆盖两张独立完整图及两条真实状态继承的三轮链，共 $8$ 次事务，正常 $16$ 次、保守 $24$ 次逻辑调用及 $72$ 次 HTTP 尝试，不加入 `all`。逐轮检查全部记忆、答案、追问、拒绝与过滤，成功事务保存分阶段请求、schema 和原始回复；语义审阅仍单列为待办，自动通过不代表模型泛化通过。显式 `--prompt-variant paper`／`retrieval_questions` 保留旧调用预算，不自动回退。
>
> 全部 $117$ 项离线回归及字符、Python 语法和差异检查通过，新增检查覆盖默认与显式候选及底层入口的精确请求一致性、首阶段失败停止、维护入口结构化参数转发、两条独立链的快照继承和高分重输过滤；原单次选择测试改为显式指定旧变体。通过新默认入口免费重放既有两张完整图和六次链事务，共 $8$ 次事务、$16$ 个阶段，请求消息、schema、答案、追问、前后快照均与已人工批准的原始记录一致；私有入口及汇总为 `tmp/tmp_step5_replay.py`、`tmp/tmp_step5_replay.json`。本轮无新增付费请求，私有 `.env` 和 `logs/llm_usage.csv` 的 SHA-256 前后相同，共享 YAML 未变更。
>
> **可以推进 TODO 2.3.5 的独立 GPT-4o-mini 模块验收**，本次不把它标为完成，也不重复付费运行 GPT-4o。尚未解决的是新默认在 mini 下的真实质量验证，以及原有多跳用例在当前单跳需求协议中的兼容性；多跳、聚合等超出协议范围，模型正确标记不支持时会终止，而不是自动改走基线。受控条件题的保留、移除及永久过滤已有 GPT-4o 依据，不再按历史失败记录要求同版重试；不能据此宣布开放问题集稳定或整体交付完成。TODO 2.3.6 历史用量核对与 2.4 最终交付仍待办，C/E 集成由成员 E 负责，不作为成员 D 前置条件。

首批仅 $2$ 次 MSM 事务，每次为证据决策、最终作答及可能的排序，保守最多 $6$ 次逻辑调用，按当前 `max_retries = 2` 最多 $18$ 次 HTTP 尝试；通过后才追加跨轮 $6$ 次事务，最多 $18$ 次逻辑调用、$54$ 次 HTTP 尝试。两批合计最多 $24$ 次逻辑调用、$72$ 次 HTTP 尝试，正常单答案路径合计 $16$ 次逻辑调用；实际 HTTP 重试与业务失败分别记录。每批保存原问题、两阶段输入和结构化输出、必要证据、最终答案与追问、发布前后快照、实际模型及分阶段用量；只免费重放新增事务和受修改影响的记录。私有原型与报告放在 `tmp/tmp_*`，实际用量继续记入 [`logs/llm_usage.csv`](../logs/llm_usage.csv)。本计划不改变严格解析、永久拒绝语义、共享 YAML 默认值或私有 OpenAI 配置。

> 2026/11/09 第 1 步已完成：新增 `src/agent/evidence_decision.py`，通过公开 `manage_memory(..., prompt_variant="evidence_decision")` 显式选择候选。先提交原问题与历史记忆、达标新证据的完整并集，取得五字段决策，再将基础结构检查通过的记录连同原问题、完整图交给最终选择与作答。提示要求未知条件保留相关候选路径，区分条件为假与缺少条件证据，不注入本题人物、城市或分支答案；最终 `subsequent_question` 明确为字符串数组，继续经过既有严格解析，多答案仍执行独立排序。默认行为、`paper`、严格阈值和最终选择的永久拒绝规则保持不变。每事务两次逻辑调用，多答案时三次；未发起付费请求，未修改私有配置。
>
> 基础决策检查覆盖完成信封、字段集合、状态枚举、字符串数组及条件／必留三元组的精确图成员。新增两项离线接线测试验证完整图传递、阶段顺序、独立排序和首阶段失败停止；全部 $102$ 项离线测试通过。这不证明决策语义正确。第 2 步的发布前一致性门槛尚未实现：必留证据约束、未知条件禁答及无缺口必须作答／停止追问仍只有提示约束，尚无代码拦截；当前候选不得用于付费验收或推广。可以执行第 2 步，补齐拦截及边界、失败原子性测试后再冻结候选。本问题和 TODO 2.3.5 继续开放。

> 2026/11/09 第 2 步已完成并冻结离线候选：决策记录校验五字段集合、状态与数组类型、非空字符串及条件／必留三元组的精确提交图成员；条件证据须列入必留集合。最终输出先经过原 `parse_memory_response`，再检查必留证据未漏选、未知条件未作答，以及条件已定或非条件题且 `missing_relations` 为空时必须作答并停止追问。不一致即终止整个事务，不自动补选、补答或业务重试。决策及一致性失败提供固定诊断码，分别归于 `evidence_decision` 和 `decision_consistency` 阶段；最终解析继续保留原诊断阶段。
>
> `tests/test_evidence_decision.py` 的 $9$ 项离线测试通过，全部 $109$ 项回归通过，字符、Python 语法和差异检查通过。模拟决策覆盖未知时保留两条候选出生地、条件成立／不成立后选择对应路径、移除无关旧证据并在高分重输时过滤；必留漏选、未知条件作答、无缺口空答或继续追问均被拦截。决策字段／类型／精确成员、严格答案来源与问题数组、各阶段传输／用量错误、排序校验失败均有失败保持旧记忆和既有拒绝集合的检查；多答案仍执行独立排序，零或单答案跳过排序。严格阈值、默认与 `paper` 行为、按最终选择计算永久拒绝的规则均不变；中间记录不发布状态，公开 `manage_memory` 仅在全部阶段成功后返回可发布快照。
>
> 可以推进第 3 步：按上述固定候选对 Silver、Bronze 原完整图各运行一次，不重跑基线。本轮未发起付费请求，未修改私有配置或新增真实用量；本次只确认结构、阶段一致性及事务失败行为，不能证明决策语义正确，也未验证真实模型保留与移除证据。本问题和 TODO 2.3.5 继续开放，候选仍须显式选择，不推广为默认流程。

> 2026/11/09 执行第 3 步，Silver 首个完整题事务未通过语义门槛，已按任一失败即停的规则结束本批，未执行 Bronze，也未进入第 4 步。通过公开 `manage_memory(..., prompt_variant="evidence_decision")` 运行冻结候选，逐字使用 `BRANCH_QUESTION`，提交两条出生地与 Silver 出版社的完整图，全部分数为 $0.95$、严格阈值为 $0.7$，从独立空状态开始；沿用已有字符串数组说明，未追加提示、重跑基线、修复输出或发起业务重试。
>
> 失败定位于证据决策的回答对象语义：`condition_status` 正确为 `true`，条件证据和必留集合正确包含 Silver 出版社及 Mira Vale 出生地，`missing_relations` 为空；但 `answer_targets` 同时列出 `Mira Vale` 和 `Nolan Reed`，没有按已确定的成立分支收敛到 `Mira Vale`。这是第 3 步“决策记录须正确确定回答对象”的语义失败，不是结构解析或发布前一致性校验异常，也不能因最终答案正确而记作候选通过。证据决策的原始回复正文如下：
>
> ```json
> {
>   "condition_status": "true",
>   "condition_triplets": [["The Glass Orchard", "published_by", "Silver Press"]],
>   "answer_targets": ["Mira Vale", "Nolan Reed"],
>   "required_triplets": [["Mira Vale", "born_in", "Linden City"], ["The Glass Orchard", "published_by", "Silver Press"]],
>   "missing_relations": []
> }
> ```
>
> 最终作答阶段通过生产严格解析和阶段一致性检查，原始回复正文如下；最终记忆为所选两条证据，未选中的 Nolan Reed 出生地按既定规则进入永久拒绝，展开排除集合为全部三条提交证据，必要证据未被拒绝，输入空快照保持不变。本次仅观察到 Silver 单轮作答和状态规则通过，不证明真实跨轮旧记忆移除或 Bronze 作答已经修复。
>
> ```json
> {
>   "selected_triplets": [["Mira Vale", "born_in", "Linden City"], ["The Glass Orchard", "published_by", "Silver Press"]],
>   "predict_answer": ["Linden City"],
>   "subsequent_question": []
> }
> ```
>
> 本批实际只启动 $1$ 次 MSM 事务、$2$ 次逻辑调用及 $2$ 次成功 HTTP 请求，均返回 `gpt-4o-mini-2024-07-18`，无传输重试，无排序请求。证据决策输入／输出为 $334/84$ tokens，最终作答为 $674/53$ tokens，合计 $1008/137$ tokens；[`logs/llm_usage.csv`](../logs/llm_usage.csv) 新增两行，始于 2026/11/09 14:13:58（UTC+08:00），返回模型、用量和请求 ID 已逐条核对。私有入口为 `tmp/tmp_evidence_decision_step3.py`，完整原问题、分阶段请求及未经改写的回复、预期与实际输出、前后快照和语义检查保存在 `tmp/tmp_evidence_decision_step3_20261109T061358161308Z.json`；新增事务的免费精确请求重放通过。生产代码、共享默认值及私有 OpenAI 配置均未修改。
>
> 不能推进第 4 步，也不推广候选。本方案按第 5 步停止；待解决问题是条件已确定时 `answer_targets` 仍包含不适用分支对象，Bronze 和两条真实跨轮链尚未验收。下一次工作须针对该证据决策阶段提出代码修改，或单独评估允许的推理模型；不得用同一冻结候选反复付费重试或追加小问题、措辞对照。本问题和 TODO 2.3.5 继续开放。

> 2026/11/09 已执行“第 3 步失败后的补充”：决策协议以 `targets_by_case` 替代模型生成的平铺对象，严格校验三分支数组、字符串、对象逐字来自原问题或为提交图组件，以及条件／非条件数组的互斥约定。代码按条件状态派生 `answer_targets`，未知时取两分支稳定去重并集，允许单分支多对象及尚无事实的对象；最终提示只接收派生对象，不接收分支表。相关 $10$ 项测试及全部 $110$ 项离线回归通过，覆盖分支派生、未知合并、多对象、非条件题、最终提示消费结果，以及非法表在最终请求前失败且旧状态不变。完整问题、完整图、必留证据、严格解析、永久拒绝、排序、默认及 `paper` 行为保持原约定；离线通过不证明语义正确。
>
> 冻结修订候选后重做第 3 步：通过公开 `manage_memory`，Silver 使用原 `BRANCH_QUESTION`、完整三条证据、全部 $0.95$ 分数、严格阈值 $0.7$ 和独立空快照。首阶段返回带 Markdown 围栏的 JSON，严格解析失败，实际诊断为 `evidence_decision / decision_completion`，已按首个失败即停结束本批。没有剥离围栏、修复回复、业务重试或继续付费。以下四反引号块内为原始正文，包含模型返回的三反引号：
>
> ````text
> ```json
> {
>   "condition_status": "true",
>   "condition_triplets": [["The Glass Orchard", "published_by", "Silver Press"]],
>   "targets_by_case": {
>     "true": ["which city was Mira Vale born in?"],
>     "false": ["which city was Nolan Reed born in?"],
>     "not_conditional": []
>   },
>   "required_triplets": [["Mira Vale", "born_in", "Linden City"]],
>   "missing_relations": []
> }
> ```
> ````
>
> 首个实际错误为证据决策正文格式，尚未到分支表或必留证据校验。人工检查正文还观察到分支数组填入整句问题而非人物对象，以及出版社条件证据未进入必留集合；前者虽逐字来自原问题，来源校验仍不足以证明对象语义，后者若到达既有条件保留检查会被拦截，但本次没有执行到该处。不能将这些观察记作实际诊断，也不能凭条件状态为 `true` 判定完整决策通过。最终作答、Bronze 和跨轮链均未执行，事务未发布状态，输入记忆、拒绝及展开排除均保持空集。
>
> 本批仅 $1$ 次事务、$1$ 次逻辑调用及 $1$ 次成功 HTTP 请求，无传输重试或排序；实际模型 `gpt-4o-mini-2024-07-18`，输入／输出 $432/104$ tokens。请求始于 2026/11/09 14:31:02（UTC+08:00），`logs/llm_usage.csv` 新增一行，模型及用量已逐条核对。私有入口 `tmp/tmp_evidence_decision_step3_revised.py` 和完整报告 `tmp/tmp_evidence_decision_step3_revised_20261109T063102138107Z.json` 已从磁盘复核，新事务免费精确请求重放通过。未重跑基线、修改私有配置或共享 YAML。
>
> **不能推进第 4 步**：修订候选仍未通过 Silver，候选保持显式选择，不推广默认，本问题和 TODO 2.3.5 继续开放。本批停止，不沿用同一版本重复付费。下一次首先针对证据决策格式边界提出代码修改，例如通过客户端支持的结构化输出契约约束裸 JSON；同时明确分支对象应为实体名称而非问题句，维持条件证据必留约束。这些方向尚未实现或验证，不能保证解决对象语义和证据遗漏，不通过放宽解析或自动补选替代完整验收。

### 尚未验证模型能正确移除旧记忆

问题：原用例确实移除了旧记忆，但连必要证据也一起删除，不能算正确移除；澄清条件后的复测又保留了全部证据。离线状态转换测试通过，不等于 GPT 已正确判断哪些旧证据不再有用。

正确移除与永久屏蔽的验证统一纳入[修改计划](#修改计划分离条件决策与记忆发布)第 $4$ 步，直接使用原完整条件题的真实跨轮状态，不另设作者小问题绕过当前失败。
