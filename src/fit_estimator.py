"""Estimate whether a Hugging Face model can run on this machine.

Given a HF model URL or repo id, fetch the metadata, derive the model
shape (params, layers, KV heads, head dim, MoE-ness), and project the
memory footprint at a chosen precision and context length. Compare
against `machine_specs.yaml` to produce a verdict:

  - ``fits_gpu``     — weights + KV cache fit on the primary GPU
  - ``fits_offload`` — fits in (VRAM + system RAM); usable but slow
  - ``no_fit``       — exceeds total addressable memory

This is an estimate. Actual footprint depends on activations, batch
size, framework overhead, and whether MoE experts can be partially
offloaded — we add a fixed overhead and otherwise try to err on the
side of "this is the bare minimum to load the weights".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Bytes-per-parameter for common dtypes. GGUF quants are average bits
# including per-block overhead. Sources: llama.cpp quant table (k-quant
# block sizes), HF safetensors dtype names.
DTYPE_BYTES: Dict[str, float] = {
    "F32": 4.0, "FP32": 4.0,
    "F16": 2.0, "FP16": 2.0, "BF16": 2.0,
    "F8_E4M3": 1.0, "F8_E5M2": 1.0, "FP8": 1.0,
    "F4": 0.5, "FP4": 0.5,
    "I8": 1.0, "INT8": 1.0,
    "Q8_0":   8.50 / 8,
    "Q6_K":   6.56 / 8,
    "Q5_K_M": 5.50 / 8,
    "Q5_K_S": 5.40 / 8,
    "Q5_0":   5.50 / 8,
    "Q4_K_M": 4.50 / 8,
    "Q4_K_S": 4.40 / 8,
    "Q4_0":   4.55 / 8,
    "IQ4_XS": 4.25 / 8,
    "IQ4_NL": 4.50 / 8,
    "Q3_K_M": 3.90 / 8,
    "Q3_K_S": 3.50 / 8,
    "Q2_K":   3.00 / 8,
    "IQ3_XXS": 3.00 / 8,
    "IQ2_XXS": 2.06 / 8,
}

# Quant choices we expose in the UI dropdown, in rough quality order.
QUANT_CHOICES: List[str] = [
    "BF16", "FP16", "FP8", "FP4",
    "Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "IQ4_XS", "Q3_K_M", "Q2_K",
]

# Fixed overhead for activations + framework state, on top of weights+KV.
OVERHEAD_GB: float = 1.0

# Headroom factors — leave room for OS, browser, other GPU users.
GPU_HEADROOM: float = 0.92
TOTAL_HEADROOM: float = 0.85

_HF_URL_PATTERNS = (
    re.compile(r"huggingface\.co/([^/\s]+/[^/\s?#]+)"),
    re.compile(r"hf\.co/([^/\s]+/[^/\s?#]+)"),
)


@dataclass(frozen=True)
class ModelShape:
    """Architecture-derived dimensions used for KV-cache math."""
    total_params_b: float
    active_params_b: Optional[float]   # None for dense models
    num_layers: Optional[int]
    num_kv_heads: Optional[int]
    head_dim: Optional[int]
    max_position_embeddings: Optional[int]
    architecture: Optional[str]
    is_moe: bool
    num_experts: Optional[int]
    weight_dtype_summary: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class HFModelInfo:
    repo_id: str
    shape: Optional[ModelShape]
    file_total_bytes: Optional[int]    # sum of safetensors/gguf siblings
    tags: List[str]
    pipeline_tag: Optional[str]
    error: Optional[str]               # set when fetch failed; shape may still be None


@dataclass(frozen=True)
class FitEstimate:
    weights_gb: float
    kv_cache_gb: float
    overhead_gb: float
    total_gb: float
    verdict: str                       # "fits_gpu" | "fits_offload" | "no_fit" | "unknown_specs"
    summary: str                       # one-line human description
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def parse_repo_id(text: str) -> Optional[str]:
    """Extract ``owner/name`` from a HF URL or accept it directly."""
    text = (text or "").strip()
    if not text:
        return None
    for pat in _HF_URL_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).rstrip("/")
    if "/" in text and " " not in text and "://" not in text:
        return text.strip("/")
    return None


# ---------------------------------------------------------------------------
# HF metadata fetch
# ---------------------------------------------------------------------------

def fetch_hf_info(repo_id: str) -> HFModelInfo:
    """Pull metadata from HF Hub. Best-effort — never raises."""
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        return HFModelInfo(repo_id, None, None, [], None, f"huggingface_hub missing: {exc}")

    api = HfApi()
    try:
        info = api.model_info(repo_id, files_metadata=True)
    except Exception as exc:
        return HFModelInfo(repo_id, None, None, [], None, f"HF fetch failed: {exc}")

    # info.config from the API is the slim variant (architectures,
    # model_type only). Always pull the full config.json for the
    # architecture fields we need for KV-cache math.
    config = _fetch_config_json(repo_id)
    if not config:
        config = getattr(info, "config", None) or {}

    weight_dtype: Dict[str, int] = {}
    total_params: Optional[int] = None
    safetensors = getattr(info, "safetensors", None)
    if safetensors is not None:
        params = getattr(safetensors, "parameters", None) or {}
        weight_dtype = {str(k): int(v) for k, v in params.items()}
        total_params = getattr(safetensors, "total", None)
        if total_params is None and weight_dtype:
            total_params = sum(weight_dtype.values())

    siblings = getattr(info, "siblings", None) or []
    safetensor_bytes = 0
    gguf_files: List[Tuple[str, int]] = []
    bin_bytes = 0
    for sib in siblings:
        size = int(getattr(sib, "size", None) or 0)
        name = (getattr(sib, "rfilename", "") or "").lower()
        if size <= 0:
            continue
        if name.endswith(".safetensors"):
            safetensor_bytes += size
        elif name.endswith(".gguf"):
            gguf_files.append((name, size))
        elif name.endswith(".bin") and "pytorch_model" in name:
            bin_bytes += size

    file_total: Optional[int] = None
    if safetensor_bytes:
        file_total = safetensor_bytes
    elif gguf_files:
        # Multi-quant GGUF repos contain many copies of the same model;
        # summing them all overcounts. Use the smallest as a single-file
        # representative footprint.
        file_total = min(size for _, size in gguf_files)
    elif bin_bytes:
        file_total = bin_bytes

    if total_params is None and config:
        total_params = _params_from_config(config)

    # Don't fall back to file_total for param count: GGUF compression
    # ratios vary wildly per quant, and multi-shard safetensors repos
    # may include duplicate weight copies.

    shape = _build_shape(config, total_params, weight_dtype) if total_params else None

    return HFModelInfo(
        repo_id=repo_id,
        shape=shape,
        file_total_bytes=file_total,
        tags=list(getattr(info, "tags", []) or []),
        pipeline_tag=getattr(info, "pipeline_tag", None),
        error=None,
    )


def _fetch_config_json(repo_id: str) -> Dict:
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(repo_id, "config.json")
        import json
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as exc:
        logger.debug("config.json fetch failed for %s: %s", repo_id, exc)
        return {}


def _params_from_config(config: Dict) -> Optional[int]:
    n_layer = config.get("num_hidden_layers")
    hidden = config.get("hidden_size")
    vocab = config.get("vocab_size")
    if not (n_layer and hidden):
        return None
    inter = config.get("intermediate_size") or hidden * 4
    n_experts = config.get("num_local_experts") or config.get("n_routed_experts") or 1
    per_layer_attn = 4 * hidden * hidden
    per_layer_ffn = 3 * hidden * inter * max(1, n_experts)
    body = n_layer * (per_layer_attn + per_layer_ffn)
    embed = (vocab or 32000) * hidden * 2
    return int(body + embed)


def _build_shape(config: Dict, total_params: Optional[int], weight_dtype: Dict[str, int]) -> ModelShape:
    n_layer = config.get("num_hidden_layers")
    n_heads = config.get("num_attention_heads")
    n_kv_heads = config.get("num_key_value_heads") or n_heads
    hidden = config.get("hidden_size")
    head_dim = config.get("head_dim")
    if not head_dim and hidden and n_heads:
        head_dim = hidden // n_heads

    arch_list = config.get("architectures") or []
    arch = arch_list[0] if arch_list else None

    n_experts = (config.get("num_local_experts") or config.get("n_routed_experts")
                 or config.get("num_experts"))
    n_active = (config.get("num_experts_per_tok") or config.get("n_experts_per_tok")
                or config.get("moe_topk"))
    is_moe = bool(n_experts and n_experts > 1)

    active_params: Optional[float] = None
    if is_moe and total_params and n_experts and n_active:
        # Crude estimate: experts are ~95 % of weights for big-MoE
        # archs (DeepSeek, Mixtral); active per token = (top-k / experts)
        # of expert weights, plus the shared 5 %.
        ratio = float(n_active) / float(n_experts)
        total_b = total_params / 1e9
        active_params = total_b * 0.05 + total_b * 0.95 * ratio

    return ModelShape(
        total_params_b=(total_params or 0) / 1e9,
        active_params_b=active_params,
        num_layers=int(n_layer) if n_layer else None,
        num_kv_heads=int(n_kv_heads) if n_kv_heads else None,
        head_dim=int(head_dim) if head_dim else None,
        max_position_embeddings=config.get("max_position_embeddings"),
        architecture=arch,
        is_moe=is_moe,
        num_experts=int(n_experts) if n_experts else None,
        weight_dtype_summary=weight_dtype,
    )


# ---------------------------------------------------------------------------
# Footprint math
# ---------------------------------------------------------------------------

def weights_gb_for(total_params_b: float, quant: str) -> float:
    bpp = DTYPE_BYTES.get(quant.upper())
    if bpp is None:
        bpp = 2.0  # default to FP16
    return total_params_b * 1e9 * bpp / (1024 ** 3)


def kv_cache_gb(shape: ModelShape, context_tokens: int, kv_bytes: int = 2) -> Optional[float]:
    """KV cache size for a single sequence at the given context.

    Formula: 2 (K and V) * num_layers * num_kv_heads * head_dim *
    context_tokens * bytes_per_element. Returns None if the architecture
    fields are unknown.
    """
    if not (shape.num_layers and shape.num_kv_heads and shape.head_dim):
        return None
    elements = (2 * shape.num_layers * shape.num_kv_heads
                * shape.head_dim * context_tokens)
    return elements * kv_bytes / (1024 ** 3)


def estimate_fit(
    shape: ModelShape,
    quant: str,
    context_tokens: int,
    vram_gb: Optional[float],
    ram_gb: Optional[float],
    kv_bytes: int = 2,
) -> FitEstimate:
    """Combine weights + KV + overhead and produce a verdict."""
    weights = weights_gb_for(shape.total_params_b, quant)
    kv = kv_cache_gb(shape, context_tokens, kv_bytes=kv_bytes) or 0.0
    notes: List[str] = []
    if not shape.num_layers:
        notes.append("KV cache estimate skipped — architecture fields not in config.json.")
    overhead = OVERHEAD_GB
    total = weights + kv + overhead

    if vram_gb is None or ram_gb is None:
        return FitEstimate(weights, kv, overhead, total, "unknown_specs",
                           "Machine specs not available — fill `config/machine_specs.yaml`.",
                           notes)

    gpu_budget = vram_gb * GPU_HEADROOM
    total_budget = (vram_gb + ram_gb) * TOTAL_HEADROOM

    if total <= gpu_budget:
        verdict = "fits_gpu"
        summary = (f"Fits fully on GPU "
                   f"({total:.1f} / {vram_gb:.0f} GB VRAM, {GPU_HEADROOM:.0%} budget).")
    elif total <= total_budget:
        verdict = "fits_offload"
        moe_hint = " — MoE expert offload via `-ot ... =CPU` is the usual move." if shape.is_moe else ""
        summary = (f"Needs RAM offload "
                   f"({total:.1f} GB vs {vram_gb:.0f} GB VRAM + {ram_gb:.0f} GB RAM). "
                   f"Slower than full-GPU.{moe_hint}")
    else:
        verdict = "no_fit"
        summary = (f"Will not fit practically: {total:.1f} GB needed vs "
                   f"{total_budget:.0f} GB practical budget "
                   f"({vram_gb:.0f} GB VRAM + {ram_gb:.0f} GB RAM × "
                   f"{TOTAL_HEADROOM:.0%} headroom).")

    if shape.is_moe:
        notes.append(
            "MoE model: full expert weights still need to be resident "
            "somewhere. Active-param count only reduces compute per token, "
            "not memory."
        )
    return FitEstimate(weights, kv, overhead, total, verdict, summary, notes)


# ---------------------------------------------------------------------------
# Quant guessing from repo name
# ---------------------------------------------------------------------------

def guess_quant_from_repo_id(repo_id: str) -> Optional[str]:
    upper = repo_id.upper()
    for quant in sorted(DTYPE_BYTES.keys(), key=len, reverse=True):
        if quant in upper:
            return quant
    return None


def suggest_default_quant(info: HFModelInfo) -> Tuple[str, str]:
    """Return (quant, reason) — best guess for the dropdown default."""
    guess = guess_quant_from_repo_id(info.repo_id)
    if guess:
        return guess, f"inferred from repo name ({guess})"
    if info.shape and info.shape.weight_dtype_summary:
        dominant = max(info.shape.weight_dtype_summary.items(), key=lambda kv: kv[1])[0]
        if dominant.upper() in DTYPE_BYTES:
            return dominant.upper(), f"dominant safetensors dtype ({dominant})"
    return "Q4_K_M", "fallback default for local llama.cpp inference"
