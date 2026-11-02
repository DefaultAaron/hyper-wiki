# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "huggingface-hub>=1.0",
#   "numpy==2.4.6",
#   "onnxruntime>=1.24",
#   "tokenizers==0.23.3",
# ]
# ///
"""HW-008 spike: run an embedding model through ONNX Runtime without PyTorch.

    uv run experiments/hw008-bilingual-search/onnx_check.py KEY

KEY is a model key from run.py that has an ONNX export on the Hugging Face Hub.
Writes results/onnx-KEY.json with the same rankings shape as results/embed-KEY.json
and reports how often the top-10 lists match the sentence-transformers run.
"""

from __future__ import annotations

import json
import resource
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run

ONNX_FILES = {
    "minilm-l12": "onnx/model.onnx",
    "e5-small": "onnx/model.onnx",
    "granite-107m": "model.onnx",
}


def main(key: str) -> None:
    import numpy as np
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    spec = run.MODELS[key]
    repo = spec["name"]
    onnx_path = Path(hf_hub_download(repo, ONNX_FILES[key]))
    tokenizer_path = hf_hub_download(repo, "tokenizer.json")
    pooling = json.loads(Path(hf_hub_download(repo, "1_Pooling/config.json")).read_text())
    use_cls = bool(pooling.get("pooling_mode_cls_token"))

    start = time.perf_counter()
    options = ort.SessionOptions()
    options.intra_op_num_threads = run.THREADS
    session = ort.InferenceSession(str(onnx_path), options, providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(tokenizer_path)
    tokenizer.enable_truncation(512)
    tokenizer.enable_padding()
    load_s = time.perf_counter() - start
    input_names = {i.name for i in session.get_inputs()}

    def encode(texts: list[str], *, query: bool) -> Any:
        prefix = spec.get("query_prefix" if query else "passage_prefix", "")
        chunks = []
        for i in range(0, len(texts), 16):
            batch = tokenizer.encode_batch([prefix + t for t in texts[i : i + 16]])
            ids = np.array([e.ids for e in batch], dtype=np.int64)
            mask = np.array([e.attention_mask for e in batch], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in input_names:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = session.run(None, feed)[0]
            if use_cls:
                pooled = hidden[:, 0]
            else:
                weights = mask[..., None].astype(hidden.dtype)
                pooled = (hidden * weights).sum(1) / weights.sum(1)
            chunks.append(pooled / np.linalg.norm(pooled, axis=1, keepdims=True))
        return np.concatenate(chunks)

    passages = encode([p["text"] for p in run.PASSAGES], query=False)
    asked = run.QUERIES + run.PROBES
    scores = encode([q["text"] for q in asked], query=True) @ passages.T
    ids = [p["id"] for p in run.PASSAGES]
    rankings = {
        q["id"]: [ids[i] for i in np.argsort(-scores[n])[: run.TOP]] for n, q in enumerate(asked)
    }

    bench = [run.PASSAGES[i % len(run.PASSAGES)]["text"] for i in range(256)]
    encode(bench[:16], query=False)
    start = time.perf_counter()
    encode(bench, query=False)
    per_s = len(bench) / (time.perf_counter() - start)

    long_bench = run.long_passages()
    encode(long_bench[:4], query=False)
    start = time.perf_counter()
    encode(long_bench, query=False)
    long_per_s = len(long_bench) / (time.perf_counter() - start)

    parity = None
    reference = run.RESULTS / f"embed-{key}.json"
    if reference.exists():
        st = json.loads(reference.read_text(encoding="utf-8"))["rankings"]
        same = sum(st[q["id"]][:10] == rankings[q["id"]][:10] for q in asked)
        top1 = sum(st[q["id"]][0] == rankings[q["id"]][0] for q in asked)
        parity = {"top10_identical": same / len(asked), "top1_identical": top1 / len(asked)}

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    run.write_json(
        run.RESULTS / f"onnx-{key}.json",
        {
            "model": f"{repo} ({ONNX_FILES[key]})",
            "license": spec["license"],
            "versions": run.versions("onnxruntime", "tokenizers", "numpy"),
            "machine": run.machine(),
            "threads": run.THREADS,
            "params": None,
            "dims": int(passages.shape[1]),
            "disk_bytes": onnx_path.stat().st_size,
            "load_s": round(load_s, 2),
            "passages_per_s": round(per_s, 1),
            "bench_mean_chars": round(sum(len(t) for t in bench) / len(bench), 1),
            "max_seq_length": 512,
            "long_passages_per_s": round(long_per_s, 1),
            "peak_rss_bytes": peak if sys.platform == "darwin" else peak * 1024,
            "parity_with_sentence_transformers": parity,
            "rankings": rankings,
        },
    )
    print(json.dumps(parity))


if __name__ == "__main__":
    main(sys.argv[1])
