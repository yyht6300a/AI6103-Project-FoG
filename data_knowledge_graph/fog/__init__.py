# -*- coding: utf-8 -*-
"""FoG 复现 —— 成员 A「数据与知识图谱」子包。

对外主要入口：

* :mod:`fog.kg`         —— 知识图谱后端（离线三元组索引 / Virtuoso SPARQL / 合成测试图）
* :mod:`fog.datasets`   —— WebQSP、CWQ 官方 split 的加载器
* :mod:`fog.gold`       —— 由 SPARQL / S-expression 反推黄金推理路径
* :mod:`fog.subgraph`   —— 从源实体出发的 3 跳分层 BFS 子图展开
* :mod:`fog.naming`     —— mid ↔ 实体英文名映射与回退策略

版本与产物契约见 ``docs/DATA_CONTRACT.md``。
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
