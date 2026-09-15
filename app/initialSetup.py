#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE_FILE = PROJECT_ROOT / ".env.example"
SAMPLE_PAYLOAD_FILE = PROJECT_ROOT / "sample_run_payload.json"


PROVIDER_MODELS = {
    "deepseek": {
        "deep": ["deepseek-v4-pro"],
        "quick": ["deepseek-v4-flash"],
        "all": ["deepseek-v4-pro", "deepseek-v4-flash"],
    },
    "openai": {
        "deep": ["gpt-5.6"],
        "quick": ["gpt-5.6-luna"],
        "all": ["gpt-5.6", "gpt-5.6-luna"],
    },
    "google": {
        "deep": ["gemini-3.1"],
        "quick": ["gemini-3.1"],
        "all": ["gemini-3.1"],
    },
    "anthropic": {
        "deep": ["claude-sonnet-5"],
        "quick": ["claude-sonnet-5"],
        "all": ["claude-sonnet-5"],
    },
    "xai": {
        "deep": ["grok-4"],
        "quick": ["grok-4"],
        "all": ["grok-4"],
    },
    "qwen": {
        "deep": ["qwen-max"],
        "quick": ["qwen-plus"],
        "all": ["qwen-max", "qwen-plus"],
    },
    "qwen_cn": {
        "deep": ["qwen-max"],
        "quick": ["qwen-plus"],
        "all": ["qwen-max", "qwen-plus"],
    },
    "glm": {
        "deep": ["glm-5.3"],
        "quick": ["glm-5.3"],
        "all": ["glm-5.3"],
    },
    "glm_cn": {
        "deep": ["glm-5.3"],
        "quick": ["glm-5.3"],
        "all": ["glm-5.3"],
    },
    "minimax": {
        "deep": ["MiniMax-M1"],
        "quick": ["MiniMax-M1"],
        "all": ["MiniMax-M1"],
    },
    "minimax_cn": {
        "deep": ["MiniMax-M1"],
        "quick": ["MiniMax-M1"],
        "all": ["MiniMax-M1"],
    },
    "openrouter": {
        "deep": ["openai/gpt-5.6"],
        "quick": ["openai/gpt-5.6-luna"],
        "all": ["openai/gpt-5.6", "openai/gpt-5.6-luna"],
    },
    "ollama": {
        "deep": [],
        "quick": [],
        "all": [],
    },
    "openai_compatible": {
        "deep": [],
        "quick": [],
        "all": [],
    },
    "bedrock": {
        "deep": ["us.anthropic.claude-opus-4-8-v1:0"],
        "quick": ["us.anthropic.claude-opus-4-8-v1:0"],
        "all": ["us.anthropic.claude-opus-4-8-v1:0"],
    },
    "azure_openai": {
        "deep": ["gpt-5.6"],
        "quick": ["gpt-5.6-luna"],
        "all": ["gpt-5.6", "gpt-5.6-luna"],
    },
}


GENERIC_MODEL_ALIASES = {
    "DeepSeek V4 Flash": "deepseek-v4-flash",
    "DeepSeek V4 Pro": "deepseek-v4-pro",
    "DeepSeek Flash": "deepseek-v4-flash",
    "DeepSeek Pro": "deepseek-v4-pro",
    "deepseek-v4": "deepseek-v4-pro",
}


PROVIDER_ENV_MAPPING = {
    "openai": ["OPENAI_API_KEY"],
    "google": ["GOOGLE_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "xai": ["XAI_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "qwen": ["DASHSCOPE_API_KEY"],
    "qwen_cn": ["DASHSCOPE_CN_API_KEY"],
    "glm": ["ZHIPU_API_KEY"],
    "glm_cn": ["ZHIPU_CN_API_KEY"],
    "minimax": ["MINIMAX_API_KEY"],
    "minimax_cn": ["MINIMAX_CN_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "ollama": [],
    "openai_compatible": [],
    "bedrock": [],
    "azure_openai": [
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
    ],
}


PROVIDER_DEFAULT_MODELS = {
    "openai": ("gpt-5.6", "gpt-5.6-luna"),
    "google": ("gemini-3.1", "gemini-3.1"),
    "anthropic": ("claude-sonnet-5", "claude-sonnet-5"),
    "xai": ("grok-4", "grok-4"),
    "deepseek": ("deepseek-v4-pro", "deepseek-v4-flash"),
    "qwen": ("qwen-max", "qwen-plus"),
    "qwen_cn": ("qwen-max", "qwen-plus"),
    "glm": ("glm-5.3", "glm-5.3"),
    "glm_cn": ("glm-5.3", "glm-5.3"),
    "minimax": ("MiniMax-M1", "MiniMax-M1"),
    "minimax_cn": ("MiniMax-M1", "MiniMax-M1"),
    "openrouter": ("openai/gpt-5.6", "openai/gpt-5.6-luna"),
    "ollama": ("llama3.1", "llama3.1"),
    "openai_compatible": ("custom-model", "custom-model"),
    "bedrock": (
        "us.anthropic.claude-opus-4-8-v1:0",
        "us.anthropic.claude-opus-4-8-v1:0",
    ),
    "azure_openai": ("gpt-5.6", "gpt-5.6-luna"),
}


def prompt(text: str, default: str | None = None, required: bool = False) -> str:
    while True:
        if default is not None and default != "":
            raw = input(f"{text} [{default}]: ").strip()
            value = raw if raw else default
        else:
            value = input(f"{text}: ").strip()

        if value or not required:
            return value

        print("This field is required.")


def prompt_yes_no(text: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{text} [{suffix}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def prompt_choice(text: str, choices: list[str], default: str | None = None) -> str:
    choice_map = {str(i + 1): c for i, c in enumerate(choices)}

    print(text)
    for i, c in enumerate(choices, start=1):
        marker = " (default)" if c == default else ""
        print(f"  {i}. {c}{marker}")

    while True:
        raw = input("Enter number or value: ").strip()
        if not raw and default:
            return default
        if raw in choice_map:
            return choice_map[raw]
        if raw in choices:
            return raw
        print("Invalid choice. Try again.")


def prompt_int(text: str, default: int, min_value: int | None = None) -> int:
    while True:
        raw = prompt(text, default=str(default), required=True)
        try:
            value = int(raw)
        except ValueError:
            print("Please enter a valid integer.")
            continue

        if min_value is not None and value < min_value:
            print(f"Value must be >= {min_value}.")
            continue

        return value


def prompt_float(
    text: str,
    default: float,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    while True:
        raw = prompt(text, default=str(default), required=True)
        try:
            value = float(raw)
        except ValueError:
            print("Please enter a valid number.")
            continue

        if min_value is not None and value < min_value:
            print(f"Value must be >= {min_value}.")
            continue
        if max_value is not None and value > max_value:
            print(f"Value must be <= {max_value}.")
            continue

        return value


def prompt_date(text: str, default: str) -> str:
    while True:
        value = prompt(text, default=default, required=True)
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return value
        except ValueError:
            print("Please enter a date in YYYY-MM-DD format.")


def mask_secret(value: str) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 6:
        return "*" * len(value)
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


def detect_tradingagents() -> bool:
    try:
        import tradingagents  # noqa: F401
        return True
    except Exception:
        return False


def read_existing_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def quote_env_value(val: str) -> str:
    if any(ch in val for ch in [" ", "#", '"', "'"]):
        return '"' + val.replace('"', '\\"') + '"'
    return val


def get_ordered_env_keys(values: dict[str, str]) -> list[str]:
    preferred_order = [
        "TRADINGAGENTS_LLM_PROVIDER",
        "TRADINGAGENTS_DEEP_THINK_LLM",
        "TRADINGAGENTS_QUICK_THINK_LLM",
        "TRADINGAGENTS_MAX_DEBATE_ROUNDS",
        "TRADINGAGENTS_TEMPERATURE",
        "TRADINGAGENTS_CHECKPOINT_ENABLED",
        "TRADINGAGENTS_CACHE_DIR",
        "TRADINGAGENTS_MEMORY_LOG_PATH",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "XAI_API_KEY",
        "OPENROUTER_API_KEY",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_CN_API_KEY",
        "ZHIPU_API_KEY",
        "ZHIPU_CN_API_KEY",
        "MINIMAX_API_KEY",
        "MINIMAX_CN_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "TRADINGAGENTS_LLM_BACKEND_URL",
        "OPENAI_COMPATIBLE_REQUIRES_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
        "OLLAMA_BASE_URL",
        "AWS_DEFAULT_REGION",
        "ALPHA_VANTAGE_API_KEY",
        "FRED_API_KEY",
    ]

    seen = set()
    ordered_keys: list[str] = []

    for key in preferred_order:
        if key in values:
            ordered_keys.append(key)
            seen.add(key)

    for key in sorted(values.keys()):
        if key not in seen:
            ordered_keys.append(key)

    return ordered_keys


def write_env(path: Path, values: dict[str, str]) -> None:
    lines = [
        "# Auto-generated by initialSetup.py",
        "# Review before use.",
        "",
    ]

    for key in get_ordered_env_keys(values):
        val = values.get(key, "")
        lines.append(f"{key}={quote_env_value(val)}")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_env_example(values: dict[str, str]) -> dict[str, str]:
    example = {}
    for key in get_ordered_env_keys(values):
        value = values.get(key, "")
        if key.endswith("_API_KEY") or "SECRET" in key or "TOKEN" in key:
            example[key] = "your_value_here" if value else ""
        elif "ENDPOINT" in key:
            example[key] = value or "https://your-endpoint.example.com"
        elif key == "TRADINGAGENTS_LLM_BACKEND_URL":
            example[key] = value or "http://localhost:8000/v1"
        elif key == "OLLAMA_BASE_URL":
            example[key] = value or "http://localhost:11434/v1"
        elif key == "AWS_DEFAULT_REGION":
            example[key] = value or "us-east-1"
        elif key == "TRADINGAGENTS_MAX_DEBATE_ROUNDS":
            example[key] = value or "1"
        elif key == "TRADINGAGENTS_TEMPERATURE":
            example[key] = value or "0.0"
        elif key == "TRADINGAGENTS_CHECKPOINT_ENABLED":
            example[key] = value or "true"
        else:
            example[key] = value
    return example


def write_env_example(path: Path, values: dict[str, str]) -> None:
    example_values = build_env_example(values)
    lines = [
        "# Example environment file",
        "# Copy to .env and fill in real secrets.",
        "",
    ]

    for key in get_ordered_env_keys(example_values):
        lines.append(f"{key}={quote_env_value(example_values.get(key, ''))}")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_sample_payload(
    path: Path,
    provider: str,
    deep_model: str,
    quick_model: str,
    ticker: str,
    end_date: str,
    max_debate_rounds: int,
    temperature: float,
    checkpoint_enabled: bool,
) -> None:
    payload = f"""{{
  "model": "{deep_model}",
  "ticker": "{ticker}",
  "end_date": "{end_date}",
  "params": {{
    "llm_provider": "{provider}",
    "deep_think_llm": "{deep_model}",
    "quick_think_llm": "{quick_model}",
    "max_debate_rounds": {max_debate_rounds},
    "temperature": {temperature},
    "checkpoint_enabled": {"true" if checkpoint_enabled else "false"}
  }}
}}
"""
    path.write_text(payload, encoding="utf-8")


def provider_to_env_keys(provider: str) -> list[str]:
    return PROVIDER_ENV_MAPPING.get(provider, [])


def provider_default_models(provider: str) -> tuple[str, str]:
    return PROVIDER_DEFAULT_MODELS.get(provider, ("custom-model", "custom-model"))


def normalize_known_alias(model_name: str, provider: str | None = None, role: str | None = None) -> str:
    stripped = model_name.strip()

    if not stripped:
        return stripped

    if stripped in GENERIC_MODEL_ALIASES:
        return GENERIC_MODEL_ALIASES[stripped]

    if provider == "deepseek":
        lowered = stripped.lower()

        if lowered in {"deepseek chat", "deepseek-chat"}:
            if role == "deep":
                return "deepseek-v4-pro"
            if role == "quick":
                return "deepseek-v4-flash"
            return "deepseek-v4-flash"

        if lowered in {"deepseek flash", "deepseek-v4 flash"}:
            return "deepseek-v4-flash"

        if lowered in {"deepseek pro", "deepseek-v4 pro"}:
            return "deepseek-v4-pro"

        if lowered == "deepseek-flash":
            return "deepseek-v4-flash"

        if lowered == "deepseek-pro":
            return "deepseek-v4-pro"

    return stripped


def validate_model_name(provider: str, model_name: str, role: str) -> tuple[bool, str]:
    normalized = normalize_known_alias(model_name, provider=provider, role=role)
    provider_models = PROVIDER_MODELS.get(provider, {"deep": [], "quick": [], "all": []})

    if provider in {"ollama", "openai_compatible"}:
        if not normalized:
            return False, "Model name cannot be empty."
        return True, normalized

    allowed_by_role = provider_models.get(role, [])
    allowed_all = provider_models.get("all", [])

    if not normalized:
        return False, "Model name cannot be empty."

    if allowed_all and normalized not in allowed_all:
        return False, (
            f"Invalid model '{normalized}' for provider '{provider}'. "
            f"Allowed models: {', '.join(allowed_all)}"
        )

    if allowed_by_role and normalized not in allowed_by_role:
        return False, (
            f"Model '{normalized}' is not allowed for {role}_think_llm under provider '{provider}'. "
            f"Allowed {role} models: {', '.join(allowed_by_role)}"
        )

    return True, normalized


def sanitize_default_model(provider: str, role: str, raw_value: str, fallback: str) -> str:
    candidate = normalize_known_alias(raw_value.strip(), provider=provider, role=role) if raw_value else ""
    if not candidate:
        return fallback

    ok, normalized_or_error = validate_model_name(provider, candidate, role)
    if ok:
        return normalized_or_error

    return fallback


def prompt_validated_model(provider: str, role: str, default: str) -> str:
    provider_models = PROVIDER_MODELS.get(provider, {"deep": [], "quick": [], "all": []})
    allowed_by_role = provider_models.get(role, [])
    allowed_all = provider_models.get("all", [])

    print(f"Configure {role}_think_llm for provider '{provider}'.")

    if allowed_by_role:
        print(f"Allowed {role} model(s): {', '.join(allowed_by_role)}")
    elif allowed_all:
        print(f"Allowed model(s): {', '.join(allowed_all)}")
    else:
        print("Custom provider detected. Any non-empty model name is accepted.")

    while True:
        raw = prompt(
            f"Enter {role}_think_llm model ID",
            default=default,
            required=True,
        )
        normalized = normalize_known_alias(raw, provider=provider, role=role)

        if normalized != raw:
            print(f"⚠️  '{raw}' is a display/alias/legacy name. Using '{normalized}' instead.")

        ok, result = validate_model_name(provider, normalized, role)
        if ok:
            return result

        print(f"❌ {result}")
        print("Please enter one of the valid API model IDs.")
        print()


def validate_required_env_for_provider(env: dict[str, str]) -> list[str]:
    errors = []
    provider = env.get("TRADINGAGENTS_LLM_PROVIDER", "").strip()

    required_keys = provider_to_env_keys(provider)

    for key in required_keys:
        value = env.get(key, "").strip()
        if not value:
            errors.append(
                f"Required environment variable for provider '{provider}' is missing or blank: {key}"
            )

    if provider == "openai_compatible":
        requires_key = env.get("OPENAI_COMPATIBLE_REQUIRES_API_KEY", "false").strip().lower() == "true"
        if requires_key and not env.get("OPENAI_COMPATIBLE_API_KEY", "").strip():
            errors.append(
                "Required environment variable for provider 'openai_compatible' is missing or blank: OPENAI_COMPATIBLE_API_KEY"
            )

        if not env.get("TRADINGAGENTS_LLM_BACKEND_URL", "").strip():
            errors.append(
                "Required environment variable for provider 'openai_compatible' is missing or blank: TRADINGAGENTS_LLM_BACKEND_URL"
            )

    if provider == "ollama":
        if not env.get("OLLAMA_BASE_URL", "").strip():
            errors.append(
                "Required environment variable for provider 'ollama' is missing or blank: OLLAMA_BASE_URL"
            )

    if provider == "bedrock":
        if not env.get("AWS_DEFAULT_REGION", "").strip():
            errors.append(
                "Required environment variable for provider 'bedrock' is missing or blank: AWS_DEFAULT_REGION"
            )

    return errors


def validate_all_before_save(env: dict[str, str]) -> list[str]:
    errors = []
    provider = env.get("TRADINGAGENTS_LLM_PROVIDER", "").strip()
    deep_model = env.get("TRADINGAGENTS_DEEP_THINK_LLM", "").strip()
    quick_model = env.get("TRADINGAGENTS_QUICK_THINK_LLM", "").strip()

    if not provider:
        errors.append("TRADINGAGENTS_LLM_PROVIDER cannot be blank.")
        return errors

    errors.extend(validate_required_env_for_provider(env))

    ok, result = validate_model_name(provider, deep_model, "deep")
    if not ok:
        errors.append(result)

    ok, result = validate_model_name(provider, quick_model, "quick")
    if not ok:
        errors.append(result)

    try:
        rounds = int(env.get("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "").strip())
        if rounds < 0:
            errors.append("TRADINGAGENTS_MAX_DEBATE_ROUNDS must be >= 0.")
    except ValueError:
        errors.append("TRADINGAGENTS_MAX_DEBATE_ROUNDS must be a valid integer.")

    try:
        float(env.get("TRADINGAGENTS_TEMPERATURE", "").strip())
    except ValueError:
        errors.append("TRADINGAGENTS_TEMPERATURE must be a valid number.")

    return errors


def main() -> int:
    print("=" * 72)
    print("TradingAgents Backend Initial Setup")
    print("=" * 72)
    print()

    print("This script will help you create a .env file and a sample payload.")
    print("It validates provider credentials and model names before saving.")
    print("Optional data-provider keys can also be configured to reduce runtime failures/fallbacks.")
    print(f"Project root: {PROJECT_ROOT}")
    print()

    installed = detect_tradingagents()
    if installed:
        print("✅ TradingAgents import check: OK")
    else:
        print("⚠️  TradingAgents import check: FAILED")
        print("    The backend may not work until the package is installed in this Python environment.")
        print("    You can still continue and generate the .env file.")
    print()

    existing_env = read_existing_env(ENV_FILE)
    if existing_env:
        print(f"Found existing .env at: {ENV_FILE}")
        if not prompt_yes_no("Do you want to overwrite/update it?", default=False):
            print("Setup cancelled.")
            return 0
        print()

    env: dict[str, str] = dict(existing_env)

    provider = prompt_choice(
        "Choose your LLM provider:",
        [
            "openai",
            "google",
            "anthropic",
            "xai",
            "deepseek",
            "qwen",
            "qwen_cn",
            "glm",
            "glm_cn",
            "minimax",
            "minimax_cn",
            "openrouter",
            "ollama",
            "openai_compatible",
            "bedrock",
            "azure_openai",
        ],
        default=env.get("TRADINGAGENTS_LLM_PROVIDER", "openai"),
    )
    env["TRADINGAGENTS_LLM_PROVIDER"] = provider
    print()

    required_provider_keys = provider_to_env_keys(provider)
    for key in required_provider_keys:
        existing = env.get(key, "")
        print(f"Current {key}: {mask_secret(existing)}")
        env[key] = prompt(f"Enter {key}", default=existing, required=True)
        print()

    if provider == "ollama":
        env["OLLAMA_BASE_URL"] = prompt(
            "Ollama base URL",
            default=env.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            required=True,
        )
        print()

    if provider == "openai_compatible":
        env["TRADINGAGENTS_LLM_BACKEND_URL"] = prompt(
            "OpenAI-compatible backend URL",
            default=env.get("TRADINGAGENTS_LLM_BACKEND_URL", "http://localhost:8000/v1"),
            required=True,
        )

        requires_api_key = prompt_yes_no(
            "Does this endpoint require an API key?",
            default=env.get("OPENAI_COMPATIBLE_REQUIRES_API_KEY", "false").strip().lower() == "true",
        )
        env["OPENAI_COMPATIBLE_REQUIRES_API_KEY"] = "true" if requires_api_key else "false"

        if requires_api_key:
            env["OPENAI_COMPATIBLE_API_KEY"] = prompt(
                "Enter OPENAI_COMPATIBLE_API_KEY",
                default=env.get("OPENAI_COMPATIBLE_API_KEY", ""),
                required=True,
            )
        print()

    if provider == "bedrock":
        env["AWS_DEFAULT_REGION"] = prompt(
            "AWS region",
            default=env.get("AWS_DEFAULT_REGION", "us-east-1"),
            required=True,
        )
        print("Note: Bedrock also requires AWS credentials via env vars, ~/.aws/credentials, or IAM role.")
        print()

    if prompt_yes_no("Do you want to configure Alpha Vantage now?", default=True):
        env["ALPHA_VANTAGE_API_KEY"] = prompt(
            "Enter ALPHA_VANTAGE_API_KEY",
            default=env.get("ALPHA_VANTAGE_API_KEY", ""),
            required=True,
        )
        print()
    else:
        env.setdefault("ALPHA_VANTAGE_API_KEY", "")

    configure_fred = prompt_yes_no(
        "Do you want to configure FRED now? Recommended for macroeconomic data and fewer fallback delays.",
        default=True,
    )
    if configure_fred:
        existing_fred = env.get("FRED_API_KEY", "")
        print(f"Current FRED_API_KEY: {mask_secret(existing_fred)}")
        env["FRED_API_KEY"] = prompt(
            "Enter FRED_API_KEY",
            default=existing_fred,
            required=True,
        )
    else:
        env.setdefault("FRED_API_KEY", env.get("FRED_API_KEY", ""))
    print()

    default_deep, default_quick = provider_default_models(provider)

    existing_deep_raw = env.get("TRADINGAGENTS_DEEP_THINK_LLM", "")
    existing_quick_raw = env.get("TRADINGAGENTS_QUICK_THINK_LLM", "")

    deep_default = sanitize_default_model(provider, "deep", existing_deep_raw, default_deep)
    quick_default = sanitize_default_model(provider, "quick", existing_quick_raw, default_quick)

    env["TRADINGAGENTS_DEEP_THINK_LLM"] = prompt_validated_model(
        provider=provider,
        role="deep",
        default=deep_default,
    )
    print()

    env["TRADINGAGENTS_QUICK_THINK_LLM"] = prompt_validated_model(
        provider=provider,
        role="quick",
        default=quick_default,
    )
    print()

    max_rounds = prompt_int(
        "Max debate rounds",
        default=int(env.get("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1")),
        min_value=0,
    )
    env["TRADINGAGENTS_MAX_DEBATE_ROUNDS"] = str(max_rounds)

    temperature = prompt_float(
        "Temperature",
        default=float(env.get("TRADINGAGENTS_TEMPERATURE", "0.0")),
        min_value=0.0,
        max_value=2.0,
    )
    env["TRADINGAGENTS_TEMPERATURE"] = str(temperature)
    print()

    checkpoint_enabled = prompt_yes_no(
        "Enable checkpoint resume by default?",
        default=env.get("TRADINGAGENTS_CHECKPOINT_ENABLED", "true").strip().lower() == "true",
    )
    env["TRADINGAGENTS_CHECKPOINT_ENABLED"] = "true" if checkpoint_enabled else "false"

    env["TRADINGAGENTS_CACHE_DIR"] = prompt(
        "Checkpoint/cache directory",
        default=env.get(
            "TRADINGAGENTS_CACHE_DIR",
            str(PROJECT_ROOT / "data" / "tradingagents_cache"),
        ),
        required=True,
    )

    env["TRADINGAGENTS_MEMORY_LOG_PATH"] = prompt(
        "Decision memory log path",
        default=env.get(
            "TRADINGAGENTS_MEMORY_LOG_PATH",
            str(PROJECT_ROOT / "data" / "trading_memory.md"),
        ),
        required=True,
    )
    print()

    validation_errors = validate_all_before_save(env)
    if validation_errors:
        print("❌ Validation failed. The configuration will NOT be saved.")
        print()
        print("Problems found:")
        for err in validation_errors:
            print(f"- {err}")
        print()
        print("Please rerun the script and provide the missing required values.")
        return 1

    print("✅ Final validation passed.")
    print()

    if prompt_yes_no("Write/update .env file now?", default=True):
        write_env(ENV_FILE, env)
        print(f"✅ Wrote: {ENV_FILE}")
    else:
        print("Skipped writing .env")
    print()

    if prompt_yes_no("Write/update .env.example file now?", default=True):
        write_env_example(ENV_EXAMPLE_FILE, env)
        print(f"✅ Wrote: {ENV_EXAMPLE_FILE}")
    else:
        print("Skipped writing .env.example")
    print()

    if prompt_yes_no("Create a sample_run_payload.json file?", default=True):
        sample_ticker = prompt("Sample payload ticker", default="NVDA", required=True).upper()
        sample_end_date = prompt_date("Sample payload end_date", default="2026-01-15")

        write_sample_payload(
            SAMPLE_PAYLOAD_FILE,
            provider=provider,
            deep_model=env["TRADINGAGENTS_DEEP_THINK_LLM"],
            quick_model=env["TRADINGAGENTS_QUICK_THINK_LLM"],
            ticker=sample_ticker,
            end_date=sample_end_date,
            max_debate_rounds=max_rounds,
            temperature=temperature,
            checkpoint_enabled=checkpoint_enabled,
        )
        print(f"✅ Wrote: {SAMPLE_PAYLOAD_FILE}")
    print()

    print("=" * 72)
    print("Setup complete")
    print("=" * 72)
    print()
    print("Configured values:")
    print(f"  Provider:          {env['TRADINGAGENTS_LLM_PROVIDER']}")
    print(f"  Deep-think model:  {env['TRADINGAGENTS_DEEP_THINK_LLM']}")
    print(f"  Quick-think model: {env['TRADINGAGENTS_QUICK_THINK_LLM']}")
    print(f"  FRED configured:   {'yes' if env.get('FRED_API_KEY', '').strip() else 'no'}")
    print()

    if not env.get("FRED_API_KEY", "").strip():
        print("Note: FRED_API_KEY is not set.")
        print("      Some macroeconomic data may be skipped, and some agent flows may spend time on fallbacks.")
        print()

    print("Suggested commands:")
    print("  python initialSetup.py")
    print("  python -m uvicorn app.main:app --reload")
    print("  curl http://127.0.0.1:8000/health")
    print('  curl -X POST "http://127.0.0.1:8000/runs" -H "Content-Type: application/json" --data @sample_run_payload.json')
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())