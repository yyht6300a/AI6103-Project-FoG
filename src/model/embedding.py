"""Frozen Qwen3-Embedding-4B encoder and the on-disk embedding cache (member B).

Paper (Sec 4.1): "We adopt Qwen3-Embedding-4B as a unified pretrained encoder to
embed both natural-language queries and KG symbols (entities/relations). The
encoder is kept frozen throughout training and inference."

The paper gives no instruction prompt, relation formatting or output size, so this
module follows the Qwen3-Embedding model card and keeps the native dimension:

* last-token pooling with left padding, then L2 normalisation;
* queries (original question and sub-questions) get the ``Instruct: ...\\nQuery:``
  prefix; entity names and relation strings are encoded without a prefix;
* relation strings are written as words (``people.person.sibling_s`` ->
  ``people person sibling s``);
* unnamed nodes (Freebase ``m.xxx`` / ``g.xxx``, mostly CVTs) are not encoded:
  their cache rows stay zero and the scorer replaces them with one learnable
  CVT vector.

Cache layout (``cache_dir``)::

    meta.json            model, dim, counts, text formats
    entity_emb.npy       float16 [n_entities, dim], aligned with member A's entity ids
    entity_is_mid.npy    bool    [n_entities]
    relation_emb.npy     float16 [n_relations, dim], aligned with A's relation ids
    queries.json         list[str] of raw query texts (without the instruction)
    queries.npy          float16 [n_queries, dim]
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np
import torch

DEFAULT_MODEL_NAME = "Qwen/Qwen3-Embedding-4B"
QUERY_INSTRUCTION = (
    "Given a question, retrieve knowledge graph triples that help answer the question"
)
MID_PATTERN = re.compile(r"^[mg]\.[0-9a-z_]+$")
CACHE_FORMAT_VERSION = 2


# --------------------------------------------------------------------------- text
def is_mid(name: str) -> bool:
    """True for an unnamed Freebase node such as ``m.0gxnnwp`` (mostly CVTs)."""
    return bool(MID_PATTERN.match(name))


def relation_text(relation: str) -> str:
    """Write a Freebase relation id as words: ``people.person.sibling_s`` -> ``people person sibling s``."""
    return " ".join(relation.replace(".", " ").replace("_", " ").split())


def format_query(query: str, instruction: str = QUERY_INSTRUCTION) -> str:
    """Qwen3-Embedding query format (model card): ``Instruct: {task}\\nQuery:{query}``."""
    return f"Instruct: {instruction}\nQuery:{query}"


def index_fingerprint(texts: Sequence[str]) -> str:
    """Hash ordered ID-to-text entries, using length prefixes to avoid ambiguity."""
    digest = hashlib.sha256()
    for text in texts:
        raw = text.encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def encoder_config(encoder) -> Dict:
    return {
        "format_version": CACHE_FORMAT_VERSION,
        "model_name": encoder.model_name,
        "dim": int(encoder.dim),
        "truncate_dim": getattr(encoder, "truncate_dim", None),
        "max_length": getattr(encoder, "max_length", 512),
        "query_instruction": getattr(encoder, "instruction", QUERY_INSTRUCTION),
        "pooling": "last_token_left_padding_l2",
        "relation_format": "dots and underscores replaced by spaces",
        "entity_format": "raw text; MID rows zero",
    }


def _require_match(actual, expected):
    if any(actual.get(k) != v for k, v in expected.items()):
        raise ValueError("incompatible embedding cache (index or encoder configuration); "
                         "rebuild all parts with --no-resume or use a new cache directory")


# ------------------------------------------------------------------------ encoder
def _auto_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class QwenTextEncoder:
    """Frozen Qwen3-Embedding encoder (inference only, no gradients)."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        device: Optional[str] = None,
        dtype: Optional[torch.dtype] = None,
        max_length: int = 512,
        batch_size: int = 64,
        truncate_dim: Optional[int] = None,
        instruction: str = QUERY_INSTRUCTION,
    ) -> None:
        from transformers import AutoModel, AutoTokenizer  # heavy, import lazily

        self.device = device or _auto_device()
        if dtype is None:
            dtype = torch.bfloat16 if self.device == "cuda" else (
                torch.float16 if self.device == "mps" else torch.float32)
        self.model_name = model_name
        self.max_length = max_length
        self.batch_size = batch_size
        self.instruction = instruction
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, padding_side="left")
        self.model = AutoModel.from_pretrained(model_name, torch_dtype=dtype).to(self.device)
        self.model.eval()
        for p in self.model.parameters():
            p.requires_grad_(False)
        native = int(self.model.config.hidden_size)
        if truncate_dim is not None and not 0 < truncate_dim <= native:
            raise ValueError(f"truncate_dim must be in (0, {native}], got {truncate_dim}")
        self.truncate_dim = truncate_dim
        self.dim = truncate_dim or native

    @staticmethod
    def _last_token_pool(hidden: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        if bool((attention_mask[:, -1] == 1).all()):  # left padding
            return hidden[:, -1]
        lengths = attention_mask.sum(dim=1) - 1
        return hidden[torch.arange(hidden.shape[0], device=hidden.device), lengths]

    @torch.no_grad()
    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Encode raw texts to L2-normalised float32 vectors ``[len(texts), dim]``."""
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        if not texts:
            return out
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))  # less padding
        for start in range(0, len(order), self.batch_size):
            idx = order[start:start + self.batch_size]
            batch = self.tokenizer(
                [texts[i] for i in idx], padding=True, truncation=True,
                max_length=self.max_length, return_tensors="pt",
            ).to(self.device)
            hidden = self.model(**batch).last_hidden_state
            emb = self._last_token_pool(hidden, batch["attention_mask"]).float()
            if self.truncate_dim is not None:
                emb = emb[:, :self.truncate_dim]
            emb = torch.nn.functional.normalize(emb, p=2, dim=-1)
            out[idx] = emb.cpu().numpy()
        return out

    def encode_queries(self, queries: Sequence[str]) -> np.ndarray:
        """Encode questions / sub-questions with the query instruction."""
        return self.encode([format_query(q, self.instruction) for q in queries])


# -------------------------------------------------------------------- cache build
def _log_default(msg: str) -> None:
    print(msg, flush=True)


def build_entity_cache(
    names: Sequence[str],
    encoder,
    cache_dir: str | Path,
    chunk_size: int = 50_000,
    resume: bool = True,
    log: Callable[[str], None] = _log_default,
) -> None:
    """Encode every named entity into ``entity_emb.npy`` (row = member A's entity id).

    Unnamed (mid) rows stay zero. Progress is saved after each chunk, so an
    interrupted run continues where it stopped when ``resume`` is true.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    emb_path = cache_dir / "entity_emb.npy"
    progress_path = cache_dir / "entity_progress.json"
    n = len(names)

    signature = {**encoder_config(encoder), "n_entities": n,
                 "entity_fingerprint": index_fingerprint(names)}
    done = 0
    if resume and (emb_path.exists() or progress_path.exists()):
        if not emb_path.exists() or not progress_path.exists():
            raise ValueError("incomplete/legacy entity cache; rebuild with --no-resume")
        state = json.loads(progress_path.read_text())
        _require_match(state, signature)
        done = int(state["done"])
        emb = np.load(emb_path, mmap_mode="r+")
        if emb.shape != (n, encoder.dim) or emb.dtype != np.float16:
            raise ValueError("invalid entity cache shape/dtype; rebuild with --no-resume")
        log(f"[entity] resuming at {done:,} named entities")
    else:
        emb = np.lib.format.open_memmap(emb_path, mode="w+", dtype=np.float16, shape=(n, encoder.dim))
        progress_path.write_text(json.dumps({**signature, "done": 0}))
    mids = np.fromiter((is_mid(x) for x in names), dtype=bool, count=n)
    np.save(cache_dir / "entity_is_mid.npy", mids)
    todo = np.nonzero(~mids)[0]

    t0 = time.time()
    first = done
    for start in range(done, len(todo), chunk_size):
        rows = todo[start:start + chunk_size]
        emb[rows] = encoder.encode([names[i] for i in rows]).astype(np.float16)
        emb.flush()
        done = start + len(rows)
        progress_path.write_text(json.dumps({**signature, "done": done}))
        rate = (done - first) / max(time.time() - t0, 1e-9)
        log(f"[entity] {done:,}/{len(todo):,} named entities ({rate:,.0f}/s)")
    del emb
    log(f"[entity] done: {len(todo):,} named, {int(mids.sum()):,} unnamed (kept zero)")


def build_relation_cache(relations: Sequence[str], encoder, cache_dir: str | Path, resume: bool = True) -> None:
    """Encode every relation (as words) into ``relation_emb.npy`` (row = A's relation id)."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    signature = {**encoder_config(encoder), "n_relations": len(relations),
                 "relation_fingerprint": index_fingerprint(relations)}
    manifest = cache_dir / "relation_meta.json"
    if resume and (cache_dir / "relation_emb.npy").exists():
        _require_match(json.loads(manifest.read_text()) if manifest.exists() else {}, signature)
        return
    emb = encoder.encode([relation_text(r) for r in relations]).astype(np.float16)
    np.save(cache_dir / "relation_emb.npy", emb)
    manifest.write_text(json.dumps(signature))


def build_query_cache(queries: Iterable[str], encoder, cache_dir: str | Path, resume: bool = True) -> int:
    """Encode the given query texts (deduplicated, order kept) into ``queries.npy``.

    Queries already in an existing cache are kept and not re-encoded.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    texts: List[str] = []
    vecs: List[np.ndarray] = []
    known: Dict[str, int] = {}
    manifest = cache_dir / "query_meta.json"
    if resume and ((cache_dir / "queries.json").exists() or (cache_dir / "queries.npy").exists()):
        _require_match(json.loads(manifest.read_text()) if manifest.exists() else {}, encoder_config(encoder))
        old_texts = json.loads((cache_dir / "queries.json").read_text(encoding="utf-8"))
        old = np.load(cache_dir / "queries.npy")
        if old.shape != (len(old_texts), encoder.dim):
            raise ValueError("invalid query cache shape; rebuild with --no-resume")
        texts.extend(old_texts)
        vecs.append(old)
        known = {t: i for i, t in enumerate(old_texts)}
    new = list(dict.fromkeys(q for q in queries if q not in known))
    if new:
        vecs.append(encoder.encode_queries(new).astype(np.float16))
        texts.extend(new)
    arr = np.concatenate(vecs, axis=0) if vecs else np.zeros((0, encoder.dim), np.float16)
    np.save(cache_dir / "queries.npy", arr)
    (cache_dir / "queries.json").write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
    manifest.write_text(json.dumps(encoder_config(encoder)))
    return len(new)


def write_cache_meta(cache_dir: str | Path, encoder, n_entities: int, n_relations: int,
                     extra: Optional[Dict] = None) -> None:
    d = Path(cache_dir)
    entity = json.loads((d / "entity_progress.json").read_text())
    relation = json.loads((d / "relation_meta.json").read_text())
    _require_match(entity, encoder_config(encoder))
    _require_match(relation, encoder_config(encoder))
    if (d / "query_meta.json").exists():
        _require_match(json.loads((d / "query_meta.json").read_text()), encoder_config(encoder))
    meta = {**encoder_config(encoder), "n_entities": int(n_entities),
            "n_relations": int(n_relations),
            "entity_fingerprint": entity["entity_fingerprint"],
            "relation_fingerprint": relation["relation_fingerprint"]}
    meta.update(extra or {})
    Path(cache_dir, "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))


# -------------------------------------------------------------------- cache read
class EmbeddingStore:
    """Read-only view of the embedding cache, indexed by member A's ids.

    ``query_encoder`` (a :class:`QwenTextEncoder`) is optional; without it,
    :meth:`encode_questions` only serves queries that are already cached.
    """

    def __init__(
        self,
        entity_emb: np.ndarray,
        entity_is_mid: np.ndarray,
        relation_emb: np.ndarray,
        query_texts: Sequence[str] = (),
        query_emb: Optional[np.ndarray] = None,
        meta: Optional[Dict] = None,
        query_encoder=None,
    ) -> None:
        if entity_emb.shape[0] != entity_is_mid.shape[0]:
            raise ValueError("entity_emb and entity_is_mid have different lengths")
        if entity_emb.shape[1] != relation_emb.shape[1]:
            raise ValueError("entity and relation embeddings have different dims")
        self.entity_emb = entity_emb
        self.entity_is_mid_arr = entity_is_mid
        self.relation_emb = relation_emb
        self.meta = meta or {}
        self.query_encoder = query_encoder
        self._query_rows: Dict[str, int] = {t: i for i, t in enumerate(query_texts)}
        self._query_emb = query_emb
        self._extra_queries: Dict[str, np.ndarray] = {}
        if query_encoder is not None and query_encoder.dim != self.dim:
            raise ValueError(f"query encoder dim {query_encoder.dim} != cache dim {self.dim}")

    @classmethod
    def load(cls, cache_dir: str | Path, mmap: bool = True, query_encoder=None) -> "EmbeddingStore":
        d = Path(cache_dir)
        mode = "r" if mmap else None
        meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
        if meta.get("format_version") != CACHE_FORMAT_VERSION:
            raise ValueError("legacy embedding cache; rebuild all parts with --no-resume")
        if query_encoder is not None:
            _require_match(meta, encoder_config(query_encoder))
        config_keys = encoder_config(query_encoder).keys() if query_encoder is not None else (
            "format_version", "model_name", "dim", "truncate_dim", "max_length",
            "query_instruction", "pooling", "relation_format", "entity_format")
        expected = {k: meta[k] for k in config_keys}
        for filename, index_keys in (
            ("entity_progress.json", ("n_entities", "entity_fingerprint")),
            ("relation_meta.json", ("n_relations", "relation_fingerprint")),
        ):
            manifest = d / filename
            _require_match(json.loads(manifest.read_text()) if manifest.exists() else {},
                           {**expected, **{k: meta[k] for k in index_keys}})
        texts: List[str] = []
        qemb = None
        if (d / "queries.json").exists():
            manifest = d / "query_meta.json"
            _require_match(json.loads(manifest.read_text()) if manifest.exists() else {}, expected)
            texts = json.loads((d / "queries.json").read_text(encoding="utf-8"))
            qemb = np.load(d / "queries.npy", mmap_mode=mode)
        return cls(
            entity_emb=np.load(d / "entity_emb.npy", mmap_mode=mode),
            entity_is_mid=np.load(d / "entity_is_mid.npy"),
            relation_emb=np.load(d / "relation_emb.npy"),
            query_texts=texts,
            query_emb=qemb,
            meta=meta,
            query_encoder=query_encoder,
        )

    def validate_index(self, kg) -> None:
        _require_match(self.meta, {
            "format_version": CACHE_FORMAT_VERSION,
            "n_entities": kg.n_entities, "n_relations": kg.n_relations,
            "entity_fingerprint": index_fingerprint(kg.entities),
            "relation_fingerprint": index_fingerprint(kg.relations),
        })
        if self.entity_emb.shape != (kg.n_entities, self.meta["dim"]) or self.relation_emb.shape != (kg.n_relations, self.meta["dim"]):
            raise ValueError("embedding cache array shapes do not match metadata")

    @property
    def dim(self) -> int:
        return int(self.entity_emb.shape[1])

    @staticmethod
    def _np_ids(ids) -> np.ndarray:
        if isinstance(ids, torch.Tensor):
            ids = ids.detach().cpu().numpy()
        return np.asarray(ids, dtype=np.int64)

    def entity(self, ids) -> torch.Tensor:
        """Entity vectors ``[N, dim]`` (float32); unnamed nodes come back as zeros."""
        return torch.from_numpy(np.asarray(self.entity_emb[self._np_ids(ids)], dtype=np.float32))

    def entity_is_mid(self, ids) -> torch.Tensor:
        return torch.from_numpy(np.asarray(self.entity_is_mid_arr[self._np_ids(ids)], dtype=bool))

    def relation(self, ids) -> torch.Tensor:
        return torch.from_numpy(np.asarray(self.relation_emb[self._np_ids(ids)], dtype=np.float32))

    def has_query(self, text: str) -> bool:
        return text in self._query_rows or text in self._extra_queries

    def encode_questions(self, texts: Sequence[str]) -> torch.Tensor:
        """Question / sub-question vectors ``[len(texts), dim]`` (float32).

        Cached texts are looked up; the rest are encoded online with
        ``query_encoder`` and kept in memory for the rest of the run.
        """
        missing = [t for t in dict.fromkeys(texts) if not self.has_query(t)]
        if missing:
            if self.query_encoder is None:
                raise KeyError(
                    f"{len(missing)} queries are not in the cache and no query encoder is "
                    f"loaded, e.g. {missing[0]!r}"
                )
            for t, v in zip(missing, self.query_encoder.encode_queries(missing)):
                self._extra_queries[t] = v.astype(np.float32)
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            row = self._query_rows.get(t)
            out[i] = self._query_emb[row] if row is not None else self._extra_queries[t]
        return torch.from_numpy(out)
