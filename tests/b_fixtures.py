"""Shared fixtures for member B's tests: a tiny KG with CVT nodes and a fake text encoder."""

from __future__ import annotations

import hashlib

import numpy as np

from src.model.embedding import EmbeddingStore, is_mid
from src.model.kg_bridge import ensure_fog_importable

ensure_fog_importable()
from fog.kg.tsv_index import index_from_triples  # noqa: E402
from fog.schema import Triple  # noqa: E402

SIB_S = "people.person.sibling_s"
SIB = "people.sibling_relationship.sibling"
ADJ_S = "location.location.adjoin_s"
ADJ = "location.adjoining_relationship.adjoins"
AWARD = "award.award_winner.awards_won"
NOTABLE = "common.topic.notable_types"


def tiny_triples():
    rows = [
        ("Justin Bieber", SIB_S, "m.cvt1"), ("m.cvt1", SIB, "Jaxon Bieber"),
        ("Justin Bieber", SIB_S, "m.cvt2"), ("m.cvt2", SIB, "Jazmyn Bieber"),
        ("Justin Bieber", "music.artist.genre", "Pop music"),
        ("Justin Bieber", NOTABLE, "Musical Artist"),
        ("Justin Bieber", "people.person.place_of_birth", "London"),
        ("United States of America", ADJ_S, "m.adj1"), ("m.adj1", ADJ, "Canada"),
        ("United States of America", ADJ_S, "m.adj2"), ("m.adj2", ADJ, "Mexico"),
        ("United States of America", ADJ_S, "m.adj3"), ("m.adj3", ADJ, "Cuba"),
        ("Canada", NOTABLE, "Country"),
        ("United States of America", "location.country.capital", "Washington, D.C."),
    ]
    rows += [("Justin Bieber", AWARD, f"m.aw{i}") for i in range(30)]
    return [Triple(h, r, t) for h, r, t in rows]


def tiny_kg():
    return index_from_triples(tiny_triples())


def bieber_record(kg):
    return {
        "qid": "T-0", "question": "what is the name of justin bieber brother?",
        "answers": ["Jaxon Bieber"],
        "source_entities": [{"name": "Justin Bieber", "kg_id": kg.entity_id("Justin Bieber")}],
        "gold": {"matched": True, "triples": [["Justin Bieber", SIB_S, "m.cvt1"],
                                               ["m.cvt1", SIB, "Jaxon Bieber"]]},
    }


def usa_record(kg):
    return {
        "qid": "T-1", "question": "what countries border the usa?",
        "answers": ["Canada", "Mexico"],
        "source_entities": [{"name": "United States of America",
                             "kg_id": kg.entity_id("United States of America")}],
        "gold": {"matched": True, "triples": [["United States of America", ADJ_S, "m.adj1"],
                                               ["m.adj1", ADJ, "Canada"],
                                               ["Canada", NOTABLE, "Country"]]},
    }


class FakeEncoder:
    """Deterministic stand-in for QwenTextEncoder (hash-seeded unit vectors)."""

    model_name = "fake"
    truncate_dim = None
    instruction = "fake instruction"

    def __init__(self, dim: int = 16) -> None:
        self.dim = dim
        self.calls = 0
        self.n_texts = 0

    def encode(self, texts):
        self.calls += 1
        self.n_texts += len(texts)
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            seed = int.from_bytes(hashlib.sha256(t.encode("utf-8")).digest()[:8], "little")
            v = np.random.default_rng(seed).standard_normal(self.dim)
            out[i] = v / np.linalg.norm(v)
        return out

    def encode_queries(self, queries):
        return self.encode([f"Q:{q}" for q in queries])


def fake_store(kg, dim: int = 16, query_encoder=None) -> EmbeddingStore:
    enc = FakeEncoder(dim)
    mids = np.array([is_mid(x) for x in kg.entities], dtype=bool)
    ent = enc.encode(kg.entities).astype(np.float16)
    ent[mids] = 0
    rel = enc.encode(kg.relations).astype(np.float16)
    return EmbeddingStore(ent, mids, rel, query_encoder=query_encoder or FakeEncoder(dim))
