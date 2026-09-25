from pathlib import Path


def test_bootstrap_requires_python_312_for_tradingagents():
    script = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap.sh"
    content = script.read_text()

    assert 'for cmd in unzip python3.12 pip3.12 jq git gcc' in content
    assert 'python3.12 -m venv' in content
    assert 'command -v python3.12' in content
    assert '[ "${VENV_MINOR}" -lt 12 ]' in content
    assert 'Existing venv uses Python ${VENV_MAJOR}.${VENV_MINOR} (< 3.12)' in content


def test_bootstrap_env_fallback_when_s3_env_missing():
    script = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap.sh"
    content = script.read_text()

    assert 'TMP_ENV_PATH="/tmp/deploy-env.$$"' in content
    assert 'if aws s3 cp "s3://${CONFIG_BUCKET}/config/.env" "${TMP_ENV_PATH}"; then' in content
    assert 'mv "${TMP_ENV_PATH}" "${ENV_PATH}"' in content
    assert 'elif [ -f "${ENV_PATH}" ]; then' in content
    assert 'rm -f "${TMP_ENV_PATH}"' in content
    assert 'cat > "${ENV_PATH}" <<\'ENV_EOF\'' in content
    assert 'MCP_ENABLED=0' in content
    assert 'wrote minimal runtime defaults with MCP disabled' in content
    assert 'Provider-backed analysis stays unavailable until a real .env is uploaded' in content
