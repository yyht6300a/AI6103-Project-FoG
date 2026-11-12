# -*- coding: utf-8 -*-
"""数据集加载：WebQSP / CWQ 官方 split。"""

from .loader import (  # noqa: F401
    LOADERS,
    DatasetSpec,
    chains_for_example,
    get_pattern,
    load_cwq,
    load_split,
    load_webqsp,
)

__all__ = [
    "LOADERS", "DatasetSpec", "chains_for_example", "get_pattern",
    "load_cwq", "load_split", "load_webqsp",
]
