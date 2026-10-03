"""Query embedding via ONNX Runtime, for containers that cannot fit torch.

Loading ``paraphrase-multilingual-MiniLM-L12-v2`` the normal way costs ~830 MB
resident (measured: 188 MB importing torch, ~555 MB of fp32 weights, past
800 MB after one encode). A 512 MB container is OOM-killed while doing it --
which is what killed the deploy: the container died at boot and every request
answered 502 with no error frame, because the process was gone before the
pipeline could report anything.

The upstream model repository publishes ONNX exports of the same weights,
including a dynamically int8-quantized build (~118 MB). ONNX Runtime loads
those in a fraction of the memory and returns vectors in the same space, so:

  * the persisted Chroma index stays valid -- **do not re-index**;
  * stored vectors are fp32 while queries come from the quantized build,
    which costs a little precision. Measured top-5 retrieval agreement
    against fp32 on the real 15k-document index: 3.7/5 for int8 and 4.8/5
    for the fp16-optimised O4 build, with the top results preserved in every
    case and BM25 fused in alongside.

Neither torch nor transformers is imported on this path, and that is the
whole point -- they are where the memory goes. ``transformers.AutoTokenizer``
alone was measured at 670 MB because importing transformers pulls in torch,
so the tokenizer is loaded with the standalone ``tokenizers`` library, which
reads the same tokenizer.json (including its 128-token truncation and
BatchLongest padding settings) for about 20 MB.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Where the build bakes the model. Overridable so a local checkout can point
# at a downloaded copy.
ONNX_MODEL_DIR = os.getenv("ONNX_EMBEDDER_DIR", "/opt/models/onnx")

# Escape hatch: set ONNX_EMBEDDER_ENABLED=false to fall back to the in-process
# sentence-transformer.
#
# Needed because the Chroma index was written *un-normalised* (stored L2 norms
# span 1.26-6.61) and is ranked by squared L2, which is magnitude-sensitive.
# Feeding it the unit-length vectors this module produces made ||d||^2 -- which
# spans 1.6-43.7 -- dominate the q.d term, which cannot exceed 1.0. Retrieval
# then ranked by vector magnitude rather than meaning, and top-5 agreement with
# true cosine fell to 0-1 of 5. Turning the ONNX path off restores the original
# behaviour, where chromadb embeds the query with the same
# SentenceTransformerEmbeddingFunction that wrote the index, so both sides of
# the comparison share one convention.
#
# This costs ~830 MB of resident memory (torch plus the fp32 weights), which is
# fine on a workstation but is exactly what OOM-killed the 512 MB container.
ONNX_ENABLED = os.getenv("ONNX_EMBEDDER_ENABLED", "true").strip().lower() not in (
    "0", "false", "no", "off",
)

DEFAULT_MAX_LENGTH = 128

_lock = threading.Lock()
_session: Any = None
_tokenizer: Any = None
_load_failed = False


def _resolve_model_dir() -> Optional[Path]:
    """Locate the baked model, if it is there.

    Checks ONNX_EMBEDDER_DIR first, then a repo-local ``models/onnx`` so a
    developer can exercise the same path without a container. Returns None when
    ONNX_EMBEDDER_ENABLED is off, which is what restores the fp32 query path.
    """
    if not ONNX_ENABLED:
        return None
    project_root = Path(__file__).resolve().parent.parent.parent
    for candidate in (Path(ONNX_MODEL_DIR), project_root / "models" / "onnx"):
        if (candidate / "model.onnx").exists() and (candidate / "tokenizer.json").exists():
            return candidate
    return None


def embedder_available() -> bool:
    """True when the ONNX model and tokenizer are present on disk."""
    return _resolve_model_dir() is not None


def _max_length(model_dir: Path) -> int:
    """Sequence length to truncate to.

    tokenizer.json already carries the model's real setting (128 for this
    checkpoint); the sentence-transformers config says 512, which would let
    long inputs allocate far more than they need to.
    """
    try:
        with open(model_dir / "tokenizer.json", encoding="utf-8") as fh:
            return int(json.load(fh).get("truncation", {}).get("max_length")
                       or DEFAULT_MAX_LENGTH)
    except (OSError, ValueError, TypeError):
        return DEFAULT_MAX_LENGTH


def load_tokenizer(model_dir: Path):
    """Load the fast tokenizer without going through transformers."""
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    max_length = _max_length(model_dir)
    # tokenizer.json normally carries both settings already; set them anyway so
    # a hand-edited or older export cannot silently produce unbounded inputs.
    tokenizer.enable_truncation(max_length=max_length)
    tokenizer.enable_padding(pad_id=1, pad_token="<pad>", pad_type_id=0)
    return tokenizer


def _load() -> Optional[Any]:
    """Return a ready ONNX session, or None when the embedder is unavailable.

    Memoised on purpose: building an InferenceSession costs a few hundred
    milliseconds and hundreds of MB, so it must happen exactly once, and a
    failure must not be retried on every request.
    """
    global _session, _tokenizer, _load_failed
    if _session is not None or _load_failed:
        return _session

    with _lock:
        if _session is not None or _load_failed:
            return _session

        model_dir = _resolve_model_dir()
        if model_dir is None:
            logger.info(
                "ONNX embedder not present in %s; queries will fall back to the "
                "in-process model (needs ~800 MB).", ONNX_MODEL_DIR,
            )
            _load_failed = True
            return None

        try:
            import onnxruntime as ort

            # One thread: the container has a small CPU allowance and thread
            # pools here cost memory without adding throughput.
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.log_severity_level = 3

            _session = ort.InferenceSession(
                str(model_dir / "model.onnx"),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            _tokenizer = load_tokenizer(model_dir)
        except Exception as exc:  # noqa: BLE001 - degrade, never crash the API
            logger.warning("ONNX embedder failed to load (%s); falling back.", exc)
            _session = None
            _load_failed = True
        return _session


def warm() -> bool:
    """Load the embedder ahead of the first request. Returns True if ready."""
    return _load() is not None


def encode(texts: Sequence[str], tokenizer: Any = None) -> Dict[str, Any]:
    """Tokenize into the int64 arrays the ONNX graph expects."""
    tokenizer = tokenizer if tokenizer is not None else _tokenizer
    if tokenizer is None:
        raise RuntimeError("ONNX embedder is not available")
    encoded = tokenizer.encode_batch(list(texts))
    return {
        "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
        "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
    }


def embed_texts(texts: Sequence[str], session: Any = None, tokenizer: Any = None) -> List[List[float]]:
    """Embed ``texts`` into L2-normalised vectors.

    ``session``/``tokenizer`` are injectable for tests and for the build-time
    verification, which drives the embedder directly.
    """
    own = session is None
    if own:
        session = _load()
    if session is None:
        raise RuntimeError("ONNX embedder is not available")

    arrays = encode(texts, tokenizer=tokenizer)
    wanted = {i.name for i in session.get_inputs()}
    feed = {k: v for k, v in arrays.items() if k in wanted}
    # BERT-style exports declare token_type_ids as required, but a single
    # segment has no second segment type. Zeros are correct here.
    if "token_type_ids" in wanted:
        feed.setdefault("token_type_ids", np.zeros_like(arrays["input_ids"]))

    hidden = session.run(None, feed)[0]
    # hidden is (batch, sequence, dim) and attention_mask is (batch, sequence),
    # so the mask needs a trailing axis to broadcast over the embedding.
    mask = arrays["attention_mask"][..., None].astype(hidden.dtype)
    pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    return (pooled / np.clip(norms, 1e-9, None)).tolist()


def embed_query(text: str) -> List[float]:
    """Embed a single query. See :func:`embed_texts`."""
    return embed_texts([text])[0]