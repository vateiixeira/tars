"""Execução restrita do Claude Code CLI com `--output-format stream-json`.

Ao contrário de `ClaudeCli._run` (só o campo `result`), aqui o backend lê os eventos brutos para
enxergar o resultado cru das ferramentas (ex.: `listar_opcoes` do MCP de issues) e o estado do MCP.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from collections.abc import Sequence
from pathlib import Path

from whisper_ditado.claude_cli import ClaudeCli, ClaudeError, stream_user_message
from whisper_ditado.prints.parts import ImagePart
from whisper_ditado.procs import current_cancellation, kill_all, kill_group, register, unregister

__all__ = ["MCP_SERVER", "StreamRun", "ToolCall", "kill_all", "run_stream"]

MCP_SERVER = "issues"
_GRACE_S = 5  # espera pelo que sobrou nos pipes depois do SIGKILL


@dataclass
class ToolCall:
    name: str
    input: dict
    result: str | None
    is_error: bool


@dataclass
class StreamRun:
    events: list[dict]
    result: str | None
    is_error: bool
    timed_out: bool
    returncode: int | None
    stderr: str

    def tool_calls(self, name: str) -> list[ToolCall]:
        """Chamadas da ferramenta `name`, na ordem, já pareadas com o `tool_result` (se chegou)."""
        results: dict[str, dict] = {}
        for block in self._blocks("user", "tool_result"):
            results[str(block.get("tool_use_id"))] = block
        calls = []
        for block in self._blocks("assistant", "tool_use"):
            if block.get("name") != name:
                continue
            got = results.get(str(block.get("id")))
            calls.append(ToolCall(
                name=name,
                input=block.get("input") if isinstance(block.get("input"), dict) else {},
                result=_content_text(got.get("content")) if got is not None else None,
                is_error=bool(got.get("is_error")) if got is not None else False,
            ))
        return calls

    def mcp_status(self, server: str) -> str | None:
        for event in self.events:
            if event.get("type") == "system" and event.get("subtype") == "init":
                for item in event.get("mcp_servers") or []:
                    if isinstance(item, dict) and item.get("name") == server:
                        status = item.get("status")
                        return status if isinstance(status, str) else None
                return None
        return None

    def _blocks(self, event_type: str, block_type: str):
        for event in self.events:
            if event.get("type") != event_type:
                continue
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == block_type:
                        yield block


def _content_text(content) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def _parse_events(out: str) -> list[dict]:
    events = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _text(value) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _drain(proc: subprocess.Popen) -> tuple[str, str]:
    try:
        out, err = proc.communicate(timeout=_GRACE_S)
    except subprocess.TimeoutExpired as e:
        return _text(e.output), _text(e.stderr)
    return out or "", err or ""


def run_stream(
    cli: ClaudeCli,
    prompt: str,
    *,
    cwd: Path | None,
    tools: str,
    allowed: list[str],
    mcp_url: str | None = None,
    mcp_server: str = MCP_SERVER,
    settings: dict | None = None,
    system_append: str | None = None,
    images: Sequence[ImagePart] = (),
    timeout_s: float,
) -> StreamRun:
    input_flags = ["--input-format", "stream-json"] if images else []
    cmd = [cli._resolve_bin(), "-p", *input_flags, "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--restricted", "--tools", tools,
           "--allowedTools", ",".join(allowed), "--permission-mode", "dontAsk", "--strict-mcp-config"]
    if mcp_url:
        mcp = {"mcpServers": {mcp_server: {"type": "http", "url": mcp_url}}}
        cmd += ["--mcp-config", json.dumps(mcp)]
    if settings is not None:
        cmd += ["--settings", json.dumps(settings)]
    if system_append:
        cmd += ["--append-system-prompt", system_append]
    if cli.model:
        cmd += ["--model", cli.model]
    if cli.effort:
        cmd += ["--effort", cli.effort]

    stdin = stream_user_message(prompt, images) + "\n" if images else prompt

    def execute(workdir: str) -> StreamRun:
        cancellation = current_cancellation()
        if cancellation is not None:
            cancellation.check()
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", start_new_session=True, cwd=workdir,
            )
        except (FileNotFoundError, PermissionError, NotADirectoryError) as e:
            raise ClaudeError("Claude Code não encontrado") from e
        register(proc)
        if cancellation is not None:
            cancellation.attach(proc)
        timed_out = False
        try:
            try:
                out, err = proc.communicate(stdin, timeout=timeout_s)
            except subprocess.TimeoutExpired as e:
                timed_out = True
                partial_out, partial_err = _text(e.output), _text(e.stderr)
                kill_group(proc)
                out, err = _drain(proc)
                out, err = out or partial_out, err or partial_err
            except BaseException:
                kill_group(proc)
                _drain(proc)
                raise
        finally:
            if cancellation is not None:
                cancellation.detach(proc)
            unregister(proc)
        if cancellation is not None:
            cancellation.check()
        events = _parse_events(out)
        final = next((e for e in reversed(events) if e.get("type") == "result"), None)
        result = final.get("result") if final else None
        return StreamRun(
            events=events,
            result=result if isinstance(result, str) else None,
            is_error=True if final is None else bool(final.get("is_error")),
            timed_out=timed_out,
            returncode=proc.returncode,
            stderr=err or "",
        )

    if cwd is not None:
        return execute(str(cwd))
    with tempfile.TemporaryDirectory(prefix="whisper-ditado-claude-", ignore_cleanup_errors=True) as neutral_cwd:
        return execute(neutral_cwd)
