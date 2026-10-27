import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).parents[2] / "src" / "whisper_ditado" / "issues" / "attach_guard.py"
TOOL_CREATE = "mcp__issues__criar_issue"
JPG = b"\xff\xd8\xff\xe0 falso jpeg \x00\x01\xff\xd9"


@pytest.fixture
def guard(tmp_path):
    jpg = tmp_path / "print-P1.jpg"
    jpg.write_bytes(JPG)
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"P1": str(jpg)}))

    def run(tool_input, tool_name="mcp__issues__anexar", stdin=None):
        payload = stdin if stdin is not None else json.dumps({"tool_name": tool_name, "tool_input": tool_input})
        return subprocess.run([sys.executable, "-I", str(GUARD), str(mapping), TOOL_CREATE],
                              input=payload, capture_output=True, text=True, timeout=20)

    return run


B64 = base64.b64encode(JPG).decode("ascii")


def test_guard_replaces_markers_and_blocks_create(guard):
    r = guard({"issue": 7, "data": "@@PRINT_B64:P1@@", "url": "@@PRINT_DATAURL:P1@@"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "allow"
    assert out["updatedInput"] == {"issue": 7, "data": B64, "url": "data:image/jpeg;base64," + B64}

    blocked = guard({"title": "x"}, tool_name=TOOL_CREATE)
    assert blocked.returncode == 2 and "Não crie issues nesta etapa" in blocked.stderr

    unknown = guard({"data": "@@PRINT_B64:P9@@"})
    assert unknown.returncode == 2 and "P9" in unknown.stderr


def test_guard_replaces_inside_nested_values_and_keeps_the_rest(guard):
    r = guard({"files": [{"name": "print-P1.jpg", "content": "@@PRINT_B64:P1@@"}],
               "body": "veja ![x](@@PRINT_DATAURL:P1@@) aqui", "n": None, "ok": True})
    assert r.returncode == 0, r.stderr
    new = json.loads(r.stdout)["hookSpecificOutput"]["updatedInput"]
    assert new == {"files": [{"name": "print-P1.jpg", "content": B64}],
                   "body": f"veja ![x](data:image/jpeg;base64,{B64}) aqui", "n": None, "ok": True}


def test_guard_blocks_unreadable_payload(guard):
    assert guard(None, stdin="isto não é json").returncode == 2
    assert guard(None, stdin=json.dumps({"tool_name": "mcp__issues__anexar", "tool_input": []})).returncode == 2
