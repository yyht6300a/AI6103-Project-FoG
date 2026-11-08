# 成员 D 模块

[English](part_d.md) | [简体中文](part_d.zh-CN.md)

本文介绍成员 D 已实现的模块及其接口。配置和测试命令见 [README](../README.zn-CN.md#llm-配置)。潜力子图过滤、问题级记忆快照及 LLM 引导的记忆替换已实现；新增拒绝状态记录和检索排除仍待实现。

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

下述更新编排器先完整校验 LLM 结果，再构造新快照，并继承已有永久拒绝集合。保留旧快照可支持失败时维持原状态。此容器仅保存显式状态，不执行证据选择、拒绝判定、LLM 调用或检索过滤。阈值过滤不会填充任何集合。检索排除仍属于后续任务。

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

图或输出无效时抛出 `ValueError`，错误消息不含原始内容。缺少、多余或重复字段、额外文字、代码围栏、尾随值、非标准 JSON 数值、数组元素格式错误，以及越界三元组或实体均被拒绝，不自动修复。拒绝重复键和限制精确字段集合属于避免歧义的解析选择。解析器不调用 API、不增加重试，也不改写记忆或永久拒绝状态。下述编排器负责响应封装校验和状态更新；检索排除仍待实现。

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

潜在输入沿用提示词的图格式，支持空图和生成器。编排器先物化一次，确保构造提示词和解析回复使用同一份证据。状态或图格式无效、潜在三元组与已有永久拒绝集合重叠时，在请求前抛出 `ValueError`；调用方须提供合格证据。本项尚不新增拒绝判定或检索过滤。该策略接通前，未选中三元组仅移出记忆，不加入拒绝集合。

仅正常完成（`finish_reason: stop`）、未拒绝且三个输出字段全部有效的回复才生成新快照。无效响应封装、非法 JSON、越界选择或答案（包括仅存在于被省略输入中的答案）抛出 `ValueError`；传输和用量记录错误向上传递。所有路径均不修改输入快照。调用方负责客户端生命周期，并仅在成功后保存返回状态。传输重试和逐次用量记录仍由 `LLMClient` 负责；输出校验不增加重试或回退。试探性答案校验及语义边界见上文。

`tests/test_memory_update.py` 覆盖受控多轮保留、移除及清空记忆、生成器与原字符串、有序回复、已有拒绝状态保持、无效输入拦截、完整输出校验、拒绝及截断回复、失败时状态保持、问题隔离，以及模拟 HTTP 重试和用量记录集成。所有检查均离线运行。

## 后续子问题

TODO 2.1.9 对应 FoG 第 3.3 节公式 11 和附录 C：图 8 要求 LLM 在选中证据无法确定答案时生成 $1$ 至 $3$ 个澄清问题。`update_memory` 通过 `response.subsequent_question` 返回这些有序字符串元组，表示下一轮检索的 $Q'_{\mathrm{sub}}$。调用方应将其传给检索模块，无需重新执行初始问题分解；后续记忆管理仍使用 `state.original_question` 中的原问题。检索器和推理循环接线留待集成。

解析器将空 `predict_answer` 视为模型声明答案未确定，此时必须返回 $1$ 至 $3$ 个问题，输入图或选中图为空时也适用。有试探性答案时允许 $0$ 至 $3$ 个问题，因为部分答案仍可能需要补充证据，不强制两字段互斥。这是显式校验约定，不是语义充分性检验。缺失证据和问题措辞由 LLM 判断；代码不证明相关性、不按图大小推断充分性、不生成替代问题，也不额外调用模型。

子问题保留原字符串、空白、Unicode、顺序及重复项。它们是自然语言检索问题，无须完全匹配输入实体串或使用特定结尾标点。空白或非字符串项、超过 $3$ 个问题、无答案且无问题时，均在发布新快照前抛出 `ValueError`，保留旧记忆和永久拒绝状态，不增加业务重试。测试覆盖有无答案时的数量边界、原字符串保持、无效输出的状态保持，以及通过模拟检索传递子问题并在第二轮获得答案的受控流程。离线检查不代表已验证真实模型的问题质量。
