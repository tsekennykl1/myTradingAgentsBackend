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

    assert 'if aws s3 cp "s3://${CONFIG_BUCKET}/config/.env" "${APP_ROOT}/.env"; then' in content
    assert 'elif [ -f "${APP_ROOT}/.env" ]; then' in content
    assert 'Missing .env in S3 config bucket and no existing ${APP_ROOT}/.env fallback' in content
