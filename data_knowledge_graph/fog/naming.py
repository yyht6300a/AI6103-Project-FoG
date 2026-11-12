# -*- coding: utf-8 -*-
"""实体命名：mid ↔ 英文名映射（决策点 2）、问题提及链接（决策点 1）。

两个子模块
----------
``MidNameResolver``
    维护 ``mid -> 英文名``。构建来源是**标注本身**（WebQSP 的
    ``TopicEntityName`` / ``Answers[].EntityName`` / ``Constraints[].EntityName``，
    CWQ 的 ``answers[].answer_id -> answers[].answer``），因此不需要全量
    ``type.object.name`` 就能覆盖绝大多数"链上出现过的有名字实体"。
    查不到时按决策点 2 的约定**回退为 mid**（与 RoG 序列化子图的口径一致）。

``EntityLinker``
    把问题文本对 KG 实体词表做最长匹配，产出候选提及。CWQ 官方 JSON 没有
    ``topicEntityMid``，必须靠链接得到源实体；它也用作 WebQSP 的回退。

两者都是"确定性 + 可复现"的：同一份输入永远给出同一结果，没有模型、没有随机数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .kg.tsv_index import TripleIndex
from .schema import SOURCE_FROM_ANNOTATION, SOURCE_FROM_FALLBACK, SOURCE_FROM_LINKING, SourceEntity
from .utils import read_json, write_json

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-\.&]*")


# --------------------------------------------------------------------------- mid -> name
class MidNameResolver:
    """mid -> 英文名；找不到时回退为 mid。"""

    def __init__(self, mapping: Optional[Dict[str, str]] = None) -> None:
        self._map: Dict[str, str] = dict(mapping or {})

    def __len__(self) -> int:
        return len(self._map)

    def __contains__(self, mid: str) -> bool:
        return mid in self._map

    def add(self, mid: Optional[str], name: Optional[str]) -> None:
        if not mid or not name:
            return
        mid = mid.strip()
        name = name.strip()
        if not mid or not name or mid == name:
            return
        # 已有映射不覆盖（先到的通常更可信：话题实体 > 答案 > 约束）
        self._map.setdefault(mid, name)

    def to_name(self, mid: Optional[str]) -> str:
        """名字空间字符串：有名字用名字，否则回退 mid。"""
        if not mid:
            return ""
        return self._map.get(mid, mid)

    def name_of(self, mid: str) -> Optional[str]:
        return self._map.get(mid)

    def as_dict(self) -> Dict[str, str]:
        return dict(self._map)

    def to_names(self, mids: Iterable[str]) -> List[str]:
        return [self.to_name(m) for m in mids]

    def save(self, path) -> None:
        write_json(path, dict(sorted(self._map.items())), indent=None)

    @classmethod
    def load(cls, path) -> "MidNameResolver":
        return cls(read_json(path))

    # -------------------------------------------------------------- 从标注构建
    @classmethod
    def from_webqsp(cls, questions: Sequence[dict], base: Optional["MidNameResolver"] = None
                    ) -> "MidNameResolver":
        r = base if base is not None else cls()
        for q in questions:
            for p in q.get("Parses") or []:
                r.add(p.get("TopicEntityMid"), p.get("TopicEntityName"))
                for a in p.get("Answers") or []:
                    r.add(a.get("AnswerArgument"), a.get("EntityName"))
                for c in p.get("Constraints") or []:
                    if c.get("ArgumentType") == "Entity":
                        r.add(c.get("Argument"), c.get("EntityName"))
        return r

    @classmethod
    def from_cwq(cls, records: Sequence[dict], base: Optional["MidNameResolver"] = None
                 ) -> "MidNameResolver":
        r = base if base is not None else cls()
        for rec in records:
            for a in rec.get("answers") or []:
                r.add(a.get("answer_id"), a.get("answer"))
                for alias in a.get("aliases") or []:
                    # aliases 只补名字，不覆盖主名
                    if a.get("answer_id") and alias and a.get("answer") is None:
                        r.add(a.get("answer_id"), alias)
        return r


# --------------------------------------------------------------------------- 提及链接
@dataclass
class Mention:
    text: str
    start: int
    end: int
    entity_id: int
    name: str

    def to_dict(self) -> Dict:
        return {"text": self.text, "start": self.start, "end": self.end,
                "entity_id": self.entity_id, "name": self.name}


class EntityLinker:
    """在 KG 实体词表上做最长匹配实体链接（无模型、确定性）。"""

    MAX_SPAN = 6  # 最多 6 个 token 的提及

    def __init__(self, kg: TripleIndex, min_chars: int = 2) -> None:
        self.kg = kg
        self.min_chars = min_chars
        self._ci: Dict[str, int] = kg.ent_map_ci
        # 只保留"看起来像实体名"的键，避免 mid 参与链接
        self._skip_mid_keys = True

    # ------------------------------------------------------------------
    def _token_spans(self, text: str) -> List[Tuple[int, int, str]]:
        toks = [(m.start(), m.end(), m.group(0)) for m in _TOKEN_RE.finditer(text)]
        spans: List[Tuple[int, int, str]] = []
        n = len(toks)
        for i in range(n):
            for k in range(1, self.MAX_SPAN + 1):
                j = i + k
                if j > n:
                    break
                start, end = toks[i][0], toks[j - 1][1]
                span = text[start:end]
                if len(span) < self.min_chars:
                    continue
                spans.append((start, end, span))
        return spans

    def link(self, question: str, max_mentions: int = 12) -> List[Mention]:
        """返回按长度降序、互不重叠的候选提及。"""
        cands: List[Mention] = []
        seen_ids: set[int] = set()
        for start, end, span in self._token_spans(question):
            eid = self._ci.get(span.casefold())
            if eid is None and span.casefold().endswith("'s"):
                # 所有格：'s 不属于实体名本身（"justin bieber's brother"）
                eid = self._ci.get(span[:-2].casefold())
            if eid is None:
                continue
            name = self.kg.entities[eid]
            if self._skip_mid_keys and (name.startswith("m.") or name.startswith("g.")) and name == span:
                continue
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            cands.append(Mention(text=span, start=start, end=end, entity_id=eid, name=name))

        # 长提及优先，重叠丢弃
        cands.sort(key=lambda m: (-(m.end - m.start), m.start))
        chosen: List[Mention] = []
        used: List[Tuple[int, int]] = []
        for m in cands:
            if any(not (m.end <= s or m.start >= e) for s, e in used):
                continue
            used.append((m.start, m.end))
            chosen.append(m)
            if len(chosen) >= max_mentions:
                break
        return chosen

    def link_names(self, question: str, max_mentions: int = 12) -> List[str]:
        return [m.name for m in self.link(question, max_mentions)]


# --------------------------------------------------------------------------- 源实体
def make_source_entity(
    name: str,
    kg: TripleIndex,
    mid: Optional[str] = None,
    method: str = SOURCE_FROM_ANNOTATION,
    mention: Optional[str] = None,
) -> SourceEntity:
    """构造 :class:`SourceEntity` 并记录它是否真的存在于 KG 中。"""
    eid = kg.resolve_entity(name) if name else None
    return SourceEntity(
        name=name or (mid or ""),
        mid=mid,
        kg_id=eid,
        method=method,
        mention=mention,
        in_kg=eid is not None,
    )


def fallback_source_entity(mid: Optional[str], mid2name: MidNameResolver,
                           kg: TripleIndex) -> SourceEntity:
    """最后兜底：用 mid 当名字（决策点 2 的回退策略）。"""
    name = mid2name.to_name(mid) if mid else ""
    eid = kg.resolve_entity(name) if name else None
    is_mid_string = bool(mid) and name == mid
    return SourceEntity(
        name=name,
        mid=mid,
        kg_id=eid,
        method=SOURCE_FROM_FALLBACK if is_mid_string else SOURCE_FROM_LINKING,
        mention=None,
        in_kg=eid is not None,
    )

