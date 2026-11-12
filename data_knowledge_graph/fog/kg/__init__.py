# -*- coding: utf-8 -*-
"""知识图谱后端。

* :class:`~fog.kg.tsv_index.TripleIndex` —— 离线 Freebase 子图索引（默认，MVR 用）
* :mod:`fog.kg.virtuoso`                  —— 真实 Freebase / Virtuoso SPARQL 后端（论文级复现用）
* :mod:`fog.kg.mock`                      —— 单元测试与冒烟用的合成小图
"""

from .tsv_index import TripleIndex, build_and_save, index_from_triples  # noqa: F401

__all__ = ["TripleIndex", "build_and_save", "index_from_triples"]
