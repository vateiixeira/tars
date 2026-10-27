"""Cliente do MCP de issues: opções (`listar_opcoes`), criação de issues (`criar_issue`) e anexo de prints.

As chamadas passam pelo Claude Code CLI do usuário, que gerencia a autenticação, com o
MCP de issues como único MCP e só a ferramenta daquela operação liberada. A criação ainda passa por um
hook PreToolUse (`create_guard.py`) que só deixa sair os argumentos aprovados, uma vez. O anexo dos
prints libera as ferramentas do MCP e usa outro hook (`attach_guard.py`), que bloqueia a criação e troca
os marcadores dos prints pelo conteúdo real dos arquivos.
"""

from __future__ import annotations

import json
import shlex
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from whisper_ditado.issues.attach_guard import MARKER
from whisper_ditado.issues.create_guard import canon
from whisper_ditado.issues.prompts import ATTACH_PROMPT, CREATE_PROMPT, KIND_LABELS, OPTIONS_PROMPT, PRIORITIES
from whisper_ditado.issues.runner import StreamRun, run_stream
from whisper_ditado.claude_cli import _AUTH_RE, ClaudeCli, ClaudeError
from whisper_ditado.store import TaskSuggestion

SERVER = "issues"
TOOL_OPTIONS = "mcp__issues__listar_opcoes"
TOOL_CREATE = "mcp__issues__criar_issue"

OPTIONS_TIMEOUT_S = 120
CREATE_TIMEOUT_S = 180
HOOK_TIMEOUT_S = 30
TITLE_MAX = 300
UNCERTAIN_MESSAGE = "Pode ter sido criada — confira no MCP de issues"
LOGIN_MESSAGE = ("Sem login no MCP de issues. Abra o Claude Code na pasta raiz, digite /mcp, escolha "
                 "issues e faça o login.")
CLAUDE_LOGIN_MESSAGE = "Claude Code não está logado — rode `claude` no terminal"
ATTACH_UNSUPPORTED = "O MCP de issues não aceita imagens"
ATTACH_PARTIAL = "Alguns prints não foram enviados ao MCP de issues"
ATTACH_NOT_DONE = "O Claude não anexou os prints; nenhum print foi enviado"
ATTACH_INTERRUPTED = "O Claude não concluiu o anexo dos prints; nenhum print foi enviado"
_KINDS = ("people", "teams", "products", "stages")


class IssueError(Exception):
    pass


@dataclass
class Option:
    id: int
    name: str


@dataclass
class IssueOptions:
    username: str
    people: list[Option]
    teams: list[Option]
    products: list[Option]
    stages: list[Option]
    fetched_at: datetime

    def to_json(self) -> dict:
        def dump(options: list[Option]) -> list[dict]:
            return [{"id": o.id, "name": o.name} for o in options]

        return {"username": self.username, "people": dump(self.people), "teams": dump(self.teams),
                "products": dump(self.products), "stages": dump(self.stages),
                "fetched_at": self.fetched_at.isoformat()}

    def stage_named(self, name: str) -> Option | None:
        wanted = name.strip().casefold()
        return next((s for s in self.stages if s.name.strip().casefold() == wanted), None)

    def has(self, kind: str, id: int) -> bool:
        if kind not in _KINDS:
            return False
        return any(o.id == id for o in getattr(self, kind))


@dataclass
class IssueFields:
    title: str
    description: str
    assignee: int
    team: int
    products: list[int]
    stage: int
    priority: str

    def as_tool_input(self) -> dict:
        return {"title": self.title, "description": self.description, "assignee": self.assignee,
                "team": self.team, "products": list(self.products), "stage": self.stage,
                "priority": self.priority}


@dataclass
class CreateOutcome:
    state: Literal["created", "uncertain", "failed"]
    number: int | None
    url: str | None
    error: str | None


@dataclass
class AttachOutcome:
    state: Literal["sent", "unsupported", "failed"]
    error: str | None


def issue_fields_from(task: TaskSuggestion) -> IssueFields:
    title = (task.title or "").strip()
    missing = [label for label, ok in (
        ("título", bool(title)), ("responsável", task.assignee_id is not None),
        ("equipe", task.team_id is not None), ("produto", task.product_id is not None),
        ("etapa", task.stage_id is not None)) if not ok]
    if missing:
        raise ValueError("Falta: " + ", ".join(missing))
    if task.kind not in KIND_LABELS:
        raise ValueError("Tipo inválido")
    if task.priority not in PRIORITIES:
        raise ValueError("Prioridade inválida")
    if len(title) > TITLE_MAX:
        raise ValueError(f"O título passa de {TITLE_MAX} caracteres")
    kind_line = f"**Tipo:** {KIND_LABELS[task.kind]}"
    body = task.description or ""
    description = f"{kind_line}\n\n{body}" if body.strip() else kind_line
    return IssueFields(title=title, description=description, assignee=task.assignee_id, team=task.team_id,
                       products=[task.product_id], stage=task.stage_id, priority=task.priority)


_NOT_COMPARABLE = object()


def _as_id(value):
    """Id de um campo da resposta: o próprio valor numérico ou o "id" de um objeto; senão, não comparável."""
    if isinstance(value, dict) and "id" in value:
        value = value["id"]
    if isinstance(value, (bool, int, float)):
        return value
    return _NOT_COMPARABLE


def _response_differences(data: dict, fields: IssueFields) -> list[str]:
    """Campos (em pt-BR) que a resposta de `criar_issue` traz diferentes do enviado.

    Só compara o que veio na resposta, num formato reconhecível; comparação estrita (a do guard).
    """
    differing = []
    title = data.get("title")
    if isinstance(title, str) and canon(title) != canon(fields.title):
        differing.append("título")
    for label, key, expected in (("responsável", "assignee", fields.assignee), ("equipe", "team", fields.team),
                                 ("etapa", "stage", fields.stage)):
        got = next((_as_id(data[k]) for k in (key, f"{key}_id") if data.get(k) is not None), _NOT_COMPARABLE)
        if got is not _NOT_COMPARABLE and canon(got) != canon(expected):
            differing.append(label)
    raw = next((data[k] for k in ("products", "product_ids") if data.get(k) is not None), None)
    if isinstance(raw, list):
        ids = [_as_id(item) for item in raw]
        if ids and _NOT_COMPARABLE not in ids and sorted(map(canon, ids)) != sorted(map(canon, fields.products)):
            differing.append("produto")
    # ordem estável pedida: título, responsável, equipe, produto, etapa
    order = ["título", "responsável", "equipe", "produto", "etapa"]
    return sorted(differing, key=order.index)


def _connection_error(run: StreamRun, server: str = SERVER) -> str | None:
    """Mensagem de login/conexão quando o MCP de issues não está utilizável; None se está tudo bem."""
    status = run.mcp_status(server)
    if status == "needs-auth":
        return LOGIN_MESSAGE.replace("escolha issues", f"escolha {server}")
    if status is not None and status != "connected":
        return f"Não foi possível conectar ao MCP de issues ({status})"
    return None


def _auth_text_error(run: StreamRun, tool_texts: list[str], server: str = SERVER) -> str | None:
    # O texto final só conta quando o Claude terminou em erro (senão pode ser eco do conteúdo da issue).
    texts = tool_texts + [run.stderr] + ([run.result] if run.is_error and run.result else [])
    if not _AUTH_RE.search("\n".join(texts)):
        return None
    # Sem informação do MCP no `init`, o problema é o login do próprio Claude Code.
    if run.mcp_status(server) is not None:
        return LOGIN_MESSAGE.replace("escolha issues", f"escolha {server}")
    return CLAUDE_LOGIN_MESSAGE


def _parse_options(raw: str) -> IssueOptions:
    try:
        data = json.loads(raw)
    except ValueError:
        raise IssueError("Resposta inesperada do MCP de issues") from None
    if not isinstance(data, dict):
        raise IssueError("Resposta inesperada do MCP de issues")

    def options(key: str) -> list[Option]:
        found = []
        for item in data.get(key) or []:
            if not isinstance(item, dict) or isinstance(item.get("id"), bool) or not isinstance(item.get("id"), int):
                continue
            name = next((v.strip() for v in (item.get("name"), item.get("username"))
                         if isinstance(v, str) and v.strip()), f"#{item['id']}")
            found.append(Option(item["id"], name))
        return found

    you = data.get("voce")
    username = you.get("username") if isinstance(you, dict) else None
    return IssueOptions(
        username=username if isinstance(username, str) else "",
        people=options("pessoas"), teams=options("equipes"), products=options("produtos"),
        stages=options("etapas"), fetched_at=datetime.now(timezone.utc),
    )


class IssueClient:
    def __init__(self, cli: ClaudeCli, url_fn: Callable[[], str | None],
                 server_fn: Callable[[], str] = lambda: SERVER):
        self.cli = cli
        self.url_fn = url_fn
        self.server_fn = server_fn

    def list_options(self) -> IssueOptions:
        url, server = self.url_fn(), self.server_fn()
        if not url:
            raise IssueError("Configure o endereço do MCP de issues em Configurações → Criador de issues")
        tool_options = f"mcp__{server}__listar_opcoes"
        prompt = OPTIONS_PROMPT.replace("do issues", f"do {server}")
        try:
            run = run_stream(self.cli, prompt, cwd=None, tools="", allowed=[tool_options],
                             mcp_url=url, mcp_server=server, timeout_s=OPTIONS_TIMEOUT_S)
        except ClaudeError as e:
            raise IssueError(str(e)) from e
        calls = run.tool_calls(tool_options)
        good = [c for c in calls if not c.is_error and c.result is not None]
        if good:  # resultado bom vale mais que o status do MCP (como em `_outcome`)
            return _parse_options(good[-1].result)
        problem = _connection_error(run, server)
        if problem:
            raise IssueError(problem)
        if run.timed_out:
            raise IssueError("O MCP de issues demorou demais para responder")
        problem = _auth_text_error(run, [c.result or "" for c in calls], server)
        if problem:
            raise IssueError(problem)
        if calls:
            raise IssueError("O MCP de issues recusou a consulta das opções")
        raise IssueError("O MCP de issues não respondeu às opções")

    def create_issue(self, fields: IssueFields) -> CreateOutcome:
        url, server = self.url_fn(), self.server_fn()
        if not url:
            return CreateOutcome("failed", None, None, "Configure o endereço do MCP de issues; nada foi enviado")
        tool_create = f"mcp__{server}__criar_issue"
        guard = Path(__file__).with_name("create_guard.py")
        outcome: CreateOutcome | None = None
        try:
            # ignore_cleanup_errors: uma falha ao limpar a pasta nunca pode apagar o resultado já calculado.
            with tempfile.TemporaryDirectory(prefix="whisper-ditado-create-", ignore_cleanup_errors=True) as tmp:
                expected, marker = Path(tmp) / "expected.json", Path(tmp) / "sent.marker"
                if not guard.is_file():
                    return CreateOutcome("failed", None, None, "Verificação de segurança ausente; nada foi enviado")
                try:
                    tool_input = fields.as_tool_input()
                    expected.write_text(json.dumps(tool_input, ensure_ascii=False), encoding="utf-8")
                    # `-I` isola sys.path e PYTHON*; `|| exit 2` fecha o hook contra falhas fora do Python
                    # (interpretador ausente, erro do shell, sinal): o Claude só bloqueia com a saída 2.
                    command = shlex.join([sys.executable, "-I", str(guard), str(expected), str(marker), tool_create]) + " || exit 2"
                    settings = {"hooks": {"PreToolUse": [{"matcher": tool_create, "hooks": [
                        {"type": "command", "command": command, "timeout": HOOK_TIMEOUT_S}]}]}}
                    prompt = CREATE_PROMPT.replace("do issues", f"do {server}").format(fields=json.dumps(tool_input, ensure_ascii=False))
                    run = run_stream(self.cli, prompt, cwd=None, tools="", allowed=[tool_create],
                                     mcp_url=url, mcp_server=server, settings=settings, timeout_s=CREATE_TIMEOUT_S)
                except ClaudeError as e:
                    outcome = CreateOutcome("failed", None, None, str(e))
                    return outcome
                except Exception:
                    # Falha depois de iniciar: o marcador diz se o hook chegou a liberar a chamada.
                    sent = self._sent(marker)
                    outcome = (CreateOutcome("failed", None, None, "Erro inesperado ao criar a issue; nada foi enviado")
                               if sent is False else CreateOutcome("uncertain", None, None, UNCERTAIN_MESSAGE))
                    return outcome
                # Marcador e interpretação ainda dentro da pasta temporária: depois dela o marcador some.
                sent = self._sent(marker)
                try:
                    outcome = self._outcome(run, fields, sent is not False, server=server)
                except Exception:
                    outcome = (CreateOutcome("failed", None, None, "Erro inesperado ao interpretar a resposta do MCP de issues; nada foi enviado")
                               if sent is False else CreateOutcome("uncertain", None, None, UNCERTAIN_MESSAGE))
                return outcome
        except Exception:
            if outcome is not None:  # falha só na limpeza da pasta temporária
                return outcome
            return CreateOutcome("failed", None, None, "Erro inesperado ao criar a issue; nada foi enviado")

    def attach_prints(self, number: int, url: str | None, files: list[tuple[str, Path]]) -> AttachOutcome:
        """Tenta anexar os prints (pares rótulo, JPEG pronto) à issue `number`. Nunca levanta.

        O Claude escolhe a ferramenta de anexo do MCP e passa marcadores; o hook `attach_guard.py`
        troca os marcadores pelo conteúdo dos arquivos e bloqueia `criar_issue`.
        """
        mcp_url, server = self.url_fn(), self.server_fn()
        if not mcp_url:
            return AttachOutcome("failed", "Configure o endereço do MCP de issues; nenhum print foi enviado")
        guard = Path(__file__).with_name("attach_guard.py")
        prefix = f"mcp__{server}__"
        try:
            with tempfile.TemporaryDirectory(prefix="whisper-ditado-attach-", ignore_cleanup_errors=True) as tmp:
                if not guard.is_file():
                    return AttachOutcome("failed", "Verificação de segurança ausente; nenhum print foi enviado")
                mapping = Path(tmp) / "prints.json"
                mapping.write_text(json.dumps({label: str(path) for label, path in files}), encoding="utf-8")
                # Mesmo isolamento e falha fechada do hook da criação (ver `create_issue`).
                command = shlex.join([sys.executable, "-I", str(guard), str(mapping),
                                      f"{prefix}criar_issue"]) + " || exit 2"
                settings = {"hooks": {"PreToolUse": [{"matcher": f"{prefix}.*", "hooks": [
                    {"type": "command", "command": command, "timeout": HOOK_TIMEOUT_S}]}]}}
                prompt = ATTACH_PROMPT.format(
                    number=number, url=url or "sem link", server=server,
                    prints="\n".join(f"- {label}: {path.name}" for label, path in files))
                run = run_stream(self.cli, prompt, cwd=None, tools="", allowed=[f"mcp__{server}"],
                                 mcp_url=mcp_url, mcp_server=server, settings=settings,
                                 timeout_s=CREATE_TIMEOUT_S)
            return self._attach_outcome(run, server, [label for label, _ in files])
        except ClaudeError as e:
            return AttachOutcome("failed", str(e))
        except Exception as e:
            return AttachOutcome("failed", f"Erro inesperado ao anexar os prints ({type(e).__name__}: {e})")

    @staticmethod
    def _attach_outcome(run: StreamRun, server: str, labels: list[str]) -> AttachOutcome:
        """Classifica o anexo só pelas chamadas que levaram prints (marcadores na entrada original).

        `sent` só quando todo rótulo saiu numa chamada bem-sucedida; qualquer print que não chegou
        vira `failed`, para o operador ver o aviso. `unsupported` exige SEM_SUPORTE numa execução limpa.
        """
        covered: set[str] = set()
        marker_calls = 0
        for call in run.calls_with_prefix(f"mcp__{server}__"):
            used = set(MARKER.findall(json.dumps(call.input, ensure_ascii=False)))
            if not used:
                continue
            marker_calls += 1
            if not call.is_error and call.result is not None:
                covered |= {label for _, label in used}
        if marker_calls:
            if set(labels) <= covered:
                return AttachOutcome("sent", None)
            return AttachOutcome("failed", ATTACH_PARTIAL)
        problem = _connection_error(run, server) or _auth_text_error(run, [], server)
        if problem:
            return AttachOutcome("failed", problem)
        if run.timed_out:
            return AttachOutcome("failed", "O MCP de issues demorou demais para responder; nenhum print foi enviado")
        if run.is_error or run.returncode != 0:
            return AttachOutcome("failed", ATTACH_INTERRUPTED)
        if "SEM_SUPORTE" in (run.result or ""):
            return AttachOutcome("unsupported", ATTACH_UNSUPPORTED)
        return AttachOutcome("failed", ATTACH_NOT_DONE)

    @staticmethod
    def _sent(marker: Path) -> bool | None:
        """True/False se o marcador do hook existe; None se não deu para saber (tratado como "pode ter sido")."""
        try:
            return marker.exists()
        except Exception:
            return None

    @staticmethod
    def _outcome(run: StreamRun, fields: IssueFields, sent: bool, server: str = SERVER) -> CreateOutcome:
        calls = run.tool_calls(f"mcp__{server}__criar_issue")
        for call in calls:
            if call.is_error or call.result is None:
                continue
            try:
                data = json.loads(call.result)
            except ValueError:
                continue
            number = data.get("number") if isinstance(data, dict) else None
            if isinstance(number, int) and not isinstance(number, bool):
                url = data.get("url") if isinstance(data.get("url"), str) else None
                approved = fields.as_tool_input()
                error = None
                if not sent or {k: v for k, v in call.input.items() if v is not None} != approved:
                    error = "Confira no MCP de issues: o conteúdo criado pode diferir do aprovado"
                else:
                    differing = _response_differences(data, fields)
                    if differing == ["título"]:
                        error = "Confira no MCP de issues: o título devolvido é diferente do enviado"
                    elif differing:
                        error = ("Confira no MCP de issues: o MCP de issues devolveu campos diferentes do enviado ("
                                 + ", ".join(differing) + ")")
                return CreateOutcome("created", number, url, error)
        if sent:
            return CreateOutcome("uncertain", None, None, UNCERTAIN_MESSAGE)
        problem = _connection_error(run, server)
        if problem:
            return CreateOutcome("failed", None, None, problem)
        problem = _auth_text_error(run, [], server)
        if problem:
            return CreateOutcome("failed", None, None, problem)
        blocked = [c for c in calls if c.is_error]
        if blocked:
            # Sem marcador, o erro veio do hook (ou de permissão), nunca do MCP de issues. Corta o JSON repetido.
            reason = (blocked[-1].result or "").split(" Argumentos aprovados:")[0]
            return CreateOutcome("failed", None, None, f"Criação não enviada ao MCP de issues: {reason}")
        if run.timed_out:
            return CreateOutcome("failed", None, None, "O Claude demorou demais para responder; nada foi enviado")
        return CreateOutcome("failed", None, None, "O Claude não chamou a criação da issue; nada foi enviado")


class OptionsCache:
    """Cache das opções do MCP de issues (TTL) com cache negativo curto para falhas.

    A entrada guarda a URL e o nome do MCP; ao trocar qualquer um, as opções são buscadas de novo.
    """

    def __init__(self, client: IssueClient, ttl_s: float = 600, clock: Callable[[], float] = time.monotonic,
                 url_fn: Callable[[], str | None] | None = None, negative_ttl_s: float = 30):
        self._client = client
        self._ttl_s = ttl_s
        self._negative_ttl_s = negative_ttl_s
        self._clock = clock
        url_getter = url_fn or getattr(client, "url_fn", None) or (lambda: None)
        server_getter = getattr(client, "server_fn", None) or (lambda: SERVER)
        self._identity_fn = lambda: (url_getter(), server_getter())
        self._fetch_lock = threading.Lock()
        self._entry: tuple[IssueOptions, float, tuple[str | None, str]] | None = None
        self._failure: tuple[IssueError, float, tuple[str | None, str]] | None = None

    def peek(self) -> IssueOptions | None:
        entry = self._entry
        return entry[0] if entry and entry[2] == self._identity_fn() else None

    def get(self, refresh: bool = False) -> IssueOptions:
        url = self._identity_fn()
        if not refresh:
            fresh = self._fresh(url)
            if fresh is not None:
                return fresh
        with self._fetch_lock:
            if not refresh:  # outra thread pode ter buscado enquanto esperávamos o lock
                fresh = self._fresh(url)
                if fresh is not None:
                    return fresh
            try:
                options = self._client.list_options()
            except IssueError as exc:
                if not refresh:
                    self._failure = (exc, self._clock(), url)
                raise
            self._entry = (options, self._clock(), url)
            self._failure = None
            return options

    def _fresh(self, url: tuple[str | None, str]) -> IssueOptions | None:
        """Entrada ainda válida; se não há, levanta a falha recente (cache negativo) ou devolve None."""
        entry = self._entry
        if entry and entry[2] == url and self._clock() - entry[1] < self._ttl_s:
            return entry[0]
        failure = self._failure
        if failure and failure[2] == url and self._clock() - failure[1] < self._negative_ttl_s:
            raise IssueError(str(failure[0])) from None
        return None
