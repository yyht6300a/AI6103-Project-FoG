# -*- coding: utf-8 -*-
"""数据集加载器。

统一输出 :class:`fog.schema.QAExample`，屏蔽两个数据集在标注格式上的差异：

======================  ==============================  ==========================
字段                    WebQSP                          CWQ
======================  ==============================  ==========================
qid                     ``QuestionId``                  ``ID``
question                ``RawQuestion``                 ``question``
源实体                  ``Parses[].TopicEntityName``    **无**，需靠链接得到
答案                    ``Answers[].EntityName``        ``answers[].answer``
答案 mid                ``Answers[].AnswerArgument``    ``answers[].answer_id``
关系链来源              ``InferentialChain``            ``sparql`` 图模式
约束                    ``Constraints[]``               ``sparql`` 里的旁支三元组
======================  ==============================  ==========================

CWQ 官方 JSON 没有 ``topicEntityMid``，也没有 ``sexpr``（见
``reports/A_数据核查.md``），所以源实体必须由 :class:`fog.naming.EntityLinker`
在问题文本上做提及链接得到；S-expression 解析器仍保留在
:mod:`fog.gold.sexpr` 里，供拿到带 sexpr 的版本时切换。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..gold.sparql import SparqlPattern, candidate_chains, parse_sparql
from ..schema import GoldPath, GoldStep, QAExample, SourceEntity
from ..utils import read_json

# --------------------------------------------------------------------------- 通用


@dataclass
class DatasetSpec:
    name: str
    format: str          # 'webqsp' | 'cwq'
    files: Dict[str, str]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DatasetSpec":
        return cls(name=d["name"], format=d["format"], files=dict(d["files"]))


def _best_webqsp_parse(parses: Sequence[dict]) -> Optional[dict]:
    """优先取 ParseQuality == Complete 的解析。"""
    if not parses:
        return None
    for p in parses:
        quality = ((p.get("AnnotatorComment") or {}).get("ParseQuality") or "").lower()
        if quality == "complete":
            return p
    return parses[0]


def _answers_from_webqsp(parse: dict) -> tuple[List[str], List[str]]:
    names, mids = [], []
    for a in parse.get("Answers") or []:
        name = (a.get("EntityName") or "").strip()
        mid = (a.get("AnswerArgument") or "").strip()
        if a.get("AnswerType") not in (None, "Entity", "Value"):
            continue
        if name:
            names.append(name)
        elif mid:
            names.append(mid)
        if mid:
            mids.append(mid)
    # 去重保序
    return list(dict.fromkeys(names)), list(dict.fromkeys(mids))


def _answers_from_cwq(rec: dict) -> tuple[List[str], List[str]]:
    names, mids = [], []
    for a in rec.get("answers") or []:
        name = (a.get("answer") or "").strip()
        mid = (a.get("answer_id") or "").strip()
        if name:
            names.append(name)
        elif mid:
            names.append(mid)
        if mid:
            mids.append(mid)
    return list(dict.fromkeys(names)), list(dict.fromkeys(mids))


# --------------------------------------------------------------------------- WebQSP
def load_webqsp(path: str | Path, split: str) -> List[QAExample]:
    data = read_json(path)
    questions = data["Questions"] if isinstance(data, dict) else data
    out: List[QAExample] = []
    for q in questions:
        parse = _best_webqsp_parse(q.get("Parses") or [])
        if parse is None:
            continue
        qid = q.get("QuestionId") or parse.get("ParseId")
        question = (q.get("RawQuestion") or q.get("ProcessedQuestion") or "").strip()
        answers, answer_mids = _answers_from_webqsp(parse)

        chain_rels = list(parse.get("InferentialChain") or [])
        steps = [
            GoldStep(relation=r, head_mid=None, tail_mid=None, direction="fwd")
            for r in chain_rels
        ]
        gold = GoldPath(steps=steps, n_path_hops=len(steps))

        topic_mid = (parse.get("TopicEntityMid") or "").strip() or None
        topic_name = (parse.get("TopicEntityName") or "").strip() or (topic_mid or "")
        sources = [SourceEntity(name=topic_name, mid=topic_mid, method="annotation",
                                mention=parse.get("PotentialTopicEntityMention"))]

        pattern = parse_sparql(parse.get("Sparql") or "")
        out.append(QAExample(
            qid=qid, dataset="webqsp", split=split, question=question,
            answers=answers, answer_mids=answer_mids, source_entities=sources, gold=gold,
            extra={
                "parse_id": parse.get("ParseId"),
                "parse_quality": (parse.get("AnnotatorComment") or {}).get("ParseQuality"),
                "topic_mid": topic_mid,
                "inferential_chain": chain_rels,
                "constraints": parse.get("Constraints") or [],
                "sparql": parse.get("Sparql") or "",
                "sparql_pattern": pattern.to_dict(),
                "sparql_select": pattern.select_vars,
                "sparql_warnings": pattern.warnings,
            },
        ))
    return out


# --------------------------------------------------------------------------- CWQ
def load_cwq(path: str | Path, split: str, with_answers: bool = True) -> List[QAExample]:
    data = read_json(path)
    records = data["Questions"] if isinstance(data, dict) else data
    out: List[QAExample] = []
    for i, rec in enumerate(records):
        qid = rec.get("ID") or f"cwq-{split}-{i}"
        question = (rec.get("question") or rec.get("machine_question") or "").strip()
        answers, answer_mids = _answers_from_cwq(rec) if with_answers else ([], [])
        pattern = parse_sparql(rec.get("sparql") or "")
        out.append(QAExample(
            qid=qid, dataset="cwq", split=split, question=question,
            answers=answers, answer_mids=answer_mids, source_entities=[], gold=None,
            extra={
                "machine_question": rec.get("machine_question"),
                "webqsp_id": rec.get("webqsp_ID"),
                "webqsp_question": rec.get("webqsp_question"),
                "composition_answer": rec.get("composition_answer"),
                "compositionality_type": rec.get("compositionality_type"),
                "sparql": rec.get("sparql") or "",
                "sparql_pattern": pattern.to_dict(),
                "sparql_select": pattern.select_vars,
                "sparql_warnings": pattern.warnings,
                "has_sexpr": any(k in rec for k in ("sexpr", "Sexpr")),
            },
        ))
    return out


# --------------------------------------------------------------------------- 工厂
LOADERS = {
    "webqsp": load_webqsp,
    "cwq": load_cwq,
}


def load_split(spec: DatasetSpec, split: str, path: str | Path,
               with_answers: bool = True) -> List[QAExample]:
    if spec.format == "webqsp":
        return load_webqsp(path, split)
    if spec.format == "cwq":
        return load_cwq(path, split, with_answers=with_answers)
    raise ValueError(f"未知数据集格式: {spec.format}")


def get_pattern(example: QAExample) -> SparqlPattern:
    """从 ``extra`` 里还原 SPARQL 图模式（避免重复解析）。"""
    d = example.extra.get("sparql_pattern")
    if d:
        return SparqlPattern.from_dict(d)
    return parse_sparql(example.extra.get("sparql") or "")


def chains_for_example(example: QAExample) -> Dict[str, List[tuple]]:
    """该样本所有可用的 mid 常量 → 关系链候选。"""
    return candidate_chains(get_pattern(example))
