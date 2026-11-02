# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "jieba==0.42.1",
#   "numpy>=2.0",
#   "sentence-transformers>=5.0",
#   "scipy==1.17.1",
#   "torch==2.12.0",
#   "transformers>=4.51",
# ]
# ///
"""HW-008 spike: Chinese-capable keyword search and multilingual embeddings.

Throwaway measurement harness, not product code. Runs in its own environment
(from the repository root, with D=experiments/hw008-bilingual-search):

    uv run $D/run.py keyword      # SQLite FTS5 tokenizer variants
    uv run $D/run.py embed KEY    # one embedding model (own process)
    uv run $D/run.py embed-all    # every model, one process each
    uv run $D/run.py report       # metrics and fusions -> results.md

Set HF_HOME to choose where models are downloaded. Results go to $D/results/.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import re
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Callable, Iterable
from itertools import zip_longest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CORPUS = json.loads((HERE / "corpus.json").read_text(encoding="utf-8"))
PASSAGES: list[dict[str, str]] = CORPUS["passages"]
QUERIES: list[dict[str, Any]] = CORPUS["queries"]
PROBES: list[dict[str, Any]] = CORPUS["probes"]
LANG = {p["id"]: p["lang"] for p in PASSAGES}
TOP = 20
THREADS = 4
SCALE_COPIES = 200
RRF_K = 60

WORD = re.compile(r"\w+")
HAN_CLASS = "㐀-䶿一-鿿豈-﫿"
HAN_OR_OTHER = re.compile(f"[{HAN_CLASS}]+|[^{HAN_CLASS}]+")
HAN = re.compile(f"[{HAN_CLASS}]+")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def bigram_tokens(text: str, *, unigrams: bool = False) -> list[str]:
    """Han runs become overlapping bigrams (a lone character stays a unigram).

    With ``unigrams`` every Han character is also emitted, so one-character
    queries can match; queries are still analyzed without it.
    """
    tokens: list[str] = []
    for run in WORD.findall(normalize(text)):
        for segment in HAN_OR_OTHER.findall(run):
            if not HAN.fullmatch(segment) or len(segment) == 1:
                tokens.append(segment)
            else:
                tokens.extend(segment[i : i + 2] for i in range(len(segment) - 1))
                if unigrams:
                    tokens.extend(segment)
    return tokens


def jieba_tokens(text: str) -> list[str]:
    import jieba

    return [t for t in jieba.cut_for_search(normalize(text)) if WORD.fullmatch(t)]


def unicode61_query_tokens(text: str) -> list[str]:
    return WORD.findall(text)


def trigram_query_tokens(text: str) -> list[str]:
    grams: list[str] = []
    for run in WORD.findall(text.casefold()):
        grams.extend(run[i : i + 3] for i in range(len(run) - 2))
    return grams


def fts_or_query(tokens: Iterable[str]) -> str:
    quoted = ['"' + t.replace('"', '""') + '"' for t in dict.fromkeys(tokens)]
    return " OR ".join(quoted)


class KeywordMethod:
    def __init__(
        self,
        name: str,
        tokenize: str,
        index_text: Callable[[str], str],
        query_tokens: Callable[[str], list[str]],
        like_fallback: bool = False,
    ) -> None:
        self.name = name
        self.tokenize = tokenize
        self.index_text = index_text
        self.query_tokens = query_tokens
        self.like_fallback = like_fallback

    def build(self, conn: sqlite3.Connection, texts: list[str]) -> None:
        conn.execute(f"CREATE VIRTUAL TABLE t USING fts5(body, tokenize='{self.tokenize}')")
        conn.executemany(
            "INSERT INTO t(rowid, body) VALUES (?, ?)",
            ((i, self.index_text(text)) for i, text in enumerate(texts)),
        )
        conn.commit()

    def search(self, conn: sqlite3.Connection, query: str, limit: int = TOP) -> list[int]:
        rows: list[int] = []
        expression = fts_or_query(self.query_tokens(query))
        if expression:
            rows = [
                r[0]
                for r in conn.execute(
                    "SELECT rowid FROM t WHERE t MATCH ? ORDER BY bm25(t) LIMIT ?",
                    (expression, limit),
                )
            ]
        if self.like_fallback:
            short = [r for r in WORD.findall(query.casefold()) if len(r) < 3]
            for run in short:
                for (rowid,) in conn.execute(
                    "SELECT rowid FROM t WHERE body LIKE ? LIMIT ?", (f"%{run}%", limit)
                ):
                    if rowid not in rows:
                        rows.append(rowid)
        return rows[:limit]


def keyword_methods() -> list[KeywordMethod]:
    return [
        KeywordMethod("unicode61", "unicode61", lambda s: s, unicode61_query_tokens),
        KeywordMethod("trigram", "trigram", lambda s: s, trigram_query_tokens, like_fallback=True),
        KeywordMethod("jieba", "unicode61", lambda s: " ".join(jieba_tokens(s)), jieba_tokens),
        KeywordMethod("bigram", "unicode61", lambda s: " ".join(bigram_tokens(s)), bigram_tokens),
        KeywordMethod(
            "bigram+uni",
            "unicode61",
            lambda s: " ".join(bigram_tokens(s, unigrams=True)),
            bigram_tokens,
        ),
    ]


def versions(*packages: str) -> dict[str, str]:
    found = {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version}
    for package in packages:
        try:
            found[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            found[package] = "missing"
    return found


def machine() -> dict[str, str]:
    brand = ""
    if sys.platform == "darwin":
        brand = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
        ).stdout.strip()
    return {"platform": platform.platform(), "cpu": brand or platform.processor()}


def run_keyword() -> None:
    import jieba

    jieba.setLogLevel(60)
    start = time.perf_counter()
    jieba.initialize()
    jieba_init_s = time.perf_counter() - start

    texts = [p["text"] for p in PASSAGES]
    ids = [p["id"] for p in PASSAGES]
    out: dict[str, Any] = {
        "versions": versions("jieba"),
        "machine": machine(),
        "jieba_init_s": round(jieba_init_s, 3),
        "methods": {},
    }
    scaled = [t for _ in range(SCALE_COPIES) for t in texts]
    scaled_chars = sum(len(t) for t in scaled)
    for method in keyword_methods():
        conn = sqlite3.connect(":memory:")
        method.build(conn, texts)
        rankings = {
            q["id"]: [ids[r] for r in method.search(conn, q["text"])] for q in QUERIES + PROBES
        }
        conn.close()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scale.db"
            conn = sqlite3.connect(path)
            start = time.perf_counter()
            method.build(conn, scaled)
            build_s = time.perf_counter() - start
            conn.execute("INSERT INTO t(t) VALUES('optimize')")
            conn.commit()
            conn.execute("VACUUM")
            size = path.stat().st_size
            start = time.perf_counter()
            for q in QUERIES:
                method.search(conn, q["text"])
            query_ms = (time.perf_counter() - start) / len(QUERIES) * 1000
            conn.close()

        out["methods"][method.name] = {
            "rankings": rankings,
            "scale": {
                "rows": len(scaled),
                "chars": scaled_chars,
                "build_s": round(build_s, 3),
                "db_bytes": size,
                "mean_query_ms": round(query_ms, 3),
            },
        }
    write_json(RESULTS / "keyword.json", out)


MODELS: dict[str, dict[str, Any]] = {
    "minilm-l12": {
        "name": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "license": "Apache-2.0",
    },
    "e5-small": {
        "name": "intfloat/multilingual-e5-small",
        "license": "MIT",
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
    },
    "granite-107m": {
        "name": "ibm-granite/granite-embedding-107m-multilingual",
        "license": "Apache-2.0",
    },
    "bge-m3": {"name": "BAAI/bge-m3", "license": "MIT"},
    "qwen3-0.6b": {
        "name": "Qwen/Qwen3-Embedding-0.6B",
        "license": "Apache-2.0",
        "query_prompt_name": "query",
    },
}


def run_embed(key: str) -> None:
    import numpy as np
    import torch
    from huggingface_hub import scan_cache_dir
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(THREADS)
    spec = MODELS[key]
    start = time.perf_counter()
    model = SentenceTransformer(spec["name"], device="cpu")
    model.float()  # some checkpoints ship fp16/bf16, which is slow on CPU
    load_s = time.perf_counter() - start

    def encode(texts: list[str], *, query: bool) -> Any:
        prefix = spec.get("query_prefix" if query else "passage_prefix", "")
        kwargs: dict[str, Any] = {"batch_size": 16, "normalize_embeddings": True}
        if query and "query_prompt_name" in spec:
            kwargs["prompt_name"] = spec["query_prompt_name"]
        return model.encode([prefix + t for t in texts], **kwargs)

    passages = encode([p["text"] for p in PASSAGES], query=False)
    asked = QUERIES + PROBES
    queries = encode([q["text"] for q in asked], query=True)
    scores = queries @ passages.T
    ids = [p["id"] for p in PASSAGES]
    rankings = {
        q["id"]: [ids[i] for i in np.argsort(-scores[n])[:TOP]] for n, q in enumerate(asked)
    }

    bench = [PASSAGES[i % len(PASSAGES)]["text"] for i in range(256)]
    encode(bench[:16], query=False)
    start = time.perf_counter()
    encode(bench, query=False)
    per_s = len(bench) / (time.perf_counter() - start)

    long_bench = long_passages()
    encode(long_bench[:4], query=False)
    start = time.perf_counter()
    encode(long_bench, query=False)
    long_per_s = len(long_bench) / (time.perf_counter() - start)
    long_tokens = [len(model.tokenizer(t)["input_ids"]) for t in long_bench]

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = peak if sys.platform == "darwin" else peak * 1024
    disk = next((r.size_on_disk for r in scan_cache_dir().repos if r.repo_id == spec["name"]), None)
    params = sum(p.numel() for p in model.parameters())
    write_json(
        RESULTS / f"embed-{key}.json",
        {
            "model": spec["name"],
            "license": spec["license"],
            "versions": versions("torch", "sentence-transformers", "transformers"),
            "machine": machine(),
            "threads": THREADS,
            "params": params,
            "dims": int(passages.shape[1]),
            "disk_bytes": disk,
            "load_s": round(load_s, 2),
            "passages_per_s": round(per_s, 1),
            "bench_mean_chars": round(sum(len(t) for t in bench) / len(bench), 1),
            "max_seq_length": model.max_seq_length,
            "long_passages_per_s": round(long_per_s, 1),
            "long_mean_chars": round(sum(len(t) for t in long_bench) / len(long_bench), 1),
            "long_mean_tokens": round(sum(long_tokens) / len(long_tokens), 1),
            "peak_rss_bytes": peak_bytes,
            "rankings": rankings,
        },
    )


def long_passages() -> list[str]:
    """64 chunks of ten consecutive passages each, closer to real evidence chunk sizes."""
    n = len(PASSAGES)
    return [" ".join(PASSAGES[(i + j) % n]["text"] for j in range(10)) for i in range(64)]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def rrf(*rankings: list[str]) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, pid in enumerate(ranking, start=1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores, key=lambda pid: -scores[pid])


def balanced(fused: list[str], query: str) -> list[str]:
    """Alternate languages, starting with the query's (Han characters mean Chinese)."""
    first = "zh" if HAN.search(query) else "en"
    same = [pid for pid in fused if LANG[pid] == first]
    other = [pid for pid in fused if LANG[pid] != first]
    out: list[str] = []
    for pair in zip_longest(same, other):
        out.extend(pid for pid in pair if pid is not None)
    return out[:TOP]


BUCKETS = ("en>en", "zh>zh", "en>zh", "zh>en")


def first_rank(ranking: list[str], wanted: Iterable[str]) -> int | None:
    wanted = set(wanted)
    return next((n for n, pid in enumerate(ranking, start=1) if pid in wanted), None)


def evaluate(rankings: dict[str, list[str]]) -> dict[str, Any]:
    hits: dict[str, list[int | None]] = {b: [] for b in BUCKETS}
    both = 0
    paraphrase: list[int | None] = []
    for q in QUERIES:
        ranking = rankings[q["id"]]
        ranks = {}
        for target in ("en", "zh"):
            wanted = [pid for pid in q["relevant"] if LANG[pid] == target]
            ranks[target] = first_rank(ranking, wanted)
            hits[f"{q['lang']}>{target}"].append(ranks[target])
        if all(r is not None and r <= 10 for r in ranks.values()):
            both += 1
        if q["style"] == "paraphrase":
            paraphrase.append(ranks[q["lang"]])

    def at(ranks: list[int | None], k: int) -> float:
        return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)

    def mrr(ranks: list[int | None]) -> float:
        return sum(1 / r for r in ranks if r is not None and r <= 10) / len(ranks)

    every = [r for b in BUCKETS for r in hits[b]]
    return {
        "recall@5": {b: round(at(hits[b], 5), 3) for b in BUCKETS},
        "mrr@10": round(mrr(every), 3),
        "both@10": round(both / len(QUERIES), 3),
        "paraphrase_same_lang@5": round(at(paraphrase, 5), 3),
    }


def probe_results(rankings: dict[str, list[str]]) -> dict[str, bool]:
    out = {}
    for probe in PROBES:
        ranking = rankings[probe["id"]][:10]
        ok = all(pid in ranking for pid in probe["expect"])
        best = first_rank(ranking, probe["expect"])
        for rival in probe.get("outrank", []):
            rival_rank = first_rank(ranking, [rival])
            if best is None or (rival_rank is not None and rival_rank < best):
                ok = False
        out[probe["id"]] = ok
    return out


def run_report() -> None:
    keyword = json.loads((RESULTS / "keyword.json").read_text(encoding="utf-8"))
    embeds = {
        key: json.loads((RESULTS / f"embed-{key}.json").read_text(encoding="utf-8"))
        for key in MODELS
        if (RESULTS / f"embed-{key}.json").exists()
    }
    systems: dict[str, dict[str, list[str]]] = {}
    for name, data in keyword["methods"].items():
        systems[f"keyword:{name}"] = data["rankings"]
    for key, data in embeds.items():
        systems[f"embed:{key}"] = data["rankings"]
    onnx = {
        key: json.loads((RESULTS / f"onnx-{key}.json").read_text(encoding="utf-8"))
        for key in MODELS
        if (RESULTS / f"onnx-{key}.json").exists()
    }
    for key, data in onnx.items():
        systems[f"onnx:{key}"] = data["rankings"]
    texts = {q["id"]: q["text"] for q in QUERIES + PROBES}
    for kw in ("jieba", "bigram", "bigram+uni", "trigram"):
        kw_rank = keyword["methods"][kw]["rankings"]
        for key, data in embeds.items():
            fused = {qid: rrf(kw_rank[qid], data["rankings"][qid]) for qid in texts}
            systems[f"rrf({kw}, {key})"] = {qid: f[:TOP] for qid, f in fused.items()}
            systems[f"balanced({kw}, {key})"] = {
                qid: balanced(f, texts[qid]) for qid, f in fused.items()
            }
    summary = {
        name: {"metrics": evaluate(r), "probes": probe_results(r)} for name, r in systems.items()
    }
    write_json(RESULTS / "summary.json", summary)
    runtimes = embeds | {f"onnx:{key}": data for key, data in onnx.items()}
    (HERE / "results.md").write_text(render(keyword, runtimes, summary), encoding="utf-8")


def render(keyword: dict[str, Any], embeds: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        "# HW-008 spike results",
        "",
        "Generated by `run.py report` from the synthetic corpus in `corpus.json` "
        "(50 passages, 36 queries, 9 probes). Smoke results on invented text, not a "
        "benchmark.",
        "",
        f"Machine: {keyword['machine']['cpu']} ({keyword['machine']['platform']}); "
        f"embeddings on CPU with {THREADS} threads.",
        "",
        "Versions: "
        + ", ".join(f"{k} {v}" for k, v in keyword["versions"].items())
        + (
            ", "
            + ", ".join(
                f"{k} {v}"
                for k, v in next(iter(embeds.values()))["versions"].items()
                if k not in keyword["versions"]
            )
            if embeds
            else ""
        ),
        "",
        "## Retrieval",
        "",
        "Recall@5 is the share of queries whose relevant passage in the target language "
        "ranks in the top 5 (`en>zh`: English query, Chinese passage). `both@10`: both "
        "the English and the Chinese passage in the top 10. `para@5`: paraphrased "
        "queries, same-language passage in the top 5. `rrf(k, e)`: reciprocal rank fusion "
        f"(k={RRF_K}) of keyword method k and embedding model e; `balanced(k, e)`: the same "
        "fused list, alternating languages starting with the query's. Fusions with jieba and "
        "plain bigram are in `results/summary.json`.",
        "",
        "| System | en>en | zh>zh | en>zh | zh>en | MRR@10 | both@10 | para@5 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, row in summary.items():
        if name.startswith(("rrf(", "balanced(")) and not name.split("(")[1].startswith(
            ("bigram+uni,", "trigram,")
        ):
            continue
        m = row["metrics"]
        r = m["recall@5"]
        lines.append(
            f"| {name} | {r['en>en']:.2f} | {r['zh>zh']:.2f} | {r['en>zh']:.2f} | "
            f"{r['zh>en']:.2f} | {m['mrr@10']:.2f} | {m['both@10']:.2f} | "
            f"{m['paraphrase_same_lang@5']:.2f} |"
        )
    lines += [
        "",
        "## Tokenizer probes",
        "",
        "Pass: every expected passage in the top 10 and, where given, ranked above the rival.",
        "",
        "| Probe | Query | " + " | ".join(keyword["methods"]) + " |",
        "|---|---|" + "---|" * len(keyword["methods"]),
    ]
    for probe in PROBES:
        cells = [
            "pass" if summary[f"keyword:{m}"]["probes"][probe["id"]] else "FAIL"
            for m in keyword["methods"]
        ]
        lines.append(f"| {probe['about']} | `{probe['text']}` | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Keyword index cost",
        "",
        f"Corpus repeated {SCALE_COPIES} times; jieba dictionary load "
        f"{keyword['jieba_init_s']} s (once per process).",
        "",
        "| Method | Rows | Build s | Index MB | Mean query ms |",
        "|---|---|---|---|---|",
    ]
    for name, data in keyword["methods"].items():
        s = data["scale"]
        lines.append(
            f"| {name} | {s['rows']} | {s['build_s']} | {s['db_bytes'] / 1e6:.1f} | "
            f"{s['mean_query_ms']} |"
        )
    lines += [
        "",
        "## Embedding models",
        "",
        "| Key | Model | License | Params (M) | Dims | Max tokens | Disk MB | Load s | "
        "Short/s | Long/s | Peak RSS MB |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key, data in embeds.items():
        disk = f"{data['disk_bytes'] / 1e6:.0f}" if data["disk_bytes"] else "?"
        params = f"{data['params'] / 1e6:.0f}" if data["params"] else "same"
        lines.append(
            f"| {key} | `{data['model']}` | {data['license']} | {params} | "
            f"{data['dims']} | {data.get('max_seq_length', '?')} | {disk} | {data['load_s']} | "
            f"{data['passages_per_s']} | {data.get('long_passages_per_s', '?')} | "
            f"{data['peak_rss_bytes'] / 1e6:.0f} |"
        )
    if embeds:
        mean_chars = next(iter(embeds.values()))["bench_mean_chars"]
        first = next(iter(embeds.values()))
        lines += [
            "",
            f"Short: 256 passages of {mean_chars} characters on average. Long: 64 chunks of "
            f"{first.get('long_mean_chars', '?')} characters (ten passages each); a model "
            "truncates input beyond its max tokens. Load time is from the local cache. Disk is "
            "the Hugging Face cache size of the files the library downloaded.",
        ]
        for key, data in embeds.items():
            parity = data.get("parity_with_sentence_transformers")
            if parity:
                lines.append(
                    f"`{key}` (onnxruntime {data['versions'].get('onnxruntime')}) "
                    "ranks match sentence-transformers for "
                    f"{parity['top10_identical']:.0%} of queries and probes (top 10) and "
                    f"{parity['top1_identical']:.0%} (top 1)."
                )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> None:
    command = argv[0] if argv else "all"
    if command == "keyword":
        run_keyword()
    elif command == "embed":
        run_embed(argv[1])
    elif command in ("embed-all", "all"):
        if command == "all":
            run_keyword()
        for key in MODELS:
            print(f"embedding with {key}", flush=True)
            subprocess.run([sys.executable, __file__, "embed", key], check=True)
        if command == "all":
            run_report()
    elif command == "report":
        run_report()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
