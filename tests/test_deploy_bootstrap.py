import re
from pathlib import Path


def test_bootstrap_requires_python_312_for_tradingagents():
    script = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap.sh"
    content = script.read_text()

    assert re.search(
        r'install -y --allowerasing\s*\\\s*aws-cli curl git unzip python3\.12\b',
        content,
    )
    assert 'command -v python3.12' in content
    assert '"${VENV_MINOR}" -lt 12' in content
    assert 'Existing venv uses Python ${VENV_MAJOR}.${VENV_MINOR} (< 3.12)' in content
