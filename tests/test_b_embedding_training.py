import copy
import math
import random
import gzip
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
import torch

from src.model.embedding import (EmbeddingStore, build_entity_cache, build_query_cache,
                                 build_relation_cache, format_query, is_mid, relation_text,
                                 write_cache_meta)
from src.model.sampler import TrainingRegionBuilder
from src.model.scorer import ForesightTripleScorer, ScorerConfig
from src.model.training import (TrainConfig, collate, evaluate, question_bce_loss, roc_auc,
                                threshold_metrics, train, split_qa_records)
from scripts.train_scorer import load_training_records, build_examples
from b_fixtures import FakeEncoder, bieber_record, fake_store, tiny_kg, usa_record


class TextFormatTest(unittest.TestCase):
    def test_text_helpers(self):
        self.assertTrue(is_mid("m.0gxnnwp"))
        self.assertTrue(is_mid("g.11b6ddk8_7"))
        self.assertFalse(is_mid("Justin Bieber"))
        self.assertFalse(is_mid("m.a b"))
        self.assertEqual(relation_text("people.person.sibling_s"), "people person sibling s")
        self.assertEqual(format_query("who?", "task"), "Instruct: task\nQuery:who?")


class CacheTest(unittest.TestCase):
    def test_build_and_load_roundtrip(self):
        names = ["Justin Bieber", "m.cvt1", "Jaxon Bieber", "g.abc"]
        rels = ["people.person.sibling_s", "people.sibling_relationship.sibling"]
        enc = FakeEncoder(8)
        with tempfile.TemporaryDirectory() as d:
            build_entity_cache(names, enc, d, chunk_size=1, log=lambda _m: None)
            build_relation_cache(rels, enc, d)
            build_query_cache(["q1", "q2", "q1"], enc, d)
            self.assertEqual(build_query_cache(["q2", "q3"], enc, d), 1)   # q2 already cached
            write_cache_meta(d, enc, len(names), len(rels))
            store = EmbeddingStore.load(d)
            self.assertEqual(store.dim, 8)
            self.assertEqual(store.meta["n_entities"], 4)
            ent = store.entity([0, 1, 2, 3])
            self.assertTrue(torch.all(ent[[1, 3]] == 0))
            self.assertTrue(torch.allclose(ent[0], torch.from_numpy(enc.encode(["Justin Bieber"])[0]), atol=1e-3))
            self.assertEqual(store.entity_is_mid([0, 1]).tolist(), [False, True])
            self.assertTrue(torch.allclose(store.relation([0])[0],
                                           torch.from_numpy(enc.encode(["people person sibling s"])[0]), atol=1e-3))
            q = store.encode_questions(["q3", "q1"])
            self.assertTrue(torch.allclose(q[1], torch.from_numpy(enc.encode_queries(["q1"])[0]), atol=1e-3))
            with self.assertRaises(KeyError):
                store.encode_questions(["not cached"])
            self.assertEqual(json.loads((Path(d) / "queries.json").read_text()), ["q1", "q2", "q3"])

    def test_entity_cache_resumes(self):
        names = [f"e{i}" for i in range(10)]
        with tempfile.TemporaryDirectory() as d:
            enc = FakeEncoder(4)
            build_entity_cache(names, enc, d, chunk_size=3, log=lambda _m: None)
            enc2 = FakeEncoder(4)
            build_entity_cache(names, enc2, d, chunk_size=3, log=lambda _m: None)
            self.assertEqual(enc2.n_texts, 0)        # everything was already done
            full = np.load(Path(d) / "entity_emb.npy")
            self.assertTrue(np.allclose(full, enc.encode(names), atol=1e-3))

    def test_resume_rejects_changed_index_and_encoder(self):
        with tempfile.TemporaryDirectory() as d:
            enc = FakeEncoder(8)
            build_entity_cache(["a", "b"], enc, d, log=lambda _: None)
            before = (Path(d) / "entity_emb.npy").read_bytes()
            for names, encoder in [(["b", "a"], enc), (["a", "b"], FakeEncoder(4))]:
                with self.assertRaisesRegex(ValueError, "rebuild"):
                    build_entity_cache(names, encoder, d)
            enc2 = FakeEncoder(8)
            enc2.model_name = "other-model"
            with self.assertRaises(ValueError):
                build_entity_cache(["a", "b"], enc2, d)
            enc2 = FakeEncoder(8)
            enc2.max_length = 128
            with self.assertRaises(ValueError):
                build_entity_cache(["a", "b"], enc2, d)
            self.assertEqual(before, (Path(d) / "entity_emb.npy").read_bytes())
            build_relation_cache(["r1", "r2"], enc, d)
            with self.assertRaises(ValueError):
                build_relation_cache(["r2", "r1"], enc, d)
            build_query_cache(["q"], enc, d)
            with self.assertRaises(ValueError):
                build_query_cache(["q"], enc2, d)
            state = Path(d) / "entity_progress.json"
            state.write_text(json.dumps({"n_entities": 2, "dim": 8, "done": 2}))
            with self.assertRaisesRegex(ValueError, "rebuild"):
                build_entity_cache(["a", "b"], enc, d)

    def test_interrupted_resume(self):
        with tempfile.TemporaryDirectory() as d:
            class InterruptEncoder(FakeEncoder):
                def encode(self, texts):
                    if self.calls == 1:
                        raise RuntimeError("interrupted")
                    return super().encode(texts)
            with self.assertRaises(RuntimeError):
                build_entity_cache(["a", "b", "c"], InterruptEncoder(8), d,
                                   chunk_size=1, log=lambda _: None)
            enc = FakeEncoder(8)
            build_entity_cache(["a", "b", "c"], enc, d, chunk_size=1, log=lambda _: None)
            self.assertEqual(enc.n_texts, 2)

    def test_online_query_encoder(self):
        kg = tiny_kg()
        store = fake_store(kg, dim=8, query_encoder=FakeEncoder(8))
        a = store.encode_questions(["x", "y", "x"])
        self.assertTrue(torch.allclose(a[0], a[2]))
        self.assertTrue(store.has_query("y"))


class ScoreDistributionTest(unittest.TestCase):
    def test_mid_flags_align_across_microbatches(self):
        from scripts.analyze_score_distribution import predict, mid_group_stats
        kg = tiny_kg()
        store = fake_store(kg, dim=8)
        builder = TrainingRegionBuilder(kg)
        examples = [builder.build(bieber_record(kg)), builder.build(usa_record(kg))]
        model = ForesightTripleScorer(ScorerConfig(emb_dim=8, num_heads=2, dropout=0.0))
        original = predict(model, examples, store, torch.device("cpu"), 3)
        labels, probs, loss, flags = predict(model, examples, store, torch.device("cpu"), 3,
                                             group_by_mid=True)
        self.assertEqual(len(original), 3)
        np.testing.assert_array_equal(labels, original[0])
        np.testing.assert_array_equal(probs, original[1])
        self.assertEqual(loss, original[2])
        np.testing.assert_array_equal(labels, np.concatenate([ex.labels for ex in examples]))
        heads = np.concatenate([ex.head_ids for ex in examples])
        tails = np.concatenate([ex.tail_ids for ex in examples])
        expected = store.entity_is_mid(heads).numpy() | store.entity_is_mid(tails).numpy()
        np.testing.assert_array_equal(flags, expected)
        self.assertEqual(flags.dtype, np.bool_)
        self.assertTrue(flags.any() and (~flags).any())
        self.assertFalse((flags & ~flags).any())
        self.assertTrue((flags | ~flags).all())
        groups = mid_group_stats(labels, probs, flags)
        self.assertEqual(sum(g["n_triples"] for g in groups.values()), len(labels))
        self.assertEqual(sum(g["n_positive"] for g in groups.values()), int(labels.sum()))
        self.assertEqual(groups["contains_mid"]["n_triples"], int(expected.sum()))
        for group in groups.values():
            self.assertEqual(group["n_positive"] + group["n_negative"], group["n_triples"])
            self.assertEqual(group["positive"]["count"], group["n_positive"])
            self.assertEqual(group["negative"]["count"], group["n_negative"])

    def test_mid_group_statistics_and_empty_group(self):
        from scripts.analyze_score_distribution import mid_group_stats
        labels = np.array([1, 0, 1, 0, 1, 0])
        probs = np.array([0.9, 0.8, 0.2, 0.05, 0.65, 0.2])
        groups = mid_group_stats(labels, probs, np.array([False, False, True, True, True, True]))
        named, mid = groups["named_only"], groups["contains_mid"]
        self.assertEqual((named["n_triples"], mid["n_triples"]), (2, 4))
        self.assertEqual((mid["n_positive"], mid["n_negative"]), (2, 2))
        self.assertEqual(mid["positive_ratio"], 0.5)
        self.assertEqual(named["auc"], 1.0)
        self.assertEqual(named["tau_p_recall"], 1.0)
        self.assertEqual(mid["tau_e_recall"], 1.0)
        self.assertEqual(mid["tau_p_recall"], 0.0)
        self.assertAlmostEqual(mid["positive"]["mean"], 0.425)
        self.assertAlmostEqual(mid["negative"]["median"], 0.125)
        empty = mid_group_stats(labels, probs, np.ones(len(labels), dtype=bool))["named_only"]
        self.assertEqual(empty["n_triples"], 0)
        self.assertTrue(np.isnan(empty["auc"]))
        self.assertTrue(np.isnan(empty["tau_e_recall"]))
        self.assertIsNone(empty["positive"]["mean"])

    def test_mid_cli_is_opt_in_and_preserves_global_summary(self):
        from scripts import analyze_score_distribution as analysis
        kg = tiny_kg()
        store = fake_store(kg, dim=8)
        builder = TrainingRegionBuilder(kg)
        examples = [builder.build(bieber_record(kg)), builder.build(usa_record(kg))]
        model = ForesightTripleScorer(ScorerConfig(emb_dim=8, num_heads=2, dropout=0.0))
        metrics = evaluate(model, examples, store, torch.device("cpu"), batch_questions=1,
                           amp=False, max_triples_per_forward=3)
        reference = {f"eval_original_{key}": value for key, value in metrics.items()}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "epoch1.pt"
            model.save(checkpoint)
            checkpoint_bytes = checkpoint.read_bytes()
            argv = ["analyze_score_distribution.py", "--dataset", "cwq", "--checkpoint", str(checkpoint),
                    "--cache", "unused", "--out-dir", str(root), "--device", "cpu",
                    "--max-triples-per-forward", "3"]
            summaries, histograms = [], []
            for options in ([], ["--group-by-mid"]):
                with patch.object(analysis, "validation_examples", return_value=(
                        examples, store, reference, builder.config, "train_qid_holdout_10pct", 1)), \
                     patch("sys.argv", argv + options), patch("builtins.print") as output:
                    analysis.main()
                summaries.append(json.loads((root / "summary.json").read_text()))
                histograms.append((root / "histogram.csv").read_bytes())
                if options:
                    self.assertTrue(any("contains_mid" in str(call) for call in output.call_args_list))
            self.assertNotIn("groups", summaries[0])
            grouped = summaries[1].pop("groups")
            self.assertEqual(set(grouped), {"named_only", "contains_mid"})
            self.assertEqual(sum(g["n_triples"] for g in grouped.values()), summaries[1]["n_triples"])
            self.assertEqual(summaries[0], summaries[1])
            self.assertEqual(histograms[0], histograms[1])
            self.assertTrue(all(v["close"] for v in summaries[1]["epoch_comparison"].values()))
            self.assertEqual(checkpoint_bytes, checkpoint.read_bytes())


class TrainingTest(unittest.TestCase):
    def test_loss_is_sum_per_question_then_mean(self):
        logits = torch.tensor([0.0, 2.0, -1.0])
        labels = torch.tensor([1.0, 0.0, 1.0])
        q_index = torch.tensor([0, 0, 1])
        bce = torch.nn.functional.binary_cross_entropy_with_logits(logits, labels, reduction="none")
        expected = ((bce[0] + bce[1]) + bce[2]) / 2
        self.assertAlmostEqual(question_bce_loss(logits, labels, q_index, 2).item(), expected.item(), places=6)

    def test_auc_and_threshold_metrics(self):
        labels = np.array([1, 0, 1, 0, 0])
        scores = np.array([0.9, 0.8, 0.4, 0.4, 0.1])
        pairs = [(p, n) for p in scores[labels == 1] for n in scores[labels == 0]]
        manual = np.mean([1.0 if p > n else 0.5 if p == n else 0.0 for p, n in pairs])
        self.assertAlmostEqual(roc_auc(labels, scores), manual)
        self.assertTrue(np.isnan(roc_auc(np.ones(3), np.zeros(3))))
        m = threshold_metrics(labels, scores, 0.5)
        self.assertAlmostEqual(m["recall"], 0.5)
        self.assertAlmostEqual(m["kept_frac"], 0.4)

    def test_qid_split_and_dev_selection(self):
        rows = [{"qid": str(i)} for i in range(100)]
        a, b = split_qa_records(rows + rows[:3], seed=7)
        a2, b2 = split_qa_records(list(reversed(rows + rows[:3])), seed=7)
        self.assertEqual({r["qid"] for r in b}, {r["qid"] for r in b2})
        self.assertEqual(len({r["qid"] for r in b}), 10)
        self.assertTrue({r["qid"] for r in a}.isdisjoint(r["qid"] for r in b))
        with self.assertRaises(ValueError):
            split_qa_records(rows, rows[:1])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "webqsp"
            root.mkdir()
            def write(split, records):
                with gzip.open(root / f"qa_{split}.jsonl.gz", "wt") as fh:
                    for rec in records:
                        fh.write(json.dumps(rec) + "\n")
            write("train", rows)
            write("test", [{"qid": "test"}])
            self.assertEqual(load_training_records(d, "webqsp")[2], "train_qid_holdout_10pct")
            write("val", [{"qid": "val"}])
            self.assertEqual(load_training_records(d, "webqsp")[2], "val")
            write("dev", [{"qid": "dev"}])
            self.assertEqual(load_training_records(d, "webqsp")[2], "dev")
            self.assertEqual(load_training_records(d, "webqsp", eval_split="test")[2], "test")

    def test_state_provider_gradients(self):
        kg = tiny_kg()
        store = fake_store(kg, dim=8)
        ex = TrainingRegionBuilder(kg).build(bieber_record(kg), sub_questions=["Who is related?"])
        model = ForesightTripleScorer(ScorerConfig(emb_dim=8, num_heads=2, dropout=0.0))
        # Fixture tensors only: no synthetic states are used by production training.
        hs = torch.nn.Parameter(torch.ones(len(ex.labels), 8))
        ho = torch.nn.Parameter(torch.full_like(hs, 0.5))
        cfg = TrainConfig(epochs=1, amp=False)
        old = hs.detach().clone()
        train(model, [ex], store, torch.device("cpu"), cfg, log=lambda _: None,
              state_provider=lambda examples, batch: (hs, ho), extra_parameters=[hs, ho])
        for grad in (model.f_attn.weight.grad[:, 8:16], model.f_attn.weight.grad[:, 16:],
                     model.mlp_s[0].weight.grad[:, 8:16], model.mlp_s[0].weight.grad[:, 16:24],
                     hs.grad, ho.grad):
            self.assertTrue(torch.isfinite(grad).all())
            self.assertGreater(grad.abs().sum().item(), 0)
        self.assertFalse(torch.equal(old, hs))

    def test_cli_pipeline_with_fake_encoder(self):
        from scripts import build_embedding_cache as cache_cli, train_scorer as train_cli
        kg = tiny_kg()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "webqsp").mkdir()
            with gzip.open(root / "webqsp/qa_train.jsonl.gz", "wt") as fh:
                for i in range(20):
                    rec = bieber_record(kg) if i % 2 else usa_record(kg)
                    rec["qid"] = f"cli-{i}"
                    fh.write(json.dumps(rec) + "\n")
            def encoder(*args, **kwargs):
                enc = FakeEncoder(16)
                enc.device = "cpu"
                return enc
            with patch.object(cache_cli, "load_kg", return_value=kg), \
                 patch.object(cache_cli, "QwenTextEncoder", side_effect=encoder), \
                 patch("sys.argv", ["build_embedding_cache.py", "--out", str(root / "cache"),
                                    "--processed-root", d, "--datasets", "webqsp"]):
                cache_cli.main()
            with patch.object(train_cli, "load_kg", return_value=kg), \
                 patch("sys.argv", ["train_scorer.py", "--dataset", "webqsp",
                                    "--cache", str(root / "cache"), "--out-dir", str(root / "out"),
                                    "--processed-root", d, "--epochs", "1", "--device", "cpu", "--no-amp",
                                    "--max-triples-per-forward", "5"]):
                train_cli.main()
            split = json.loads((root / "out/split.json").read_text())
            self.assertTrue(set(split["train_qids"]).isdisjoint(split["eval_qids"]))
            self.assertEqual(len(split["eval_qids"]), 2)
            self.assertTrue((root / "out/epoch1.pt").exists())
            queries = json.loads((root / "cache/queries.json").read_text())
            self.assertEqual(set(queries), {bieber_record(kg)["question"], usa_record(kg)["question"]})

    def test_disk_cache_split_train_checkpoint_smoke(self):
        kg = tiny_kg()
        enc = FakeEncoder(16)
        builder = TrainingRegionBuilder(kg)
        records = []
        for i in range(20):
            rec = bieber_record(kg) if i % 2 else usa_record(kg)
            rec["qid"] = f"smoke-{i}"
            records.append(rec)
        train_rows, val_rows = split_qa_records(records)
        tr, va = build_examples(builder, train_rows), build_examples(builder, val_rows)
        with tempfile.TemporaryDirectory() as d:
            build_entity_cache(kg.entities, enc, d, log=lambda _: None)
            build_relation_cache(kg.relations, enc, d)
            build_query_cache([q for r in records for q in builder.sub_questions_for(r)], enc, d)
            write_cache_meta(d, enc, kg.n_entities, kg.n_relations)
            store = EmbeddingStore.load(d)
            store.validate_index(kg)
            model = ForesightTripleScorer(ScorerConfig(emb_dim=16, num_heads=2, dropout=0.0))
            history = train(model, tr, store, torch.device("cpu"),
                            TrainConfig(epochs=1, amp=False), va, log=lambda _: None)
            self.assertIn("eval_original_tau_e_recall", history[0])
            self.assertIn("eval_original_tau_p_recall", history[0])
            self.assertTrue(np.isfinite(history[0]["eval_original_auc"]))
            model.save(Path(d) / "scorer.pt")
            loaded = ForesightTripleScorer.load(Path(d) / "scorer.pt")
            loaded.eval()
            batch, qs, mask, _ = collate(va, store)
            with torch.no_grad():
                self.assertTrue(torch.equal(model(batch, qs, mask), loaded(batch, qs, mask)))
            kg.entities = list(reversed(kg.entities))
            with self.assertRaises(ValueError):
                store.validate_index(kg)

    def test_microbatch_loss_gradients_and_single_optimizer_step(self):
        kg = tiny_kg()
        store = fake_store(kg, dim=8)
        builder = TrainingRegionBuilder(kg)
        examples = [builder.build(rec, sub_questions=["An independent question?"])
                    for rec in (bieber_record(kg), usa_record(kg))]
        torch.manual_seed(17)
        reference = ForesightTripleScorer(ScorerConfig(emb_dim=8, num_heads=2, dropout=0.0))
        chunked = copy.deepcopy(reference)
        reference_states = torch.nn.Embedding(kg.n_entities, 8)
        chunked_states = copy.deepcopy(reference_states)

        def states(module, records, batch):
            self.assertEqual(sum(len(ex.labels) for ex in records), batch.n)
            heads = torch.as_tensor(np.concatenate([ex.head_ids for ex in records]))
            tails = torch.as_tensor(np.concatenate([ex.tail_ids for ex in records]))
            return module(heads), module(tails)

        batch, qs, mask, labels = collate(examples, store)
        hs, ho = states(reference_states, examples, batch)
        logits = reference(batch, qs, mask, h_s=hs, h_o=ho)
        expected = question_bce_loss(logits, labels, batch.q_index, len(examples))
        expected.backward()
        calls, steps = [], []
        handle = chunked.register_forward_pre_hook(lambda model, args: calls.append(args[0].n))
        with patch.object(torch.optim.AdamW, "step", autospec=True,
                          side_effect=lambda optimizer: steps.append(len(calls))):
            history = train(chunked, examples, store, torch.device("cpu"),
                            TrainConfig(epochs=1, batch_questions=2, amp=False,
                                        max_triples_per_forward=5), log=lambda _: None,
                            state_provider=lambda records, batch: states(chunked_states, records, batch),
                            extra_parameters=chunked_states.parameters())
        handle.remove()
        n = sum(len(ex.labels) for ex in examples)
        self.assertEqual(steps, [math.ceil(n / 5)])
        self.assertEqual(sum(calls), n)
        self.assertLessEqual(max(calls), 5)
        self.assertAlmostEqual(history[0]["train_loss"], expected.item(), places=5)
        for (name, ref), (_, actual) in zip(reference.named_parameters(), chunked.named_parameters()):
            torch.testing.assert_close(actual.grad, ref.grad, rtol=2e-5, atol=2e-6, msg=name)
        torch.testing.assert_close(chunked_states.weight.grad, reference_states.weight.grad,
                                   rtol=2e-5, atol=2e-6)

    def test_microbatch_single_question_and_final_question_batch(self):
        kg = tiny_kg()
        store = fake_store(kg, dim=8)
        builder = TrainingRegionBuilder(kg)
        examples = [builder.build(bieber_record(kg)), builder.build(usa_record(kg)),
                    builder.build(bieber_record(kg))]
        model = ForesightTripleScorer(ScorerConfig(emb_dim=8, num_heads=2, dropout=0.0))
        calls, steps = [], []
        handle = model.register_forward_pre_hook(lambda model, args: calls.append(args[0].n))
        with patch.object(torch.optim.AdamW, "step", autospec=True,
                          side_effect=lambda optimizer: steps.append(len(calls))):
            train(model, examples, store, torch.device("cpu"),
                  TrainConfig(epochs=1, batch_questions=2, max_triples_per_forward=5, amp=False),
                  log=lambda _: None)
        handle.remove()
        order = list(range(len(examples)))
        random.Random(0).shuffle(order)
        first = math.ceil(sum(len(examples[i].labels) for i in order[:2]) / 5)
        last = math.ceil(len(examples[order[-1]].labels) / 5)
        self.assertEqual(steps, [first, first + last])
        self.assertEqual(sum(calls), sum(len(ex.labels) for ex in examples))
        self.assertLessEqual(max(calls), 5)

    def test_microbatch_evaluation_matches_unsplit(self):
        kg = tiny_kg()
        store = fake_store(kg, dim=8)
        builder = TrainingRegionBuilder(kg)
        examples = [builder.build(bieber_record(kg)), builder.build(usa_record(kg)),
                    builder.build(bieber_record(kg))]
        model = ForesightTripleScorer(ScorerConfig(emb_dim=8, num_heads=2, dropout=0.0))
        device = torch.device("cpu")
        full = evaluate(model, examples, store, device, batch_questions=2,
                        max_triples_per_forward=10000, amp=False)
        calls = []
        handle = model.register_forward_pre_hook(lambda model, args: calls.append(args[0].n))
        chunked = evaluate(model, examples, store, device, batch_questions=2,
                           max_triples_per_forward=5, amp=False)
        handle.remove()
        self.assertEqual(full.keys(), chunked.keys())
        for name in full:
            self.assertAlmostEqual(full[name], chunked[name], places=5, msg=name)
        self.assertLessEqual(max(calls), 5)
        self.assertEqual(sum(calls), sum(len(ex.labels) for ex in examples))
        # Independent reference for the original per-question summed BCE.
        batch, qs, mask, labels = collate(examples, store)
        with torch.no_grad():
            expected = question_bce_loss(model(batch, qs, mask), labels, batch.q_index, len(examples))
        self.assertAlmostEqual(chunked["loss"], expected.item(), places=5)

    def test_collate_and_a_few_training_steps(self):
        kg = tiny_kg()
        store = fake_store(kg, dim=16)
        builder = TrainingRegionBuilder(kg)
        examples = [builder.build(bieber_record(kg)), builder.build(usa_record(kg))]
        batch, q_sub, mask, labels = collate(examples, store)
        self.assertEqual(batch.n, sum(len(ex.labels) for ex in examples))
        self.assertEqual(mask.sum(1).tolist(), [1, 1])   # original questions only
        self.assertTrue(batch.head_is_mid.any() or batch.tail_is_mid.any())

        torch.manual_seed(0)
        model = ForesightTripleScorer(ScorerConfig(emb_dim=16, num_heads=2, dropout=0.0))
        cfg = TrainConfig(epochs=30, lr=3e-3, batch_questions=2, amp=False)
        before = evaluate(model, examples, store, torch.device("cpu"), amp=False)
        history = train(model, examples, store, torch.device("cpu"), cfg, log=lambda _m: None)
        after = evaluate(model, examples, store, torch.device("cpu"), amp=False)
        self.assertEqual(len(history), 30)
        self.assertLess(after["loss"], before["loss"])
        self.assertGreater(after["auc"], 0.9)


if __name__ == "__main__":
    unittest.main()
