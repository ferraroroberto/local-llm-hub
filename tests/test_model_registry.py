"""Registry parsing and per-host filtering."""

from __future__ import annotations

import yaml

from src import host_profile, model_registry


def _write_config(tmp_path, content: dict):
    cfg = tmp_path / "models.yaml"
    cfg.write_text(yaml.safe_dump(content), encoding="utf-8")
    return cfg


def _patch_config_path(monkeypatch, cfg_path):
    # _load_config in both modules reads module-level CONFIG_PATH at call time,
    # so monkeypatching the attribute (no reload) is enough.
    monkeypatch.setattr(host_profile, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(model_registry, "CONFIG_PATH", cfg_path, raising=False)


def test_resolves_hostname_match(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path, {
        "hub": {"port": 8000},
        "hosts": {
            "pc":  {"platform": "win32", "hostname": "TEST-PC", "enabled": ["qwen"]},
            "mac": {"platform": "darwin", "default": True, "enabled": []},
        },
        "models": {
            "qwen": {"display_name": "qwen3.5-9b", "backend": "openai", "port": 8081},
            "claude": {"display_name": "claude-haiku-4-5", "backend": "claude"},
        },
    })
    _patch_config_path(monkeypatch, cfg)
    monkeypatch.delenv("LOCAL_LLM_HUB_HOST", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "test-pc")

    prof = host_profile.resolve()
    assert prof.id == "pc"
    assert prof.enabled == ["qwen"]

    ids = {m.id for m in model_registry.enabled_models()}
    assert "claude" in ids and "qwen" in ids


def test_env_override_wins(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path, {
        "hub": {"port": 8000},
        "hosts": {
            "pc":  {"platform": "win32", "default": True, "enabled": ["qwen", "glm"]},
            "mac": {"platform": "darwin", "enabled": ["qwen"]},
        },
        "models": {
            "qwen": {"display_name": "qwen3.5-9b", "backend": "openai", "port": 8081},
            "glm":  {"display_name": "glm-4.5-air", "backend": "openai", "port": 8082},
            "claude": {"display_name": "claude-haiku-4-5", "backend": "claude"},
        },
    })
    _patch_config_path(monkeypatch, cfg)
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "mac")

    prof = host_profile.resolve()
    assert prof.id == "mac"
    ids = {m.id for m in model_registry.enabled_models()}
    assert "glm" not in ids
    assert "qwen" in ids


def test_resolve_by_alias(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path, {
        "hub": {"port": 8000},
        "hosts": {
            "pc": {"platform": "win32", "default": True, "enabled": ["qwen"]},
        },
        "models": {
            "qwen": {
                "display_name": "qwen3.5-9b",
                "backend": "openai",
                "port": 8081,
                "aliases": ["qwen", "qwen3.5"],
            },
        },
    })
    _patch_config_path(monkeypatch, cfg)
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "pc")

    m = model_registry.resolve("qwen3.5")
    assert m is not None
    assert m.id == "qwen"
    assert model_registry.resolve("nonexistent") is None


def test_gemma_per_host_filtering(tmp_path, monkeypatch):
    """Both gemma4 rows must show on pc-cuda and stay hidden on mac-mini-m4."""
    cfg = _write_config(tmp_path, {
        "hub": {"port": 8000},
        "hosts": {
            "pc-cuda":     {"platform": "win32", "default": True, "enabled": ["qwen", "glm", "gemma4_e4b", "gemma4_26b"]},
            "mac-mini-m4": {"platform": "darwin", "enabled": ["qwen"]},
        },
        "models": {
            "qwen":       {"display_name": "qwen3.5-9b",        "backend": "openai", "port": 8081},
            "glm":        {"display_name": "glm-4.5-air",       "backend": "openai", "port": 8082},
            "gemma4_e4b": {"display_name": "gemma4-e4b-it",     "backend": "openai", "port": 8086},
            "gemma4_26b": {"display_name": "gemma4-26b-a4b-it", "backend": "openai", "port": 8087},
        },
    })
    _patch_config_path(monkeypatch, cfg)

    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "pc-cuda")
    names_pc = {m.display_name for m in model_registry.enabled_models()}
    assert {"qwen3.5-9b", "glm-4.5-air", "gemma4-e4b-it", "gemma4-26b-a4b-it"} <= names_pc
    assert model_registry.resolve("gemma4-e4b-it").port == 8086
    assert model_registry.resolve("gemma4-26b-a4b-it").port == 8087

    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "mac-mini-m4")
    names_mac = {m.display_name for m in model_registry.enabled_models()}
    assert "gemma4-e4b-it" not in names_mac
    assert "gemma4-26b-a4b-it" not in names_mac
    assert model_registry.resolve("gemma4-e4b-it") is None
    assert model_registry.resolve("gemma4-26b-a4b-it") is None


def test_whisper_entry(tmp_path, monkeypatch):
    """Whisper is a distinct backend; runs on 8090, surfaces on pc-cuda only."""
    cfg = _write_config(tmp_path, {
        "hub": {"port": 8000},
        "hosts": {
            "pc-cuda":     {"platform": "win32", "default": True, "enabled": ["qwen", "whisper"]},
            "mac-mini-m4": {"platform": "darwin", "enabled": ["qwen"]},
        },
        "models": {
            "qwen":    {"display_name": "qwen3.5-9b",    "backend": "openai",  "port": 8081},
            "whisper": {
                "display_name": "whisper-large-v3-turbo",
                "backend": "whisper",
                "engine": "whisper-server",
                "port": 8090,
                "hf_repo": "ggerganov/whisper.cpp",
                "hf_pattern": "ggml-large-v3-turbo.bin",
                "model_path": "models/ggml-large-v3-turbo.bin",
                "args": ["--threads", "4", "--gpu", "1"],
            },
        },
    })
    _patch_config_path(monkeypatch, cfg)

    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "pc-cuda")
    m = model_registry.resolve("whisper-large-v3-turbo")
    assert m is not None
    assert m.id == "whisper"
    assert m.backend == "whisper"
    assert m.engine == "whisper-server"
    assert m.port == 8090
    assert m.url == "http://127.0.0.1:8090/v1"
    assert "--gpu" in m.args

    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "mac-mini-m4")
    assert model_registry.resolve("whisper-large-v3-turbo") is None


def test_whisper_translate_lazy_entry(tmp_path, monkeypatch):
    """Lazy translate slot lives next to turbo: same backend, different engine + port."""
    cfg = _write_config(tmp_path, {
        "hub": {"port": 8000},
        "hosts": {
            "pc-cuda": {"platform": "win32", "default": True,
                        "enabled": ["whisper", "whisper_translate"]},
        },
        "models": {
            "whisper": {
                "display_name": "whisper-large-v3-turbo",
                "backend": "whisper",
                "engine": "whisper-server",
                "port": 8090,
                "model_path": "models/ggml-large-v3-turbo.bin",
            },
            "whisper_translate": {
                "display_name": "whisper-medium-translate",
                "backend": "whisper",
                "engine": "whisper-server-lazy",
                "port": 8091,
                "internal_port": 18091,
                "idle_seconds": 300,
                "hf_repo": "ggerganov/whisper.cpp",
                "hf_pattern": "ggml-medium.bin",
                "model_path": "models/ggml-medium.bin",
                "args": ["-ng", "--inference-path", "/v1/audio/transcriptions"],
            },
        },
    })
    _patch_config_path(monkeypatch, cfg)
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "pc-cuda")

    # Both whisper rows surface side-by-side on the same host.
    ids = {m.id for m in model_registry.enabled_models()}
    assert {"whisper", "whisper_translate"} <= ids

    turbo = model_registry.resolve("whisper-large-v3-turbo")
    assert turbo.port == 8090
    assert turbo.engine == "whisper-server"

    lazy = model_registry.resolve("whisper-medium-translate")
    assert lazy is not None
    assert lazy.id == "whisper_translate"
    assert lazy.backend == "whisper"
    assert lazy.engine == "whisper-server-lazy"
    assert lazy.port == 8091
    assert lazy.internal_port == 18091
    assert lazy.idle_seconds == 300
    assert "-ng" in lazy.args
    # External contract URL still matches whisper-server's shape.
    assert lazy.url == "http://127.0.0.1:8091/v1"


def test_tts_entry(tmp_path, monkeypatch):
    """TTS is a distinct backend (engine tts-server); two engines side by side."""
    cfg = _write_config(tmp_path, {
        "hub": {"port": 8000},
        "hosts": {
            "pc-cuda":     {"platform": "win32", "default": True,
                            "enabled": ["chatterbox", "orpheus"]},
            "mac-mini-m4": {"platform": "darwin", "default": True, "enabled": ["qwen"]},
        },
        "models": {
            "qwen": {"display_name": "qwen3.5-9b", "backend": "openai", "port": 8081},
            "chatterbox": {
                "display_name": "chatterbox-tts",
                "aliases": ["audio_speech"],
                "backend": "tts",
                "engine": "tts-server",
                "tts_engine": "chatterbox",
                "port": 8092,
                "args": ["--device", "auto"],
            },
            "orpheus": {
                "display_name": "orpheus-tts",
                "backend": "tts",
                "engine": "tts-server",
                "tts_engine": "orpheus",
                "port": 8093,
                "internal_port": 18093,
                "hf_repo": "isaiahbjork/orpheus-3b-0.1-ft-Q4_K_M-GGUF",
                "hf_pattern": "*q4_k_m*.gguf",
                "model_path": "models/orpheus-3b-0.1-ft-q4_k_m.gguf",
                "args": ["--device", "auto"],
            },
        },
    })
    _patch_config_path(monkeypatch, cfg)
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "pc-cuda")

    ids = {m.id for m in model_registry.enabled_models()}
    assert {"chatterbox", "orpheus"} <= ids

    # The audio_speech role alias resolves to the chatterbox row.
    cb = model_registry.resolve("audio_speech")
    assert cb is not None
    assert cb.id == "chatterbox"
    assert cb.backend == "tts"
    assert cb.engine == "tts-server"
    assert cb.tts_engine == "chatterbox"
    assert cb.port == 8092
    assert cb.model_path is None       # weights come from the HF cache, not models/
    assert cb.url == "http://127.0.0.1:8092/v1"

    orph = model_registry.resolve("orpheus-tts")
    assert orph.tts_engine == "orpheus"
    assert orph.port == 8093
    assert orph.internal_port == 18093
    assert orph.model_path == "models/orpheus-3b-0.1-ft-q4_k_m.gguf"

    # TTS rows are host-scoped: absent on the Mac mini (qwen-only).
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "mac-mini-m4")
    assert model_registry.resolve("audio_speech") is None


def test_resolve_by_registry_id(tmp_path, monkeypatch):
    """Regression: the SPA Playground dropdown sends ``m.id`` (the YAML
    key), not ``display_name``. resolve() must accept it — otherwise
    rows whose id is not also listed under aliases (qwen35_4b,
    gemma4_e4b, gemma4_26b, gemini_flash_lite in the real registry)
    400 on the Playground.
    """
    cfg = _write_config(tmp_path, {
        "hub": {"port": 8000},
        "hosts": {
            "pc": {"platform": "win32", "default": True,
                   "enabled": ["qwen35_4b", "gemma4_26b"]},
        },
        "models": {
            # id != display_name, and id NOT in aliases — same shape as
            # the real qwen35_4b / gemma4_26b rows.
            "qwen35_4b": {
                "display_name": "qwen3.5-4b",
                "backend": "openai",
                "port": 8088,
                "aliases": ["agentic_light"],
            },
            "gemma4_26b": {
                "display_name": "gemma4-26b-a4b-it",
                "backend": "openai",
                "port": 8087,
                "aliases": ["agentic_heavy"],
            },
            "claude_haiku": {
                "display_name": "claude-haiku-4-5",
                "backend": "claude",
                "aliases": ["claude_haiku"],
            },
        },
    })
    _patch_config_path(monkeypatch, cfg)
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "pc")

    # Resolve via every channel: id, display_name, alias — all three
    # land on the same model.
    by_id = model_registry.resolve("qwen35_4b")
    by_display = model_registry.resolve("qwen3.5-4b")
    by_alias = model_registry.resolve("agentic_light")
    assert by_id is not None and by_display is not None and by_alias is not None
    assert by_id.id == by_display.id == by_alias.id == "qwen35_4b"

    # The gemma row's id also resolves, even though it's not in aliases.
    assert model_registry.resolve("gemma4_26b").id == "gemma4_26b"

    # Claude row keeps working — its id is already in aliases.
    assert model_registry.resolve("claude_haiku").id == "claude_haiku"

    # ``all_names`` now includes the id so /v1/models lists every handle.
    qwen = model_registry.resolve("qwen35_4b")
    assert "qwen35_4b" in qwen.all_names
    assert "qwen3.5-4b" in qwen.all_names
    assert "agentic_light" in qwen.all_names


def test_model_url_from_port(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path, {
        "hub": {"port": 8000},
        "hosts": {"pc": {"platform": "win32", "default": True, "enabled": ["qwen"]}},
        "models": {
            "qwen": {"display_name": "qwen3.5-9b", "backend": "openai", "port": 8081},
        },
    })
    _patch_config_path(monkeypatch, cfg)
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "pc")

    m = model_registry.resolve("qwen3.5-9b")
    assert m.url == "http://127.0.0.1:8081/v1"
