"""LLM provider inspection endpoints.

Lets the dashboard answer three questions without exposing any secret:

* which LLM provider/models the engine is currently configured with;
* which other providers already have a usable API key in ``.env``
  (so the user can switch to them from the browser);
* whether that key still works and, where the provider publishes it,
  how much credit is left.

Only a handful of providers expose a balance at all (DeepSeek and
OpenRouter today). For everyone else we report ``supported: false`` with a
validity check instead of inventing a number.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.initialSetup import (
    PROVIDER_DEFAULT_MODELS,
    PROVIDER_ENV_MAPPING,
    PROVIDER_MODELS,
    quote_env_value,
    read_existing_env,
    validate_model_name,
)

router = APIRouter(prefix="/providers", tags=["providers"])

BASE_DIR = Path(__file__).resolve().parents[2]
_ENV_CANDIDATES = [BASE_DIR / ".env", BASE_DIR / "app" / ".env"]

PROVIDER_LABELS = {
    "openai": "OpenAI",
    "google": "Google Gemini",
    "anthropic": "Anthropic Claude",
    "xai": "xAI Grok",
    "deepseek": "DeepSeek",
    "qwen": "Qwen (DashScope)",
    "qwen_cn": "Qwen China",
    "glm": "GLM (Zhipu)",
    "glm_cn": "GLM China",
    "minimax": "MiniMax",
    "minimax_cn": "MiniMax China",
    "openrouter": "OpenRouter",
    "ollama": "Ollama (local)",
    "openai_compatible": "OpenAI-compatible endpoint",
    "bedrock": "AWS Bedrock",
    "azure_openai": "Azure OpenAI",
}

# Providers that publish a spendable balance over HTTP.
BALANCE_PROVIDERS = {"deepseek", "openrouter"}
# Providers needing no API key at all.
KEYLESS_PROVIDERS = {"ollama", "bedrock", "openai_compatible"}

HTTP_TIMEOUT = 12


# --------------------------------------------------------------------------- env


def env_path() -> Path:
    for candidate in _ENV_CANDIDATES:
        if candidate.exists():
            return candidate
    return _ENV_CANDIDATES[0]


def load_env() -> dict[str, str]:
    """`.env` first, process environment as a fallback (containers/systemd)."""
    values = read_existing_env(env_path())
    for key in {k for keys in PROVIDER_ENV_MAPPING.values() for k in keys} | {
        "TRADINGAGENTS_LLM_PROVIDER",
        "TRADINGAGENTS_DEEP_THINK_LLM",
        "TRADINGAGENTS_QUICK_THINK_LLM",
        "TRADINGAGENTS_LLM_BACKEND_URL",
    }:
        if not values.get(key) and os.getenv(key):
            values[key] = os.environ[key]
    return values


def update_env_file(updates: dict[str, str]) -> None:
    """Rewrite only the given keys, leaving every other line untouched."""
    path = env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={quote_env_value(remaining.pop(key))}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={quote_env_value(value)}")
    path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[key] = value


def provider_key(env: dict[str, str], provider: str) -> str | None:
    for name in PROVIDER_ENV_MAPPING.get(provider, []):
        value = (env.get(name) or "").strip()
        if value:
            return value
    return None


def provider_configured(env: dict[str, str], provider: str) -> bool:
    if provider in KEYLESS_PROVIDERS:
        return True
    required = PROVIDER_ENV_MAPPING.get(provider, [])
    return all((env.get(name) or "").strip() for name in required) if required else False


# ------------------------------------------------------------------------- http


def _get_json(url: str, headers: dict[str, str]) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, body
    except urllib.error.HTTPError as exc:  # invalid key, quota, etc.
        return exc.code, exc.read().decode("utf-8", "replace")[:300]
    except Exception as exc:  # network unreachable / DNS / TLS
        return 0, str(exc)[:300]


def _auth_probe(url: str, headers: dict[str, str]) -> dict[str, Any]:
    status, payload = _get_json(url, headers)
    if status == 0:
        return {"status": "unreachable", "message": str(payload)}
    if status in (401, 403):
        return {"status": "invalid", "message": "The provider rejected this API key."}
    if status == 429:
        return {"status": "valid", "message": "Key accepted but currently rate limited."}
    if 200 <= status < 300:
        return {"status": "valid"}
    return {"status": "unknown", "message": f"Provider responded {status}."}


# --------------------------------------------------------------------- balances


def _deepseek_balance(key: str) -> dict[str, Any]:
    status, payload = _get_json(
        "https://api.deepseek.com/user/balance",
        {"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    if status == 0:
        return {"status": "unreachable", "message": str(payload)}
    if status in (401, 403):
        return {"status": "invalid", "message": "The provider rejected this API key."}
    if not (200 <= status < 300) or not isinstance(payload, dict):
        return {"status": "unknown", "message": f"Provider responded {status}."}
    infos = payload.get("balance_infos") or []
    info = infos[0] if infos else {}
    try:
        balance = float(info.get("total_balance", 0))
    except (TypeError, ValueError):
        balance = 0.0
    return {
        "status": "valid",
        "balance": balance,
        "currency": str(info.get("currency") or "USD").upper(),
        "topped_up": bool(payload.get("is_available", True)),
    }


def _openrouter_balance(key: str) -> dict[str, Any]:
    status, payload = _get_json(
        "https://openrouter.ai/api/v1/credits",
        {"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    if status == 0:
        return {"status": "unreachable", "message": str(payload)}
    if status in (401, 403):
        return {"status": "invalid", "message": "The provider rejected this API key."}
    if not (200 <= status < 300) or not isinstance(payload, dict):
        return {"status": "unknown", "message": f"Provider responded {status}."}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    try:
        total = float(data.get("total_credits", 0))
        used = float(data.get("total_usage", 0))
    except (TypeError, ValueError):
        return {"status": "valid", "message": "Balance not reported."}
    return {"status": "valid", "balance": total - used, "currency": "USD"}


def _validate_only(provider: str, key: str, env: dict[str, str]) -> dict[str, Any]:
    if provider == "openai":
        return _auth_probe("https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"})
    if provider == "anthropic":
        return _auth_probe(
            "https://api.anthropic.com/v1/models",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
    if provider == "google":
        return _auth_probe(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", {}
        )
    if provider == "xai":
        return _auth_probe("https://api.x.ai/v1/api-key", {"Authorization": f"Bearer {key}"})
    if provider in ("qwen", "qwen_cn"):
        host = "dashscope-intl" if provider == "qwen" else "dashscope"
        return _auth_probe(
            f"https://{host}.aliyuncs.com/compatible-mode/v1/models",
            {"Authorization": f"Bearer {key}"},
        )
    if provider in ("glm", "glm_cn"):
        host = "api.z.ai/api/paas/v4" if provider == "glm" else "open.bigmodel.cn/api/paas/v4"
        return _auth_probe(f"https://{host}/models", {"Authorization": f"Bearer {key}"})
    if provider == "azure_openai":
        endpoint = (env.get("AZURE_OPENAI_ENDPOINT") or "").strip().rstrip("/")
        version = (env.get("AZURE_OPENAI_API_VERSION") or "2024-02-01").strip()
        if not endpoint:
            return {"status": "unknown", "message": "AZURE_OPENAI_ENDPOINT is not set."}
        return _auth_probe(f"{endpoint}/openai/models?api-version={version}", {"api-key": key})
    return {"status": "unknown", "message": "This provider does not publish a key check."}


def check_provider(provider: str, env: dict[str, str]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    base: dict[str, Any] = {
        "provider": provider,
        "label": PROVIDER_LABELS.get(provider, provider),
        "supports_balance": provider in BALANCE_PROVIDERS,
        "checked_at": now,
        "balance": None,
        "currency": None,
    }
    if provider in KEYLESS_PROVIDERS:
        return {
            **base,
            "status": "no_key_required",
            "message": "This provider runs without an API key.",
        }
    key = provider_key(env, provider)
    if not key:
        return {**base, "status": "missing_key", "message": "No API key saved for this provider."}
    if provider == "deepseek":
        result = _deepseek_balance(key)
    elif provider == "openrouter":
        result = _openrouter_balance(key)
    else:
        result = _validate_only(provider, key, env)
    return {**base, **result}


# ------------------------------------------------------------------------ models


class ActiveProviderRequest(BaseModel):
    provider: str = Field(..., min_length=1)
    deep_model: str | None = None
    quick_model: str | None = None


# ------------------------------------------------------------------------ routes


@router.get("")
def list_providers() -> dict[str, Any]:
    env = load_env()
    active = (env.get("TRADINGAGENTS_LLM_PROVIDER") or "").strip() or None
    providers = []
    for provider in PROVIDER_ENV_MAPPING:
        deep, quick = PROVIDER_DEFAULT_MODELS.get(provider, ("custom-model", "custom-model"))
        models = PROVIDER_MODELS.get(provider, {"deep": [], "quick": []})
        providers.append(
            {
                "provider": provider,
                "label": PROVIDER_LABELS.get(provider, provider),
                "configured": provider_configured(env, provider),
                "supports_balance": provider in BALANCE_PROVIDERS,
                "env_keys": PROVIDER_ENV_MAPPING.get(provider, []),
                "deep_models": models.get("deep", []),
                "quick_models": models.get("quick", []),
                "default_deep_model": deep,
                "default_quick_model": quick,
            }
        )
    return {
        "active": {
            "provider": active,
            "deep_model": (env.get("TRADINGAGENTS_DEEP_THINK_LLM") or "").strip() or None,
            "quick_model": (env.get("TRADINGAGENTS_QUICK_THINK_LLM") or "").strip() or None,
            "configured": bool(active) and provider_configured(env, active or ""),
        },
        "providers": providers,
    }


@router.get("/credit")
def provider_credit(provider: str | None = None) -> dict[str, Any]:
    env = load_env()
    target = (provider or env.get("TRADINGAGENTS_LLM_PROVIDER") or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="No provider given and none configured.")
    if target not in PROVIDER_ENV_MAPPING:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{target}'.")
    return check_provider(target, env)


@router.post("/active")
def set_active_provider(payload: ActiveProviderRequest) -> dict[str, Any]:
    env = load_env()
    provider = payload.provider.strip()
    if provider not in PROVIDER_ENV_MAPPING:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'.")
    if not provider_configured(env, provider):
        raise HTTPException(
            status_code=400,
            detail=(
                f"No API key saved for '{provider}'. Add "
                f"{', '.join(PROVIDER_ENV_MAPPING.get(provider, [])) or 'its credentials'} to .env first."
            ),
        )
    default_deep, default_quick = PROVIDER_DEFAULT_MODELS.get(
        provider, ("custom-model", "custom-model")
    )
    deep = (payload.deep_model or default_deep).strip()
    quick = (payload.quick_model or default_quick).strip()
    resolved: dict[str, str] = {}
    for role, name in (("deep", deep), ("quick", quick)):
        ok, message = validate_model_name(provider, name, role)
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        resolved[role] = message
    deep, quick = resolved["deep"], resolved["quick"]
    update_env_file(
        {
            "TRADINGAGENTS_LLM_PROVIDER": provider,
            "TRADINGAGENTS_DEEP_THINK_LLM": deep,
            "TRADINGAGENTS_QUICK_THINK_LLM": quick,
        }
    )
    return {
        "active": {
            "provider": provider,
            "deep_model": deep,
            "quick_model": quick,
            "configured": True,
        },
        "credit": check_provider(provider, load_env()),
        "restart_required": True,
    }
