"""Pre-flight comparison for the ``audio_translate`` role: ES speech -> EN text.

Three paths run on the same public FLEURS Spanish clips, each scored against
FLEURS' own English text for the same sentence:

  * Canary-1b-v2 on the Mac ANE (``mac/parakeet-worker`` ``CanaryWorker``).
  * ``whisper_translate`` through the hub (``POST /v1/audio/translations``).
  * The two-stage default through the hub: whisper transcribes Spanish, then
    ``gemma4_26b`` translates and polishes the transcript.

    python scripts/canary_preflight.py prepare --out <dir>
    python scripts/canary_preflight.py hub     --out <dir> [--base-url http://127.0.0.1:8000]
    python scripts/canary_preflight.py report  --out <dir> --canary <canary.jsonl>

``prepare`` downloads FLEURS ``es_419`` / ``en_us`` test data (CC-BY-4.0,
public) into ``--out``, which belongs outside the tree. The Canary leg runs on
the Mac: feed it ``clips/<file>`` paths, one per line, and save its stdout.

BLEU here is a 13a-tokenised corpus BLEU close to sacreBLEU's default, not
byte-identical to it. The references are one human English sentence per clip,
so the number ranks the paths against each other more reliably than it grades
any one of them in absolute terms.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import random
import re
import sys
import tarfile
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

import httpx

log = logging.getLogger("canary_preflight")

_FLEURS = "https://huggingface.co/datasets/google/fleurs/resolve/main/data/{lang}"
_SRC_LANG, _REF_LANG = "es_419", "en_us"
_WINDOW_S = 15.0  # Canary's fixed decode window; longer clips take the VAD path
_SAMPLE_RATE = 16000
_SEED = 920  # fixed so a re-run picks the same clips
_HUB_TIMEOUT_S = 600.0  # the first whisper_translate call loads the model on demand
_POLISH_PROMPT = (
    "Translate the Spanish dictation transcript into English. Remove filler words "
    "and false starts, keep the meaning and register, add nothing. Reply with the "
    "English text only."
)


# --------------------------------------------------------------------------- #
# prepare
# --------------------------------------------------------------------------- #
def _download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=600.0) as resp:
        resp.raise_for_status()
        # runtime-write-exempt: --out is the caller's folder, outside the tree
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    tmp.replace(dest)


def _read_tsv(path: Path) -> List[List[str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE) if len(r) >= 6]
    if not rows:
        raise SystemExit(f"{path} parsed to 0 rows; delete it and re-run")
    return rows


def prepare(out: Path, short: int, long_: int) -> int:
    cache = out / "cache"
    # runtime-write-exempt: --out is the caller's folder, outside the tree
    cache.mkdir(parents=True, exist_ok=True)
    src_tsv, ref_tsv = cache / "src.tsv", cache / "ref.tsv"
    tarball = cache / "src_audio.tar.gz"
    _download(_FLEURS.format(lang=_SRC_LANG) + "/test.tsv", src_tsv)
    _download(_FLEURS.format(lang=_REF_LANG) + "/test.tsv", ref_tsv)
    _download(_FLEURS.format(lang=_SRC_LANG) + "/audio/test.tar.gz", tarball)

    refs: Dict[str, str] = {}
    for row in _read_tsv(ref_tsv):
        refs.setdefault(row[0], row[2])  # the same sentence id is read by several speakers
    by_id: Dict[str, List[str]] = {}
    for row in _read_tsv(src_tsv):
        if row[0] in refs:
            by_id.setdefault(row[0], row)  # first speaker per sentence
    pool = list(by_id.values())
    random.Random(_SEED).shuffle(pool)

    def seconds(row: List[str]) -> float:
        return int(row[5]) / _SAMPLE_RATE

    chosen = [r for r in pool if seconds(r) <= _WINDOW_S][:short]
    chosen += [r for r in pool if seconds(r) > _WINDOW_S][:long_]
    wanted = {r[1]: r for r in chosen}

    clips = out / "clips"
    # runtime-write-exempt: --out is the caller's folder, outside the tree
    clips.mkdir(exist_ok=True)
    with tarfile.open(tarball) as tar:
        for member in tar:
            name = Path(member.name).name
            if name in wanted and member.isfile():
                # runtime-write-exempt: --out is the caller's folder, outside the tree
                (clips / name).write_bytes(tar.extractfile(member).read())  # type: ignore[union-attr]
    missing = [n for n in wanted if not (clips / n).exists()]
    if missing:
        raise SystemExit(f"{len(missing)} selected clips were not in the tarball: {missing[:3]}")

    manifest = [
        {"id": r[0], "file": r[1], "seconds": round(seconds(r), 2), "es": r[2], "ref_en": refs[r[0]]}
        for r in chosen
    ]
    # runtime-write-exempt: --out is the caller's folder, outside the tree
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("✅ %d clips (%d short, %d long) -> %s", len(manifest), short, long_, out / "manifest.json")
    return 0


# --------------------------------------------------------------------------- #
# hub legs
# --------------------------------------------------------------------------- #
def _post_audio(base: str, path: str, wav: Path, data: Dict[str, str]) -> str:
    with wav.open("rb") as fh:
        resp = httpx.post(
            f"{base}{path}", files={"file": (wav.name, fh, "audio/wav")}, data=data, timeout=_HUB_TIMEOUT_S
        )
    resp.raise_for_status()
    body = resp.text.strip()
    if body.startswith("{"):
        return str(json.loads(body).get("text", body)).strip()
    return body


def _polish(base: str, model: str, es_text: str, think: bool) -> str:
    body: Dict[str, object] = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1000,
        "messages": [{"role": "system", "content": _POLISH_PROMPT}, {"role": "user", "content": es_text}],
    }
    if not think:
        # A translate+polish call needs no reasoning; with it on, one clip took 140 s
        # and a capped reply came back as the model's reasoning instead of a translation.
        body["chat_template_kwargs"] = {"enable_thinking": False}
    resp = httpx.post(f"{base}/v1/chat/completions", json=body, timeout=_HUB_TIMEOUT_S)
    resp.raise_for_status()
    return str(resp.json()["choices"][0]["message"]["content"]).strip()


def _timed(fn) -> Dict[str, object]:
    start = time.perf_counter()
    try:
        return {"ok": True, "text": fn(), "elapsed_s": round(time.perf_counter() - start, 3)}
    except Exception as exc:  # one clip failing must not hide the others
        log.warning("❌ %s: %s", type(exc).__name__, exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "elapsed_s": None}


def run_hub(out: Path, base: str, stage1: str, stage2: str, think: bool) -> int:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    results = []
    for i, clip in enumerate(manifest, 1):
        wav = out / "clips" / clip["file"]
        log.info("ℹ️ [%d/%d] %s (%.1fs)", i, len(manifest), clip["id"], clip["seconds"])
        translate = _timed(
            lambda: _post_audio(
                base, "/v1/audio/translations", wav, {"model": "whisper_translate", "response_format": "text"}
            )
        )
        asr = _timed(
            lambda: _post_audio(
                base, "/v1/audio/transcriptions", wav,
                {"model": stage1, "language": "es", "response_format": "text"},
            )
        )
        two_stage: Dict[str, object] = {"ok": False, "error": "stage 1 failed", "elapsed_s": None}
        if asr["ok"]:
            polish = _timed(lambda: _polish(base, stage2, str(asr["text"]), think))
            two_stage = dict(polish)
            two_stage["es_transcript"] = asr["text"]
            two_stage["stage1_s"] = asr["elapsed_s"]
            if polish["ok"]:
                two_stage["elapsed_s"] = round(float(asr["elapsed_s"]) + float(polish["elapsed_s"]), 3)  # type: ignore[arg-type]
        results.append({"id": clip["id"], "whisper_translate": translate, "two_stage": two_stage})
    # runtime-write-exempt: --out is the caller's folder, outside the tree
    (out / "hub_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("✅ hub legs -> %s", out / "hub_results.json")
    return 0


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def _tokenize(text: str) -> List[str]:
    """mteval 13a tokenisation (case kept)."""
    text = f" {text} "
    text = re.sub(r"([\{-\~\[-\` -\&\(-\+\:-\@\/])", r" \1 ", text)
    text = re.sub(r"([^0-9])([\.,])", r"\1 \2 ", text)
    text = re.sub(r"([\.,])([^0-9])", r" \1 \2", text)
    text = re.sub(r"([0-9])(-)", r"\1 \2 ", text)
    return text.split()


def _ngrams(tokens: List[str], n: int) -> Counter:
    return Counter(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def corpus_bleu(hyps: List[str], refs: List[str]) -> float:
    matches, totals = [0] * 4, [0] * 4
    hyp_len = ref_len = 0
    for hyp, ref in zip(hyps, refs):
        h, r = _tokenize(hyp), _tokenize(ref)
        hyp_len += len(h)
        ref_len += len(r)
        for n in range(1, 5):
            hn, rn = _ngrams(h, n), _ngrams(r, n)
            matches[n - 1] += sum(min(c, rn[g]) for g, c in hn.items())
            totals[n - 1] += max(len(h) - n + 1, 0)
    if hyp_len == 0 or min(matches) == 0:
        return 0.0
    precision = sum(math.log(m / t) for m, t in zip(matches, totals)) / 4
    brevity = 1.0 if hyp_len > ref_len else math.exp(1 - ref_len / hyp_len)
    return 100.0 * brevity * math.exp(precision)


def unigram_f1(hyp: str, ref: str) -> float:
    h, r = Counter(w.lower() for w in _tokenize(hyp) if w.isalnum()), Counter(
        w.lower() for w in _tokenize(ref) if w.isalnum()
    )
    overlap = sum((h & r).values())
    if not overlap:
        return 0.0
    p, q = overlap / sum(h.values()), overlap / sum(r.values())
    return 2 * p * q / (p + q)


def _median(values: List[float]) -> Optional[float]:
    s = sorted(values)
    return s[len(s) // 2] if s else None


def build_report(out: Path, canary_path: Path) -> int:
    manifest = {c["id"]: c for c in json.loads((out / "manifest.json").read_text(encoding="utf-8"))}
    ordered = list(manifest)
    hub = {r["id"]: r for r in json.loads((out / "hub_results.json").read_text(encoding="utf-8"))}
    canary_lines = [
        json.loads(line)
        for line in canary_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("{")
    ]
    if len(canary_lines) != len(ordered):
        raise SystemExit(f"canary results {len(canary_lines)} != manifest {len(ordered)}; clip order must match")
    paths: Dict[str, Dict[str, dict]] = {"canary": {}, "whisper_translate": {}, "two_stage": {}}
    for cid, line in zip(ordered, canary_lines):
        paths["canary"][cid] = {"ok": line["ok"], "text": line.get("text"), "elapsed_s": line.get("elapsedS"),
                                "peak_rss_mb": line.get("peakRssMb"), "vad": line.get("vad"), "error": line.get("error")}
        paths["whisper_translate"][cid] = hub[cid]["whisper_translate"]
        paths["two_stage"][cid] = hub[cid]["two_stage"]

    summary = {}
    for name, per_clip in paths.items():
        for label, subset in (("all", ordered), ("short", [c for c in ordered if manifest[c]["seconds"] <= _WINDOW_S]),
                              ("long", [c for c in ordered if manifest[c]["seconds"] > _WINDOW_S])):
            ok = [c for c in subset if per_clip[c]["ok"]]
            hyps = [str(per_clip[c]["text"]) for c in ok]
            refs = [manifest[c]["ref_en"] for c in ok]
            lat = [float(per_clip[c]["elapsed_s"]) for c in ok]
            rtfx = [manifest[c]["seconds"] / float(per_clip[c]["elapsed_s"]) for c in ok if per_clip[c]["elapsed_s"]]
            summary[f"{name}/{label}"] = {
                "clips": len(subset), "ok": len(ok), "bleu": round(corpus_bleu(hyps, refs), 2) if ok else None,
                "mean_f1": round(sum(unigram_f1(h, r) for h, r in zip(hyps, refs)) / len(ok), 3) if ok else None,
                "median_s": _median(lat), "median_rtfx": _median(rtfx),
            }
    summary["canary_peak_rss_mb"] = max(
        (float(v["peak_rss_mb"]) for v in paths["canary"].values() if v.get("peak_rss_mb")), default=None
    )
    clips = [
        {"id": cid, "seconds": manifest[cid]["seconds"], "es": manifest[cid]["es"], "ref_en": manifest[cid]["ref_en"],
         **{name: paths[name][cid] for name in paths}}
        for cid in ordered
    ]
    # runtime-write-exempt: --out is the caller's folder, outside the tree
    (out / "report.json").write_text(json.dumps({"summary": summary, "clips": clips}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    prep = sub.add_parser("prepare", help="download FLEURS data and pick clips")
    prep.add_argument("--short", type=int, default=12, help="clips of at most 15 s")
    prep.add_argument("--long", dest="long_", type=int, default=4, help="clips over 15 s (Canary VAD path)")
    hub = sub.add_parser("hub", help="run whisper_translate and the two-stage path through the hub")
    hub.add_argument("--base-url", default="http://127.0.0.1:8000")
    hub.add_argument("--stage1", default="whisper", help="ASR model for the two-stage path")
    hub.add_argument("--stage2", default="gemma4_26b", help="LLM for the two-stage translate+polish")
    hub.add_argument("--think", action="store_true", help="leave the stage-2 model's thinking on (slow)")
    rep = sub.add_parser("report", help="merge the three legs and score them")
    rep.add_argument("--canary", type=Path, required=True, help="CanaryWorker stdout (JSON lines, manifest order)")
    for p in (prep, hub, rep):
        p.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.cmd == "prepare":
        return prepare(args.out, args.short, args.long_)
    if args.cmd == "hub":
        return run_hub(args.out, args.base_url.rstrip("/"), args.stage1, args.stage2, args.think)
    return build_report(args.out, args.canary)


if __name__ == "__main__":
    sys.exit(main())
