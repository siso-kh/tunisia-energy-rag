"""Fetch the ONNX build of the embedding model, for containers without torch.

Why this exists
---------------
The app embeds queries with
``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2``. Loaded the
usual way that costs ~830 MB resident -- measured: 188 MB for importing
torch, ~555 MB for the fp32 weights, and past 800 MB after a single encode.
That is what killed the deploy: the container was OOM-killed at boot while
loading it, so every request answered 502 with no error frame, because the
process was gone before the pipeline could report anything.

The upstream model repository already publishes ONNX exports of the *same*
weights. Running those through ONNX Runtime takes torch off the query path
entirely and produces vectors in the same space, so the existing Chroma
index stays valid and must NOT be rebuilt.

Measured against fp32 on the real 15k-document index:

    build      size    cosine vs fp32    top-5 agreement
    fp32      470 MB        1.000             5/5 (reference)
    O4        235 MB        1.000             4.8/5   <- default
    int8      118 MB        0.89-0.99         3.7/5

This script runs at image build time only, where torch is available so the
download can be verified against the fp32 model it replaces. A mismatch
fails the build rather than shipping silently degraded retrieval.

    python scripts/fetch_onnx_embedder.py --out /opt/models/onnx
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Run as `python scripts/fetch_onnx_embedder.py`, so the project root is not
# on sys.path yet and `src.rag.onnx_embedder` would not import.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MODEL_REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# ONNX builds upstream publishes, best first.
#
#   model_O4           235 MB, fp16-optimised: measured cosine 1.0000 against
#                      fp32, and top-5 retrieval agreement 4.8/5 on the real
#                      15k-document index. Effectively lossless, so it is the
#                      default.
#   model_qint8_*      118 MB, dynamic int8: cosine 0.89-0.99 (worst on
#                      Arabic) and 3.7/5 retrieval agreement. Roughly 150 MB
#                      lighter at runtime; only worth choosing when the
#                      container is genuinely tiny.
#   model_quint8_avx2  118 MB, the int8 build for CPUs without AVX512.
#
# The first one that downloads is used.
MODEL_FILES = (
    "onnx/model_O4.onnx",
    "onnx/model_qint8_avx512_vnni.onnx",
    "onnx/model_quint8_avx2.onnx",
)
TOKENIZER_FILES = (
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
)

# English, French and Arabic: the corpus is multilingual and handling all
# three is the entire reason this model was chosen.
SAMPLE_TEXTS = (
    "What is the role of STEG in Tunisia?",
    "Comment fonctionne le compteur electrique en Tunisie ?",
    "هل تعمل محطات الطاقة الشمسية في تونس؟",
    "photovoltaic capacity 2024",
)

# A drifted model would silently degrade every answer, so hold the line high.
MIN_COSINE = 0.95


def download(out_dir: Path) -> Path:
    """Download the ONNX weights and tokenizer. Returns the model path."""
    from huggingface_hub import hf_hub_download

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "model.onnx"
    if target.exists():
        print(f"[onnx] {target} already present ({target.stat().st_size / 2**20:.0f} MB)")
    else:
        last_error: Exception | None = None
        for remote in MODEL_FILES:
            try:
                cached = hf_hub_download(repo_id=MODEL_REPO, filename=remote)
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                last_error = exc
                print(f"[onnx] {remote} unavailable: {exc}")
                continue
            shutil.copyfile(cached, target)
            print(f"[onnx] fetched {remote} -> {target} "
                  f"({target.stat().st_size / 2**20:.0f} MB)")
            break
        else:
            raise SystemExit(f"[onnx] FAILED to download any ONNX build: {last_error}")

    for name in TOKENIZER_FILES:
        try:
            cached = hf_hub_download(repo_id=MODEL_REPO, filename=name)
        except Exception as exc:  # noqa: BLE001 - optional files
            print(f"[onnx] skipping {name}: {exc}")
            continue
        shutil.copyfile(cached, out_dir / name)
    return target


def verify(out_dir: Path) -> None:
    """Compare the ONNX pipeline against the fp32 model it replaces."""
    import numpy as np
    import onnxruntime as ort
    from sentence_transformers import SentenceTransformer

    from src.rag.onnx_embedder import embed_texts, load_tokenizer

    model = SentenceTransformer(MODEL_REPO)
    session = ort.InferenceSession(
        str(out_dir / "model.onnx"), providers=["CPUExecutionProvider"]
    )
    tokenizer = load_tokenizer(out_dir)
    print(f"[onnx] inputs: {[i.name for i in session.get_inputs()]}")

    worst = 1.0
    for text in SAMPLE_TEXTS:
        onnx_vector = embed_texts([text], session=session, tokenizer=tokenizer)[0]
        reference = model.encode([text], normalize_embeddings=True)[0]
        cosine = float(np.dot(onnx_vector, reference))
        worst = min(worst, cosine)
        # ASCII-safe: the build log is read in terminals that cannot render
        # the Arabic sample, and a UnicodeEncodeError here would look like a
        # verification failure.
        label = text[:44].encode("ascii", "replace").decode("ascii")
        print(f"[onnx] cosine fp32 vs onnx = {cosine:.4f}  | {label}")

    if worst < MIN_COSINE:
        raise SystemExit(
            f"[onnx] FAILED: worst cosine {worst:.4f} < {MIN_COSINE}. This ONNX "
            "build does not reproduce the fp32 model; refusing to ship it."
        )
    print(f"[onnx] OK, worst cosine {worst:.4f} (threshold {MIN_COSINE})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="/opt/models/onnx", help="output directory")
    parser.add_argument("--skip-verify", action="store_true",
                        help="skip the fp32 comparison (needs torch, for "
                             "environments where it is unavailable)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    download(out_dir)
    if not args.skip_verify:
        verify(out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
