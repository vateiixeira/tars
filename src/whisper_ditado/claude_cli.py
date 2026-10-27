"""Integração com o Claude Code CLI (`claude -p`): resumo, título e nomes de falantes.

Cada chamada é um subprocesso sem ferramentas e sem persistência de sessão; o prompt vai
pelo stdin e a resposta é lida do campo `result` do JSON de saída.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from whisper_ditado import procs
from whisper_ditado.issues.prompts import PRINTS_BLOCK
from whisper_ditado.prints.parts import ImagePart
from whisper_ditado.store import Segment, Speaker

TITLE_MAX = 80
_AUTH_RE = re.compile(r"not logged in|/login|log ?in|authenticat|unauthori[sz]ed|api key", re.I)

TITLE_PROMPT = (
    "Escreva um título curto em português do Brasil para a reunião transcrita abaixo. "
    "No máximo 60 caracteres, sem aspas, sem ponto final. Responda apenas com o título."
)

NAMES_PROMPT = (
    "Na transcrição abaixo, os falantes aparecem com rótulos genéricos. Para cada rótulo "
    "da lista, diga o nome da pessoa SOMENTE se a transcrição der evidência explícita "
    "(alguém se apresentou, ou foi chamado pelo nome logo antes de responder). "
    "Não deduza nem adivinhe: sem evidência explícita, não inclua o rótulo.\n"
    "Responda apenas com um array JSON no formato "
    '[{{"label": "...", "name": "...", "evidence": "trecho curto da transcrição"}}]. '
    "Se não houver nenhum, responda [].\n\nRótulos: {labels}"
)


class ClaudeError(Exception):
    pass


@dataclass
class NameSuggestion:
    label: str
    name: str
    evidence: str


def _fmt_time(seconds: float) -> str:
    total = int(seconds)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    # [h:mm:ss] só a partir de 1 hora; abaixo disso [mm:ss].
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def stream_user_message(prompt: str, images: Sequence[ImagePart]) -> str:
    """Linha JSON (`--input-format stream-json`): o prompt e, para cada print, o rótulo e a imagem."""
    content: list[dict] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append({"type": "text", "text": f"[{image.label} — {_fmt_time(image.at_s)}]"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": image.media_type, "data": image.data_b64}})
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}},
                      ensure_ascii=False)


def transcript_text(segments: list[Segment], speakers: list[Speaker]) -> str:
    names = {s.id: (s.label or "Desconhecido") for s in speakers}
    lines = []
    for seg in segments:
        name = names.get(seg.speaker_id, "Desconhecido")
        suffix = " (ditado)" if seg.dictated else ""
        lines.append(f"[{_fmt_time(seg.start_s)}] {name}: {seg.text}{suffix}")
    return "\n".join(lines)


def sync_summary_cli(cli, settings) -> None:
    """Aplica ao `cli` o binário, modelo e effort do resumo/títulos/nomes (`claude_*`)."""
    cli.bin_path = settings.get("claude_bin")
    cli.model = settings.get("claude_model")
    cli.effort = settings.get("claude_effort")


def sync_tasks_cli(cli, settings) -> None:
    """Aplica ao `cli` do criador de issues (levantamento, opções e criação) o binário e os `tasks_*`."""
    cli.bin_path = settings.get("claude_bin")
    cli.model = settings.get("tasks_model")
    cli.effort = settings.get("tasks_effort")


def resolve_bin(bin_path: str | None) -> str:
    """Caminho do `claude`: o configurado, o do PATH ou `~/.local/bin/claude`."""
    if bin_path:
        return bin_path
    # O serviço systemd pode não ter ~/.local/bin no PATH.
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    raise ClaudeError("Claude Code não encontrado")


class ClaudeCli:
    def __init__(self, bin_path: str | None, model: str | None, timeout_s: float = 180,
                 effort: str | None = None):
        self.bin_path = bin_path
        self.model = model
        self.effort = effort
        self.timeout_s = timeout_s

    def _resolve_bin(self) -> str:
        return resolve_bin(self.bin_path)

    def _run(self, prompt: str, images: Sequence[ImagePart] = ()) -> str:
        # `--tools ""` é o que desliga as ferramentas (`--allowedTools ""` sozinho não basta);
        # `--setting-sources ""` evita hooks/plugins/CLAUDE.md do usuário (o login continua valendo);
        # `--strict-mcp-config` sem `--mcp-config` não carrega nenhum servidor MCP.
        # Com imagens a entrada é uma mensagem stream-json, que exige a saída em stream-json (e --verbose).
        format_flags = (["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
                        if images else ["--output-format", "json"])
        cmd = [self._resolve_bin(), "-p", *format_flags,
               "--no-session-persistence", "--tools", "", "--allowedTools", "",
               "--setting-sources", "", "--strict-mcp-config"]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        stdin = stream_user_message(prompt, images) + "\n" if images else prompt
        with tempfile.TemporaryDirectory(prefix="whisper-ditado-claude-") as neutral_cwd:
            try:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", start_new_session=True, cwd=neutral_cwd,
                )
            except (FileNotFoundError, PermissionError) as e:
                raise ClaudeError("Claude Code não encontrado") from e
            procs.register(proc)  # `procs.kill_all` (desligamento do backend) também mata este processo
            try:
                out, err = proc.communicate(stdin, timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                raise ClaudeError("O Claude demorou demais para responder") from None
            except BaseException:
                self._kill(proc)
                raise
            finally:
                procs.unregister(proc)
        return self._parse(out, err, proc.returncode, stream=bool(images))

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # o claude pode ter filhos
        except ProcessLookupError:
            pass
        proc.communicate()

    @staticmethod
    def _parse(out: str, err: str, returncode: int, stream: bool = False) -> str:
        if stream:
            data = _last_result_event(out)
        else:
            try:
                data = json.loads(out)
            except ValueError:
                data = None
        if not isinstance(data, dict):
            if _AUTH_RE.search(f"{out}\n{err}"):
                raise ClaudeError("Claude Code não está logado — rode `claude` no terminal")
            raise ClaudeError("Resposta inesperada do Claude")
        result = data.get("result")
        if data.get("is_error"):
            if _AUTH_RE.search(str(result or "") + "\n" + err):
                raise ClaudeError("Claude Code não está logado — rode `claude` no terminal")
            raise ClaudeError("Resposta inesperada do Claude")
        if not isinstance(result, str) or returncode != 0:
            raise ClaudeError("Resposta inesperada do Claude")
        return result

    def summarize(self, transcript: str, prompt: str, images: Sequence[ImagePart] = ()) -> str:
        if images:
            prompt = f"{prompt}\n\n{PRINTS_BLOCK}"
        return self._run(f"{prompt}\n\n--- TRANSCRIÇÃO ---\n{transcript}", images).strip()

    def title(self, transcript: str) -> str:
        raw = self._run(f"{TITLE_PROMPT}\n\n--- TRANSCRIÇÃO ---\n{transcript}")
        lines = raw.strip().splitlines()
        line = lines[0] if lines else ""
        return line.strip().strip("\"'“”‘’«»`").strip()[:TITLE_MAX]

    def suggest_names(self, transcript: str, unknown_labels: list[str]) -> list[NameSuggestion]:
        if not unknown_labels:
            return []
        prompt = NAMES_PROMPT.format(labels=json.dumps(unknown_labels, ensure_ascii=False))
        raw = self._run(f"{prompt}\n\n--- TRANSCRIÇÃO ---\n{transcript}")
        wanted = set(unknown_labels)
        suggestions = []
        seen = set()
        for item in _first_json_array(raw):
            if not isinstance(item, dict):
                continue
            label, name = item.get("label"), item.get("name")
            if label not in wanted or label in seen or not isinstance(name, str) or not name.strip():
                continue
            seen.add(label)
            evidence = item.get("evidence")
            suggestions.append(NameSuggestion(label, name.strip(), evidence if isinstance(evidence, str) else ""))
        return suggestions


def _last_result_event(out: str) -> dict | None:
    """Último evento `result` da saída stream-json (uma linha JSON por evento)."""
    found = None
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            found = event
    return found


def _first_json_array(text: str) -> list:
    decoder = json.JSONDecoder()
    pos = text.find("[")
    while pos != -1:
        try:
            value, _ = decoder.raw_decode(text, pos)
        except ValueError:
            value = None
        if isinstance(value, list) and any(isinstance(i, dict) for i in value):
            return value
        pos = text.find("[", pos + 1)
    return []
