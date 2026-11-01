"""Portable static PowerShell contract; actual PS 5.1 validation on Windows."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "platform.ps1"


def test_ps_entrypoint_has_single_python_owner_and_no_direct_spawn():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'obsidian_prediction.fleet_lifecycle' in source
    assert 'Start-Process' not in source
    for action in ("start", "stop", "restart", "status", "logs", "plan", "doctor"):
        assert action in source
    assert 'Push-Location' in source
    assert 'Pop-Location' in source


@pytest.mark.skipif(not shutil.which("powershell"), reason="Windows PowerShell 5.1 unavailable")
def test_windows_powershell_parser():
    script = str(SCRIPT).replace("'", "''")
    program = (
        "$tok=$null;$err=$null;"
        f"[System.Management.Automation.Language.Parser]::ParseFile('{script}',"
        "[ref]$tok,[ref]$err) | Out-Null;"
        "if($err.Count){$err | Out-String | Write-Error;exit 1}"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", program],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
