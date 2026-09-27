"""
Override hardcoded model defaults in the tradingagents library.

Import and call apply_all() once at app startup - before any
TradingAgentsGraph is constructed. Because DEFAULT_CONFIG and
MODEL_OPTIONS are plain dicts/lists, mutating them in-place
propagates everywhere the library reads them.
"""

from __future__ import annotations
import os
import logging

logger = logging.getLogger(__name__)

MODEL_REMAP: dict[str, str] = {
    "gpt-5.6":       "gpt-5.5",
    "gpt-5.6-luna":  "gpt-5.5",
}


def _remap(model_id: str) -> str:
    return MODEL_REMAP.get(model_id, model_id)


def patch_default_config():
    from tradingagents.default_config import DEFAULT_CONFIG

    for key in ("deep_think_llm", "quick_think_llm"):
        old = DEFAULT_CONFIG.get(key)
        new = _remap(old) if old else old
        if new != old:
            logger.info("tradingagents_overrides: DEFAULT_CONFIG[%r] %r -> %r", key, old, new)
            DEFAULT_CONFIG[key] = new

    # Also honour our .env — but remap those values too!
    env_deep = os.getenv("TRADINGAGENTS_DEEP_THINK_LLM", "").strip()
    env_quick = os.getenv("TRADINGAGENTS_QUICK_THINK_LLM", "").strip()
    if env_deep:
        DEFAULT_CONFIG["deep_think_llm"] = _remap(env_deep)
    if env_quick:
        DEFAULT_CONFIG["quick_think_llm"] = _remap(env_quick)


def patch_model_catalog():
    from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS

    for provider, modes in MODEL_OPTIONS.items():
        if not isinstance(modes, dict):
            continue
        for mode, options in modes.items():
            if not isinstance(options, list):
                continue
            MODEL_OPTIONS[provider][mode] = [
                (label, _remap(model_id))
                for label, model_id in options
            ]


_applied = False


def apply_all():
    global _applied
    if _applied:
        return
    patch_default_config()
    patch_model_catalog()
    _applied = True
    logger.info("tradingagents_overrides: all patches applied (remap table: %s)", MODEL_REMAP)
