from pathlib import Path


def test_bootstrap_requires_python_312_for_tradingagents():
    script = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap.sh"
    content = script.read_text()

    assert 'for cmd in unzip python3.12 pip3.12 jq git gcc' in content
    assert 'python3.12 -m ensurepip --upgrade' in content
    assert 'pip3.12 not found after package install' in content
    assert 'python3.12 -m venv' in content
    assert 'command -v python3.12' in content
    assert '[ "${VENV_MAJOR}" -ne 3 ] || [ "${VENV_MINOR}" -lt 12 ] || [ "${VENV_MINOR}" -gt 12 ]' in content
    assert 'Existing venv uses Python ${VENV_MAJOR}.${VENV_MINOR} (!= 3.12)' in content
