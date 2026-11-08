# 成员 D 模块

[English](part_d.md) | [简体中文](part_d.zh-CN.md)

本文介绍成员 D 已实现的模块及其接口。配置和测试命令见 [README](../README.zn-CN.md#llm-配置)。潜力子图过滤、问题级记忆快照、LLM 引导的记忆替换、永久拒绝记录及展开排除已实现；检索器和推理循环接线留待集成。

## LLM 配置与请求

[`llm_config.py`](../src/agent/llm_config.py) 将私有 `.env` 中的字面值与共享 YAML 默认值合并，并校验最终配置。[`llm_client.py`](../src/agent/llm_client.py) 提供 `LLMClient.complete(messages)`，支持兼容 OpenAI 的 Chat Completions 接口。请求显式接收配置，不继承进程配置。调用方负责管理和关闭客户端；超时、连接失败、HTTP $429$ 和 HTTP $5xx$ 按客户端的有限重试策略处理。

## 逐次请求用量记录

[`llm_usage.py`](../src/agent/llm_usage.py) 为每次实际 HTTP 尝试追加一行 UTF-8 CSV 记录，包括重试和失败。元数据包含请求开始时间、模型、返回的 token 数、请求结果、异常类名和请求 ID。未知值留空，不记录凭据及请求、响应内容。后续应用层校验失败不会修改已成功的 HTTP 用量记录。配置和用量记录测试见 `tests/test_llm_configuration.py` 和 `tests/test_llm_usage.py`。

## 初始问题分解

[`question_decomposition.py`](../src/agent/question_decomposition.py) 实现 FoG 第 3.1 节和附录 C 图 7（论文第 21 页）中的初始 $Q_{\mathrm{sub}}$。提示词保留图中的措辞及从 `2` 开始的原始编号：生成可独立理解、共同解答原问题的子问题，不假定事实缺失，仅在必要时澄清。

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

示例中的检索和打分函数表示调用方依赖，并非已实现函数。调用入口会产生 LLM 用量，空输入或空潜在图也会请求。入口仅调用一次现有记忆更新编排器，传输重试和用量记录仍由 `LLMClient` 负责。

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

## 后续子问题

TODO 2.1.9 对应 FoG 第 3.3 节公式 11 和附录 C：图 8 要求 LLM 在选中证据无法确定答案时生成 $1$ 至 $3$ 个澄清问题。`update_memory` 通过 `response.subsequent_question` 返回这些有序字符串元组，表示下一轮检索的 $Q'_{\mathrm{sub}}$。调用方应将其传给检索模块，无需重新执行初始问题分解；后续记忆管理仍使用 `state.original_question` 中的原问题。检索器和推理循环接线留待集成。

解析器将空 `predict_answer` 视为模型声明答案未确定，此时必须返回 $1$ 至 $3$ 个问题，输入图或选中图为空时也适用。有试探性答案时允许 $0$ 至 $3$ 个问题，因为部分答案仍可能需要补充证据，不强制两字段互斥。这是显式校验约定，不是语义充分性检验。缺失证据和问题措辞由 LLM 判断；代码不证明相关性、不按图大小推断充分性、不生成替代问题，也不额外调用模型。

子问题保留原字符串、空白、Unicode、顺序及重复项。它们是自然语言检索问题，无须完全匹配输入实体串或使用特定结尾标点。空白或非字符串项、超过 $3$ 个问题、无答案且无问题时，均在发布新快照前抛出 `ValueError`，保留旧记忆和永久拒绝状态，不增加业务重试。测试覆盖有无答案时的数量边界、原字符串保持、无效输出的状态保持，以及通过模拟检索传递子问题并在第二轮获得答案的受控流程。离线检查不代表已验证真实模型的问题质量。
