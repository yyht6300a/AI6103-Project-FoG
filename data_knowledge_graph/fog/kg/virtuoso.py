# -*- coding: utf-8 -*-
"""Virtuoso / SPARQL 后端：论文级复现（全量 Freebase）时用来**物化**子图。

设计取向
--------
本环境没有 Virtuoso 实例，也没有 30 GB+ 的 Freebase 转储，因此这里刻意**不**实现
"在线逐边查询式的 3 跳 BFS"（那样会有成千上万次往返，且无法离线验证）。取而代之：
用 SPARQL 把某批问题的源实体邻域**一次性物化成 TSV**，再交给
:class:`fog.kg.tsv_index.TripleIndex` 走与 MVR 完全相同的代码路径。

这样做的收益：
* 全量 Freebase 与"预处理子图"两种模式共用同一套下游逻辑，只有数据来源不同；
* 物化产物可缓存、可审计、可 diff，符合复现实验"可重跑"的要求。

仅有的未在本机验证的部分是 SPARQL 语句文本本身（见 :data:`NEIGHBOR_QUERY`），
在有 Virtuoso 的机器上按 README 的步骤即可直接使用。

用法::

    kg = VirtuosoKG("http://localhost:3001/sparql")
    rows = kg.neighbors_1hop(["Justin Bieber"], limit=5000)
    kg.materialize_neighborhood(["Justin Bieber"], hops=3, out_path="data/raw/kg/my_seed.tsv")
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Sequence

from ..schema import Triple

FB = "http://rdf.freebase.com/ns/"

#: 一跳邻域（出边 + 入边）。用 UNION 一次取回，避免 N+1 次往返。
#: 只保留 IRI 目标（实体），字面量对本任务无用。
NEIGHBOR_QUERY = """
PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?s ?p ?o WHERE {
  {
    ?s ns:type.object.name %(lit)s .
    ?s ?p ?o .
    FILTER(isIRI(?o))
  } UNION {
    ?o ns:type.object.name %(lit)s .
    ?s ?p ?o .
    FILTER(isIRI(?s))
  }
}
LIMIT %(limit)d
"""


def _lit(name: str) -> str:
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"@en'


def _strip_ns(uri: str) -> str:
    if uri.startswith(FB):
        return uri[len(FB):]
    if uri.startswith("http://rdf.freebase.com/ns/"):
        return uri[len("http://rdf.freebase.com/ns/"):]
    return uri


class VirtuosoKG:
    """极简 Virtuoso SPARQL 客户端 + 邻域物化。"""

    def __init__(self, endpoint: str, timeout: int = 120, retries: int = 2) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.retries = retries

    # ------------------------------------------------------------------ 基础
    def sparql(self, query: str) -> List[Dict[str, str]]:
        data = urllib.parse.urlencode({"query": query, "format": "application/sparql-results+json"}).encode()
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(
                    self.endpoint, data=data,
                    headers={"Accept": "application/sparql-results+json",
                             "Content-Type": "application/x-www-form-urlencoded"},
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                rows = []
                for b in payload["results"]["bindings"]:
                    rows.append({k: v["value"] for k, v in b.items()})
                return rows
            except Exception as exc:  # pragma: no cover - 依赖外部服务
                last = exc
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"SPARQL 查询失败 {self.endpoint}: {last}")  # pragma: no cover

    def resolve_mid(self, name: str) -> str | None:
        q = (f"PREFIX ns: <http://rdf.freebase.com/ns/>\n"
             f"SELECT ?s WHERE {{ ?s ns:type.object.name {_lit(name)} }} LIMIT 1")
        rows = self.sparql(q)
        if not rows:
            return None
        return _strip_ns(rows[0]["s"])

    # ------------------------------------------------------------------ 邻域
    def neighbors_1hop(self, names: Sequence[str], limit: int = 5000) -> List[Triple]:
        out: List[Triple] = []
        seen: set[tuple[str, str, str]] = set()
        for name in names:
            q = NEIGHBOR_QUERY % {"lit": _lit(name), "limit": limit}
            for row in self.sparql(q):
                tr = Triple(_strip_ns(row["s"]), _strip_ns(row["p"]), _strip_ns(row["o"]))
                if tr.key() in seen:
                    continue
                seen.add(tr.key())
                out.append(tr)
        return out

    def neighbors_by_mid(self, mids: Sequence[str], limit: int = 5000) -> List[Triple]:
        """按 mid 取邻域（mid 更可靠，避免同名实体歧义）。"""
        out: List[Triple] = []
        seen: set[tuple[str, str, str]] = set()
        for mid in mids:
            q = (f"PREFIX ns: <http://rdf.freebase.com/ns/>\n"
                 f"SELECT DISTINCT ?s ?p ?o WHERE {{ {{ ns:{mid} ?p ?o . BIND(ns:{mid} AS ?s) }} "
                 f"UNION {{ ?s ?p ns:{mid} . BIND(ns:{mid} AS ?o) }} }} LIMIT {limit}")
            for row in self.sparql(q):
                tr = Triple(_strip_ns(row["s"]), _strip_ns(row["p"]), _strip_ns(row["o"]))
                if tr.key() in seen:
                    continue
                seen.add(tr.key())
                out.append(tr)
        return out

    def materialize_neighborhood(
        self,
        seeds: Sequence[str],
        hops: int,
        out_path: str | Path,
        limit_per_node: int = 5000,
        log=print,
    ) -> int:
        """把若干源实体的 ``hops`` 跳邻域物化成 TSV（head \\t relation \\t tail）。"""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        seen: set[tuple[str, str, str]] = set()
        frontier = list(dict.fromkeys(seeds))
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            for hop in range(hops):
                log(f"  hop {hop + 1}/{hops}: frontier={len(frontier)} 个实体")
                nxt: list[str] = []
                for name in frontier:
                    for tr in self.neighbors_1hop([name], limit=limit_per_node):
                        if tr.key() in seen:
                            continue
                        seen.add(tr.key())
                        fh.write(f"{tr.h}\t{tr.r}\t{tr.t}\n")
                        nxt.append(tr.t if tr.h == name else tr.h)
                frontier = list(dict.fromkeys(nxt))
                if not frontier:
                    break
        log(f"  物化完成：{len(seen):,} 条三元组 -> {out_path}")
        return len(seen)
