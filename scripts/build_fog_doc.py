# -*- coding: utf-8 -*-
"""Build the FoG paper analysis + reproduction plan DOCX."""
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

LATIN = "Calibri"
EAST = "Microsoft YaHei"
BLACK = RGBColor(0, 0, 0)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
ACCENT = "1F4E79"   # dark blue header fill
ALT = "EEF3F8"      # pale blue alternate row
BORDER = "D9D9D9"   # light gray border


def _apply_font(font_obj, rpr, size, bold, color):
    font_obj.name = LATIN
    if size is not None:
        font_obj.size = Pt(size)
    if bold is not None:
        font_obj.bold = bold
    if color is not None:
        font_obj.color.rgb = color
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), LATIN)
    rfonts.set(qn("w:hAnsi"), LATIN)
    rfonts.set(qn("w:eastAsia"), EAST)


def style_run(run, size=10.5, bold=False, color=BLACK):
    _apply_font(run.font, run._element.get_or_add_rPr(), size, bold, color)


def configure_styles(doc):
    normal = doc.styles["Normal"]
    _apply_font(normal.font, normal._element.get_or_add_rPr(), 10.5, False, BLACK)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.35

    title = doc.styles["Title"]
    _apply_font(title.font, title._element.get_or_add_rPr(), 22, True, BLACK)
    title.paragraph_format.space_after = Pt(8)

    for lvl, size in ((1, 15), (2, 12.5), (3, 11)):
        st = doc.styles["Heading %d" % lvl]
        _apply_font(st.font, st._element.get_or_add_rPr(), size, True, BLACK)
        st.paragraph_format.space_before = Pt(12 if lvl == 1 else 9)
        st.paragraph_format.space_after = Pt(5 if lvl == 1 else 4)
        st.paragraph_format.keep_with_next = True

    lb = doc.styles["List Bullet"]
    _apply_font(lb.font, lb._element.get_or_add_rPr(), 10.5, False, BLACK)


def add_para(doc, text, size=10.5, space_after=6, line=1.35, align=None, indent=None):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_after = Pt(space_after)
    pf.line_spacing = line
    if align is not None:
        p.alignment = align
    if indent is not None:
        pf.left_indent = Pt(indent)
    for i, seg in enumerate(text.split("**")):
        if seg == "":
            continue
        style_run(p.add_run(seg), size=size, bold=(i % 2 == 1))
    return p


def add_bullet(doc, text, size=10.5):
    return add_para(doc, "•  " + text, size=size, indent=14, space_after=4)


def add_heading(doc, text, level):
    p = doc.add_heading("", level=level)
    style_run(p.add_run(text), size=None, bold=True)
    # remove the default heading color/run so our run-level style controls it
    return p


def set_table_borders(table):
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement("w:" + edge)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), BORDER)
        borders.append(el)
    tblPr.append(borders)


def set_cell_margins(table, top=70, bottom=70, left=100, right=100):
    tblPr = table._tbl.tblPr
    mar = OxmlElement("w:tblCellMar")
    for tag, val in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        el = OxmlElement("w:" + tag)
        el.set(qn("w:w"), str(val))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tblPr.append(mar)


def set_cell_shading(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)


def cell_text(cell, text, size=9.5, bold=False, color=BLACK, align="left"):
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p = cell.paragraphs[0]
    p.text = ""
    pf = p.paragraph_format
    pf.space_before = Pt(2)
    pf.space_after = Pt(2)
    pf.line_spacing = 1.15
    if align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == "left":
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    style_run(p.add_run(text), size=size, bold=bold, color=color)


def make_table(doc, headers, rows, widths, aligns, size=9.5):
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    set_table_borders(t)
    set_cell_margins(t)
    hdr = t.rows[0].cells
    for j, h in enumerate(headers):
        cell_text(hdr[j], h, size=size, bold=True, color=WHITE, align="center")
        set_cell_shading(hdr[j], ACCENT)
    for i, row in enumerate(rows):
        cells = t.add_row().cells
        fill = ALT if i % 2 == 1 else None
        for j, val in enumerate(row):
            cell_text(cells[j], str(val), size=size, bold=False, color=BLACK, align=aligns[j])
            if fill:
                set_cell_shading(cells[j], fill)
    for j, w in enumerate(widths):
        for row in t.rows:
            row.cells[j].width = Cm(w)
    return t


doc = Document()
configure_styles(doc)
sec = doc.sections[0]
sec.top_margin = Cm(2.0)
sec.bottom_margin = Cm(2.0)
sec.left_margin = Cm(2.2)
sec.right_margin = Cm(2.2)

# ---------------- Title ----------------
doc.add_heading("Foresight over Graph 论文解析与复现计划", 0)
add_para(
    doc,
    "本文档系统解析论文《Foresight-over-Graph: Reasoning Beyond Local Horizons for "
    "Knowledge Base Question Answering》（简称 FoG，NeurIPS 2026，天津大学与北京交通大学），"
    "并在代码尚未开源的前提下给出分阶段复现计划。文档面向准备复现 FoG、或在其基础上做阈值优化等改进的研究与工程人员。"
    "第一部分梳理论文要解决的问题、方法、各模块效果与局限，并解释关键术语；第二部分给出复现路线、里程碑与验收标准，"
    "对所有需要自行拍板的点统一标注为待决策点。",
    size=10.5,
)

# ---------------- 1 论文概览 ----------------
add_heading(doc, "1 论文概览", 1)
add_para(
    doc,
    "FoG 针对的是 LLM 引导的图谱推理中逐跳贪心或束搜索剪枝的短视问题。主流 KGQA 方法为了让 LLM 在大规模知识图谱上可计算，"
    "会在每一步按问题与候选关系的局部相关性保留 top-k 或 beam 宽度的分支；但早期阶段的局部相关性无法预见某条分支最终是否通向答案，"
    "answer-critical 边容易被提前剪掉且难以恢复。FoG 用两个核心机制对抗这种短视：一是远到近的前瞻反馈传播，"
    "把远处证据的相关性信号回传给近源候选并重新打分；二是持久化的记忆子图，跨轮次累积高置信证据并允许回头恢复。",
)
add_para(
    doc,
    "在 WebQSP 与 CWQ 两个 Freebase 基准上，FoG 以 GPT-4o 取得 Hit 91.63 / F1 81.28（WebQSP）与 Hit 87.03 / F1 71.34（CWQ），"
    "对最强 baseline 的 Hit 相对提升在 CWQ 上约 16.6%、WebQSP 上约 1.0%；同时把每问的 LLM 调用次数从 ToG 的 15.9 / 22.6 次"
    "降到 2.3 / 2.8 次。增益集中在分支因子更大的 CWQ 上，这与其动机一致。",
)

# ---------------- 2 名词解释 ----------------
add_heading(doc, "2 名词解释", 1)
add_para(doc, "下表按出现顺序解释本文档涉及的术语。", size=10.5)

glossary = [
    ("知识库问答（KBQA）", "给定自然语言问题与结构化知识库，检索并推理出答案的任务。"),
    ("知识图谱（KG）", "以实体为节点、关系为边的多关系有向图，用三元组表示事实。"),
    ("三元组（triple）", "KG 的基本事实单元，形式为（头实体，关系，尾实体）。"),
    ("实体 / 关系", "KG 的节点与边类型；Freebase 中实体以 mid 标识，关系为可读字符串。"),
    ("大语言模型（LLM）", "基于 Transformer 的生成式模型，本文用于子问题分解、记忆更新与作答。"),
    ("检索增强生成（RAG）", "先检索外部证据再交给 LLM 生成答案，以降低幻觉。"),
    ("逐跳推理", "沿 KG 边逐步扩展搜索空间的多跳推理方式。"),
    ("贪心 / 束搜索", "每步只保留当前最优的 1 个 / k 个候选分支的剪枝策略。"),
    ("top-k 剪枝", "按相关性分数每步只保留前 k 条候选关系，是短视剪枝的直接来源。"),
    ("源实体", "从问题中识别出的、作为检索起点的实体。"),
    ("证据子图", "从 KG 中检索出的、支持答案的紧凑子图。"),
    ("黄金推理路径", "数据集标注中通向正确答案的推理路径，用于训练监督。"),
    ("前瞻 / 远见（foresight）", "在早期决策中利用远处证据信号判断某条分支最终是否有用。"),
    ("远到近反馈", "把外层 frontier 的信号沿向内方向回传给近源节点的消息传递。"),
    ("记忆子图", "跨迭代保留高置信证据的持久子图。"),
    ("子问题分解", "LLM 把原问题拆成若干子目标以引导检索。"),
    ("嵌入编码器", "把文本或 KG 符号映射为向量的模型，本文用 Qwen3-Embedding-4B（冻结）。"),
    ("Freebase", "Google 构建的大规模知识库，本文的底层 KG。"),
    ("WebQSP / CWQ", "两个基于 Freebase 的多跳 KBQA 基准数据集。"),
    ("Virtuoso / SPARQL", "承载 Freebase 的图数据库与查询语言。"),
    ("mid", "Freebase 实体标识符，例如 m.0h89dsf。"),
    ("Hit / Hit@1 / F1", "命中率、首位正确率、精确率与召回率的调和均值。"),
    ("阈值 τe / τp", "展开阶段与 potential 阶段的分数截断常数，本文为 0.1 与 0.7。"),
    ("多跳误差累积", "早期选择错误随推理深度累积，最终导致整体失败。"),
]
make_table(
    doc,
    ["术语", "解释"],
    glossary,
    widths=[4.2, 11.4],
    aligns=["left", "left"],
)

# ---------------- 3 论文深度解析 ----------------
add_heading(doc, "3 论文深度解析", 1)

add_heading(doc, "3.1 要解决的问题", 2)
add_para(
    doc,
    "KGQA 要求 LLM 不依赖参数记忆、而是基于结构化证据作答。问题在于 KG 规模大且节点度数高，逐跳展开会导致候选爆炸，"
    "因此现有 LLM 引导的图智能体普遍采用逐跳贪心或束搜索剪枝。这类局部决策是短视的：在源头附近，很多关系在局部相关性上几乎无法区分，"
    "真正通往答案的边可能暂时不突出，于是被提前剪掉；一旦关键边被剪，后续探索漂移进无关邻域，证据链难以恢复。"
    "论文用图 1 的示例说明该失败模式，并在附录 D 给出真实案例。",
)

add_heading(doc, "3.2 方法总览与数据流", 2)
add_para(
    doc,
    "一次推理是一个迭代循环：从问题抽取源实体；LLM 把原问题分解为子问题集合；从源实体出发做最多 L=3 跳的分层 BFS 展开，"
    "用前瞻评分器对 frontier 边打分并按 τe 剪枝；在展开子图上做远到近的反馈传播后对全部边重新打分；按 τp 选出 potential 子图交给 LLM；"
    "LLM 更新记忆子图、给出试探性答案并生成新的子问题以驱动下一轮。循环直到可以作答。",
)

add_heading(doc, "3.3 三个模块与训练信号", 2)
add_para(
    doc,
    "**前瞻子图展开（FSE）**负责入口处的候选供给。它用冻结的 Qwen3-Embedding-4B 编码 KG 符号与问题，"
    "对每个三元组加可学习角色偏置后，先经 self-attention 建模三元组内部交互，再经 cross-attention 注入问题语义得到问题引导表示 z_t；"
    "前瞻评分器把 z_t 与结构上下文拼接，并对子问题集合做注意力池化后输出分数 s_t。展开时保留 s_t 大于 τe=0.1 的边，得到高召回低噪声的展开子图。",
)
add_para(
    doc,
    "**前瞻反馈传播（FFP）**是核心机制。它把展开子图按到源实体的最短距离定向成向内指向的图，从最外层开始按距离递减逐层更新节点，"
    "把外层实体的隐藏态与三元组表示融合后作为消息传回近源节点；传播完成后用同一个评分器对所有边重新打分。"
    "其作用是让近源、原本模糊的边借用远处证据的相关性信号，从而修正早期剪枝。",
)
add_para(
    doc,
    "**记忆子图管理（MSM）**负责证据累积与容错。重打分后按 τp=0.7 选出高置信的 potential 子图，仅把这一小部分交给 LLM 以压缩上下文；"
    "LLM 在结构化 prompt 下同时输出更新后的记忆子图、试探性答案和新的子问题。记忆子图跨轮次持久化，已吸收的边不再重复展开，"
    "被 LLM 明确拒绝的边永久屏蔽，暂时未入选的边在后续子问题指回该邻域时可以重新出现。",
)
add_para(
    doc,
    "**训练信号**贯穿 FSE 与 FFP 的评分器：以黄金推理路径上的三元组为正样本，以正样本实体附近按 κ=10 采样的一跳噪声邻边为负样本，"
    "用二元交叉熵训练评分器，嵌入编码器全程冻结。推理时不依赖黄金路径，只按学到的分数检索。",
)

add_heading(doc, "3.4 各模块效果对比", 2)
add_para(
    doc,
    "下表为论文 Table 2 的消融结果（GPT-4o）。去掉 FFP 在 WebQSP 上降幅最大；去掉 MSM 在 CWQ 上对 Hit@1 与 F1 的伤害最大。"
    "这暗示 FFP 负责把正确路径留在搜索空间里（保住 Hit），MSM 负责把证据攒够并精确定位答案（保住 Hit@1 与 F1），FSE 是相对次要的底座。",
)
make_table(
    doc,
    ["配置", "WebQSP Hit", "WebQSP F1", "CWQ Hit", "CWQ Hit@1", "CWQ F1"],
    [
        ("FoG（完整）", "91.63", "81.28", "87.03", "80.21", "71.34"),
        ("去掉 FSE", "90.42", "77.41", "85.22", "73.95", "62.98"),
        ("去掉 FFP", "89.27", "73.85", "78.61", "68.43", "57.34"),
        ("去掉 MSM", "90.19", "77.19", "79.73", "67.12", "56.15"),
    ],
    widths=[3.6, 2.6, 2.6, 2.4, 2.4, 2.0],
    aligns=["left", "center", "center", "center", "center", "center"],
)
add_para(
    doc,
    "论文还做了两组关键对比：把 FFP 换成同一子图上的无向 GNN 后，WebQSP Hit 从 91.63 降到 89.92、CWQ 从 87.03 降到 84.06，"
    "说明收益来自有方向的远到近传播而非泛泛的消息传递；换用不同强度的子问题分解 LLM 时性能整体稳定，说明收益主要来自图侧机制本身。",
)

add_heading(doc, "3.5 主要结果与效率", 2)
add_para(
    doc,
    "FoG 在无需任务专用微调的情况下超过多数需要微调的 baseline。效率上，FoG 每问约用 R+1 次 LLM 调用，与 beam 宽度 K 解耦："
    "WebQSP 上 2.3 次、CWQ 上 2.8 次，而 ToG 为 15.9 / 22.6 次；总 token 也显著下降。代价是图侧多了展开、打分与反馈传播的计算，"
    "但这些操作轻量且可并行。",
)

add_heading(doc, "3.6 局限性与改进空间", 2)
for t in (
    "训练信号依赖黄金推理路径，scorer 需逐数据集有监督训练，削弱泛化与迁移能力。",
    "τe=0.1 与 τp=0.7 为全局固定常数，不随问题、实体、深度变化；论文 Figure 5 已显示阈值敏感，单轮完整取回证据集在 CWQ 上掉得快。",
    "远见有界：反馈只在 L=3 跳的已展开子图内传播，且展开阶段先用 τe 剪一刀，关键边可能在反馈生效前就被删掉。",
    "评分器对边独立打分，不建模多边之间的联合一致性，与多跳误差累积、约束组合失败两类错误直接相关。",
    "MSM 强制答案为输入三元组中原样出现的实体串，对计数、布尔、日期、superlative 类问题不友好。",
    "仅 3 次运行、baseline 大多无误差棒，未做显著性检验；WebQSP 上增益仅约 1.0%，强结论主要由 CWQ 单点支撑。",
    "只在 WebQSP / CWQ（Freebase）上验证，未覆盖垂域 KG 与不完备 KG 场景。",
):
    add_bullet(doc, t)

# ---------------- 4 复现计划 ----------------
add_heading(doc, "4 复现计划", 1)
add_para(
    doc,
    "以下计划基于公开仓库尚未放出源码这一前提（公开仓库仅含 README 与架构图，真实代码在作者私有的 FoG-private 仓库）。"
    "计划覆盖两部分：先复现论文架构，再在此框架内开展阈值优化实验，两者共用同一套评分器与检索管线。",
)

add_heading(doc, "4.1 复现目标分级", 2)
add_para(
    doc,
    "**最小复现（MVR，推荐先做）**：在 Freebase 子集上复现 scorer、FSE 与 FFP，用检索层指标验证远到近反馈能保住关键边。"
    "**端到端复现**：接入 LLM 的 MSM 循环，在 WebQSP / CWQ 上跑出 Hit / Hit@1 / F1。"
    "**论文级复现**：全量 Freebase + Virtuoso + GPT-4o，对齐 Table 1 的绝对数字。建议先完成 MVR 再逐步外扩。",
)

add_heading(doc, "4.2 分阶段路线", 2)

phases = [
    (
        "Phase 0 环境与依赖",
        "Python 3.8 环境，torch、transformers、datasets、numpy、openai SDK、SPARQL 客户端。README 的 requirement.txt 未开源，按模块实际需要安装。",
    ),
    (
        "Phase 1 数据准备",
        "准备 WebQSP / CWQ 官方 split、Freebase KG（全量或子集）、KG 符号到文本的映射、黄金推理路径。"
        "实体以 mid 标识，需要映射为英文名后才能送入嵌入模型。**待决策点 1、2、3**。",
    ),
    (
        "Phase 2 嵌入层",
        "加载并冻结 Qwen3-Embedding-4B，把实体名、关系串、原始问题编码为向量并缓存到磁盘，避免重复编码。",
    ),
    (
        "Phase 3 问题引导的三元组编码器与评分器",
        "严格按论文实现角色偏置、self-attention、cross-attention、子问题注意力池化与 MLP 评分器。这是可训练的核心。",
    ),
    (
        "Phase 4 前瞻子图展开（FSE）",
        "从源实体出发做 L=3 跳分层 BFS，每层对 frontier 边批量打分，保留 s_t 大于 τe 的边。打分要批量化，避免逐条过模型。",
    ),
    (
        "Phase 5 前瞻反馈传播（FFP）",
        "把展开子图定向为向内图，按距离递减做远到近消息传递，传播完成后重打分。层数 Lm 与维度 d 论文未给，需实验确定。**待决策点 5**。",
    ),
    (
        "Phase 6 训练评分器",
        "正样本为黄金路径三元组，负样本为按 κ=10 采样的局部噪声邻边并过滤极高度数通用关系；BCE 损失，AdamW lr=1e-5、5 epochs。**待决策点 7**。",
    ),
    (
        "Phase 7 记忆子图管理与 LLM",
        "按 τp 选出 potential 子图，用附录 C 的 prompt 让 LLM 输出记忆更新、试探性答案与子问题；记忆严格覆盖更新。**待决策点 6**。",
    ),
    (
        "Phase 8 推理循环与评估",
        "跑 R 轮循环，计算 Hit / Hit@1 / F1；先复现逐跳贪心对照以确认短视失败模式，再对比 FoG 的增益。",
    ),
]
for title, body in phases:
    add_para(doc, "**" + title + "**  " + body, size=10.5, space_after=6)

add_heading(doc, "4.3 阈值优化实验", 2)
add_para(
    doc,
    "在完成 MVR 之后，围绕 τe 与 τp 的优化是本文档讨论的重点实验方向。固定阈值的问题在于它不随问题、实体、深度变化："
    "简单题太松、难题太紧；高度数实体与低度数实体共用同一刻度；近源边本不该和远端边一样严格。",
)
add_para(
    doc,
    "**自适应 τe**：深度调度 τe(l) = τlo + (τhi - τlo) × (l-1)/(L-1)，近源宽松、远端严格；"
    "度数归一化保留 s_t ≥ μv + β·σv 的边，其中 μv、σv 为该 frontier 实体候选分数集的均值与标准差，β 为全局旋钮。",
)
add_para(
    doc,
    "**自适应 τp**：改用预算式 top-k，保留分数最高的 k_p = k0 + α·|Qsub| 条边，k_p 随子问题数（问题复杂度）线性增长，"
    "等价于一个随问题自适应的阈值。",
)
add_para(
    doc,
    "**量化方法**：沿用论文检索层指标 R_evid、P_prune、H_path、A_1shot，把固定与自适应方案画到同一 recall-precision 平面，"
    "目标是相同 P_prune 下 R_evid 更高、Pareto 前沿整体上移；再确认端到端 Hit / Hit@1 / F1 经 bootstrap 与 McNemar 检验显著，"
    "并构造 oracle 上界（黄金边永不剪）量化剩余 headroom。自适应阈值初值与 oracle 定义见**待决策点 9、10**。",
)

add_heading(doc, "4.4 待决策点清单", 2)
add_para(doc, "下表汇总复现过程中需要自行拍板的点。状态一栏全部为待定，需在进入对应阶段前确认。", size=10.5)
make_table(
    doc,
    ["编号", "待决策点", "影响", "建议默认方案", "状态"],
    [
        ("1", "源实体抽取方法", "决定检索起点", "用数据集自带话题实体或根实体", "待定"),
        ("2", "mid 到文本映射与无名实体回退", "决定嵌入质量", "type.object.name 取英文名，无则用 mid", "待定"),
        ("3", "黄金路径转换规则", "决定训练正样本", "由 WebQSP SPARQL 或 CWQ S-expression 反推", "待定"),
        ("4", "阈值刻度（sigmoid 概率 vs logit）", "决定 τ 的含义", "sigmoid 概率刻度", "待定"),
        ("5", "FFP 层数 Lm 与维度 d", "决定反馈强度", "Lm=2、d=256，需实验", "待定"),
        ("6", "子问题分解 LLM 与采样参数", "决定分解质量", "GPT-4o-mini、温度 0", "待定"),
        ("7", "极高度数通用关系的过滤阈值", "决定负样本质量", "度数大于 1000 过滤", "待定"),
        ("8", "全量 Freebase 或子集", "决定复现成本", "先用源实体 3 跳子图", "待定"),
        ("9", "自适应阈值初值", "决定优化实验起点", "τlo=0.05、τhi=0.25、β=1.0、k0=5、α=3", "待定"),
        ("10", "oracle 上界定义", "决定 headroom 计算", "黄金边永不剪并留固定噪声预算", "待定"),
    ],
    widths=[1.2, 4.2, 3.6, 4.8, 1.8],
    aligns=["center", "left", "left", "left", "center"],
)

add_heading(doc, "4.5 里程碑与验收标准", 2)
for t in (
    "Phase 1 完成：能对一个 dev 问题取出源实体、黄金三元组与 3 跳子图。",
    "Phase 3 至 6 完成：固定阈值下黄金边分数显著高于随机噪声边。",
    "Phase 5 验证：FFP 前后黄金路径边排名整体上移；去掉 FFP 或换成无向 GNN 后 R_evid 明显下降。",
    "Phase 7 至 8 完成：端到端 Hit 在 CWQ 上超过贪心或束搜索对照，LLM 调用降到 R+1 量级。",
    "阈值优化完成：相同 P_prune 下 R_evid 更高且端到端提升经统计检验显著。",
):
    add_bullet(doc, t)

# ---------------- 5 附录 ----------------
add_heading(doc, "5 附录 关键超参与公式", 1)
for t in (
    "跳数预算 L=3；展开阈值 τe=0.1；potential 阈值 τp=0.7。",
    "评分器训练：AdamW lr=1e-5、β1=0.9、β2=0.999、weight decay=1e-3、dropout=0.1、每数据集 5 epochs、单卡 RTX 5090。",
    "负采样：κ=10，过滤极高度数通用关系。",
    "三元组表示 u_t = [e_s+p_s; e_r+p_r; e_o+p_o]，结构上下文 c_t = [z_t; h_s; h_o]，分数 s_t = MLP_s([c_t; q̄_t])。",
    "远到近消息 m_t = h_s + MLP_m([h_s; z_t])，节点更新 h_o ← h_o + φ(Σ_t α_t m_t)。",
    "深度调度 τe(l) = τlo + (τhi - τlo) × (l-1)/(L-1)；度数归一化保留 s_t ≥ μv + β·σv；预算式 k_p = k0 + α·|Qsub|。",
):
    add_bullet(doc, t)

out = r"D:\Repo\03-Project\FoG论文解析与复现计划.docx"
doc.save(out)
print("saved:", out)
