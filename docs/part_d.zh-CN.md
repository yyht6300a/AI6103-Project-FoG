# 成员 D 模块

[English](part_d.md) | [简体中文](part_d.zh-CN.md)

本文说明当前已实现的成员 D 模块。默认流程为需求抽取、代码求值、证据选择与作答、发布前校验，以及多答案独立排序。公开入口是 `manage_memory`，默认使用 `prompt_variant="evidence_decision"`。配置与环境准备见 [README](../README.zn-CN.md#llm-配置)。成员 D 使用受控检索和打分输入配合真实 LLM 验收模块；成员 E 负责 C/E 流水线集成、实际检索和整体运行效果，集成不作为成员 D 模块验收的前置条件。

## 当前默认架构

```mermaid
flowchart TD
    A[原问题、旧快照、检索三元组和对齐分数] --> B[校验输入、严格阈值过滤、排除已有记忆和永久拒绝]
    B --> C[合并历史记忆与合格新证据]
    C --> D[LLM 按实体分组抽取条件与事实需求]
    D --> E[代码校验事实端点、求值条件或两跳路径、匹配完整图]
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

首阶段使用严格结构化输出，`description.kind` 为 `conditional_equals`、`nonconditional`、`nonconditional_path` 或 `unsupported`。题型由原问题决定：只有原题显式规定相等条件的真假分支时才使用 `conditional_equals`；先定位实体再询问其属性属于路径依赖，即使图中已知中间实体，也使用 `nonconditional_path`。相等条件描述主体、关系、运算符 `eq`、比较值、原问题中的条件引文及可选单值前提引文。比较值只作为条件约束，不作为已观察事实；不能从图中制造条件，也不能仅凭定冠词推断单值。

单跳需求的每个分支按 `requirements: [{entity, fact_queries: [{relation, binding}]}]` 组织。`entity` 是原题询问其属性或关系的对象，不是检索到的答案端点；每个查询从所属组继承对象，不重复生成另一份对象字段。条件题提供 `true` 和 `false` 两分支，非条件单跳题提供单一 `branch`。

`observed` 绑定引用本轮完整排序图的稳定三元组编号。代码校验编号、精确关系和目标端点，从端点位置派生出向或入向；多个引用须支持相同方向，自环或角色歧义终止事务。确定方向后仍扫描完整图，收集同一对象、关系和方向的全部匹配事实，避免只保留模型引用的一条。

`missing` 绑定声明待检索方向，不携带预测答案。代码检查两个方向的精确事实：声明方向已有事实时拒绝伪缺口，仅另一方向命中时保守判定冲突；两侧均无命中才允许缺口。此规则可能拒绝合法的另一方向查询，不代表通用关系方向推理。编号只在当前请求内有效，不能跨轮复用。

### 非条件两跳路径求值

`nonconditional_path` 使用逐字来自原题的 `root_entity` 和恰好两项的有序 `steps`，每步为 `relation + binding`。关系已在图中出现时必须精确匹配；缺失关系由模型描述，不填写中间实体或预测答案。路径绑定使用独立 schema，不改变单跳和条件分支的绑定契约。

`observed` 绑定仅引用当前完整排序图的 `evidence_ids`，代码依据当前可达前沿的端点角色推导该步方向，再遍历同方向的全部精确匹配事实，将另一端作为下一步前沿。编号越界、关系或连接不符、自环、端点角色歧义，以及声明方向无事实但反向命中，均终止事务。同一步部分候选已有事实、部分缺失时，缺失候选继续使用观测事实推导的方向。

完全缺边时绑定只能为 `{"kind": "missing"}`，不携带方向或证据编号；只有当前前沿所有实体的两侧均无该精确关系，才生成 `scope="incident"` 的关系邻接缺口。已有事实却报缺失时失败。前沿为空后仅保留下游需求，不编造中间实体，也不生成尚不可达对象的查询。后续事实到达后重新使用观测绑定推导方向。旧路径 `missing + direction` 和 `steps[].direction` 均被严格拒绝。

代码保护全部可达路径前缀，任一候选仍有缺口时禁止作答；无缺口时答案必须来自终跳 `answer_candidates`，不能回答中间实体或不相连的城市。路径求值复用完整图、必留证据、拒绝及排序发布规则，不持久化跨轮证据编号或另建路径状态。

### 条件求值与必留集合

代码通过完整提交图中的精确主体和关系查找实际条件值。匹配比较值时为 `true`；存在不同值且原题明确单值时为 `false`；否则为 `unknown`。单值前提下出现多个实际值会失败；非条件题状态为 `not_conditional`。缺少事实不自动视为条件不成立。

必留集合由实际条件事实与适用分支的全部匹配事实构成。条件未知时合并两分支，保护已有候选路径；条件确定后只使用对应分支。代码同时派生回答对象、逐项证据匹配及缺失关系，交给最终选择阶段，不依赖模型重新列举必留证据。

### 缺口查询与发布门槛

[`memory_prompt.py`](../src/agent/memory_prompt.py) 的 `build_decision_memory_prompt` 向最终阶段提交原问题、完整证据及派生决策。有缺口时，`missing_relation_queries` 按对象、关系和方向稳定去重，安全引用原字符串，生成以下固定查询：

```text
Which entity does "<target>" connect to through relation "<relation>"?
Which entity connects to "<target>" through relation "<relation>"?
Find triples involving "<target>" with relation "<relation>".
```

前两种模板用于已有方向的缺口；第三种仅用于两跳路径完全缺边时的 `scope="incident"` 缺口。邻接请求允许召回目标实体作为头端或尾端的关系事实，明确扩大了检索范围；不先猜方向、不自动翻转旧查询，也不同时发送两条正反查询。例如仅知书的作者为 `Mira Vale` 时，查询为 `Find triples involving "Mira Vale" with relation "born_in".`；空图只查书的作者关系。关系名称与原字符串用 JSON 字符串规则安全引用。该契约不保证缺失关系命名正确，实际召回和开销由成员 E 集成验证。

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

`multihop` 仅使用默认 `evidence_decision`，复用 `single.sufficient`、`single.insufficient`、`single.empty`、`multi.partial`、`multi.recovery`、`multi.exclusion_probe` 的事实及实际返回快照，不运行分解或独立隔离用例。正常为 $12$ 次逻辑调用；上表保守计入可选排序和额外两次传输重试。共用 `check_two_hop` 检查完整两跳抽取、必留路径、精确答案／空答案、具体允许查询、阈值隐藏事实、恢复及两阶段实际提交图；同一检查也用于默认 `single`、`multi` 和 `all` 中的六例，显式旧提示对照保留原行为。缺失关系仅接受脚本中列出的有限语义别名；图中已有关系必须精确匹配。不要求选择或拒绝无关干扰边；`distractor_rejection` 和 `multihop_observations` 单独记录自然拒绝及过滤是否已观察到，未拒绝时明确为 `not_observed`。任何必需断言失败立即停止、退出码为 $1$，不继承失败轮候选快照；成功事务保留原始抽取供人工审阅。该组六个场景已通过 GPT-4o-mini/OpenAI 受控真实验收，结果见下方验收状态。

`conditional` 不加入 `all`，对 Silver／Bronze 各执行一张独立完整图和一条从空快照开始的三轮链。首轮只给两条出生地，次轮真实继承快照且仅补出版社，末轮以 $0.95$ 重输全部事实。逐轮检查完整记忆、答案、每条查询、永久拒绝及提交前过滤；失败即停。自动检查仍需人工审阅原始需求抽取、方向、条件语义及证据支持。

显式 `--prompt-variant paper` 或 `--prompt-variant retrieval_questions` 可运行对照；对应 `all` 仍为最多 $25$ 次逻辑调用、$75$ 次 HTTP 尝试，不自动回退。`reference_diagnostic`、`clarification_diagnostic` 和 `empty_diagnostic` 为独立诊断组，可免费预览各自预算，均不加入 `all`。

每次运行保存唯一的私有 `tmp/tmp_part_d_validation_<UTC timestamp>.json`，包含受控输入、实际查询、有效输出、成功事务的分阶段请求与 schema、原始完成回复及前后快照。失败只保存异常类及固定诊断，不保存失败原文、密钥或任意异常消息。实际 HTTP 用量继续写入共享账本；报告不提交仓库。退出码 $0$ 表示结构、状态及该组自动检查通过，`semantic_review` 仍待人工审阅。

## 当前验收状态与剩余事项

2026/11/09，当前默认架构已完成采用。GPT-4o 的两张完整条件图和两条三轮链已通过受控验收，覆盖未知时保留候选路径、条件确定后的正确答案、只移除无关旧证据和高分重输后的永久过滤。两条链共 $6$ 次事务、$12$ 次 HTTP 尝试，无失败、重试或排序请求，实际模型均为 `gpt-4o-2024-08-06`。这些结果支持关闭本轮条件证据保留、作答与旧记忆移除排查。

当前两跳协议及验收入口已通过全部 $136$ 项离线回归。GPT-4o-mini/OpenAI 的六个受控两跳场景均通过自动门槛及原始抽取人工审阅：完整图只答终跳城市，部分图保留作者并只查出生关系，空图只查书的作者；三轮链真实继承快照及查询，阈值隐藏事实未提交且未拒绝，恢复后接通完整路径，自然拒绝及后续两轮过滤均已观察到。私有报告为 `tmp/tmp_part_d_validation_20261109T152528273320Z.json` 和 `tmp/tmp_part_d_validation_20261109T152612446031Z.json`。

同一修订下，Silver／Bronze 两张条件完整图也通过自动检查及原始抽取人工审阅，分别只答 `Linden City`／`Cedar City`，无追问。私有报告为 `tmp/tmp_part_d_validation_20261109T152640586851Z.json`。上述八次事务共 $16$ 次成功 HTTP／逻辑调用，输入 $22376$、输出 $967$ tokens，无重试或排序；实际用量保留在共享账本。本次文档清理不新增 API 请求，不修改实现、私有配置或账本。

当前支持非条件单跳需求、单关系相等条件，以及上文的[非条件两跳线性路径](#非条件两跳路径求值)。其他路径长度、条件内多跳、聚合和复杂需求仍为 `unsupported`。本地检查只能保证相对于抽取需求与提交图的一致性，不能证明原题理解、需求完整性或缺失关系命名正确；关系邻接查询的实际召回和开销由成员 E 集成验证。

TODO 2.3.5 的独立 GPT-4o-mini/OpenAI 模块验收保持待办，不能由 GPT-4o 结果替代；两跳专项验收和条件完整图冒烟不能替代该关卡要求的完整独立验收。TODO 2.3.6 的历史用量核对和 2.4 最终交付也尚未完成。实际 C/E 接线与召回由成员 E 验证，不作为成员 D 模块验收前置条件。已删除的排查过程保留在 Git 历史和私有原始报告中，本文只维护当前架构、已确认结果及剩余事项。
