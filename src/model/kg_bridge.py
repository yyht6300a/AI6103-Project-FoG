"""Access to member A's knowledge-graph package (``data_knowledge_graph/fog``).

Member A ships the KG index and the subgraph config as a separate package that
lives in ``data_knowledge_graph/``. This module puts that directory on
``sys.path`` once, so member B code can import ``fog`` without copying it, and
exposes the few things B needs: the triple index and A's relation filter.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FOG_ROOT = REPO_ROOT / "data_knowledge_graph"
DEFAULT_INDEX_DIR = FOG_ROOT / "data" / "processed" / "kg" / "index_all"
DEFAULT_PROCESSED_ROOT = FOG_ROOT / "data" / "processed"


def ensure_fog_importable() -> None:
    """Make member A's ``fog`` package importable."""
    if not (FOG_ROOT / "fog" / "__init__.py").exists():
        raise ImportError(
            f"member A's package not found at {FOG_ROOT}; check out the part_a code first"
        )
    root = str(FOG_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def load_kg(index_dir: str | Path | None = None) -> Any:
    """Load member A's ``TripleIndex`` (about 2 s for the full index)."""
    ensure_fog_importable()
    from fog.kg.tsv_index import TripleIndex

    return TripleIndex.load(Path(index_dir) if index_dir is not None else DEFAULT_INDEX_DIR)


def default_subgraph_config() -> Any:
    """Member A's ``SubgraphConfig`` with its default (calibrated) values.

    B reuses its ``relation_allowed`` rule for negative sampling, so training and
    inference-time expansion filter relations the same way.
    """
    ensure_fog_importable()
    from fog.subgraph import SubgraphConfig

    return SubgraphConfig()
