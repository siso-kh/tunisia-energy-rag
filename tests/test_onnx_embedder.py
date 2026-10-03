"""Tests for the ONNX query embedder.

The live retrieval path must not import sentence-transformers: loading the
fp32 model through it costs ~830 MB resident and OOM-killed the deploy at
boot. These tests pin the pieces that decide whether that happens, without
needing the 235 MB model or a network round-trip.
"""

import numpy as np
import pytest

from src.rag import onnx_embedder


class FakeSession:
    """Stands in for an ONNX Runtime session.

    Returns per-token vectors whose magnitude grows with the token id, so a
    wrong pooling or a wrong mask is visible in the result.
    """

    def __init__(self, names=("input_ids", "attention_mask", "token_type_ids")):
        self._names = list(names)

    def get_inputs(self):
        return [type("I", (), {"name": n})() for n in self._names]

    def run(self, _outputs, feed):
        ids = feed["input_ids"]
        hidden = np.zeros(ids.shape + (4,), dtype=np.float32)
        for pos in range(ids.shape[1]):
            hidden[:, pos, :] = ids[:, pos][:, None].astype(np.float32)
        return [hidden]


class FakeTokenizer:
    """Mimics the real one: BatchLongest padding to a pad_id of 1."""

    def encode_batch(self, texts):
        rows = []
        for text in texts:
            ids = [1] + [min(len(t), 90) + 2 for t in text.split()] + [2]
            rows.append(ids[:8])
        width = max(len(r) for r in rows)
        encoded = []
        for ids in rows:
            padding = width - len(ids)
            encoded.append(type("E", (), {
                "ids": ids + [1] * padding,
                "attention_mask": [1] * len(ids) + [0] * padding,
            })())
        return encoded


def test_embed_texts_returns_normalised_vectors():
    vectors = onnx_embedder.embed_texts(
        ["hello world", "a"], session=FakeSession(), tokenizer=FakeTokenizer()
    )
    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector) == 4
        assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-5)


def test_padding_is_masked_out_of_the_mean():
    """A short text must not be diluted by the padding a longer one adds."""
    tokenizer = FakeTokenizer()
    short = "aa bb"
    long = "aa bb cc dd ee ff gg hh ii jj kk"

    alone = onnx_embedder.embed_texts(
        [short], session=FakeSession(), tokenizer=tokenizer
    )[0]
    batched = onnx_embedder.embed_texts(
        [short, long], session=FakeSession(), tokenizer=tokenizer
    )[0]

    assert batched == pytest.approx(alone, abs=1e-6), (
        "padding leaked into the mean pool"
    )


def test_missing_token_type_ids_are_supplied():
    """The BERT graph declares token_type_ids required; omit them and it errors."""
    session = FakeSession()
    tokenizer = FakeTokenizer()
    vectors = onnx_embedder.embed_texts(
        ["hi"], session=session, tokenizer=tokenizer
    )
    assert vectors, "token_type_ids should be synthesised as zeros"


def test_graph_without_token_type_ids_still_works():
    session = FakeSession(names=("input_ids", "attention_mask"))
    vectors = onnx_embedder.embed_texts(
        ["hi"], session=session, tokenizer=FakeTokenizer()
    )
    assert vectors


def test_unavailable_embedder_never_raises(monkeypatch):
    """Retrieval must degrade to the fp32 path, not fail the request."""
    monkeypatch.setattr(onnx_embedder, "_resolve_model_dir", lambda: None)
    monkeypatch.setattr(onnx_embedder, "_session", None)
    monkeypatch.setattr(onnx_embedder, "_load_failed", True)
    assert onnx_embedder.warm() is False
    with pytest.raises(RuntimeError):
        onnx_embedder.embed_query("anything")


def test_load_failure_is_not_retried_forever(monkeypatch):
    monkeypatch.setattr(onnx_embedder, "_resolve_model_dir", lambda: None)
    monkeypatch.setattr(onnx_embedder, "_session", None)
    monkeypatch.setattr(onnx_embedder, "_load_failed", False)
    assert onnx_embedder._load() is None
    assert onnx_embedder._load_failed is True


def test_truncation_length_comes_from_the_tokenizer(monkeypatch, tmp_path):
    (tmp_path / "tokenizer.json").write_text(
        '{"truncation": {"max_length": 77}}', encoding="utf-8"
    )
    assert onnx_embedder._max_length(tmp_path) == 77


def test_truncation_length_falls_back_when_absent(tmp_path):
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    assert onnx_embedder._max_length(tmp_path) == onnx_embedder.DEFAULT_MAX_LENGTH


def test_dense_query_prefers_onnx_and_never_loads_fp32(monkeypatch):
    """The regression that matters: no sentence-transformer on the query path.

    Chroma's ``query_texts`` embeds the query with the collection's persisted
    embedding function, which loads the fp32 model. The hybrid path must pass
    ``query_embeddings`` instead.
    """
    from src.rag import hybrid

    class Recorder:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            return {"ids": [["a", "b"]]}

    monkeypatch.setattr(onnx_embedder, "embedder_available", lambda: True)
    monkeypatch.setattr(onnx_embedder, "warm", lambda: True)
    monkeypatch.setattr(
        onnx_embedder, "embed_query", lambda q: [0.1, 0.2, 0.3, 0.4]
    )

    recorder = Recorder()
    hybrid._dense_query(recorder, "a question", n=5)

    (call,) = recorder.calls
    assert "query_embeddings" in call
    assert "query_texts" not in call, (
        "query_texts makes Chroma load the fp32 model (~830 MB) and OOM-kill "
        "the container"
    )
    assert call["n_results"] == 5


def test_dense_query_falls_back_to_fp32_without_onnx(monkeypatch):
    from src.rag import hybrid

    class Recorder:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            return {"ids": [["a"]]}

    monkeypatch.setattr(onnx_embedder, "embedder_available", lambda: False)

    recorder = Recorder()
    hybrid._dense_query(recorder, "a question", n=3)

    (call,) = recorder.calls
    assert call["query_texts"] == ["a question"]
    assert "query_embeddings" not in call