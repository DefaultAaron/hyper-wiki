# ADR 0005: Bilingual keyword analyzer, embedding model, and fusion

Date: 2026-11-02. Status: accepted for M1 (HW-008); to be confirmed on the private library in HW-015 and HW-017. Number 0004 is reserved for the HW-006 change-detection record.

## Context

The [product design](../../PRODUCT_DESIGN.md) requires keyword search that segments Chinese correctly, a local multilingual embedding model that runs without any provider, and queries in either language that find evidence in both. [ADR 0001](0001-core-architecture.md) fixes SQLite/FTS5 and an exact vector scan to start with, and leaves the tokenizer and embedding model to M0. Hyper-Wiki runs on researchers' laptops, so install size, indexing time on a CPU, and memory matter as much as retrieval quality.

## Evidence

The spike harness is in [`experiments/hw008-bilingual-search`](../../experiments/hw008-bilingual-search). It uses a synthetic corpus written for this spike: 50 passages and 36 queries.
- **Passages:** 16 topics, each with one English and one Chinese passage reporting the same invented finding in independent wording. Hard negatives and lexical distractors are mixed in.
- **Queries:** keyword, question, and paraphrase styles in both languages.
- **Probes:** 9 tokenizer probes for one- and two-character terms, mixed script, full-width letters, identifiers, compounds, segmentation ambiguity, and traditional script.

Full output is in [results.md](../../experiments/hw008-bilingual-search/results.md). It was measured on 2026-11-02 on an Apple M4 Pro, with embeddings on the CPU using 4 threads.

**Keyword analyzers** (all on SQLite 3.50.4 FTS5 with BM25 and OR queries):

| Analyzer | Same-language recall@5, en / zh | Probes passed | Index, 10,000 rows | Mean query |
|---|---|---|---|---|
| FTS5 `unicode61` | 0.94 / 0.00 | 0 of 9 | 1.7 MB | 0.28 ms |
| FTS5 `trigram`, `LIKE` fallback below 3 characters | 1.00 / 0.94 | 7 of 9 | 3.7 MB | 1.61 ms |
| jieba 0.42.1 `cut_for_search` | 0.94 / 1.00 | 6 of 9 | 2.1 MB | 0.61 ms |
| Han bigrams | 0.94 / 1.00 | 7 of 9 | 2.7 MB | 0.47 ms |
| Han bigrams, plus unigrams in the index | 0.94 / 1.00 | 8 of 9 | 3.8 MB | 0.48 ms |

- `unicode61` indexes a run of Han characters as one token, so no Chinese word inside a sentence can match.
- `trigram` cannot match one- or two-character terms without a full-table `LIKE` scan, and that scan is unranked. A natural-language Chinese query also has to be broken into a bag of trigrams. It missed full-width letters because the text was not normalized.
- jieba segmented the same characters differently depending on context. The query 权重量化到4比特 gave 权重/量化, but the passage text 4比特权重量化后 gave 比/特权/重量/化后. As a result, 量化 (quantization) never matched its passage. Its last release (0.42.1) is from 2020-01-20, it raises `SyntaxWarning`s on Python 3.12, and it imports the deprecated `pkg_resources`.
- Bigrams do not depend on context: the same characters always produce the same tokens. Adding unigrams to the index makes one-character terms such as 酸 match.
- Every analyzer failed to match the traditional-script passage (強化學習) to the simplified query 强化学习.

**Embedding models** (sentence-transformers 6.2.0, torch 2.12.0, fp32):

| Model | License | Recall@5: en>zh / zh>en | both@10 | Max tokens | Chunks/s, ~300 tokens | Peak RSS |
|---|---|---|---|---|---|---|
| paraphrase-multilingual-MiniLM-L12-v2 | Apache-2.0 | 0.94 / 0.78 (zh>zh 0.89) | 0.94 | 128 | 231 (truncated) | 1.0 GB |
| multilingual-e5-small | MIT | 0.61 / 0.28 | 0.58 | 512 | 75 | 1.2 GB |
| granite-embedding-107m-multilingual | Apache-2.0 | 0.94 / 0.83 | 0.94 | 512 | 145 | 1.5 GB |
| bge-m3 | MIT | 0.94 / 1.00 | 0.97 | 8192 | 10.5 | 2.6 GB |
| Qwen3-Embedding-0.6B | Apache-2.0 | 1.00 / 1.00 | 1.00 | 32768 | 7.3 | 4.1 GB |

- `both@10` is the share of queries that find both the English and the Chinese passage in the top 10.
- Same-language recall@5 was 1.00 for every model except MiniLM's Chinese.
- Granite missed three Chinese-to-English queries (two of them paraphrases) and one English-to-Chinese paraphrase.
- Through ONNX Runtime 1.31.1 and tokenizers 0.23.3, with no PyTorch, granite's `model.onnx` (fp32, 428 MB) gave identical top-10 rankings on all 45 queries and probes. Its speed was 88 chunks/s on ~300-token chunks, against 145 with PyTorch, and peak RSS was 1.8 GB.

**Fusion** (Han bigrams plus unigrams, with granite):

| Method | Recall@5: en>en / zh>zh / en>zh / zh>en | both@10 | Probes passed |
|---|---|---|---|
| Reciprocal rank fusion (k=60) | 1.00 / 1.00 / 0.72 / 0.72 | 0.89 | 9 of 9 |
| Language-balanced (the same fused list, alternating languages) | 1.00 / 1.00 / 0.94 / 0.94 | 0.94 | 9 of 9 |

Plain fusion pushes cross-language passages down, because the keyword list only ever holds the query's language. Interleaving the fused list by language restores cross-language recall without losing same-language recall. The embedding side also matches the traditional-script passage.

**Install size:**
- **ONNX Runtime:** wheels are 15–24 MB (MIT).
- **PyTorch 2.12.0:** wheels are 88 MB on macOS, 123 MB on Windows, and 532 MB on Linux x86_64. The Linux wheel also pulls in NVIDIA CUDA packages.

## Decision

1. **Keyword analyzer.** Hyper-Wiki uses its own analyzer of a few dozen lines and adds no dependency.
   - **Normalization:** text is NFKC-normalized and case-folded.
   - **Han characters** (CJK Unified Ideographs, Extension A, and the compatibility block): each run is indexed as overlapping bigrams plus single characters. On the query side, a run produces bigrams, and a one-character run produces that character.
   - **Other text:** each other word run is indexed as one token.
   - **Storage:** the analyzed text goes into an FTS5 table with the `unicode61` tokenizer, ranked by BM25 with OR semantics.
   - **What HW-015 decides:** the table layout (external-content or contentless), and whether to boost phrase matches.
2. **Default embedding model:** `ibm-granite/granite-embedding-107m-multilingual` (Apache-2.0; its model card lists Chinese among its 12 languages).
   - **Pinned revision:** `d6cffd338414d6a1c1f5decfad5fec62eebc90d5`.
   - **Runtime:** its ONNX export runs through ONNX Runtime and tokenizers, so the core install never needs PyTorch.
   - **Output:** CLS pooling, L2-normalized 384-dimensional vectors, searched by exact cosine scan.
   - **Chunk size:** evidence chunks must stay under the 512-token limit. About 800 mixed characters measured roughly 300 tokens.
   - **Fingerprint:** the model ID, revision, and runtime are part of the embedding fingerprint (HW-012), so changing them triggers re-embedding.
3. **Optional higher-quality model:** Qwen3-Embedding-0.6B (Apache-2.0). It is offered as a setting for users who accept about 20 times slower indexing and about 4 GB of memory. On this machine, the arithmetic for an assumed 24,000 chunks (300 PDFs × 20 pages × 4 chunks) is about 3 minutes with granite on PyTorch, 5 minutes on ONNX, and 55 minutes with Qwen3. That is an extrapolation, not a measured end-to-end run. Running Qwen3 without PyTorch needs a separate ONNX check.
4. **Fusion.** Search fuses the keyword and vector lists with reciprocal rank fusion (k=60). It then interleaves the result by language, starting with the query's language; a query containing Han characters counts as Chinese. HW-015 decides whether users can ask for one language only.

**Rejected and why:**
- **Analyzers:**
  - `unicode61` alone fails on Chinese.
  - `trigram` gives unranked `LIKE` scans for short terms and slower queries.
  - jieba segments technical terms inconsistently and is unmaintained.
  - Word segmentation may return for entity extraction in M3 (the Rust-backed `rjieba`, MIT, is the candidate), but not for the keyword index.
- **Models tested and rejected:**
  - MiniLM has a 128-token limit and weaker Chinese.
  - multilingual-e5-small had weak cross-language recall on this corpus.
  - bge-m3 came within one query of Qwen3, but its weights are twice the size (2.3 GB against 1.2 GB) and it is still more than ten times slower than granite. Offering one optional heavier model is enough.
- **Not tested:**
  - EmbeddingGemma uses the Gemma terms, which carry use restrictions.
  - jina-embeddings-v3 is CC-BY-NC-4.0.
  - gte-multilingual-base, nomic-embed-text-v2-moe, and snowflake-arctic-embed-m-v2.0 need `trust_remote_code`, which runs code from the model repository at load time. They are not used unless that code is reviewed.

## Consequences and open items

- **Dependencies:** HW-015 adds onnxruntime (MIT), tokenizers (Apache-2.0), and numpy (BSD-3-Clause), and records their versions in the dependency table when adopted. The model weights are Apache-2.0 with no use restrictions ([ADR 0002](0002-license.md)). The weights are downloaded at setup and never committed.
- **Traditional script:** keyword matching of traditional-script Chinese is unresolved. HW-015 evaluates converting traditional to simplified script with OpenCC (Apache-2.0) at index and query time. Embeddings already bridge the two scripts on the probe.
- **Untested:** the reranker variant of evaluation C2 was not tested.
- **Model downloads:** on the test network, Hugging Face downloads ran at about 2.7 MB/s while PyPI downloads stalled near 80 KB/s. Setup (M4) must show download progress, resume interrupted downloads, and accept a model directory the user has already downloaded.
- **Limits of the evidence:**
  - The corpus is small, synthetic, and written by the same author who chose the methods. With 18 queries per language direction, one query moves a recall figure by 0.06.
  - Only one machine was measured. Windows, Linux, and older laptops are unmeasured.
  - ONNX Runtime was slower than PyTorch on longer chunks. Graph optimization and int8 quantization are untried.
  - Confirm these choices on the private library with real questions (HW-015, HW-017) before calling them settled.

## Reproduce

From the repository root (each script runs in its own uv environment):

```sh
D=experiments/hw008-bilingual-search
uv run $D/run.py keyword
HF_HOME=/path/to/model-cache uv run $D/run.py embed-all
HF_HOME=/path/to/model-cache uv run $D/onnx_check.py granite-107m
uv run $D/run.py report
```

## Sources

Accessed 2026-11-02.

- SQLite, [FTS5 extension](https://www.sqlite.org/fts5.html) (unicode61 and trigram tokenizers, BM25).
- Hugging Face model cards and repository metadata (licenses, parameter counts, files): [granite-embedding-107m-multilingual](https://huggingface.co/ibm-granite/granite-embedding-107m-multilingual), [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [bge-m3](https://huggingface.co/BAAI/bge-m3), [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small), [paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), [embeddinggemma-300m](https://huggingface.co/google/embeddinggemma-300m), [jina-embeddings-v3](https://huggingface.co/jinaai/jina-embeddings-v3).
- PyPI metadata for [jieba](https://pypi.org/project/jieba/), [rjieba](https://pypi.org/project/rjieba/), [OpenCC](https://pypi.org/project/OpenCC/), [onnxruntime](https://pypi.org/project/onnxruntime/), and [torch](https://pypi.org/project/torch/) (versions, release dates, licenses, wheel sizes).
