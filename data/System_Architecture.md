# System Architecture Documentation: Knowledge Base Ingestion Pipeline

## 1. Executive Summary

The **Tunisia Energy RAG Ingestion Pipeline** is an automated, fault-tolerant ETL (Extract, Transform, Load) subsystem designed to convert unstructured, multi-lingual domain literature (Arabic, French, and English) into clean, semantically enriched text chunks ready for vector embeddings.

> **Confidence:** 100%

The pipeline addresses three major challenges in domain-specific Retrieval-Augmented Generation (RAG):

| Challenge | Description |
|-----------|-------------|
| **Source Reliability & Diversity** | Collecting technical PDFs across heterogeneous international and regional databases. |
| **Signal-to-Noise Ratio (SNR)** | Eliminating non-informative documents (event flyers, short brochures, cover-only PDFs) through automated triage. |
| **Context Continuity** | Preventing contextual fragmentation during chunking using hierarchical document splitting and explicit metadata tracking. |

> **Confidence:** 95%

---

## 2. System Architecture Overview

### Stage 1: Hybrid Data Collection

```
Target Search Queries ──► [ SerpApi (Primary) ]
                                 │
                          (Quota Limit / Fail)
                                 ▼
                          [ DuckDuckGo (DDGS) ]
                                 │
                                 ▼
                   Raw PDF Downloads (data/raw/)
```

- **Primary Engine:** SerpApi (Google Search API) for high-precision index queries.
- **Fallback Engine:** DuckDuckGo Search (`ddgs`) triggered automatically if SerpApi credentials are missing or API rate limits are exhausted.

### Stage 2: Quality Triage & Filtering

```
data/raw/*.pdf ──► Multi-Gate Triage (Validation & Content Filter)
                                 │
                                 ▼
                    Filtered High-Signal PDFs
                         (data/filtered/)
```

### Stage 3: Extraction & Recursive Chunking

```
data/filtered/*.pdf ──► pdfplumber Layout Text Extraction
                                 │
                                 ▼
                        Text Normalization
                                 │
                                 ▼
                   RecursiveCharacterTextSplitter
                (1000 chars / 150 overlap / ["\n\n", "\n", " "])
                                 │
                                 ▼
                    Metadata Enrichment Pipeline
                      (Source, Page, Tunisia-Tag)
                                 │
                                 ▼
                 data/processed/processed_chunks.json
```

---

## 3. Detailed Component Breakdown

### 3.1 Stage 1: Multi-Engine Scraper & Collection Layer (`src/collector.py`)

**Objective:** Automate targeted search across domain-specific domains (`ademe.fr`, `rcreee.org`, `irena.org`) and technical topic queries.

#### Engine Routing

| Priority | Engine | Purpose |
|----------|--------|---------|
| Primary | SerpApi | High-precision index queries via Google Search API. |
| Fallback | `ddgs` (DuckDuckGo) | Auto-triggered on missing credentials or rate limit exhaustion. |

#### Fault Tolerance & Resilience

- Implements customized HTTP headers to bypass standard user-agent blocking.
- Handles network timeout, SSL verification failures, and broken URL links.
- Maintains `data/collection_log.json` to prevent duplicate downloads and record provenance.

---

### 3.2 Stage 2: Quality Triage Subsystem (`src/triage.py`)

**Objective:** Filter out low-value content to maximize the density of technical knowledge fed into the vector database.

#### Filtering Criteria

| Gate | Criteria | Purpose |
|------|----------|---------|
| **File Integrity Check** | Verifies structural validity of downloaded PDFs. | Prevents corrupted files from entering downstream stages. |
| **Document Length Gate** | Excludes ultra-short PDFs (< 3 pages). | Filters out posters, news alerts, and event flyers. |
| **Domain Specificity Filter** | Retains documents covering energy efficiency, building audits, water pumping, and renewable transition. | Ensures domain relevance. |

#### Yield

> Successfully isolated **73 validated, high-signal technical documents** out of **80 initial raw downloads**.
>
> **Confidence:** 98%

---

### 3.3 Stage 3: Extraction, Normalization & Semantic Chunking (`src/ingest_chunks.py`)

#### Text Extraction

- Utilizes **pdfplumber** for precise, page-by-page character and layout extraction across multi-column French and Arabic technical documents.

#### Normalization Pipeline

- Strips excessive white spaces, tabulations, and broken PDF line breaks.
- Filters out blank/cover pages containing less than 50 characters.

#### Hierarchical Splitting Strategy

- Uses **LangChain's `RecursiveCharacterTextSplitter`**.

| Parameter | Value |
|-----------|-------|
| **Chunk Size** | 1,000 characters |
| **Chunk Overlap** | 150 characters (15% sliding window) |
| **Separators Hierarchy** | `["\n\n", "\n", " ", ""]` |

> The separator hierarchy prioritizes semantic breaks (paragraphs) over arbitrary character bounds to prevent context fragmentation across boundaries.

---

## 4. Metadata Schema & Data Specifications

Every chunk generated by Stage 3 is structured with the following schema before storage:

```json
{
  "source": "tunisia_ee_fact_sheet_print.pdf",
  "page": 4,
  "content": "Le secteur du bâtiment en Tunisie représente une part majeure de la consommation d'électricité...",
  "is_tunisia_specific": true
}
```

### Metadata Fields

| Field | Type | Description |
|-------|------|-------------|
| `source` | `string` | Name of the originating PDF file for explicit source attribution during RAG generation. |
| `page` | `integer` | Exact page number within the source document. |
| `content` | `string` | Cleaned text block (1,000 max characters). |
| `is_tunisia_specific` | `boolean` | Dynamic tag derived from keyword match (`tunis`) in filename or chunk content to enable metadata filtering in ChromaDB. |

> **Confidence:** 95%

---

## 5. Pipeline Yield Statistics

| Pipeline Metric | Value |
|-----------------|-------|
| Target Queries Processed | 6 multi-lingual domain queries |
| Raw PDFs Collected | 80 documents |
| Triage Pass Rate | **91.25%** (73 valid documents) |
| Primary Languages | French, Arabic, English |
| Output Destination | `data/processed/processed_chunks.json` |
| Pipeline Status | ✅ Completed — Ready for Vectorization |

---

## File Structure Summary

```
project-root/
├── src/
│   ├── collector.py          # Stage 1: Hybrid Data Collection
│   ├── triage.py             # Stage 2: Quality Triage & Filtering
│   └── ingest_chunks.py      # Stage 3: Extraction & Chunking
├── data/
│   ├── raw/                  # Raw PDF downloads
│   ├── filtered/             # Validated high-signal PDFs
│   ├── processed/
│   │   └── processed_chunks.json   # Final output
│   └── collection_log.json   # Download provenance & deduplication
```
