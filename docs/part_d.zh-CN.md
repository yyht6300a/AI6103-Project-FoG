# 成员 D 模块

[English](part_d.md) | [简体中文](part_d.zh-CN.md)

本文说明当前已实现的成员 D 模块。默认流程为需求抽取、代码求值、证据选择与作答、发布前校验，以及多答案独立排序。公开入口是 `manage_memory`，默认使用 `prompt_variant="evidence_decision"`。配置与环境准备见 [README](../README.zn-CN.md#llm-配置)。成员 D 使用受控检索和打分输入配合真实 LLM 验收模块；成员 E 负责 C/E 流水线集成、实际检索和整体运行效果，集成不作为成员 D 模块验收的前置条件。

## 当前默认架构

```mermaid
flowchart TD
    A[原问题、旧快照、检索三元组和对齐分数] --> B[校验输入、严格阈值过滤、排除已有记忆和永久拒绝]
    B --> C[合并历史记忆与合格新证据]
    C --> D[LLM 按实体分组抽取条件与事实需求]
    D --> E[代码校验事实端点、求值条件、匹配完整图]
    E --> F[派生必留证据、回答对象、缺口与固定查询]
    F --> G[LLM 选择证据、作答和选择后续查询]
    G --> H[严格解析与决策一致性校验]
    H --> I[构造候选快照和永久拒绝集合]
    I --> J{是否有多个答案}
    J -->|是| K[LLM 独立排序并校验候选多重集]
    J -->|否| L[返回完整 MSMResult]
    K --> L
    L --> M[调用方发布新快照并驱动下一轮检索]
```

中间决策不发布记忆。最终输出必须同时满足结构、输入归属、必留证据、条件状态和缺口查询约束；排序也成功后，公开入口才返回可发布结果。任一阶段失败均向上传递异常，调用方继续持有原快照；不补选、补答、改写错误问题或增加业务重试。

条件未知时保护两条分支的已匹配证据，并禁止作答；条件确定后，只保护适用分支和实际条件事实。必要证据漏选会在计算可发布的永久拒绝状态前被拦截。模型仍负责正确理解原问题和抽取需求，本地一致性检查不证明所有抽取语义正确。支持范围和验收边界见[当前验收状态与剩余事项](#当前验收状态与剩余事项)。

## LLM 配置与请求

[`llm_config.py`](../src/agent/llm_config.py) 合并私有 `.env` 与共享 YAML 默认值，并在请求前校验最终配置。共享默认值保持 `gpt-4o-mini` 和 `https://api.openai.com/v1`；本地 DeepSeek 开发及最终切回 OpenAI 的要求见 README。密钥只保存在私有 `.env`，不写入报告或仓库。

[`llm_client.py`](../src/agent/llm_client.py) 提供 `LLMClient.complete(messages, response_format=...)`，使用兼容 OpenAI 的 Chat Completions 协议。配置显式传入，不继承进程代理、凭据等设置。超时、连接失败、HTTP $429$ 和 HTTP $5xx$ 按有限传输重试策略处理；模型、端点和协议不自动回退。调用方负责关闭客户端。

## 逐次请求用量记录

[`llm_usage.py`](../src/agent/llm_usage.py) 为每次实际 HTTP 尝试追加一行 UTF-8 CSV，包括失败和重试。权威账本为 [`logs/llm_usage.csv`](../logs/llm_usage.csv)，列为 `timestamp, model, input_tokens, output_tokens, status, error_type, request_id`。

时间记录请求开始时刻，使用带偏移的 UTC。模型和 token 数优先采用服务返回值；未知 token 数及请求 ID 留空，已报告的零值保留。HTTP 成功后的业务校验失败不改写 HTTP 记账结果。日志不含密钥、提示词、三元组、回复正文或任意异常消息；用量未知不等于零消耗。客户端在发送前检查表头和可写性，追加失败向上传递。并发进程须由调用方串行化 CSV 写入。

## 初始问题分解

[`question_decomposition.py`](../src/agent/question_decomposition.py) 实现 FoG 第 3.1 节和附录 C 图 7 的初始子问题。`decompose_question(question, client)` 默认使用 `explicit_references`：每个问题单独提交检索，写全已知实体名或未知实体的关系描述，不假定前一问已经得到答案，不编造实体名。返回有序问题列表，供调用方分别检索。

`build_decomposition_prompt` 和 `decompose_question` 均接受仅限关键字的 `prompt_variant`；显式 `paper` 保留论文原版措辞及校正后的指令编号。两种变体共享严格解析、传输重试和用量记录，非法输出不自动修复。此增强改善独立指代，实际召回由成员 E 集成验证。

## 潜力子图构建

[`potential_subgraph.py`](../src/agent/potential_subgraph.py) 实现 FoG 第 3.3 节公式 10：$G^{\mathrm{pot}} = \{t \in G^{\mathrm{xpd}} \mid s_t > \tau_p\}$。`build_potential_subgraph(triples, scores, potential_threshold=0.7)` 接收成员 C 在反馈传播后得到的精炼分数，与扩展三元组逐项对齐。

```python
from src.agent.potential_subgraph import build_potential_subgraph

triples = [
    ("Country A", "anthem", "Song C"),
    ("Country A", "led_by", "Person B"),
]
potential = build_potential_subgraph(triples, [0.95, 0.7])
assert potential == [triples[0]]
```

阈值为有限实数，默认 $0.7$，采用严格大于；等于阈值不入选。调用方从 YAML 读取阈值时须显式传入。分数必须是一维、位置对齐的有限实数序列，布尔值无效；张量须先显式转换。图行必须包含三个非空白字符串。所有行先校验再过滤，低分无效行也会报错；证据身份、方向和原字符串不改写。精确重复行只要任一对应分数达标即可进入后续证据集合。

过滤不调用 LLM，也不建立拒绝状态。本轮未提交且不在历史记忆中的证据不会被永久拒绝，后续重新检索和打分达标时仍可使用。

## 问题级持久记忆

[`memory_state.py`](../src/agent/memory_state.py) 提供 `QuestionMemory(original_question, memory_triplets=(), rejected_triplets=())`。每次独立问题运行创建新实例，即使问题文本相同；同一次运行在各轮之间保存快照。持久仅指运行内保留，不包含磁盘存储或全局问题缓存。

原问题必须是非空白字符串并逐字保存。记忆和永久拒绝均复制为 `frozenset[tuple[str, str, str]]`，精确重复项去重，原字符串及边方向不变；两个集合不得重叠。快照及嵌套值不可变，不受调用后修改输入列表影响。新快照由更新流程构造，调用失败不会更改旧快照。

`state.excluded_triplets` 返回当前记忆与永久拒绝的并集。`state.filter_expansion_triplets(candidates)` 按原顺序排除这些完整三元组、精确去重并校验全部候选。共享实体或反转方向不会自动排除另一条边。已吸收记忆不重复展开，但每轮仍独立提交给记忆管理；历史记忆不受本轮检索分数限制。

## 条件证据决策与记忆发布

[`evidence_decision.py`](../src/agent/evidence_decision.py) 实现默认流程的需求抽取与确定性求值。首阶段始终接收原问题，以及历史记忆和合格新证据的完整并集；不先让模型裁剪图。

### 需求抽取与端点绑定

首阶段使用严格结构化输出，`description.kind` 为 `conditional_equals`、`nonconditional` 或 `unsupported`。相等条件描述主体、关系、运算符 `eq`、比较值、原问题中的条件引文及可选单值前提引文。比较值只作为条件约束，不作为已观察事实。

每个分支按 `requirements: [{entity, fact_queries: [{relation, binding}]}]` 组织。`entity` 是原题询问其属性或关系的对象，不是检索到的答案端点；每个查询从所属组继承对象，不重复生成另一份对象字段。条件题提供 `true` 和 `false` 两分支，非条件题提供单一 `branch`。

`observed` 绑定引用本轮完整排序图的稳定三元组编号。代码校验编号、精确关系和目标端点，从端点位置派生出向或入向；多个引用须支持相同方向，自环或角色歧义终止事务。确定方向后仍扫描完整图，收集同一对象、关系和方向的全部匹配事实，避免只保留模型引用的一条。

`missing` 绑定声明待检索方向，不携带预测答案。代码检查两个方向的精确事实：声明方向已有事实时拒绝伪缺口，仅另一方向命中时保守判定冲突；两侧均无命中才允许缺口。此规则可能拒绝合法的另一方向查询，不代表通用关系方向推理。编号只在当前请求内有效，不能跨轮复用。

### 条件求值与必留集合

代码通过完整提交图中的精确主体和关系查找实际条件值。匹配比较值时为 `true`；存在不同值且原题明确单值时为 `false`；否则为 `unknown`。单值前提下出现多个实际值会失败；非条件题状态为 `not_conditional`。缺少事实不自动视为条件不成立。

必留集合由实际条件事实与适用分支的全部匹配事实构成。条件未知时合并两分支，保护已有候选路径；条件确定后只使用对应分支。代码同时派生回答对象、逐项证据匹配及缺失关系，交给最终选择阶段，不依赖模型重新列举必留证据。

### 缺口查询与发布门槛

[`memory_prompt.py`](../src/agent/memory_prompt.py) 的 `build_decision_memory_prompt` 向最终阶段提交原问题、完整证据及派生决策。有缺口时，`missing_relation_queries` 按对象、关系和方向稳定去重，安全引用原字符串，生成以下固定查询：

```text
Which entity does "<target>" connect to through relation "<relation>"?
Which entity connects to "<target>" through relation "<relation>"?
```

最终请求统一使用基础 `MEMORY_RESPONSE_FORMAT` 严格 schema，不生成动态查询枚举。模型通过 `subsequent_question` 原样选择查询；本地校验要求非空、无重复、全部属于本轮允许集合，并包含所有未知条件查询。至多三问，多缺口可以分轮检索；未询问的缺口不会因此变成已满足。

最终输出先经过 `parse_memory_response`，再由 `validate_decision_response` 检查：全部必留证据已选中；条件未知时无答案；条件已确定或非条件题且无缺口时必须作答、不再追问。任何不一致均在返回可继承快照前失败。固定查询保证与派生缺口一致，不能证明模型抽取完整或真实检索必然召回。

### 显式对照接口

`manage_memory` 和 `update_memory` 均默认选择上述完整决策流程。显式 `prompt_variant="paper"` 保留图 8 基线，`retrieval_questions` 保留原检索措辞增强版，`answer_ranking` 保留单次选择的实验对照；默认流程失败时不自动切换这些变体。所有公开 `manage_memory` 变体都保留多答案独立排序。

`build_memory_prompt` 只构造单次选择提示，仍默认 `retrieval_questions`，不代表完整事务入口。它保留论文的高召回、仅依据证据作答和精确复制要求；调用方应使用 `manage_memory` 获得默认发布保护。

## 结构化记忆输出

[`memory_response.py`](../src/agent/memory_response.py) 提供 `parse_memory_response(content, memory_triplets, potential_triplets)`。它是最终输出的基础解析边界，不负责首阶段抽取或完整事务发布。回复必须是完整的标准 JSON 对象，恰有以下三个数组字段：

```json
{
  "selected_triplets": [],
  "predict_answer": [],
  "subsequent_question": []
}
```

全空对象只是格式示例：没有答案时仍须返回 $1$ 至 $3$ 个非空白问题。`selected_triplets` 每行须包含三个非空白字符串，逐字匹配本次实际提交并集中的完整三元组；行内字段位置、方向、大小写、空白及 Unicode 不归一化。`predict_answer` 必须逐字匹配选中三元组的头或尾实体；仅出现在未选中证据、仅作为关系出现或经过改写的答案均被拒绝。空选择只能返回空答案。

返回不可变 `MemoryResponse`，三个字段均为元组，三元组行也为元组。基础解析保留原字符串、行顺序和重复项；默认流程另对追问执行上文的集合与去重校验。缺少、多余或重复字段、代码围栏、额外文字、尾随值、非标准 JSON 数值和越界值全部失败，不修复输出。

解析器能检查任意已选路径的实体归属，但默认需求协议的支持范围另见下文。实体归属不能证明答案语义、完整性或支持强弱，排序也不补齐遗漏答案。直接构造 `MemoryResponse` 容器不会执行这些校验。

## LLM 引导的记忆替换

[`memory_update.py`](../src/agent/memory_update.py) 提供 `update_memory(state, potential_triplets, client)`，负责排序前的决策、最终选择、解析和候选快照构造。新记忆严格等于最终选中三元组的集合；旧证据只有再次选中才保留，不自动与旧记忆取并集。默认必留约束先保护当前需求所需证据，再允许移除已不适用的旧证据。

潜在输入支持空图和生成器，先物化一次，确保所有阶段使用同一份证据。无效状态、图格式错误，或潜在输入与永久拒绝集合重叠时，请求前失败。仅正常完成、未拒绝且通过全部输出及决策检查的事务才构造候选快照。传输和记账错误向上传递。该底层接口不执行独立排序，完整 MSM 应使用下文公开入口。

## 探索效率

设实际提交并集为 $U = G^{\mathrm{mem}} \cup G^{\mathrm{pot}}$，最终选中集合为 $S$，已有永久拒绝为 $R$。通过全部校验后，新记忆为 $S$，永久拒绝更新为 $R' = R \cup (U \setminus S)$。已提交但未选中的新证据和旧记忆均进入永久拒绝，规则在问题运行内持续有效，记忆清空也不重置。

将省略的已提交输入解释为“显式拒绝”是用户批准的复现假设，论文没有明确给出这一映射。已发布的拒绝不会自动解除；默认流程通过发布前的必留校验保护必要证据。格式、决策、一致性、传输或排序失败均不向调用方发布新增拒绝。

调用方在检索和每步展开边界、打分前调用 `state.filter_expansion_triplets`。公开 `manage_memory` 还会在合格潜在输入上应用过滤，保证永久拒绝证据不会再次提交给 LLM。已吸收记忆从旧快照独立补回证据并集。本模块不实现检索器，也不修改成员 C 的打分逻辑。

## 阈值隐藏证据的容错

分数 $s_t \leq \tau_p$、且未在历史记忆中的证据不属于本轮提交并集，不产生永久拒绝。调用方把实际 `response.subsequent_question` 交给检索器，重新展开、过滤并打分后，原隐藏证据只要分数严格超过阈值，仍可重新进入潜在图。恢复依赖再次检索与打分，不自动降低阈值或重新插入事实。

空潜在图合法：仍提交历史记忆；两者都为空时提交空图，缺失关系驱动后续查询。永久拒绝的证据即使高分重输也保持过滤。所有状态按独立问题运行隔离，不因问题文本相同而共享。

## 独立答案排序

[`answer_ranking.py`](../src/agent/answer_ranking.py) 在最终作答产生不少于两个答案时单独调用 LLM。输入仅含原问题、实际选中三元组和实际候选答案，不传检索分数、金标准或未选中证据。零或单答案自动跳过，无关闭排序的业务开关。

排序回复必须正常完成、未拒绝，仅返回 `predict_answer`。候选原字符串和多重集须完全不变，禁止增删、改名或去重；只允许改变顺序，不改变证据、问题、记忆或永久拒绝，也不能补救上游漏答案或漏路径。排序失败时，完整 `manage_memory` 不返回候选快照。`manage_memory_ranked` 是 `manage_memory` 的兼容别名，两者行为相同。

用户已接受排序的工程效果及模型语义限制，并关闭排序调优，不要求开放问题集上零失败。当前调用预算统一见[真实验收入口](#真实验收入口)。

## MSM 重打分轮次接口

[`memory_management.py`](../src/agent/memory_management.py) 提供 `manage_memory(state, triples, scores, client, potential_threshold=0.7)`。成员 C 提供对齐三元组与精炼分数；成员 E 持有问题快照、检索循环、客户端及停止策略。函数本身不读取配置、不检索、不原地修改状态。

```python
from src.agent.llm_client import LLMClient
from src.agent.llm_config import load_llm_config
from src.agent.memory_management import manage_memory
from src.agent.memory_state import QuestionMemory

state = QuestionMemory("Which city was Mira Vale born in?")
client = LLMClient(load_llm_config())
try:
    result = manage_memory(
        state,
        [("Mira Vale", "born_in", "Linden City")],
        [0.95],
        client,
        potential_threshold=0.7,
    )
    state = result.state  # Publish only after every stage succeeds.
    next_queries = result.response.subsequent_question
finally:
    client.close()
```

这段示例会产生真实 API 用量。返回 `MSMResult(state, response)`；仅整体成功后发布 `result.state`，再用 `response.subsequent_question` 驱动下一轮检索。当前默认每轮正常 $2$ 次逻辑请求，多答案为 $3$ 次，各自遵循客户端传输重试及逐次记账。

## LLM 异常与状态保持

默认事务的首阶段、最终阶段和可选排序均属同一公开调用。HTTP、记账、回复封装、严格 JSON、证据或答案归属、决策一致性及排序校验中任一失败，均不返回部分 `MSMResult`。调用方继续持有原快照及既有拒绝集合；输出校验不触发业务重试或备用提供方。如何停止整题、记录失败或稍后显式重试由成员 E 决定。

[`validation_error.py`](../src/agent/validation_error.py) 通过 `ValueError` 子类和固定 `failure_stage`、`error_code` 定位记忆输出失败，覆盖 `evidence_decision`、`decision_consistency` 及基础解析阶段。未识别异常记为 `unclassified`，不复制任意异常消息。HTTP 成功与业务验收成功分别记录。

## 后续子问题

`response.subsequent_question` 是下一轮检索使用的有序字符串元组，不是面向用户的问答。调用方直接传递这些查询，不重新执行初始分解；后续事务仍使用 `state.original_question`。

默认查询由已派生的缺口生成并在发布前校验，规则见[缺口查询与发布门槛](#缺口查询与发布门槛)。无答案时须有 $1$ 至 $3$ 问；有真实缺口时可以分轮补充证据；条件已定且无缺口时必须作答并停止追问。基础解析器保留字符串，不生成替代问题；完整默认事务额外限制允许集合、重复项和未知条件必问项。

## 真实验收入口

[`scripts/validate_part_d.py`](../scripts/validate_part_d.py) 通过公开接口加载真实配置并调用 LLM，仅以受控数据替代 C/E 检索打分，严格阈值为 $0.7$。不修复输出、不增加业务重试，最终关闭客户端。

```text
python scripts/validate_part_d.py --list-cases
python scripts/validate_part_d.py --suite multihop --list-cases
python scripts/validate_part_d.py --suite multihop
python scripts/validate_part_d.py --suite conditional --list-cases
python scripts/validate_part_d.py --suite single
python scripts/validate_part_d.py --suite multi
python scripts/validate_part_d.py --suite removal
python scripts/validate_part_d.py --suite ranking
python scripts/validate_part_d.py --suite conditional
python scripts/validate_part_d.py --suite all
```

`--list-cases` 免费且不读取私有配置；其余命令产生真实用量。默认每个记忆事务最多 $3$ 次逻辑调用，初始分解为 $1$ 次。下表按共享 YAML 当前额外重试 $2$ 次计算 HTTP 上限：

| 验收组 | 用例／事务数 | 逻辑调用上限 | HTTP 尝试上限 |
| --- | --- | --- | --- |
| `single`，含一次分解 | $4$ | $10$ | $30$ |
| `multi` | $4$ | $12$ | $36$ |
| `removal` | $3$ | $9$ | $27$ |
| `ranking` | $2$ | $6$ | $18$ |
| `all`，上述四组 | $13$ | $37$ | $111$ |
| `conditional`，单独运行 | $8$ | $24$ | $72$ |
| `multihop`，单独运行 | $6$ | $18$ | $54$ |

`multihop` 仅使用默认 `evidence_decision`，复用 `single.sufficient`、`single.insufficient`、`single.empty`、`multi.partial`、`multi.recovery`、`multi.exclusion_probe` 的事实及实际返回快照，不运行分解或独立隔离用例。正常为 $12$ 次逻辑调用；上表保守计入可选排序和额外两次传输重试。共用 `check_two_hop` 检查完整两跳抽取、必留路径、精确答案／空答案、具体允许查询、阈值隐藏事实、恢复及两阶段实际提交图；同一检查也用于默认 `single`、`multi` 和 `all` 中的六例，显式旧提示对照保留原行为。缺失关系仅接受脚本中列出的有限语义别名；图中已有关系必须精确匹配。不要求选择或拒绝无关干扰边；`distractor_rejection` 和 `multihop_observations` 单独记录自然拒绝及过滤是否已观察到，未拒绝时明确为 `not_observed`。任何必需断言失败立即停止、退出码为 $1$，不继承失败轮候选快照；成功事务保留原始抽取供人工审阅。新增入口已通过离线检查，尚未做真实两跳验收。

`conditional` 不加入 `all`，对 Silver／Bronze 各执行一张独立完整图和一条从空快照开始的三轮链。首轮只给两条出生地，次轮真实继承快照且仅补出版社，末轮以 $0.95$ 重输全部事实。逐轮检查完整记忆、答案、每条查询、永久拒绝及提交前过滤；失败即停。自动检查仍需人工审阅原始需求抽取、方向、条件语义及证据支持。

显式 `--prompt-variant paper` 或 `--prompt-variant retrieval_questions` 可运行对照；对应 `all` 仍为最多 $25$ 次逻辑调用、$75$ 次 HTTP 尝试，不自动回退。`reference_diagnostic`、`clarification_diagnostic` 和 `empty_diagnostic` 为独立诊断组，可免费预览各自预算，均不加入 `all`。

每次运行保存唯一的私有 `tmp/tmp_part_d_validation_<UTC timestamp>.json`，包含受控输入、实际查询、有效输出、成功事务的分阶段请求与 schema、原始完成回复及前后快照。失败只保存异常类及固定诊断，不保存失败原文、密钥或任意异常消息。实际 HTTP 用量继续写入共享账本；报告不提交仓库。退出码 $0$ 表示结构、状态及该组自动检查通过，`semantic_review` 仍待人工审阅。

## 当前验收状态与剩余事项

2026/11/09，当前默认架构已完成采用。GPT-4o 的两张完整条件图和两条三轮链已通过受控验收，覆盖未知时保留候选路径、条件确定后的正确答案、只移除无关旧证据和高分重输后的永久过滤。两条链共 $6$ 次事务、$12$ 次 HTTP 尝试，无失败、重试或排序请求，实际模型均为 `gpt-4o-2024-08-06`。这些结果支持关闭本轮条件证据保留、作答与旧记忆移除排查。

默认采用经全部 $117$ 项离线回归，以及既有 $8$ 次已验收事务、$16$ 个阶段的免费精确重放，核对请求消息、schema、答案、查询和前后快照完全一致。私有复核汇总为 `tmp/tmp_step5_replay.json`，实际用量以共享账本为准。本次文档清理不新增 API 请求，不修改实现、私有配置或账本。

当前协议支持非条件单跳需求、单关系相等条件，以及 `nonconditional_path` 非条件两跳线性路径。路径从原题中的 `root_entity` 开始，每步使用 `relation + binding`：观测绑定引用本轮排序图编号，由可达前沿端点推导方向；缺失绑定声明方向，已有事实却报缺失时失败。旧 `steps[].direction` 严格拒绝。代码遍历完整提交图，保护全部可达前缀，仅为当前可达实体生成缺口查询。任一候选有缺口时禁止作答，无缺口时答案只能来自代码派生的终跳 `answer_candidates`。自环、角色歧义和仅反向命中以固定诊断失败。其他多跳、聚合和复杂需求仍为 `unsupported`。原题理解、需求完整性和缺失关系方向仍依赖 LLM，两跳真实验收尚未完成。

TODO 2.3.5 的独立 GPT-4o-mini/OpenAI 模块验收保持待办，不能由 GPT-4o 结果替代；原有 `all` 中的两跳用例仍保留为回归，不能预先视为通过。TODO 2.3.6 的历史用量核对和 2.4 最终交付也尚未完成。实际 C/E 接线与召回由成员 E 验证，不作为成员 D 模块验收前置条件。已删除的排查过程保留在 Git 历史和私有原始报告中，本文只维护当前架构、已确认结果及剩余事项。

### 多跳问题与新默认协议不兼容

原有验收要求模块沿“书 → 作者 → 出生城市”的两跳路径作答，但新默认 `evidence_decision` 只描述已知对象的一跳需求，并明确要求将多跳标为 `unsupported`。模型按提示返回该类型时，代码会以 `decision_unsupported` 终止，无法继续保留路径、追问或作答。这是协议与既有用例的兼容问题，不只是模型答错。

**受控用例**

原问题为 `Which city was the author of The Glass Orchard born in?`，正确答案为 `Linden City`。输入事实如下；阈值为 $0.7$，只有严格高于阈值的新事实才提交给模型。

```text
AUTHOR     = ("The Glass Orchard", "written_by", "Mira Vale")
BIRTH      = ("Mira Vale", "born_in", "Linden City")
DISTRACTOR = ("The Copper Harbor", "written_by", "Nolan Reed")
```

| 用例 | 输入与预期行为 |
| --- | --- |
| `single.sufficient` | 空快照，提供 `AUTHOR`、`BIRTH`，分数均为 $0.95$。保留两条路径事实，回答 `Linden City`，不再追问。 |
| `single.insufficient` | 空快照，仅提供 $0.95$ 的 `AUTHOR`。保留作者事实，不回答，追问 `Mira Vale` 的出生城市。 |
| `single.empty` | 空快照、空图。不回答，先追问书的作者；不能编造作者名或城市。 |
| `multi.partial` | 空快照，依次提供三条事实，分数为 $0.95$、$0.7$、$0.95$。保留 `AUTHOR`，追问出生城市；`BIRTH` 未提交，不能被永久拒绝。 |
| `multi.recovery` | 继承上一轮实际快照和查询，三条事实均改为 $0.95$。补回 `BIRTH`，保留完整路径并回答 `Linden City`；已永久拒绝的干扰事实仍须过滤。 |
| `multi.exclusion_probe` | 继承恢复轮快照，再次以 $0.95$ 输入三条事实。路径和答案保持有效，已永久拒绝的干扰事实不能重新提交。 |

这些用例位于 [`scripts/validate_part_d.py`](../scripts/validate_part_d.py) 的 `QUESTION`、`CASES` 和 `run_cases`；`single`、`multi` 均包含在 `all` 中。`multi.independent` 是独立的一跳隔离用例，不是本问题的两跳复现。干扰事实只有实际提交且被省略后才进入永久拒绝，不能预先假定模型一定将其拒绝。

**已观察到的表现与证据边界**

- 旧默认单次选择流程曾使用 `deepseek-flash` 通过上述单轮和多轮场景：完整图保留两跳并回答 `Linden City`；只有作者时保留作者并追问出生城市；空图追问作者；阈值隐藏的出生事实在下一轮恢复，干扰事实被拒绝后持续过滤。历史结果见[旧版单轮记录](https://github.com/yyht6300a/AI6103-Project-FoG/blob/b7f4faac112fca370499803c7242ac23d6675d72/docs/part_d.zh-CN.md#单轮真实验收结果)，亦可查阅[英文单轮与多轮记录](part_d.md#single-round-live-result)。这些结果不能算作新默认通过。
- 新默认的已验收 GPT-4o 条件题直接点名 `Mira Vale`、`Nolan Reed`，只需读取各自的一跳出生事实；它们没有验证从书推导作者再查询出生地的能力。
- 新默认对上述两跳用例尚无已确认的真实通过结果。原记录中的 `unsupported → decision_unsupported` 来自提示和解析逻辑；下方归因已用合成回复核实默认入口的终止、漏保留和错误答案放行边界，不代表真实模型一定生成这些回复。本次不调用 API。
- 旧记录引用的私有报告 `tmp/tmp_part_d_validation_20261108T163125709480Z.json`、`tmp/tmp_part_d_validation_20261108T163553245365Z.json` 当前工作区不存在；历史表现依据已提交文档，不能声称已重新核验原始回复。

**定位入口与修复边界**

协议与求值入口为 [`evidence_decision.py`](../src/agent/evidence_decision.py) 的 `DECISION_INSTRUCTIONS`、`decode_description`、`resolve_query` 和 `derive_decision`；发布边界见 [`memory_update.py`](../src/agent/memory_update.py) 与 [`memory_management.py`](../src/agent/memory_management.py)。下方方案沿这些入口修复，不另建流水线。

[`tests/test_memory_response.py`](../tests/test_memory_response.py) 的多跳实体归属测试和 [`tests/test_memory_update.py`](../tests/test_memory_update.py) 的路径选择测试仅覆盖各自使用的解析或选择流程，不能替代新默认完整事务的多跳验收。免费入口为 `python scripts/validate_part_d.py --suite single --list-cases` 和 `python scripts/validate_part_d.py --suite multi --list-cases`；去掉 `--list-cases` 会产生真实 API 用量。

方案以这些受控用例达标为目标，保留现有条件题的证据保护、严格阈值、永久拒绝和失败不发布规则。不要求开放问题集零失败，不把不同模型效果一致或 GPT-4o-mini 达到其他模型水平作为修复目标；TODO 未完成项和 C/E 集成不列为本问题的排查内容。以下是待实施方案，不表示代码已修复或真实验收已通过。

#### 归因：协议缺少依赖，发布校验只能保护已抽取需求

2026/11/09 对当前代码进行了静态检查，并以合成结构化回复通过公开 `manage_memory` 免费核实边界。私有复现脚本为 `tmp/tmp_multihop_attribution.py`；它不加载 `.env`、不发 HTTP 请求、不改账本。结论如下。

| 层次 | 当前机制及归因 |
| --- | --- |
| 直接阻断 | `DECISION_INSTRUCTIONS` 明确禁止多跳；`decode_description` 收到 `unsupported` 后立即抛出 `decision_unsupported`。合成回复证实只调用首阶段，最终选择不执行，旧快照保持。重试、换模型或降低阈值不能补上协议能力。 |
| 表达缺口 | `BRANCH_SCHEMA` 和 `decode_branch` 将每组 `entity` 变成独立的固定 `target`；`resolve_query` 只能对该字符串查一条关系，`derive_decision` 不会把上一跳的另一端绑定为下一跳对象。多个实体组是并列需求，不等于路径依赖。 |
| 漏保留与永久损失 | `Mira Vale` 出现在图中，因此只抽取其 `born_in` 仍通过对象归属检查。此时 `required_triplets` 只有 `BIRTH`，`missing_relations` 为空；只选择 `BIRTH` 并回答 `Linden City` 可以通过完整默认事务，随后 `update_memory` 将未选的 `AUTHOR` 永久拒绝。已免费复现；这是抽取缺项逃过校验后的结果，不是阈值或拒绝集合公式算错。 |
| 空图与中间实体未知 | 空图中不能合法填写尚未观察到的 `Mira Vale`，现有结构又没有“第一跳结果”这个对象。把题中 `author of The Glass Orchard` 当成 `entity` 只会通过字符串子串归属，生成以这段描述为字面实体的出生地查询；合成求值已确认。字符串在题中不证明它已绑定到图实体。 |
| 答案校验不足 | `parse_memory_response` 只证明答案是所选图的某个端点。合成回复中，选择 `BIRTH` 并回答人物 `Mira Vale` 也能通过默认事务。必须增加路径终点约束，不能把端点归属误当作路径推理正确。 |
| 验收盲区 | `memory_round` 检查结构、旧快照不变及拒绝集合公式；`single` 未逐例断言路径和答案，`multi_observations` 中的恢复等布尔值也未统一作为失败门槛。即便脚本退出码为 $0$，上述语义错误仍可能存在。条件组已有专门断言，不能替两跳组覆盖。 |

完整图上若人为列出“书的作者”和“作者的出生地”两个实体组，现有求值可以将两条边都加入必留集合，漏选会被 `decision_required_omitted` 拦截；合成检查也已确认。因此现有保护机制可复用，真正缺失的是从原题根实体出发的依赖表达和求值。只删除多跳禁令、要求“高召回”或多列几个已知实体，均不能覆盖空图、未知作者及跨轮恢复。

#### 修复选择：增加受限两跳路径协议

保留 `evidence_decision` 默认入口、现有单跳与相等条件描述，新增明确的 `nonconditional_path` 描述变体，先只支持本问题所需的非条件两跳线性路径；聚合、递归和条件内多跳仍明确不支持。路径长度先严格限制为 $2$，不顺带实现通用查询语言或任意依赖图。

首阶段仍只调用一次 LLM：抽取题中根实体及按顺序排列的关系和方向，不填写中间实体名、预测答案或自行判断路径已满足。第 1 步已实现以下协议，第 2 步已完成离线场景门槛，第 3 步已补齐验收脚本断言；真实验收待第 4 步完成。

```json
{
  "description": {
    "kind": "nonconditional_path",
    "root_entity": "The Glass Orchard",
    "steps": [
      {"relation": "written_by", "direction": "outgoing"},
      {"relation": "born_in", "direction": "outgoing"}
    ]
  }
}
```

`root_entity` 必须逐字来自原题，避免用检索到的人名跳过根路径；关系在图中已出现时使用精确关系名，缺失时仍由模型描述待检索关系，与现有缺口语义一致。严格 schema 和本地解析同步约束字段、非空字符串、方向枚举和步数。路径方向由模型抽取，代码只能验证实际连接，不能证明自然语言关系方向理解正确；两跳受控断言和人工审阅仍不可省略。

不把新路径强塞进旧 `entity` 字符串或旧 `observed` 编号绑定：中间实体未知时无法提供这种绑定。新增路径由代码统一对完整图求值；原有单跳端点绑定、条件求值及其保守冲突规则继续按原分支执行。模型若仍将原题错误压成旧单跳描述，本地结构校验无法普遍识别该语义遗漏；本题验收必须检查抽取确实包含两跳，不能因为最终城市碰巧正确就通过，也不为此硬编码书名或问题文本。

#### 路径求值、缺口与发布规则

在 `evidence_decision.py` 增加独立的受限路径求值分支，输入仍为历史记忆与合格新证据的完整并集；内部复用 `required_triplets`、`missing_relations`、`query_matches` 和现有查询渲染，另给路径决策提供代码派生的终跳 `answer_candidates`。不更改公开 `MSMResult`、`QuestionMemory` 或最终三数组回复格式。

1. **从根实体逐跳连接**：第一跳只匹配 `The Glass Orchard` 的 `written_by`；下一跳对象只能取这些实际三元组的另一端。按声明方向精确匹配关系及端点，不反转、补造或改写三元组。图中单独存在某人的出生事实，不能证明他是这本书的作者。
2. **保护所有已匹配前缀**：每一步扫描完整提交图，保留所有可达候选对应的路径边，并将其并入 `required_triplets`。未完成的前缀也必留，不能只保护最终出生地。若同一书匹配多个作者，不任取第一条；对每个实际作者继续求值，某条前缀尚有缺口时保守地暂不发布答案。
3. **只询问当前可解析的缺口**：没有作者边时，只生成根实体的作者查询，下游出生地步骤保持待解析，不生成虚构人名或描述占位实体。已有 `AUTHOR` 而无 `BIRTH` 时，生成 `Mira Vale` 的出生地查询。使用现有固定查询模板、去重和至多三问规则；某个候选缺失不妨碍继续保护其他候选已有的路径。声明方向未命中而仅反向命中时按保守冲突报错，自环也报错，不能静默翻转方向。
4. **约束路径答案**：路径尚有缺口时禁止 `predict_answer`；完整时必须回答且停止追问。每个答案必须来自从根实体真实走完两跳所得的 `answer_candidates`，不能只检查它出现在某条所选边上。终跳约束只作用于新路径变体；既有单跳和条件题契约不扩大修改。
5. **在拒绝计算前失败**：最终阶段仍由 LLM 选择原始证据并作答；代码只校验，不自动补选或补答。漏掉任一必留前缀、跳过缺口、答中间人物或答不相连城市，均须在计算可发布永久拒绝集合前抛出固定诊断。沿用最终排序成功后才返回的事务边界，任一步失败都保留原快照。
6. **保持跨轮语义**：每轮用原问题和实际继承快照重新求值，不保存跨轮证据编号，也不新增路径缓存。`BIRTH` 分数为 $0.7$ 时既不参与求值，也不进入拒绝集合；升至 $0.95$ 后可与旧 `AUTHOR` 接通。已有永久拒绝继续在请求前过滤，不能借修复清空拒绝状态。已误拒绝 `AUTHOR` 的旧运行无法靠重新输入恢复，应从新 `QuestionMemory` 重跑该题，不能宣称新代码会自动纠正历史快照。

#### 最短实施与验收顺序

1. **先修协议与求值**：同步修改 `evidence_decision.py` 的提示、严格 schema、本地解码和受限路径求值；在 `memory_prompt.py`、`validate_decision_response` 中接入前缀、缺口及终跳候选约束，新增诊断时登记到 `validation_error.py`。`memory_update.py` 和 `memory_management.py` 原则上只需核对现有流程是否已转发新决策，不重写事务、阈值、拒绝或排序机制。当前归因已经有免费证据，不先花钱重复证明旧协议不支持多跳。

   > 2026/11/09：第 1 步已实现。新增严格两跳结构及本地解码、完整图逐跳求值、全部前缀保护、可达缺口与终跳答案约束，登记四个固定诊断。已核对现有更新和公开入口转发完整决策，并在拒绝计算和最终排序发布前执行校验；调用预算不变。现有离线测试 $117$ 项通过，未调用真实 API。可以进入第 2 步，补齐公开默认入口的六场景和反例门槛；本记录不代表两跳真实验收或独立交付完成。

2. **用公开默认入口完成离线门槛**：在 `tests/test_evidence_decision.py` 等现有测试中用合成首阶段和最终回复覆盖上表六个场景，真实继承每轮返回快照；另补两个有区分力的反例：有出生事实但缺少作者连接时只能问作者；正确路径旁有不相连出生事实时不能据此作答。覆盖两条作者候选中一条尚缺出生地，确认不会只凭另一条完整路径提前作答。分别注入漏选作者边、错误终跳答案和无效路径结构，检查抛错位置与旧快照保持；另检查已有精确连接时不会产生伪缺口。对真正已拒绝干扰边的过滤用合成快照验证，不能要求真实 LLM 必然拒绝可选干扰边。

   > 2026/11/09：第 2 步已完成。`tests/test_evidence_decision.py` 新增 `TwoHopDefaultEntryTests` 的 $8$ 项测试，以合成首阶段和最终回复通过公开默认 `manage_memory` 覆盖 `single.sufficient`、`single.insufficient`、`single.empty`、`multi.partial`、`multi.recovery`、`multi.exclusion_probe`，多轮真实继承返回快照及查询。逐轮核对两阶段完整提交图、必留前缀、可达缺口、终跳候选、精确答案／查询及拒绝集合；阈值等于 $0.7$ 的出生事实不提交、不拒绝，升至 $0.95$ 后接通旧记忆，已有精确连接不产生伪缺口。补齐断开出生事实、无关城市、双作者一条缺证据的反例，并用预置拒绝快照确认高分干扰边在两阶段请求前过滤，不依赖真实模型主动拒绝。漏选作者边、回答中间人物／不相连城市、缺口时提前回答、错误查询及 $13$ 种无效路径结构均被对应阶段拦截，未构造新快照，旧记忆和永久拒绝保持。定向命令 `python -m unittest discover -s tests -p test_evidence_decision.py -v` 的 $21$ 项及完整命令 `python -m unittest discover -s tests -v` 的 $125$ 项全部通过；未调用真实 API，未修改生产逻辑、私有配置或共享用量账本。可以推进第 3 步，补齐维护验收脚本的 `multihop` 组及失败断言；真实两跳验收和 TODO 2.3.5 独立交付仍待办。

3. **补齐同一组用例的验收断言**：在 `scripts/validate_part_d.py` 复用现有事实和轮次，增加只运行上述六个场景的 `multihop` 组，保留 `single`、`multi` 的既有入口；共用两跳检查函数，避免复制两份预期。把必需路径、精确答案、空答案、具体允许查询、阈值隐藏事实未拒绝、恢复及已拒绝事实未重新提交设为失败门槛。完整图要求包含 `AUTHOR`、`BIRTH` 并回答 `Linden City`；空图问作者；部分图保留 `AUTHOR` 并问 `Mira Vale` 的出生地。不把是否选中无关干扰边设为修复成败条件；若它从未被拒绝，报告应明确未观察到该项自然拒绝，不能冒称过滤已实测。失败立即停止，不继承失败轮结果。

   > 2026/11/09：第 3 步已完成。新增 `--suite multihop`，仅复用指定六例，保留默认 `single`、`multi` 入口并共用 `check_two_hop`。必留路径、精确答案／空答案、具体允许查询、完整两跳抽取、阈值隐藏事实未提交且未拒绝、继承恢复及两阶段实际提交图均成为失败门槛；失败立即停止，CLI 返回 $1$，不继承失败轮候选结果。干扰边可保留也可拒绝，报告分别记录自然拒绝和过滤覆盖；从未拒绝时明确 `not_observed`。新增离线测试覆盖六例真实快照／查询交接、自然拒绝与始终保留干扰边两种情况、碰巧答对的错误单跳抽取及逐项断言故障；脚本层 $11$ 项、完整离线 $129$ 项全部通过。免费命令 `python scripts/validate_part_d.py --suite multihop --list-cases` 确认六例和 $18$ 次逻辑调用上限；按额外两次传输重试，HTTP 上限为 $54$ 次。未调用真实 API，未修改生产模块、私有配置或共享账本。可以推进第 4 步，在 GPT-4o-mini/OpenAI 上执行一次有界真实验收并人工审阅抽取；真实两跳效果、条件冒烟和 TODO 2.3.5 独立交付仍待后续步骤。

4. **离线通过后才做有界真实验证**：先运行新增路径测试及 `python -m unittest discover -s tests -v`，确认现有条件保护、严格阈值、永久拒绝和排序失败不发布均未回归。再用维护入口免费预览新组，随后只执行一次六场景真实验收，使用私有配置中的 GPT-4o-mini/OpenAI，不加业务重试、不换模型做效果竞赛。六次事务正常为 $12$ 次逻辑调用；按现有每事务最多三次及额外两次传输重试，保守上限为 $18$ 次逻辑调用、$54$ 次 HTTP 尝试。第 3 步已提供 `--suite multihop`；其离线通过及免费预览不代表真实验收已完成。

   > 2026/11/09：已执行第 4 步，验收未通过。使用项目 conda 环境运行新增路径测试 $21$ 项及完整离线回归 $129$ 项，全部通过；免费预览确认六例及 $18$ 次逻辑调用上限。随后仅运行一次 `python scripts/validate_part_d.py --suite multihop`，实际模型为 `gpt-4o-mini-2024-07-18`，端点为 OpenAI。首例 `single.sufficient` 在首阶段触发 `evidence_decision / decision_conflict`，CLI 返回 $1$ 并立即停止；旧空快照保持不变，未发布记忆或永久拒绝，未执行最终选择、排序、其余五例或条件冒烟。共享账本新增 $1$ 次成功 HTTP 请求，输入 $1284$、输出 $126$ tokens，无失败或重试；HTTP 成功不代表业务验收通过。私有报告为 `tmp/tmp_part_d_validation_20261109T120233376333Z.json`，逐次用量与调用计数核对通过，私有 `.env` 哈希未变。失败报告按现有策略不保存原始回复，因此不能人工审阅本次抽取，也不能断言具体关系、方向或题型错误；该诊断对应已有事实与缺口声明冲突，或单值条件出现多个值，尚需定位实际触发分支。不能推进第 5 步；下一项按第 6 步定位首阶段冲突，修复后只复验失败场景及受影响依赖链，不重跑完整批次或更换模型。TODO 2.3.5 独立交付验收保持待办，未修改生产代码。

   **第 4 步补充归因与处置方案**

   > 2026/11/09 执行结果：已修正首阶段提示的四类路由，明确两跳依赖即使已知中间实体也必须使用 `nonconditional_path`，并加入不同实体示例；未修改 schema、求值器或发布契约。同一验收入口新增可重复的 `--case`，支持 `multihop`／`conditional` 的既有用例、保留原顺序，遗漏前置依赖时在加载配置前失败。受控两跳首阶段业务失败时保存经生产解码、字段词汇和长度限制的投影及精确命中编号；无效／未知文本只记 `unavailable`，不保存原始失败回复。新增单例执行、依赖拦截、假缺口投影及敏感文本排除测试，完整离线 $131$ 项通过；字符扫描通过。免费预览 `--suite multihop --case single.sufficient --list-cases` 确认仅一例，最多 $3$ 次逻辑调用／$9$ 次 HTTP。
   >
   > 仅复验 `single.sufficient`，使用 `gpt-4o-mini-2024-07-18`／OpenAI，仍在首阶段触发 `evidence_decision / decision_conflict`，立即停止。私有报告 `tmp/tmp_part_d_validation_20261109T122246762856Z.json` 的投影确认本次实际错误：`kind=conditional_equals`；把书的 `written_by=Mira Vale` 当作条件，把整句原题当作 `condition_quote`；`true` 分支引用编号 $0$ 的出生事实，`false` 分支却把同一人的 `born_in` 标为 `missing/outgoing`，完整图编号 $0$ 精确命中，触发假缺口校验。此证据只说明本次回复，不能还原上一次历史失败。未执行最终选择、排序或后五例，旧空快照及拒绝集合不变。共享账本仅新增 $1$ 次成功 HTTP，输入 $1395$、输出 $135$ tokens，调用计数相符，无重试；私有 `.env` SHA-256 前后相同。
   >
   > 不能推进第 5 步。下一项仍是第 6 步的首阶段修复：明确关系依赖／定语从句不是分支条件，`conditional_equals` 必须有原题显式真假分支，禁止从图中关系构造条件；保留已有两跳路由和严格校验。补充本次实际错误结构的免费回归后，只再次复验首例，新增预算单独列明；通过后才运行剩余五例及条件完整图冒烟。不改求值器，不自动把旧结构转换成路径，不重复相同提示调用。TODO 2.3.5 独立交付验收保持待办。

   **执行后再归因与下一轮方案**

   > 2026/11/09 执行结果：已实施条件准入修订，仅修改 `DECISION_INSTRUCTIONS`，增加依赖题与真实条件题对照，明确比较值来自原题、禁止由图制造条件和由定冠词补出单值前提；schema、解码、求值及发布规则保持原契约。新增实际错误结构与“仅修假缺口仍抽错类型”的免费回归，原错误保持 `decision_conflict`／一次调用／旧快照不变，反事实仍被 `two_hop_extraction` 拦截；完整离线 $132$ 项通过，字符扫描通过。条件准入方案已执行到真实复验；后续以本记录下方的最新归因与方案为准。
   >
   > 首例报告 `tmp/tmp_part_d_validation_20261109T123849256549Z.json`：GPT-4o-mini／OpenAI 实际返回 `nonconditional_path`，根为 `The Glass Orchard`，路径为 `written_by/outgoing → born_in/outgoing`；两边必留、无缺口、唯一候选及答案为 `Linden City`、无后续查询，全部受控断言通过，实际 $2$ 次请求。随后报告 `tmp/tmp_part_d_validation_20261109T123903269418Z.json`：`single.insufficient`、`single.empty`、`multi.partial`、`multi.recovery` 通过，恢复轮真实继承部分轮快照和查询；干扰边已在部分轮自然拒绝，隐藏出生边未被提交或拒绝，恢复轮成功作答。空图实际将首关系命名为 `author`，其余路径使用 `written_by`；空图允许描述缺失关系，受控检查通过，但实际检索召回仍未验证。
   >
   > 最后一例 `multi.exclusion_probe` 首阶段失败，立即停止，未执行条件冒烟。受限投影为正确题型和根实体，但路径是 `written_by/outgoing → born_in/incoming`；提交图中的出生事实为 `Mira Vale → born_in → Linden City`，从作者继续应使用 `outgoing`。反向声明触发 `evidence_decision / decision_path_conflict`，未进入最终选择或排序，旧快照（两条记忆及一条永久拒绝）保持不变。此次是方向抽取错误，不能归因为条件误判复发或拒绝过滤失效；模型内部原因未知。路径段尚未明确说明方向相对于“当前可达节点”，现有 head/tail 说明位于仅适用于分组一跳绑定的段落，这是下一处可针对性修订的提示边界。
   >
   > 本轮累计新增 $11$ 次逻辑调用／成功 HTTP，无重试，账本新增 $11$ 行，输入 $14055$、输出 $457$ tokens；实际模型均为 `gpt-4o-mini-2024-07-18`。私有 `.env` SHA-256 前后相同。不能推进第 $5$ 步；TODO 2.3.5 独立交付关卡保持待办。方向修订、依赖链复验及追加预算以下方最新方案为准。

   **最新归因：第二跳方向抽反，过滤与事务保护正常**

   2026/11/09：核对 `tmp/tmp_part_d_validation_20261109T123849256549Z.json`、`tmp/tmp_part_d_validation_20261109T123903269418Z.json`、对应账本行及当前实现，用私有 `tmp/tmp_step4_direction_attribution.py` 免费重放最新失败和方向反事实。未读取 `.env`、调用 API、修改生产代码或账本。下文取代已执行的条件准入方案，针对最新 `multi.exclusion_probe` 失败；此前无投影的首次失败仍不能还原，不将本次原因回填历史。

   | 层次与证据 | 结论及处置 |
   | --- | --- |
   | **本轮进展**：首例及随后四例通过，最新失败投影也是 `nonconditional_path`，根实体和两个关系均正确。 | 条件准入修订在本轮六例中均选对路径类型；本次只错第二跳方向。不能继续把条件误判、协议不支持两跳或缺少 `--case` 当作当前阻塞，也不能据此宣称题型抽取普遍稳定。 |
   | **输入与状态**：失败轮继承恢复轮两条记忆和一条永久拒绝；三条重输事实分数均为 $0.95$。记录的 `submitted` 只有 `AUTHOR`、`BIRTH`，不含 `DISTRACTOR`。 | 新展开过滤会排除已有记忆与拒绝，但完整提交图重新并入历史记忆。必要边没有丢失，干扰边已过滤；不能将“没有合格新边”误作空图，也无需改阈值或清空拒绝集合。 |
   | **直接触发点**：第一跳 `written_by/outgoing` 将当前节点从书推进到 `Mira Vale`；第二跳为 `born_in/incoming`。图中只有 `Mira Vale → born_in → Linden City`。 | `derive_path_decision` 对 `incoming` 查尾端，找不到尾为作者的出生边，却在头端找到该关系，命中“声明方向无事实、仅反向有事实”的 `decision_path_conflict`。没有自环，不是假缺口条件分支或多值冲突；最终选择和排序未执行。 |
   | **免费反事实**：原投影经公开 `manage_memory` 重放仍在首阶段失败，只调用一次、旧快照不变。仅将第二跳改为 `outgoing` 后，两条必留、无缺口、唯一候选 `Linden City`；复用已保存的有效最终回复，完整事务通过，原记忆及拒绝不变。另将出生边反转后，原 `incoming` 路径也能正常求值。 | 当前方向求值符合契约；修复应落在抽取提示。不能把所有方向固定成 `outgoing`，也不能自动翻转冲突或补答。后两个成功均为合成反事实，不算真实模型复验。 |
   | **提示边界与证据限度**：head/tail 定义位于明确只适用于分组一跳绑定的段落；路径段只列出方向枚举和顺序，没有逐跳当前节点的操作定义。按当前代码重建失败轮首请求，与已保存的成功首例及恢复轮消息、schema 相同。 | 路径方向的参照点缺少明确说明，是可直接修正的提示缺口。相同请求已有成功输出，说明并非每次都抽错；不能据此断定模型内部原因或声称补一句提示就能稳定解决。失败报告未保留原始请求，重建一致不等于独立核验了当时请求原文。 |
   | **故障边界与用量**：真实报告确认旧快照不变；对应 $11$ 行账本均为成功 HTTP，合计输入 $14055$、输出 $457$ tokens，与两个报告的 $2+9$ 次逻辑调用相符。 | HTTP 成功与业务通过分开记录。本次不支持网络、重试、排序、过滤或发布事务故障的归因；不再排查这些已被证据排除的方向。 |

   **最短修复与复验方案（待实施）**

   > 2026/11/09 执行结果：已在 `DECISION_INSTRUCTIONS` 路径段补充逐跳当前节点、头尾端方向、移到另一端再判下一跳的操作定义，以及 `owns/incoming → headquarters/outgoing` 和 `owned_by/outgoing` 对照例；明确已有图按精确关系和端点判方向，缺图不编造中间实体。条件准入、schema、求值、最终提示、拒绝及发布规则未改。新增恢复快照回归验证原第二跳反向声明在首阶段以 `decision_path_conflict` 失败、仅一次调用、投影保留错误方向、两条记忆及一条拒绝不变；仅修正第二跳及实际反转出生边的对照均经公开事务成功，防止将方向一律固定为出向。完整离线 $133$ 项通过，语法检查通过。
   >
   > 免费预览确认真实依赖链仅含 `multi.partial → multi.recovery → multi.exclusion_probe`，上限 $9$ 次逻辑调用／$27$ 次 HTTP 尝试。从空快照执行一次，首轮 `multi.partial` 即失败并停止，报告为 `tmp/tmp_part_d_validation_20261109T142220796878Z.json`。实际模型 `gpt-4o-mini-2024-07-18`／OpenAI；受限投影是 `nonconditional_path`，根 `The Glass Orchard`，步骤 `written_by/incoming → born_in/outgoing`。本轮提交图只有两条作者边，出生边分数等于阈值，未提交且未拒绝；从书出发应沿 `written_by/outgoing` 到作者，模型第一跳抽反，代码触发 `evidence_decision / decision_path_conflict`。未进入最终选择、恢复轮或排除探测轮，空旧快照保持不变。
   >
   > 新旧提示差异仅为上述路径方向操作定义和混合方向例；旧失败错第二跳，新失败错第一跳，题型、根和关系名称正确。本次说明补充后仍未解决逐跳方向抽取可靠性，不能归因为求值、阈值或拒绝过滤失效，也不能推断模型内部原因。未重复发送相同提示、叠加提醒或追加预算；后续须先针对第一跳未解决字段决定具体变更，再另列复验预算并从链首重建。
   >
   > 账本新增 $1$ 行成功 HTTP，对应 $1$ 次逻辑调用，无重试，输入 $1768$、输出 $40$ tokens；HTTP 成功不代表业务通过。私有 `.env` SHA-256 前后相同。三个独立场景与条件冒烟均未执行，**不能推进第 $5$ 步**；TODO 2.3.5 仍待办。上方“待实施”为原计划状态，本引用块记录此次实际执行及停止位置。

   1. **只补路径方向的操作定义**：直接修改 `src/agent/evidence_decision.py` 的 `DECISION_INSTRUCTIONS` 路径段。初始当前节点是 `root_entity`；每跳方向相对于该跳当前可达节点，当前节点是三元组头端则 `outgoing`，是尾端则 `incoming`，下一节点取另一端，再判断下一跳。已有图时依据精确关系及当前端点判断，不按题目被动语态、问句词序或最终答案的位置猜方向；证据缺失时描述原题所需的关系和方向，不编造中间实体。用其他实体补一个混合方向例：图为 `Helios Ltd → owns → Aurora Tower`、`Helios Ltd → headquarters → Northport`，从塔询问所属公司总部时应为 `owns/incoming → headquarters/outgoing`；若第一条表示为 `Aurora Tower → owned_by → Helios Ltd`，则第一跳为 `owned_by/outgoing`。保留已有效的条件准入、四类路由和两跳约束；不改 schema、解码、求值器、最终提示、拒绝或发布规则，不新增分类调用、自动纠错或业务重试。实现时同步中英文模块说明。

   2. **补一个实际失败回归，再跑现有离线检查**：在现有 `tests/test_part_d_validation.py`／`tests/test_evidence_decision.py` 用例中复用最新投影和恢复轮快照，核对 `outgoing → incoming` 触发 `evidence_decision / decision_path_conflict`、一次调用、不执行最终阶段、两条记忆及一条拒绝保持，并确认受限投影保留错误方向。同组对照只改第二跳为 `outgoing`，应派生完整路径、无缺口、唯一城市，并通过公开事务；补一组图确实支持 `incoming` 的路径，防止修成全出向。复用既有六场景、条件保护及发布门槛，不重建诊断或复制整套路径测试。运行受影响测试和一次完整 `python -m unittest discover -s tests -v`。这些合成检查验证错误边界及防护，提示是否有效必须由下一项真实链确认。

   3. **先复验失败的真实依赖链**：免费预览以下命令加 `--list-cases` 的结果，确认仅三轮及调用上限；离线通过后，用 GPT-4o-mini/OpenAI 执行一次，不换模型，不先重跑已通过的独立首例。

      ```bash
      python scripts/validate_part_d.py --suite multihop --case multi.partial --case multi.recovery --case multi.exclusion_probe
      ```

      从空 `QuestionMemory` 开始，逐轮继承实际返回快照和查询；不能单独运行末轮、手填恢复快照，或把免费反事实当成真实前态。人工审阅每轮根、关系及逐跳方向；部分轮保留作者、只问作者出生地，阈值隐藏的出生边既不提交也不拒绝；恢复轮与探测轮保留完整路径、回答 `Linden City` 且不追问。检查两阶段实际提交图和拒绝过滤，所有现有断言及用量核对通过才接受。不要求模型自然拒绝可选干扰边；若新链始终保留它，记录自然过滤未观察到，由离线预置拒绝测试覆盖。

      任一轮失败立即停止。若仍抽错方向或题型，只对照该轮投影、图及实际提示定位首阶段，不再发送相同提示碰运气；本次定向修订后若仍错，不继续叠加泛化提醒，先明确新旧提示差异和未解决字段，再决定下一项变更与追加预算。只有方向正确而派生结果错误才查求值；派生正确而最终输出错误才查最终阶段。投影不可用先核对现有投影的拒绝原因，不扩大原始失败日志。提示再次修改后，从链首重建，不混用旧修订的前态。

   4. **链通过后才补共用提示影响的三个独立场景**：同一修订执行 `python scripts/validate_part_d.py --suite multihop --case single.sufficient --case single.insufficient --case single.empty`，先加 `--list-cases` 免费预览。分别核对完整图答案及路径、缺出生地时只问作者出生地、空图只问书的作者，不编造中间实体；空图关系别名仍按现有门槛处理，不顺带开展真实检索实验。六例在同一修订全部通过，才进入外层第 $5$ 步，用已存在的 `--suite conditional --case conditional.silver_full --case conditional.bronze_full` 做两张完整条件图冒烟；本方案不再重复执行一份条件冒烟，也不新增选择能力。核对真实条件类型及两支答案，旧输出重放与扩大条件链仍按第 $5$ 步的触发条件处理。

   | 下一轮按序关卡 | 正常逻辑调用 | 保守逻辑上限 | HTTP 尝试上限 |
   | --- | --- | --- | --- |
   | 三轮真实依赖链，先执行 | $6$ | $9$ | $27$ |
   | 三个独立场景，链通过才执行 | $6$ | $9$ | $27$ |
   | 第 $5$ 步两张条件完整图，六例通过才执行 | $4$ | $6$ | $18$ |
   | 全部依次通过时合计 | $16$ | $24$ | $72$ |

   上限沿用每事务最多 $3$ 次逻辑调用及额外 $2$ 次传输重试；这是下一轮追加预算，不含已发生的 $11$ 次或更早失败。失败后的再次修订与复验另列预算，不将多轮重跑累计冒称仍在本轮上限内。

   **方案复核：是否绕远路**

   复核后保留“补清逐跳方向 → 实际失败免费回归 → 从链首复验失败链 → 三个独立场景 → 第 $5$ 步条件冒烟”。第一项已由最新投影和方向反事实定位，无需再付费复现旧提示、修已解决的条件准入、重复建设路径协议／诊断／`--case`，或排查网络、阈值、排序、模型竞赛及 C/E 集成。三轮重建是末轮验收的真实状态依赖，不能省成手造快照；独立三例覆盖本次共用路径说明对完整图、缺证据和空图的影响。条件冒烟虽不是本次故障定位工具，但共用首提示已变，按既有第 $5$ 步保留一次两图检查即可，不扩大完整条件链或 `all`。

   本次只完成归因与计划。上一修订已有五例真实通过，但第六例失败，不能整体过关；新方向提示尚未实施或真实验证，条件冒烟未执行。提示能否改善方向仍待真实链确认；TODO 2.3.5 独立交付验收保持待办，六例及冒烟通过也不等于整个交付关卡完成。

   **本次执行再归因：方向定义已补，已观察步骤改用证据绑定（待实施）**

   2026/11/09：本节针对“最短修复与复验方案”的最新执行记录，依据 `tmp/tmp_part_d_validation_20261109T142220796878Z.json`、最新账本行、提交 `7c0e9d5` 与当前实现制定下一步方案，取代上方已执行的“只补路径方向操作定义”方案。上方“新方向提示尚未实施”是该方案实施前的历史状态，不能作为当前待办。本次仅补充归因与计划；未修改生产代码、私有配置或账本，未调用 API。

   | 证据层次 | 归因与排除 |
   | --- | --- |
   | **实际输入**：`multi.partial` 从空快照开始；作者边和干扰作者边分数均为 $0.95$，出生边为 $0.7$，阈值为 $0.7$。完整提交图排序后编号 $0$ 为 `The Copper Harbor → written_by → Nolan Reed`，编号 $1$ 为 `The Glass Orchard → written_by → Mira Vale`。 | 出生边被严格阈值正确隐藏；原题作者连接实际存在。没有历史记忆或拒绝可供误过滤，当前错误与上一轮末轮的拒绝状态无关。把出生边提前放入图、降低阈值或清空状态均没有针对触发点。 |
   | **精确触发链**：受限投影为 `nonconditional_path`，根、两个关系均正确；第一跳却为 `written_by/incoming`。`decode_description` 只验证结构、枚举及根的原题归属；`derive_path_decision` 随后查尾端，没有尾为书的作者边，却在头端发现编号 $1$，抛出 `decision_path_conflict`。 | 这是首跳已观察关系的方向抽取错误。并非第二跳缺证据导致失败；第二跳尚未求值，最终选择及排序也未执行。当前求值器按输入契约阻止错误路径，应保留其冲突保护。 |
   | **免费反事实**：用私有 `tmp/tmp_step4_first_hop_attribution.py` 经公开 `manage_memory` 重放投影，仍为首阶段一次调用失败、旧空快照不变。仅把第一跳改为 `outgoing`，代码即派生必留作者边、唯一出生地缺口、空候选；配合合成最终回复，完整事务通过，出生边仍未提交或拒绝。把实际作者边反转后，原 `incoming` 也能正确求值。 | 错误字段已隔离；修好首跳后，本轮正确行为是保留作者并追问 `Mira Vale` 的 `born_in`，不是回答城市。合法入向必须保留。反事实只证明代码边界与目标行为，不证明新提示或新协议已获真实模型认可，也不能要求真实模型必然拒绝干扰边。 |
   | **上次修订效果及可知边界**：`7c0e9d5` 已加入当前节点、头尾端及混合方向例；新失败仍抽反方向。旧失败位于第二跳，新失败位于第一跳，且两轮提交图和快照不同。 | “未说明方向参照点”已被修订，不能继续作为未处理事项，也不能用这两个不同输入估计成功率或断言修订导致退化。模型为何生成 `incoming` 仍未知；可确认的设计问题是：图中可校验的方向仍由模型自由生成，代码只能在生成后拦截。下一项应减少这个自由字段，不能再以加一句提醒作为完整修复。 |
   | **诊断与成本边界**：报告的 `failed_extraction.hits=[]` 来自只遍历分组单跳的投影逻辑，路径没有填充该字段；账本仅新增 $1$ 行，模型 `gpt-4o-mini-2024-07-18`，输入 $1768$、输出 $40$ tokens，HTTP 成功且无重试。 | `hits=[]` 不代表图中无作者边；直接核对 `submitted` 即可，不新建诊断系统。失败报告未保存原始请求与回复，当前源码重建不能冒称当时请求原文。HTTP、阈值、拒绝和事务发布均无故障证据，不继续沿这些方向排查。 |

   **解决方案：复用观测绑定，让代码推导已有边方向**

   > 2026/11/09 实施结果：路径提示、schema、严格解码及前沿求值已改为 `relation + binding`，观测编号由可达端点推导方向，继续扫描完整图并保护全部同向事实；缺失步骤保留方向声明，旧协议严格拒绝。验收入口同步检查绑定和最终派生匹配／缺口。全部 $134$ 项离线测试通过，覆盖入向／混合方向、编号错误、断连、关系不符、自环、冲突、假缺口、多候选及空前沿。
   >
   > 免费预览确认三轮上限为 $9$ 次逻辑调用／$27$ 次 HTTP 后，以 GPT-4o-mini/OpenAI 执行一次，在 `multi.partial` 的 $2$ 次调用后触发 `controlled_check_failure` 并停止。私有原始报告：`tmp/tmp_part_d_validation_20261109T144637541323Z.json`。人工核对：编号 $1$ 精确对应 `The Glass Orchard → written_by → Mira Vale`，首跳正确推导为 `outgoing`；第二跳却抽取为 `born_in + missing(incoming)`，代码和最终选择据此生成错误的入向查询，`reachable_query=false`。作者前缀保留，出生边未提交且未拒绝；候选拒绝干扰书边，旧空快照不变，失败候选未继承。恢复、探测、三个独立场景及条件冒烟均未执行，未重试相同提示。账本新增 $2$ 行成功 HTTP，无重试，实际模型 `gpt-4o-mini-2024-07-18`，合计输入 $2903$、输出 $100$ tokens；私有配置未改。
   >
   > **不能推进外层第 $5$ 步**：本轮观测边方向修复生效，仍有首阶段 `steps[1].binding.direction` 的缺失关系语义错误；缺失出生边没有可供代码推导的观测端点。最终选择执行了派生查询，不应修改选择阶段或自动翻转。下一项应修订缺失关系方向抽取契约，先用本轮原始输出做免费回归；新增真实链另列预算，从空快照重建。TODO 2.3.5 保持待办，本轮离线通过不代表真实验收通过。

   1. **直接修改两跳抽取契约，不再追加方向提醒**：在 `src/agent/evidence_decision.py` 同步修改路径提示、`DESCRIPTION_VARIANTS`、`decode_description` 和 `derive_path_decision`。保留 `root_entity`、两个有序精确关系，以及当前条件准入；每个路径步骤改为 `relation + binding`，复用现有单跳的两种绑定形式：已观察步骤为 `binding={kind: observed, evidence_ids: [...]}`，不再生成方向；真正缺失步骤为 `binding={kind: missing, direction: outgoing|incoming}`。观测编号沿用本轮完整排序图的 `Numbered knowledge`，无需新增调用、实体字段或候选枚举。上方旧协议“路径不填证据编号”的限制随本修订显式替换；不是在旧输出上偷偷翻转方向，旧 `steps[].direction` 或混合新旧字段须被严格解码拒绝，不提供自动迁移或兼容回退。

      最新失败输入的新契约目标是第一跳 `written_by + observed([1])`、第二跳 `born_in + missing(outgoing)`；编号 $1$ 的书位于头端，代码据此得到首跳 `outgoing`。前一次完整图的第二跳则应引用实际出生边，由当前作者端点推导方向，避免仅修首跳而留下原来的第二跳故障。示例只用于说明当前输入；实现提示继续使用其他实体，不写死书名、作者、关系或编号。

   2. **按可达前沿校验绑定，保留原有缺口和发布边界**：每跳从代码已有的 `frontier` 出发，校验编号列表非空、元素为非布尔整数、编号范围、精确关系，以及被引边与当前可达实体的端点连接。根据该实体是头端还是尾端推导方向；端点角色必须唯一，全部引用必须支持该步同一方向，自环、角色歧义、引用方向不一致或引用不相连边均失败。不能把单目标 `resolve_query` 原样套到多个候选上，应复用其绑定校验规则，按路径前沿校验端点。推导后仍扫描完整图，保护同方向的所有匹配边并更新全部可达实体，编号不是模型裁剪证据的授权。

      部分候选有边、部分候选缺边时，以有效观测推导该步方向，对每个可达实体分别收集事实或生成缺口；仅反向命中的候选仍按原冲突规则失败，任一缺口仍阻止作答。只有当前前沿两侧均无该关系事实时才接受 `missing`；已有事实却报缺失必须失败。前沿为空时，后续未到达步骤只保留待用需求，不引用不相连事实、不造中间实体、不提前生成查询。两跳方向仍统一到每步，遇到无法统一的情形保守失败，不扩成通用规划器。保留必留前缀、终跳候选、最终选择、永久拒绝及排序后发布规则；不增加自动补答或业务重试。

   3. **同步现有验收断言，免费检查后再付费**：在 `tests/test_evidence_decision.py` 和 `tests/test_part_d_validation.py` 复用已有两跳用例与快照，不另建套件。用最新部分图和上次完整图构造新协议合成回复，分别确认已有边的方向由端点推导、缺出生地时仅问出生地、完整图只答终跳城市；旧错误结构被新解码拒绝不能算修复成功。覆盖合法入向／混合方向、编号错误／不相连边／关系不符／自环、已有边假报缺失，以及多候选中一条缺边和空前沿旁有孤立事实。确保未引用的同向匹配边仍受保护，未知中间节点时只问当前可达缺口；复用现有失败时旧快照保持、阈值隐藏事实不拒绝等断言。

      同步 `scripts/validate_part_d.py` 的 `check_two_hop`：它当前把步骤写死为 `relation + direction`，须核对新绑定、引用连接和最终提示中代码派生的逐跳匹配／缺口，保留根、关系、必留路径、精确答案及查询检查，不能删除抽取门槛来制造通过。`extraction_projection` 沿用已有有界校验，适配新字段；路径 `hits` 不冒称命中清单，无需扩大失败原文记录。更新受影响的合成回复构造处及中英文模块说明；必要的新诊断登记在 `validation_error.py`。先运行受影响测试，再运行一次完整 `python -m unittest discover -s tests -v`；离线通过只说明契约和防护有效，不能证明 LLM 会稳定生成正确编号、关系或缺失方向。

   4. **首先重建最新失败链，失败就在该字段停止**：离线通过后，先给下列命令追加 `--list-cases` 免费预览，确认仅三轮及调用上限，再以 GPT-4o-mini/OpenAI 执行一次。

      ```bash
      python scripts/validate_part_d.py --suite multihop --case multi.partial --case multi.recovery --case multi.exclusion_probe
      ```

      从空快照开始，首轮应以作者观测绑定和出生地缺失绑定通过；出生边保持未提交、未拒绝，只保留作者前缀并问出生地。随后两轮继承本次真实快照和查询；完整图应以观测绑定接通两跳，回答 `Linden City`、无追问。每轮人工核对编号对应的精确边、可达端点、代码派生方向、最终提交图和拒绝集合。无自然干扰边拒绝时仍记录 `not_observed`，由既有预置拒绝测试覆盖过滤，不为制造拒绝追加调用。

      任一轮失败立即停止，不发送相同提示重试。若题型／根／关系错，或编号无效／不相连／已有边假报缺失，定位首阶段相应字段；若合法绑定却推导方向、前缀或缺口错误，才查新绑定解析和路径求值；若派生正确而最终输出错，才查选择阶段。失败后只提出由本轮证据支持的下一项变更并另列预算，不预设再次加提示、换模型或放宽校验。提示或 schema 再改后从链首重建，不能沿用旧修订快照。

   5. **依赖链通过后补齐三例，再交接外层第 $5$ 步**：用同一修订预览并执行 `python scripts/validate_part_d.py --suite multihop --case single.sufficient --case single.insufficient --case single.empty`，确认完整、缺证据、空图均满足新协议及原有结果约束；空图只问作者，不编造中间实体。六例全部通过且人工审阅通过后，才进入外层第 $5$ 步，执行已有 `conditional.silver_full`／`conditional.bronze_full` 两图冒烟一次。共享首阶段 schema 本次再次变化，条件冒烟有必要；不提前执行，不扩大为条件全链或 `all`。

   | 本轮追加预算，按关卡执行 | 正常逻辑调用 | 保守逻辑上限 | HTTP 尝试上限 |
   | --- | --- | --- | --- |
   | 最新失败所在三轮链 | $6$ | $9$ | $27$ |
   | 链通过后的三个独立场景 | $6$ | $9$ | $27$ |
   | 外层第 $5$ 步两张条件完整图 | $4$ | $6$ | $18$ |
   | 全部依次通过时合计 | $16$ | $24$ | $72$ |

   上限按每事务最多 $3$ 次逻辑调用、每次最多额外 $2$ 次传输重试计算；本次未产生新增真实用量，上表是未来实施后的单轮预算，不含已发生的 $1$ 次或更早调用。失败后尚未到达的关卡不执行，下一次修订另计。

   **本轮方案复核：是否绕远路**

   复核结论：保留“观测步骤改为证据绑定 → 原用例同步断言与免费检查 → 最新失败链 → 三个独立场景 → 外层第 $5$ 步两图冒烟”。绑定改动比再补一句提示涉及更多代码，但直接移除了本轮与上一轮均出错、且已有事实可供计算的方向字段；相较单纯换用 head／tail 名称，它还有现成单跳协议可复用和明确端点校验。只给首跳打补丁会留下上一轮第二跳问题，直接固定全出向会破坏合法入向，运行时自动翻转会掩盖无效抽取，均不采用。

   已删除的绕路包括：付费重现已有失败、继续堆方向提醒、增加独立方向判别调用或业务重试、补完整诊断框架、重建 `--case`、改题目／实体／阈值、比较模型、依赖 C/E 集成及先跑完整实验集。三轮重建是实际状态依赖；新契约同时影响完整图、部分图和空图，已有六例须在同一修订下通过，不能拼接旧版本成功记录。条件冒烟只在六例通过后按外层第 $5$ 步做一次，不重复计入另一份计划。

   尚未解决的风险是：LLM 仍可能选错编号、关系或缺失方向，代码也不能证明需求语义完整；本方案不是对模型可靠性的保证。当前仍停在第 $4$ 步，TODO 2.3.5 保持待办；本次免费反事实通过不构成真实验收，未来六例和条件冒烟通过也不等于整个独立交付关卡完成。

   **观测绑定执行后归因：缺失边方向仍依赖语义抽取（待修订与真实复验）**

   2026/11/09：本节专门分析上方“解决方案：复用观测绑定，让代码推导已有边方向”的执行记录，以提交 `165d9b1`、私有报告 `tmp/tmp_part_d_validation_20261109T144637541323Z.json`、对应两条账本记录和当前代码为依据。该绑定方案已实施；上方其实施前的“待实施”及预算不是当前状态。本节是下一轮权威行动顺序，仅完成归因、免费反事实和计划，未实施下述提示修订、未调用 API、未更改私有配置或用量账本。

   | 层次与直接证据 | 归因及行动含义 |
   | --- | --- |
   | **输入与已修复部分**：`multi.partial` 从空快照开始，出生边分数等于阈值 $0.7$，未提交；提交图仍只有两条作者边。原始首回复的第一步为 `written_by + observed([1])`，编号 $1$ 精确连接原题的书与 `Mira Vale`，代码派生 `outgoing`。 | 观测绑定在本轮确实生效，不能说上一方案整体无效，也不能由一次成功推断稳定性。没有旧拒绝干扰本次首轮；不得降低阈值、泄露隐藏出生边或重新实现观测方向。 |
   | **唯一错误字段**：第二步为 `born_in + missing(incoming)`；原题询问作者出生城市，目标关系应为 `(Mira Vale, born_in, ?city)`，因此当前作者位于头端，应为 `outgoing`。两侧均无出生事实，`decode_description` 接受合法枚举，`derive_path_decision` 检查无事实后直接采用声明方向。 | 直接故障在首阶段 `description.steps[1].binding.direction` 的语义，不在作者连接、缺口定位对象或两跳题型。不存在可用于推导该方向的观测端点；“图中没有这条边”只支持缺失判定，不能证明模型声明的方向正确。 |
   | **错误传播**：派生缺口为 `Mira Vale / born_in / incoming`，`missing_relation_queries` 渲染 `Which entity connects to "Mira Vale" through relation "born_in"?`；最终原始回复逐字选用该唯一允许查询，保留作者边且不作答。 | 求值器和最终选择忠实执行了错误需求。相对于抽取计划的一致性校验无法验证原题语义；不要让最终模型另选方向、修改查询渲染或自动翻转来掩盖首阶段错误。 |
   | **拦截位置与快照**：报告为 `controlled_check_failure`，仅 `reachable_query=false`，而 `two_hop_extraction=true`。公开 `manage_memory` 已成功返回候选快照，候选记忆含作者边、拒绝含干扰书边；`memory_round` 的受控断言随后失败，未将候选交给恢复轮。 | 此次不是生产解析或事务校验抛错。旧空快照保持不可变，但生产入口本身没有阻止这个语义错误候选返回；报告的 `state_after` 是未被验收采用的候选，不能写成旧状态或“未生成拒绝”。`check_two_hop` 因已有断言失败跳过 `derived_path`／`derived_directions`，字段未出现不代表派生正确或检查通过；现有精确查询门槛已足以拦截，无需为本次新增诊断框架。 |
   | **原始证据与用量**：此次报告保存了成功返回的两阶段 `messages`、`options`、`completion`，不同于此前只保存失败投影的报告。对应 HTTP 均成功，返回 `gpt-4o-mini-2024-07-18`，合计输入 $2903$、输出 $100$ tokens，无重试；没有排序调用。 | 可免费精确重放，不能再付费获取同一失败或沿用此前“原始回复不可用”的限制。顶层 `unclassified` 是验收器的 `RuntimeError` 记录，不是新的网络或解析故障。恢复轮、探测轮、三个独立场景和条件冒烟均未执行。 |

   **免费因果核验已完成**：私有脚本 `tmp/tmp_step4_missing_attribution.py` 用两条保存的原始 completion 经公开 `manage_memory` 重放，逐项核对实际生成的请求与原 `messages`／`options` 完全一致；入口仍返回上述候选，受控入口仍只因 `reachable_query` 失败。仅把第二步方向改为 `outgoing`，`derive_decision` 即得到同一作者必留前缀、空终跳候选和唯一出向出生地缺口；再给最终阶段提供相应的合成查询，公开事务与当前受控断言全部通过，隐藏出生边仍未提交或拒绝，旧空快照仍不变。两种方向都能在同一无出生边图上通过生产求值，证明现有证据不能区分这项缺失语义。脚本确认账本未变；反事实的最终回复是合成的，不能据此宣称模型已修好。

   **最短解决与排查方案**

   1. **先只修缺失边的语义取向，不再更换已有效的绑定协议**：在 `src/agent/evidence_decision.py` 的 `DECISION_INSTRUCTIONS` 路径段局部改写缺失步骤说明，明确先按原题含义把当前节点和待找节点放入假设关系槽位，再映射方向：`(CURRENT, relation, NEXT)` 为 `outgoing`，`(NEXT, relation, CURRENT)` 为 `incoming`。`CURRENT` 是本跳前沿节点，`NEXT` 是未知目标角色；仅用于解释如何选择现有枚举，不新增输出字段、不写入证据、不编造实体或答案。关系槽位表示检索需求，不是声称该事实已存在。已有边仍只用 `observed(evidence_ids)` 推导方向。

      用同一缺失谓词的两个不同角色作简短对照：已知实验室、找其所在国家，对应 `(CURRENT laboratory, located_in, NEXT country)`，为出向；已知国家、找位于该国的实验室，对应 `(NEXT laboratory, located_in, CURRENT country)`，为入向。英文问句的被动形式、答案在问句中的位置及上一跳方向均不决定本跳方向。生产示例不写死验收书名、作者、城市、编号或 `born_in → outgoing` 规则；当前失败的验收目标仍为作者 `observed([1])` 加出生地 `missing(outgoing)`。空前沿后续步骤只描述未知角色，不能命名未发现的中间实体或提前发查询。

      这是尚未直接覆盖的“缺失谓词两端语义角色”修订，不是再次提醒已有边的头尾定义。当前缺失分支只有“按原题给出方向”的泛化要求，已有示例依赖已给出的事实端点；没有实际缺失边的正反角色对照。先保持 `DESCRIPTION_VARIANTS`、解码、求值、查询渲染和最终校验不变，避免为单个语义错误重做协议。该改动仍依赖 LLM 理解谓词；模型为何在原请求中选入向未知，不能将被动语态或提示长度说成已证实的内部原因。

   2. **复用原始输出和现有反例做免费门槛**：将上述原始错误的最小合成结构及方向单字段反事实纳入 `tests/test_part_d_validation.py` 的现有路径测试，保留错误出向／入向查询的严格区分。错误 `missing(incoming)` 在无出生边图上仍会通过生产一致性检查，但必须被受控 `reachable_query` 拒绝；方向修正后配合明确标注的合成最终回复，应通过同一入口。不能把“旧输出仍能通过生产解码”当成修订失败，也不能改门槛接受两种方向以制造通过。

      复用 `tests/test_evidence_decision.py` 已有合法入向、混合方向、假缺口、多候选缺边及空前沿保护，不为提示措辞另建一套镜像测试；必要时只补尚未覆盖的断言。先运行受影响测试，再运行一次 `python -m unittest discover -s tests -v`，核对观测绑定、前缀保护、隐藏证据不拒绝和旧快照不变。同步中英文模块说明中受影响的语义边界，不把本次计划写成已实现能力。离线结果证明既有边界与验收门槛，不能证明新提示会让模型选对缺失方向。

   3. **同一修订从失败链首真实复验，失败立即停**：先给下列维护命令追加 `--list-cases` 免费核对仅有三轮及预算，再用 GPT-4o-mini/OpenAI 执行一次，不先做首阶段独立探针，也不单跑首轮后再重复首轮来接依赖链。

      ```bash
      python scripts/validate_part_d.py --suite multihop --case multi.partial --case multi.recovery --case multi.exclusion_probe
      ```

      首轮验收目标是作者观测编号正确，代码只派生作者前缀和 `Mira Vale / born_in / outgoing` 缺口，最终无答案、只问出向出生地。由现有逐轮自动门槛在未达标时立即停止，原题、分数和阈值不变；通过后恢复轮和探测轮只继承本轮真实返回的快照与查询，接通完整两跳、回答 `Linden City` 且不追问，核对已拒绝边在两阶段提交图中均被排除。流程结束后逐轮人工审阅原始抽取和代码派生；未审阅通过不进入下一关，不为人工暂停另改入口。若模型未自然拒绝干扰边，保留 `not_observed`，不为制造覆盖追加请求。不得复用上一修订的失败候选。

      分流只看最早错误字段：缺失方向再错，保留原始请求与回复，本轮结束，不继续堆提醒、重试同提示或改下游；此时这项最小语义修订尚未解决问题，下一修订须另据新证据决定。若方向、关系与编号均正确而派生错，才查求值；派生正确而最终答错／追问错，才查最终阶段。不要提前建设关系词典、知识图谱 schema 接入或增加第二个方向判别调用；当前提交图没有该关系端点，仅将 `direction` 改名为头尾角色也不会自动获得独立语义依据。

   4. **链通过才补独立三例，随后进入外层第 $5$ 步一次**：在同一修订下预览并执行 `python scripts/validate_part_d.py --suite multihop --case single.sufficient --case single.insufficient --case single.empty`。完整图用观测绑定走完两跳并只答城市；缺出生地时保留作者并问正确方向；空图只问书的作者，不编造中间实体，缺失关系别名仍服从当前门槛。六例逐项断言及原始抽取人工审阅全部通过后，才按外层第 $5$ 步运行现有 `--suite conditional --case conditional.silver_full --case conditional.bronze_full` 两图冒烟。共用首提示发生变化，因此保留这一次回归，不提前做，也不再重复预算内的条件冒烟或扩大为完整条件链／`all`。

   | 下一轮追加预算，前关通过才继续 | 正常逻辑调用 | 保守逻辑上限 | HTTP 尝试上限 |
   | --- | --- | --- | --- |
   | 从空快照重建失败所在三轮链 | $6$ | $9$ | $27$ |
   | 三个独立场景 | $6$ | $9$ | $27$ |
   | 外层第 $5$ 步两张条件完整图，仅一次 | $4$ | $6$ | $18$ |
   | 全部依次通过时合计 | $16$ | $24$ | $72$ |

   预算按每事务最多 $3$ 次逻辑调用、每次最多额外 $2$ 次传输重试计算。首轮正常只用 $2$ 次；若再次失败，余下关卡均不执行。上表不含已发生的 $2$ 次或更早用量；后续修订与重跑另计，不能把多轮失败累加仍称在同一预算内。本次归因只有本地重放，没有追加真实用量。

   **方案复核：是否绕远路**

   复核后采用“缺失边语义槽位的局部提示修订 → 原始输出与单字段反事实免费回归 → 从空快照重建失败链 → 三个独立场景 → 外层第 $5$ 步一次条件冒烟”。与上一方案相比，已有边方向已由代码接管，不再改 schema 或重做端点绑定；缺失边缺少可观察事实，不能把不可计算的语义选择包装成确定性方向修复。一次局部修订和原维护链足以检验当前假设，暂不引入新的生产字段、关系注册表、独立判别阶段或检索器接口。

   已排除：付费复现已保存输出、修最终复制查询、自动反转／双向发问、固定所有关系出向、添加答案／中间实体、降低阈值、放宽 `reachable_query`、重做诊断及 `--case`、再查已关闭题型与排序问题、比较模型或依赖 C/E 集成。三轮连续运行是恢复与拒绝过滤的真实依赖，独立三例覆盖完整／缺失／空图；只运行首轮后为接链再次付费重建首轮，才是本次可避免的重复。条件冒烟只保留外层既有的一次。

   当前仍未通过第 $4$ 步；局部提示能否改善缺失方向必须由新真实回复证明，离线检查或受控六例通过也不等于开放图上的语义保证。TODO 2.3.5 独立交付关卡保持待办，不提前进入外层第 $5$ 步或交付。

5. **只补受影响的条件回归**：新增路径变体已改变首阶段 schema，后续提示修订仍影响共用首请求，即便旧求值代码不动，也不能只靠离线测试断言模型输出无变化。六场景通过后，以现有条件入口和 `--case` 复用两张 Silver／Bronze 完整图作真实冒烟；正常增加 $4$ 次逻辑调用，保守上限 $6$ 次逻辑调用／$18$ 次 HTTP 尝试。旧三轮链优先重放已保存的有效原始输出以验证求值与状态；schema 已改变时只能称输出重放，不能称请求与 schema 精确一致。只有共享条件逻辑改动或出现回归证据，才扩大到完整条件链，不重跑历史措辞实验或完整 `all`。
6. **失败按阶段就地修复**：路径类型、根实体或关系抽取错误，检查首阶段提示与结构；路径抽取正确而必留／缺口／候选错误，修求值；派生正确而选择、答案或查询错误，修最终提示或发布校验。只复验失败场景及受影响的依赖链；恢复轮依赖部分轮时，从链首重新运行，不伪造前态。采用标准为六场景逐项断言、实际抽取人工审阅及受影响条件回归通过；只记录观察到的结果，不将本修复等同于 TODO 中整个独立交付验收完成。实现时同步中英文模块说明及预算，本次仅提交中文归因和计划。

#### 方案复核：是否绕远路

复核结论：直接实施“显式两跳协议 → 代码连接与前缀保护 → 六场景验收”，不再为已由代码和合成回复确定的协议缺陷增加真实复现批次。终跳校验和用例断言直接阻止本次已证实的错误放行，属于修复必要部分。

已排除的绕路包括：先改问题措辞或书名、换模型比较、重新调阈值、回退 `paper`／`retrieval_questions` 来制造通过、重开已关闭排序排查、依赖 C/E 集成、通用规划器、路径持久化，以及先跑完整开放问题集。新增针对性验收入口仅复用现有六例，用于避开分解和独立隔离用例的额外调用；不另建诊断框架。条件完整图冒烟保留，是因为共用 schema 确实变化，不是重新排查已解决的条件题。

剩余风险明确保留：LLM 仍可能漏抽路径、抽错关系或方向，缺失关系的命名也不保证实际检索召回；代码连接只能保证相对于抽取计划和提交图的一致性。先把原有受控两跳能力修复并验收，不以解决所有自然语言语义或跨模型一致性作为前置条件。
