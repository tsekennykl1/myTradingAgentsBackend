"""GET /models — returns the provider+model combinations the frontend can offer."""

from __future__ import annotations

import os
from fastapi import APIRouter

router = APIRouter()

# Each entry: { id, label, provider, deep_model, quick_model }
# Built from AVAILABLE_MODELS env var + any natively-keyed providers.

def _build_model_options() -> list[dict]:
    options: list[dict] = []

    # --- New API (openai_compatible) models ---
    backend_url = os.getenv("TRADINGAGENTS_LLM_BACKEND_URL", "").strip()
    compat_key = os.getenv("OPENAI_COMPATIBLE_API_KEY", "").strip()
    raw = os.getenv("AVAILABLE_MODELS", "").strip()

    if backend_url and compat_key and raw:
        for model_name in raw.split(","):
            model_name = model_name.strip()
            if not model_name:
                continue
            options.append({
                "id": f"compat:{model_name}",
                "label": f"{model_name} (New API)",
                "provider": "openai_compatible",
                "deep_model": model_name,
                "quick_model": model_name,
                "backend_url": backend_url,
            })

    # --- Native providers (if API key is set) ---
    native = [
        ("deepseek", "DEEPSEEK_API_KEY", "DeepSeek V4 Pro", "deepseek-v4-pro", "deepseek-v4-flash"),
        ("openai", "OPENAI_API_KEY", "GPT-5.5 (direct)", "gpt-5.5", "gpt-5.5"),
        ("anthropic", "ANTHROPIC_API_KEY", "Claude Sonnet 5", "claude-sonnet-5", "claude-sonnet-5"),
        ("google", "GOOGLE_API_KEY", "Gemini 3.1", "gemini-3.1", "gemini-3.1"),
    ]

    for provider, env_key, label, deep, quick in native:
        if os.getenv(env_key, "").strip():
            options.append({
                "id": f"native:{provider}",
                "label": f"{label} (direct)",
                "provider": provider,
                "deep_model": deep,
                "quick_model": quick,
            })

    return options


@router.get("/models")
def list_models():
    """Return available model configurations for the frontend dropdown."""
    options = _build_model_options()
    default_provider = os.getenv("TRADINGAGENTS_LLM_PROVIDER", "openai_compatible")
    default_deep = os.getenv("TRADINGAGENTS_DEEP_THINK_LLM", "")

    default_id = None
    for opt in options:
        if opt["provider"] == default_provider and opt["deep_model"] == default_deep:
            default_id = opt["id"]
            break

    return {
        "models": options,
        "default": default_id or (options[0]["id"] if options else None),
    }