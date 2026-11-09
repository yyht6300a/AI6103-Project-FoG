# 成员 D 模块

[English](part_d.md) | [简体中文](part_d.zh-CN.md)

本文介绍成员 D 已实现的模块及其接口。配置和测试命令见 [README](../README.zn-CN.md#llm-配置)。潜力子图过滤、问题级记忆快照、LLM 引导的记忆替换、永久拒绝记录及展开排除已实现；检索器和推理循环接线留待集成。

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
python scripts/validate_part_d.py --suite all
```

`--list-cases` 免费预览，不读取私有配置；其余命令产生真实 API 用量。全部记忆轮次统一调用 `manage_memory`，默认对多个答案追加独立排序请求，不提供关闭排序的业务配置；零或单答案跳过该请求。`single` 包含问题分解和证据充足、不足、空图共 $4$ 个用例，保守最多 $7$ 次逻辑调用；`multi` 的 $4$ 个记忆轮次最多 $8$ 次；`removal` 的 $3$ 个轮次最多 $6$ 次；`ranking` 的两个用例最多 $4$ 次。默认 `all` 共 $13$ 个用例、最多 $25$ 次逻辑调用。上限按每个记忆轮次可能返回多个答案计算，不依赖模型恰好输出预期数量；实际跳过排序时调用更少。计入 YAML 重试后，HTTP 上限为逻辑调用上限乘以 `(max_retries + 1)`，当前分别最多 $21$、$24$、$18$、$12$、$75$ 次。报告区分用例数与实际启动的逻辑调用数；任一阶段终止异常都会停止整组。

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

2026/11/09 使用未变更的 GPT-4o-mini/OpenAI 配置运行 `python scripts/validate_part_d.py --suite reference_diagnostic`，完成 $3$ 次真实调用：原版仍输出 `In which city was this author born?`，增强版输出 `In which city was the author of The Glass Orchard born?`；两版第一问均为 `Who is the author of The Glass Orchard?`。向 MSM 提交受控作者关系后，模型保留该关系，答案为空并追问 `Where was Mira Vale born?`，旧状态和状态规则通过。本用例的上下文指代问题已修正；单次观察不能保证所有问题均合格。该显式诊断不加入 `all`，可用 `--suite reference_diagnostic --list-cases` 免费预览，当前保守上限为 $4$ 次逻辑调用、$12$ 次 HTTP 尝试。

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

`build_memory_prompt`、`update_memory` 和 `manage_memory` 均接受仅限关键字的 `prompt_variant`。默认 `retrieval_questions` 在原版图 8 前置输入区之前增加检索问题约束；显式传入 `paper` 可使用未增强的论文原版。未知值在请求前抛出 `ValueError`。增强版要求围绕原问题的缺失关系，从已知实体开始逐步查询，已知中间实体后使用具名查询，不询问信息是否存在，不重复查询已有事实；证据足够时不再追问。这是实现增强，不是论文原文，也不构成语义或召回保证；证据序列化、解析、永久拒绝和传输重试规则均沿用已有约定。

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

论文已要求多答案按证据支持程度排序，因此排序是模块的默认功能，不是由队友选择开启的附加选项。公开 [`manage_memory`](../src/agent/memory_management.py) 统一执行完整流程：第一阶段完成证据选择、试探作答和后续问题；已有答案不少于两个时，第二阶段调用 [`answer_ranking.py`](../src/agent/answer_ranking.py)，仅根据原问题、实际选中三元组和实际已有答案按支持强弱排序。为提高正确率，排序及展示顺序的生成提取为一次单独的 LLM 调用。零或单答案无需比较顺序，自动跳过第二阶段。

接口不提供关闭排序的业务开关；`prompt_variant` 仅选择第一阶段提示词，即使指定 `paper` 也仍对多个答案执行独立排序。旧名 `manage_memory_ranked` 保留为 `manage_memory` 的兼容别名，两者行为相同。底层 `update_memory` 仅负责第一阶段的提示、解析和状态转换，供内部编排及提示词对照使用，不作为完整 MSM 接口替代。

这是偏离论文单次 MSM 联合输出的实现增强。排序提示词沿用冻结原型全文，未继续调优；不传检索分数、金标准或未选中证据，不使用城市顺序、关系权重表或确定性后处理。第二阶段必须正常完成、未拒答，返回仅含 `predict_answer` 的标准 JSON；候选原串及多重集必须完全相同，禁止增删、改名或去重。只允许改变答案顺序，不改变选中证据、后续问题、记忆或永久拒绝，不能补救第一阶段漏答案或漏路径。

两个阶段共用调用方持有的 `LLMClient`，每次请求及传输重试按原规则独立记入 `logs/llm_usage.csv`。多答案每轮增加一次逻辑调用，最多共 `2 * (max_retries + 1)` 次 HTTP 尝试，当前最多 $6$ 次；零或单答案仍最多 $3$ 次。任一阶段传输、记账或输出校验失败均向调用方抛错，不返回部分 `MSMResult`，不增加业务重试；只有整体成功后才发布 `result.state`。客户端生命周期仍由调用方管理。

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

4. **两图均通过后，直接验证真实跨轮状态**：Silver、Bronze 各执行三轮，共 $6$ 次候选 MSM 事务。第一轮仅提供两条出生地，要求条件未知、无答案、追问出版社且保留两条候选；第二轮只补充该图出版社，继承第一轮真实返回状态，要求正确城市、无追问、保留出版社和对应出生地，并移除另一出生地；第三轮高分重输完整图，要求该移除项已被过滤、不再提交，正确路径和答案保持。任一轮失败即停止该批，不人工重置状态、补回必要证据或解除屏蔽。若答案正确但无关证据始终保留，则仅记作作答通过、移除未通过；不据此关闭本问题或 TODO 2.3.5，也不自动追加作者用例。必要证据进入永久拒绝集合即判失败。

5. **依据门槛采用或停止，并交付可复核结果**：只有完整题和两条跨轮链均通过，才将候选纳入默认流程，保留论文基线，更新维护的验收入口、中英文模块说明和调用预算，并完成回归及提交。结果只覆盖这些受控用例，不宣称开放问题集上的稳定性。若候选失败，本方案停止；下一次工作必须直接针对已定位的失败阶段提出代码修改，或单独评估更换允许的推理模型，不能沿用同一候选反复付费碰运气。模型更换不属于本批，不自动修改私有 `.env`；TODO 2.3.5 的 `gpt-4o-mini` 验证要求仍保持。

首批仅 $2$ 次 MSM 事务，每次为证据决策、最终作答及可能的排序，保守最多 $6$ 次逻辑调用，按当前 `max_retries = 2` 最多 $18$ 次 HTTP 尝试；通过后才追加跨轮 $6$ 次事务，最多 $18$ 次逻辑调用、$54$ 次 HTTP 尝试。两批合计最多 $24$ 次逻辑调用、$72$ 次 HTTP 尝试，正常单答案路径合计 $16$ 次逻辑调用；实际 HTTP 重试与业务失败分别记录。每批保存原问题、两阶段输入和结构化输出、必要证据、最终答案与追问、发布前后快照、实际模型及分阶段用量；只免费重放新增事务和受修改影响的记录。私有原型与报告放在 `tmp/tmp_*`，实际用量继续记入 [`logs/llm_usage.csv`](../logs/llm_usage.csv)。本计划不改变严格解析、永久拒绝语义、共享 YAML 默认值或私有 OpenAI 配置。

> 2026/11/09 第 1 步已完成：新增 `src/agent/evidence_decision.py`，通过公开 `manage_memory(..., prompt_variant="evidence_decision")` 显式选择候选。先提交原问题与历史记忆、达标新证据的完整并集，取得五字段决策，再将基础结构检查通过的记录连同原问题、完整图交给最终选择与作答。提示要求未知条件保留相关候选路径，区分条件为假与缺少条件证据，不注入本题人物、城市或分支答案；最终 `subsequent_question` 明确为字符串数组，继续经过既有严格解析，多答案仍执行独立排序。默认行为、`paper`、严格阈值和最终选择的永久拒绝规则保持不变。每事务两次逻辑调用，多答案时三次；未发起付费请求，未修改私有配置。
>
> 基础决策检查覆盖完成信封、字段集合、状态枚举、字符串数组及条件／必留三元组的精确图成员。新增两项离线接线测试验证完整图传递、阶段顺序、独立排序和首阶段失败停止；全部 $102$ 项离线测试通过。这不证明决策语义正确。第 2 步的发布前一致性门槛尚未实现：必留证据约束、未知条件禁答及无缺口必须作答／停止追问仍只有提示约束，尚无代码拦截；当前候选不得用于付费验收或推广。可以执行第 2 步，补齐拦截及边界、失败原子性测试后再冻结候选。本问题和 TODO 2.3.5 继续开放。

### 尚未验证模型能正确移除旧记忆

问题：原用例确实移除了旧记忆，但连必要证据也一起删除，不能算正确移除；澄清条件后的复测又保留了全部证据。离线状态转换测试通过，不等于 GPT 已正确判断哪些旧证据不再有用。

正确移除与永久屏蔽的验证统一纳入[修改计划](#修改计划分离条件决策与记忆发布)第 $4$ 步，直接使用原完整条件题的真实跨轮状态，不另设作者小问题绕过当前失败。
