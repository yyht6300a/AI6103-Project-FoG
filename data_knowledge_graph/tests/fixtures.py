# -*- coding: utf-8 -*-
"""测试用的小型合成 KG 与样例问题。

刻意复刻论文 Figure 9/10 与 WebQSP 真实解析的结构：
一个 CVT 中间节点、一条 3 跳链、一条噪声分支、一个超高度数通用关系。
"""

from __future__ import annotations

from fog.schema import Triple

# (head, relation, tail)
TOY_TRIPLES = [
    # 1) Justin Bieber 的兄弟链（CVT 中转，2 跳）
    Triple("Justin Bieber", "people.person.sibling_s", "m.0cvt1"),
    Triple("m.0cvt1", "people.sibling_relationship.sibling", "Jaxon Bieber"),
    Triple("m.0cvt1", "people.sibling_relationship.relationship_type", "Sibling"),
    Triple("Jaxon Bieber", "people.person.gender", "Male"),
    # 2) 出生地链（2 跳，无 CVT）
    Triple("Justin Bieber", "people.person.place_of_birth", "London"),
    Triple("London", "location.location.containedby", "England"),
    # 3) 一条更长的链（3 跳），用来测 max_depth
    Triple("England", "location.location.containedby", "United Kingdom"),
    Triple("United Kingdom", "location.country.form_of_government", "Constitutional monarchy"),
    # 4) 噪声：同一个源实体的无关边
    Triple("Justin Bieber", "music.artist.genre", "Pop music"),
    Triple("Justin Bieber", "music.artist.album", "My World"),
    Triple("Pop music", "music.genre.subgenre", "Dance-pop"),
    # 5) 反方向可走：谁唱了 My World
    Triple("My World", "music.album.artist", "Justin Bieber"),
]

# 一个"超高度数通用关系"，用来验证决策点 7 的关系度数过滤
HIGH_DEGREE_REL = "common.topic.notable_for"
HIGH_DEGREE_TRIPLES = [Triple(f"Entity {i}", HIGH_DEGREE_REL, "Somewhere") for i in range(30)]

TOY_TRIPLES_ALL = TOY_TRIPLES + HIGH_DEGREE_TRIPLES

# WebQSP 官方 SPARQL 原文（WebQTrn-0）
WEBQSP_SPARQL = """PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?x
WHERE {
FILTER (?x != ns:m.06w2sn5)
FILTER (!isLiteral(?x) OR lang(?x) = '' OR langMatches(lang(?x), 'en'))
ns:m.06w2sn5 ns:people.person.sibling_s ?y .
?y ns:people.sibling_relationship.sibling ?x .
?x ns:people.person.gender ns:m.05zppz .
}
"""

# CWQ 官方 SPARQL 原文（含 CVT 与时间过滤）
CWQ_SPARQL = """PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?x
WHERE {
FILTER (?x != ?c)
FILTER (!isLiteral(?x) OR lang(?x) = '' OR langMatches(lang(?x), 'en'))
?c ns:location.country.administrative_divisions ns:m.010vz .
?c ns:government.governmental_jurisdiction.governing_officials ?y .
?y ns:government.government_position_held.office_holder ?x .
?y ns:government.government_position_held.basic_title ns:m.060c4 .
FILTER(NOT EXISTS {?y ns:government.government_position_held.from ?sk0} ||
EXISTS {?y ns:government.government_position_held.from ?sk1 .
FILTER(xsd:datetime(?sk1) <= "1980-12-31"^^xsd:dateTime) })
}
"""

# CWQ 官方 S-expression 的链条形态（用于 sexpr 解析器回归）
CWQ_SEXPR = {
    "type": "JOIN",
    "children": [
        {
            "type": "TERM",
            "function": "government.governmental_jurisdiction.governing_officials",
            "inputs": [{"type": "ARG", "value": "m.010vz"}],
        },
        {
            "type": "TERM",
            "function": "government.government_position_held.office_holder",
            "inputs": [{"type": "ARG", "value": "m.0cvt2"}],
        },
    ],
}
