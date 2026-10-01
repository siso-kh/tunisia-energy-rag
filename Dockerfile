# syntax=docker/dockerfile:1
#
# Combined image for a Hugging Face Docker Space (sdk: docker, app_port: 7860).
#
# It serves BOTH apps from one container, mirroring the local docker-compose:
#   - nginx  -> static React build (frontend/dist) + reverse-proxy /api,/health,/ready
#   - uvicorn -> FastAPI on 127.0.0.1:8000 (only reachable inside the container)
#
# HF Spaces run the container as UID 1000, so we create that user and keep every
# writable path (nginx pid/temp, HF model cache, app data) owned by it.

# ---------- Stage 1: build the React frontend ----------
FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: runtime (FastAPI + nginx in one image) ----------
FROM python:3.10-slim

# nginx serves the SPA + proxies the API. The rest are runtime deps for the
# ingestion/OCR path (poppler for pdf2image, tesseract for pytesseract) and for
# ChromaDB (build-essential pulls the tools its wheels expect).
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
        sqlite3 \
        poppler-utils \
        tesseract-ocr \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces execute the container as user ID 1000.
RUN useradd -m -u 1000 user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/huggingface \
    PYTHONUNBUFFERED=1

WORKDIR /home/user/app

# Backend dependencies (installed system-wide; readable by the user).
COPY requirements.txt ./

# Install the CPU-only torch wheel FIRST. PyPI's default Linux torch wheel is the
# CUDA build (~2.5 GB plus nvidia-* packages), which is useless on a CPU Space and
# makes the image huge and the build slow. requirements.txt then sees torch
# already satisfied and skips it.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Application code + the ChromaDB index. The corpus (data/raw, data/filtered,
# data/blacklisted) is excluded in .dockerignore; data/chroma_db is bundled.
COPY --chown=user . .

# The compiled SPA from stage 1 (overwrites anything under frontend/dist).
COPY --from=frontend --chown=user /app/dist /home/user/app/frontend/dist

# nginx main config: listens on the HF port 7860 and proxies /api to uvicorn.
COPY nginx.hf.conf /etc/nginx/nginx.conf
RUN chmod +x entrypoint.sh

# Pre-download the embedding model into the image so cold starts don't spend
# minutes fetching weights (the Space's disk is NOT persistent across restarts).
USER user
RUN python -c "\
from chromadb.utils import embedding_functions; \
embedding_functions.SentenceTransformerEmbeddingFunction( \
model_name='paraphrase-multilingual-MiniLM-L12-v2')"
# Cross-encoder reranker (~470 MB) — pre-downloaded only when reranking is on.
# Space *Variables* are passed as Docker build-args, so setting the Variable
# RERANK_ENABLED=false skips this download entirely (smaller image / faster build).
# Best-effort: a download failure never fails the build.
ARG RERANK_ENABLED=true
RUN if [ "$RERANK_ENABLED" = "true" ]; then \
      python -c "from sentence_transformers import CrossEncoder; \
CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')" \
        || echo "[build] cross-encoder pre-download skipped (fetched at runtime if enabled)"; \
    else \
      echo "[build] RERANK_ENABLED=$RERANK_ENABLED -> skipping cross-encoder download"; \
    fi

EXPOSE 7860
ENTRYPOINT ["bash", "entrypoint.sh"]
