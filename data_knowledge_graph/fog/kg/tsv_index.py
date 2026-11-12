# -*- coding: utf-8 -*-
"""离线三元组索引：把 Freebase 子图 TSV 建成可做 BFS 的邻接索引。

为什么需要它
------------
复现计划里"用源实体 3 跳子图代替全量 Freebase"（决策点 8）。3 跳展开需要**低延迟的
邻接查询**，而逐条 SPARQL 打远端 Virtuoso 在这个规模下不可行。因此先把名字空间三元组
落成一个纯 numpy 的 CSR 索引：一次构建、多次复用，B/C/D/E 都能直接用。

文件格式（``<index_dir>/``）
    ``head.npy`` / ``rel.npy`` / ``tail.npy``   int32，去重后的三元组（按 (h,r,t) 排序）
    ``out_eids.npy`` / ``out_indptr.npy``       出边 CSR（按 head 分组）
    ``in_eids.npy``  / ``in_indptr.npy``        入边 CSR（按 tail 分组）
    ``entities.tsv``                            实体 id \t 实体名（id 即行号）
    ``relations.tsv``                           关系 id \t 关系名
    ``meta.json``                               构建参数、规模、来源文件哈希

约定
----
* 实体名（``type.object.name``）与 mid 混存：RoG 预处理在实体没有英文名时用 mid 兜底，
  因此本索引也保持同一约定（决策点 2 的"无名字实体回退"）。
* 不做同义词合并；同名实体本来就已在 RoG 序列化阶段被合并过一次。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Sequence, Tuple

import numpy as np

from ..schema import Triple
from ..utils import human_bytes, human_int, sha256_file, write_json

INT32_MAX = np.iinfo(np.int32).max


def iter_tsv_triples(path: str | Path) -> Iterator[Tuple[str, str, str]]:
    """逐行读 ``head \\t relation \\t tail``，容忍 CRLF 与多余空白。"""
    with open(path, "rb") as fh:
        for lineno, raw in enumerate(fh, 1):
            if not raw or raw == b"\n":
                continue
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            parts = line.split("\t")
            if len(parts) != 3:
                # 兼容用空格分隔的导出格式
                parts = line.split(" ", 2) if line.count(" ") >= 2 and "\t" not in line else parts
            if len(parts) != 3:
                continue
            h, r, t = (p.strip() for p in parts)
            if not h or not r or not t:
                continue
            yield h, r, t


@dataclass
class BuildStats:
    files: List[str] = field(default_factory=list)
    raw_lines: int = 0
    kept_triples: int = 0
    duplicates: int = 0
    n_entities: int = 0
    n_relations: int = 0
    max_degree_out: int = 0
    max_degree_in: int = 0
    seconds: float = 0.0
    file_sha256: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "files": self.files,
            "raw_lines": self.raw_lines,
            "kept_triples": self.kept_triples,
            "duplicates": self.duplicates,
            "n_entities": self.n_entities,
            "n_relations": self.n_relations,
            "max_degree_out": self.max_degree_out,
            "max_degree_in": self.max_degree_in,
            "seconds": self.seconds,
            "file_sha256": self.file_sha256,
        }


class TripleIndex:
    """名字空间三元组索引（CSR 邻接，纯 numpy）。"""

    def __init__(
        self,
        head: np.ndarray,
        rel: np.ndarray,
        tail: np.ndarray,
        entities: List[str],
        relations: List[str],
        out_eids: np.ndarray,
        out_indptr: np.ndarray,
        in_eids: np.ndarray,
        in_indptr: np.ndarray,
        meta: Dict | None = None,
    ) -> None:
        self.head = head
        self.rel = rel
        self.tail = tail
        self.entities = entities
        self.relations = relations
        self.out_eids = out_eids
        self.out_indptr = out_indptr
        self.in_eids = in_eids
        self.in_indptr = in_indptr
        self.meta = meta or {}
        self._ent_map: Dict[str, int] | None = None
        self._ent_map_ci: Dict[str, int] | None = None
        self._rel_map: Dict[str, int] | None = None
        self._rel_degree: np.ndarray | None = None
        self._entity_degree: np.ndarray | None = None
        self._out_rel_key: np.ndarray | None = None
        self._out_rel_order: np.ndarray | None = None
        self._in_rel_key: np.ndarray | None = None
        self._in_rel_order: np.ndarray | None = None
        self._rel_stride: int | None = None

    # ------------------------------------------------------------------ 规模
    @property
    def n_triples(self) -> int:
        return int(self.head.shape[0])

    @property
    def n_entities(self) -> int:
        return len(self.entities)

    @property
    def n_relations(self) -> int:
        return len(self.relations)

    # ------------------------------------------------------------------ 映射
    @property
    def ent_map(self) -> Dict[str, int]:
        if self._ent_map is None:
            self._ent_map = {name: i for i, name in enumerate(self.entities)}
        return self._ent_map

    @property
    def ent_map_ci(self) -> Dict[str, int]:
        """大小写不敏感映射（同名取 id 最小者，稳定）。"""
        if self._ent_map_ci is None:
            m: Dict[str, int] = {}
            for i, name in enumerate(self.entities):
                key = name.casefold()
                if key not in m:
                    m[key] = i
            self._ent_map_ci = m
        return self._ent_map_ci

    @property
    def rel_map(self) -> Dict[str, int]:
        if self._rel_map is None:
            self._rel_map = {name: i for i, name in enumerate(self.relations)}
        return self._rel_map

    def entity_id(self, name: str) -> int | None:
        return self.ent_map.get(name)

    def entity_id_ci(self, name: str) -> int | None:
        return self.ent_map_ci.get(name.casefold())

    def resolve_entity(self, name: str) -> int | None:
        """精确 -> 大小写不敏感。"""
        eid = self.entity_id(name)
        if eid is None:
            eid = self.entity_id_ci(name)
        return eid

    def relation_id(self, name: str) -> int | None:
        return self.rel_map.get(name)

    # ------------------------------------------------------------------ 边
    def edge(self, eid: int) -> Tuple[int, int, int]:
        return int(self.head[eid]), int(self.rel[eid]), int(self.tail[eid])

    def edge_triple(self, eid: int) -> Triple:
        h, r, t = self.edge(eid)
        return Triple(self.entities[h], self.relations[r], self.entities[t])

    def out_edges(self, eid: int) -> np.ndarray:
        return self.out_eids[self.out_indptr[eid]:self.out_indptr[eid + 1]]

    def in_edges(self, eid: int) -> np.ndarray:
        return self.in_eids[self.in_indptr[eid]:self.in_indptr[eid + 1]]

    # --------------------------------------------------- (实体, 关系) 定向查边
    # 黄金路径对齐要频繁问"从实体 v 出发、关系 r 的边有哪些"。在枢纽实体
    # （如 United States of America，度数 13 万）上逐边扫描是 O(度)，会让对齐慢 3 个量级。
    # 这里按 (relation, head) 排序一次，用两次 searchsorted 定位，O(log E)。
    def _build_rel_index(self) -> None:
        n = self.n_entities + 1
        self._rel_stride = n
        # 出边：(rel, head)
        key = self.rel.astype(np.int64) * n + self.head
        order = np.argsort(key, kind="stable")
        self._out_rel_order = order.astype(np.int32)
        self._out_rel_key = key[order]
        # 入边：(rel, tail)
        key_in = self.rel.astype(np.int64) * n + self.tail
        order_in = np.argsort(key_in, kind="stable")
        self._in_rel_order = order_in.astype(np.int32)
        self._in_rel_key = key_in[order_in]

    def _ensure_rel_index(self) -> None:
        if self._out_rel_key is None:
            self._build_rel_index()

    def out_edges_with_rel(self, eid: int, rid: int) -> np.ndarray:
        """从实体 ``eid`` 出发、关系为 ``rid`` 的边 id 数组。"""
        self._ensure_rel_index()
        assert self._out_rel_key is not None and self._out_rel_order is not None
        base = rid * self._rel_stride + eid
        lo = np.searchsorted(self._out_rel_key, base, side="left")
        hi = np.searchsorted(self._out_rel_key, base + 1, side="left")
        return self._out_rel_order[lo:hi]

    def in_edges_with_rel(self, eid: int, rid: int) -> np.ndarray:
        """指向实体 ``eid``、关系为 ``rid`` 的边 id 数组。"""
        self._ensure_rel_index()
        assert self._in_rel_key is not None and self._in_rel_order is not None
        base = rid * self._rel_stride + eid
        lo = np.searchsorted(self._in_rel_key, base, side="left")
        hi = np.searchsorted(self._in_rel_key, base + 1, side="left")
        return self._in_rel_order[lo:hi]

    def degree_out(self, eid: int) -> int:
        return int(self.out_indptr[eid + 1] - self.out_indptr[eid])

    def degree_in(self, eid: int) -> int:
        return int(self.in_indptr[eid + 1] - self.in_indptr[eid])

    def degree(self, eid: int) -> int:
        return self.degree_out(eid) + self.degree_in(eid)

    @property
    def rel_degree(self) -> np.ndarray:
        """每个关系的全局边数（决策点 7：过滤极高度数通用关系）。"""
        if self._rel_degree is None:
            self._rel_degree = np.bincount(self.rel, minlength=self.n_relations).astype(np.int64)
        return self._rel_degree

    @property
    def entity_degree(self) -> np.ndarray:
        """每个实体的 (出度 + 入度)。缓存，供 3 跳展开做枢纽过滤。"""
        if self._entity_degree is None:
            self._entity_degree = (np.bincount(self.head, minlength=self.n_entities)
                                   + np.bincount(self.tail, minlength=self.n_entities)).astype(np.int32)
        return self._entity_degree

    def find_triples(self, h: str | None = None, r: str | None = None,
                     t: str | None = None) -> List[int]:
        """在索引中按模式查找边 id（任一位置可为 None）。"""
        hid = self.entity_id(h) if h is not None else None
        rid = self.relation_id(r) if r is not None else None
        tid = self.entity_id(t) if t is not None else None
        if h is not None and hid is None:
            return []
        if t is not None and tid is None:
            return []
        if r is not None and rid is None:
            return []
        mask = np.ones(self.n_triples, dtype=bool)
        if hid is not None:
            mask &= self.head == hid
        if rid is not None:
            mask &= self.rel == rid
        if tid is not None:
            mask &= self.tail == tid
        return np.nonzero(mask)[0].tolist()

    def has_triple(self, h: str, r: str, t: str) -> bool:
        hid, rid, tid = self.entity_id(h), self.relation_id(r), self.entity_id(t)
        if hid is None or rid is None or tid is None:
            return False
        eids = self.out_edges(hid)
        return bool(np.any((self.rel[eids] == rid) & (self.tail[eids] == tid)))

    # ------------------------------------------------------------------ 存取
    def save(self, index_dir: str | Path, meta: Dict | None = None) -> None:
        d = Path(index_dir)
        d.mkdir(parents=True, exist_ok=True)
        np.save(d / "head.npy", self.head)
        np.save(d / "rel.npy", self.rel)
        np.save(d / "tail.npy", self.tail)
        np.save(d / "out_eids.npy", self.out_eids)
        np.save(d / "out_indptr.npy", self.out_indptr)
        np.save(d / "in_eids.npy", self.in_eids)
        np.save(d / "in_indptr.npy", self.in_indptr)
        with open(d / "entities.tsv", "w", encoding="utf-8", newline="\n") as fh:
            for i, name in enumerate(self.entities):
                fh.write(f"{i}\t{name}\n")
        with open(d / "relations.tsv", "w", encoding="utf-8", newline="\n") as fh:
            for i, name in enumerate(self.relations):
                fh.write(f"{i}\t{name}\n")
        m = dict(self.meta)
        if meta:
            m.update(meta)
        m.update({
            "n_triples": self.n_triples,
            "n_entities": self.n_entities,
            "n_relations": self.n_relations,
        })
        write_json(d / "meta.json", m)

    @classmethod
    def load(cls, index_dir: str | Path, load_names: bool = True) -> "TripleIndex":
        d = Path(index_dir)
        meta = {}
        if (d / "meta.json").exists():
            with open(d / "meta.json", encoding="utf-8") as fh:
                meta = json.load(fh)
        entities: List[str] = []
        relations: List[str] = []
        if load_names:
            with open(d / "entities.tsv", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.rstrip("\n").split("\t", 1)
                    if len(parts) == 2:
                        entities.append(parts[1])
            with open(d / "relations.tsv", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.rstrip("\n").split("\t", 1)
                    if len(parts) == 2:
                        relations.append(parts[1])
        else:
            entities = [""] * int(meta.get("n_entities", 0))
            relations = [""] * int(meta.get("n_relations", 0))
        return cls(
            head=np.load(d / "head.npy"),
            rel=np.load(d / "rel.npy"),
            tail=np.load(d / "tail.npy"),
            entities=entities,
            relations=relations,
            out_eids=np.load(d / "out_eids.npy"),
            out_indptr=np.load(d / "out_indptr.npy"),
            in_eids=np.load(d / "in_eids.npy"),
            in_indptr=np.load(d / "in_indptr.npy"),
            meta=meta,
        )

    # ------------------------------------------------------------------ 构建
    @classmethod
    def build(
        cls,
        tsv_paths: Sequence[str | Path],
        log=print,
        chunk: int = 1_000_000,
        compute_sha: bool = True,
    ) -> Tuple["TripleIndex", BuildStats]:
        """从若干 TSV 文件构建索引（跨文件去重）。"""
        stats = BuildStats()
        stats.files = [str(p) for p in tsv_paths]
        if compute_sha:
            for path in tsv_paths:
                path = Path(path)
                log(f"  计算 sha256 {path.name} ...")
                stats.file_sha256[path.name] = sha256_file(path)

        def all_triples():
            for path in tsv_paths:
                path = Path(path)
                log(f"  读入 {path.name} ...")
                n0 = stats.raw_lines
                for rec in iter_tsv_triples(path):
                    stats.raw_lines += 1
                    yield rec
                log(f"    文件完成 {stats.raw_lines - n0:,} 行")

        idx, stats2 = cls._build_from_iterable(all_triples(), log=log, chunk=chunk, stats=stats)
        return idx, stats2

    @classmethod
    def _build_from_iterable(
        cls,
        triples,
        log=print,
        chunk: int = 1_000_000,
        stats: BuildStats | None = None,
    ) -> Tuple["TripleIndex", BuildStats]:
        t_start = time.time()
        stats = stats or BuildStats()
        ent_map: Dict[str, int] = {}
        rel_map: Dict[str, int] = {}
        entities: List[str] = []
        relations: List[str] = []

        h_chunks: List[np.ndarray] = []
        r_chunks: List[np.ndarray] = []
        t_chunks: List[np.ndarray] = []
        buf_h: List[int] = []
        buf_r: List[int] = []
        buf_t: List[int] = []

        def flush() -> None:
            if not buf_h:
                return
            h_chunks.append(np.asarray(buf_h, dtype=np.int32))
            r_chunks.append(np.asarray(buf_r, dtype=np.int32))
            t_chunks.append(np.asarray(buf_t, dtype=np.int32))
            buf_h.clear(); buf_r.clear(); buf_t.clear()

        for h, r, t in triples:
            hi = ent_map.get(h)
            if hi is None:
                hi = len(entities)
                ent_map[h] = hi
                entities.append(h)
            ti = ent_map.get(t)
            if ti is None:
                ti = len(entities)
                ent_map[t] = ti
                entities.append(t)
            ri = rel_map.get(r)
            if ri is None:
                ri = len(relations)
                rel_map[r] = ri
                relations.append(r)
            buf_h.append(hi); buf_r.append(ri); buf_t.append(ti)
            if len(buf_h) >= chunk:
                flush()
                log(f"    已读 {len(entities):,} 实体 / {stats.raw_lines or len(buf_h):,} 行")
        flush()

        log("  合并并去重 ...")
        head = np.concatenate(h_chunks) if h_chunks else np.zeros(0, np.int32)
        rel = np.concatenate(r_chunks) if r_chunks else np.zeros(0, np.int32)
        tail = np.concatenate(t_chunks) if t_chunks else np.zeros(0, np.int32)
        del h_chunks, r_chunks, t_chunks

        # 去重：按 (h, r, t) 排序后取每组首个
        order = np.lexsort((tail, rel, head))
        head, rel, tail = head[order], rel[order], tail[order]
        if len(head):
            keep = np.ones(len(head), dtype=bool)
            keep[1:] = (
                (head[1:] != head[:-1]) | (rel[1:] != rel[:-1]) | (tail[1:] != tail[:-1])
            )
            stats.duplicates = int((~keep).sum())
            head, rel, tail = head[keep], rel[keep], tail[keep]
        log(f"  去重后 {human_int(len(head))} 条（重复 {human_int(stats.duplicates)}）")

        if len(entities) >= INT32_MAX:
            raise ValueError("实体数超过 int32 上限，需要改用 int64")

        log("  构建出边 / 入边 CSR ...")
        n_ent = len(entities)
        out_order = np.lexsort((tail, head))
        out_eids = out_order.astype(np.int32)
        head_sorted = head[out_eids]
        out_indptr = np.zeros(n_ent + 1, dtype=np.int64)
        out_indptr[1:] = np.cumsum(np.bincount(head_sorted, minlength=n_ent))

        in_order = np.lexsort((head, tail))
        in_eids = in_order.astype(np.int32)
        tail_sorted = tail[in_eids]
        in_indptr = np.zeros(n_ent + 1, dtype=np.int64)
        in_indptr[1:] = np.cumsum(np.bincount(tail_sorted, minlength=n_ent))
        in_indptr = in_indptr.astype(np.int64)

        stats.kept_triples = int(len(head))
        stats.n_entities = len(entities)
        stats.n_relations = len(relations)
        stats.max_degree_out = int(np.diff(out_indptr).max()) if n_ent else 0
        stats.max_degree_in = int(np.diff(in_indptr).max()) if n_ent else 0
        stats.seconds = round(time.time() - t_start, 2)

        idx = cls(head, rel.astype(np.int32), tail.astype(np.int32), entities, relations,
                  out_eids, out_indptr, in_eids, in_indptr, meta=stats.to_dict())
        return idx, stats

    # ------------------------------------------------------------------ 报告
    def summary(self) -> Dict:
        out = {
            "n_triples": self.n_triples,
            "n_entities": self.n_entities,
            "n_relations": self.n_relations,
            "avg_degree_out": round(self.n_triples / max(self.n_entities, 1), 3),
        }
        if self.n_triples:
            rd = self.rel_degree
            order = np.argsort(-rd)
            out["top_relations"] = [
                {"relation": self.relations[i], "triples": int(rd[i]),
                 "share": round(float(rd[i] / self.n_triples), 4)}
                for i in order[:20]
            ]
            for thr in (100, 1000, 10000):
                out[f"relations_degree_gt_{thr}"] = int((rd > thr).sum())
                out[f"triples_in_relations_degree_gt_{thr}"] = int(rd[rd > thr].sum())
        return out

    def __repr__(self) -> str:  # pragma: no cover
        return (f"TripleIndex(triples={human_int(self.n_triples)}, "
                f"entities={human_int(self.n_entities)}, relations={human_int(self.n_relations)})")


def build_and_save(
    tsv_paths: Sequence[str | Path],
    index_dir: str | Path,
    log=print,
    compute_sha: bool = True,
) -> BuildStats:
    idx, stats = TripleIndex.build(tsv_paths, log=log, compute_sha=compute_sha)
    idx.save(index_dir, meta={"source": "build_and_save"})
    log(f"  索引已写入 {index_dir} ({human_bytes(_dir_size(index_dir))})")
    return stats


def _dir_size(d: str | Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            total += (Path(root) / f).stat().st_size
    return total


def index_from_triples(triples: Iterable[Triple]) -> TripleIndex:
    """测试/小图用：直接从三元组序列建索引（不落临时文件，沙箱友好）。"""
    seen: set[tuple[str, str, str]] = set()

    def uniq():
        for tr in triples:
            if tr.key() in seen:
                continue
            seen.add(tr.key())
            yield tr.h, tr.r, tr.t

    idx, _stats = TripleIndex._build_from_iterable(uniq(), log=lambda *_a, **_k: None)
    return idx
