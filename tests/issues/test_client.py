import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from whisper_ditado.issues.client import (
    SERVER, TOOL_CREATE, TOOL_OPTIONS, AttachOutcome, CreateOutcome, IssueFields, IssueClient, IssueError, IssueOptions,
    Option, OptionsCache, issue_fields_from,
)
from whisper_ditado.claude_cli import ClaudeCli
from whisper_ditado.store import TaskSuggestion

FAKE = str(Path(__file__).parent.parent / "fixtures" / "fake_claude.py")
URL = "https://issues.example.test/mcp"

OPTIONS_JSON = {
    "voce": {"id": 7, "username": "vi"},
    "etapas": [{"id": 2, "name": "To Do", "slug": "todo"}, {"id": 5, "name": "Done", "slug": "done"}],
    "equipes": [{"id": 3, "name": "Desenvolvimento"}],
    "produtos": [{"id": 4, "name": "dash"}],
    "pessoas": [{"id": 9, "name": "", "username": "miguel"}, {"id": 10, "name": "Ana", "username": "ana"}],
}
FIELDS = IssueFields(title="Checkout: boleto", description="**Tipo:** Bug fix\n\n## Problema\nx",
                     assignee=9, team=3, products=[4], stage=2, priority="medium")
CREATED = {"number": 217, "url": "https://issues.example.test/217", "title": FIELDS.title}


@pytest.fixture
def env(monkeypatch, tmp_path):
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "stream")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    class Env:
        client = IssueClient(ClaudeCli(FAKE, None), lambda: URL)

        @staticmethod
        def scenario(**kw):
            monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", json.dumps(kw))

        @staticmethod
        def log():
            return json.loads(log.read_text())

        @classmethod
        def argv_after(cls, flag):
            argv = cls.log()["argv"]
            return argv[argv.index(flag) + 1]

    return Env


def task(**kw):
    base = dict(id="t1", meeting_id="m1", origin="claude", position=0, kind="tarefa", title="Checkout: boleto",
                description="d", project=None, product_id=4, assignee_id=9, team_id=3, stage_id=2,
                priority="medium", suggested=None, evidence=[], edited=False, discarded=False,
                create_state="none", create_error=None, issue_number=None, issue_url=None,
                repeats_number=None, created_at="", updated_at="")
    base.update(kw)
    return TaskSuggestion(**base)


def options_call(**kw):
    return {"name": TOOL_OPTIONS, "input": {}, "result": json.dumps(OPTIONS_JSON), **kw}


def create_call(**kw):
    base = {"name": TOOL_CREATE, "input": FIELDS.as_tool_input(), "result": json.dumps(CREATED)}
    base.update(kw)
    return base


# ----- opções -----


def test_constants():
    assert (SERVER, TOOL_OPTIONS, TOOL_CREATE) == (
        "issues", "mcp__issues__listar_opcoes", "mcp__issues__criar_issue")


def test_list_options_parses_tool_result(env):
    env.scenario(calls=[options_call()])
    o = env.client.list_options()
    assert o.username == "vi" and o.stage_named("to do").id == 2 and o.stage_named("nada") is None
    assert o.people == [Option(9, "miguel"), Option(10, "Ana")]  # name vazio cai no username
    assert o.teams == [Option(3, "Desenvolvimento")] and o.products == [Option(4, "dash")]
    assert o.has("people", 9) and not o.has("people", 99) and o.has("stages", 5) and not o.has("bobagem", 1)
    assert env.argv_after("--allowedTools") == TOOL_OPTIONS and env.argv_after("--tools") == ""
    assert json.loads(env.argv_after("--mcp-config"))["mcpServers"][SERVER]["url"] == URL
    assert "listar_opcoes" in env.log()["stdin"]
    assert env.log()["cwd"].count("whisper-ditado-claude-")


def test_options_to_json(env):
    env.scenario(calls=[options_call()])
    data = env.client.list_options().to_json()
    assert set(data) == {"username", "people", "teams", "products", "stages", "fetched_at"}
    assert data["people"][0] == {"id": 9, "name": "miguel"} and data["username"] == "vi"
    datetime.fromisoformat(data["fetched_at"])


def test_list_options_uses_last_successful_result(env):
    env.scenario(calls=[options_call(result="erro", is_error=True), options_call()])
    assert env.client.list_options().username == "vi"


def test_list_options_not_logged_in(env):
    env.scenario(mcp_status="needs-auth")
    with pytest.raises(IssueError, match="Sem login no MCP de issues") as e:
        env.client.list_options()
    assert "/mcp" in str(e.value)


def test_list_options_other_mcp_status(env):
    env.scenario(mcp_status="failed")
    with pytest.raises(IssueError, match=r"Não foi possível conectar ao MCP de issues \(failed\)"):
        env.client.list_options()


def test_list_options_good_result_wins_over_connection_status(env):
    env.scenario(mcp_status="pending", calls=[options_call()])
    assert env.client.list_options().username == "vi"


def test_list_options_without_tool_call_is_error(env):
    env.scenario()
    with pytest.raises(IssueError, match="O MCP de issues não respondeu às opções"):
        env.client.list_options()


def test_list_options_bad_json_is_error(env):
    env.scenario(calls=[options_call(result="isto não é json")])
    with pytest.raises(IssueError, match="Resposta inesperada do MCP de issues"):
        env.client.list_options()


def test_list_options_timeout_is_error(env, monkeypatch):
    import whisper_ditado.issues.client as issues_client

    env.scenario(sleep_after_calls=30)
    real = issues_client.run_stream
    monkeypatch.setattr(issues_client, "run_stream", lambda *a, **kw: real(*a, **{**kw, "timeout_s": 1}))
    with pytest.raises(IssueError, match="demorou demais"):
        env.client.list_options()


def test_list_options_missing_binary_is_issues_client_error(tmp_path):
    client = IssueClient(ClaudeCli(str(tmp_path / "nao-existe"), None), lambda: URL)
    with pytest.raises(IssueError, match="Claude Code não encontrado"):
        client.list_options()


# ----- campos da issue -----


def test_issue_fields_from_task_adds_kind_line():
    f = issue_fields_from(task(kind="bugfix", description="## Problema\nx"))
    assert f.description == "**Tipo:** Bug fix\n\n## Problema\nx" and f.products == [4]
    assert (f.assignee, f.team, f.stage, f.priority) == (9, 3, 2, "medium")
    assert set(f.as_tool_input()) == {"title", "description", "assignee", "team", "products", "stage", "priority"}


def test_issue_fields_with_empty_description_keeps_only_kind_line():
    assert issue_fields_from(task(kind="feature", description="  ")).description == "**Tipo:** Feature"


def test_issue_fields_requires_mandatory():
    with pytest.raises(ValueError, match="Falta: equipe"):
        issue_fields_from(task(team_id=None))
    with pytest.raises(ValueError, match="Falta: título, responsável, produto, etapa"):
        issue_fields_from(task(title=" ", assignee_id=None, product_id=None, stage_id=None))
    with pytest.raises(ValueError, match="Tipo inválido"):
        issue_fields_from(task(kind="epico"))
    with pytest.raises(ValueError, match="Prioridade inválida"):
        issue_fields_from(task(priority="asap"))
    with pytest.raises(ValueError, match="300"):
        issue_fields_from(task(title="x" * 301))


# ----- criação -----


def test_create_success(env):
    env.scenario(calls=[create_call()])
    out = env.client.create_issue(FIELDS)
    assert out == CreateOutcome("created", 217, "https://issues.example.test/217", None)
    assert json.dumps(FIELDS.as_tool_input(), ensure_ascii=False) in env.log()["stdin"]
    assert env.argv_after("--allowedTools") == TOOL_CREATE and env.argv_after("--tools") == ""
    hook = json.loads(env.argv_after("--settings"))["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == TOOL_CREATE and hook["hooks"][0]["type"] == "command"
    assert sys.executable in hook["hooks"][0]["command"] and "create_guard.py" in hook["hooks"][0]["command"]


def test_create_with_changed_input_is_blocked_and_failed(env):
    env.scenario(calls=[create_call(input={**FIELDS.as_tool_input(), "title": "Outro"})])
    out = env.client.create_issue(FIELDS)
    assert out.state == "failed" and out.number is None and out.url is None
    assert "bloqueada" in out.error and "title" in out.error


def test_create_with_extra_field_is_blocked_and_failed(env):
    env.scenario(calls=[create_call(input={**FIELDS.as_tool_input(), "environment": "prod"})])
    assert env.client.create_issue(FIELDS).state == "failed"


def test_create_timeout_after_send_is_uncertain(env, monkeypatch):
    import whisper_ditado.issues.client as issues_client

    env.scenario(calls=[create_call(result="ainda não")], sleep_after_calls=30)
    real = issues_client.run_stream
    monkeypatch.setattr(issues_client, "run_stream", lambda *a, **kw: real(*a, **{**kw, "timeout_s": 1}))
    # a call passou no hook e o resultado chegou sem número → não dá para saber
    out = env.client.create_issue(FIELDS)
    assert out.state == "uncertain" and out.number is None
    assert out.error == "Pode ter sido criada — confira no MCP de issues"


def test_create_timeout_without_any_result_is_uncertain(env, monkeypatch):
    import whisper_ditado.issues.client as issues_client

    # sem tool_result: o process morre depois do hook ter liberado, mas antes da resposta
    env.scenario(calls=[create_call(result=json.dumps(CREATED))], sleep_after_calls=30)
    real = issues_client.run_stream

    def cut(*a, **kw):
        run = real(*a, **{**kw, "timeout_s": 1})
        run.events[:] = [e for e in run.events if e.get("type") != "user"]  # a resposta nunca chegou
        return run

    monkeypatch.setattr(issues_client, "run_stream", cut)
    assert env.client.create_issue(FIELDS).state == "uncertain"


def test_create_tool_error_after_send_is_uncertain(env):
    env.scenario(calls=[create_call(result="HTTP 500", is_error=True)])
    out = env.client.create_issue(FIELDS)
    assert out.state == "uncertain" and out.error == "Pode ter sido criada — confira no MCP de issues"


def test_create_not_logged_in_is_failed(env):
    env.scenario(mcp_status="needs-auth")
    out = env.client.create_issue(FIELDS)
    assert out.state == "failed" and "Sem login no MCP de issues" in out.error and "/mcp" in out.error


def test_create_other_mcp_status_is_failed(env):
    env.scenario(mcp_status="failed")
    out = env.client.create_issue(FIELDS)
    assert out.state == "failed" and "Não foi possível conectar ao MCP de issues (failed)" in out.error


def test_create_without_call_is_failed(env):
    env.scenario(final="Não vou criar")
    out = env.client.create_issue(FIELDS)
    assert out.state == "failed" and out.number is None


def test_create_response_title_mismatch_is_flagged(env):
    env.scenario(calls=[create_call(result=json.dumps({**CREATED, "title": "Outro título"}))])
    out = env.client.create_issue(FIELDS)
    assert out == CreateOutcome("created", 217, "https://issues.example.test/217",
                                "Confira no MCP de issues: o título devolvido é diferente do enviado")


def test_create_without_guard_marker_is_flagged(env, monkeypatch):
    import whisper_ditado.issues.client as issues_client

    # se o hook não rodou, uma criação bem-sucedida continua sendo "criada", mas com aviso
    env.scenario(calls=[create_call()])
    real = issues_client.run_stream
    monkeypatch.setattr(issues_client, "run_stream", lambda *a, **kw: real(*a, **{**kw, "settings": None}))
    out = env.client.create_issue(FIELDS)
    assert out.state == "created" and out.number == 217 and "Confira no MCP de issues" in out.error


def test_create_never_raises_on_missing_binary(tmp_path):
    client = IssueClient(ClaudeCli(str(tmp_path / "nao-existe"), None), lambda: URL)
    out = client.create_issue(FIELDS)
    assert out.state == "failed" and "Claude Code não encontrado" in out.error


# ----- cache -----


def test_options_cache_ttl_and_refresh():
    calls = []
    now = [1000.0]

    class Client:
        def list_options(self):
            calls.append(1)
            return IssueOptions("vi", [], [], [], [], datetime.now())

    cache = OptionsCache(Client(), clock=lambda: now[0])
    assert cache.peek() is None
    first = cache.get()
    assert cache.get() is first and len(calls) == 1 and cache.peek() is first
    cache.get(refresh=True)
    assert len(calls) == 2
    now[0] += 601
    cache.get()
    assert len(calls) == 3


def test_options_cache_keeps_stale_value_when_fetch_fails():
    state = {"fail": False}

    class Client:
        def list_options(self):
            if state["fail"]:
                raise IssueError("fora do ar")
            return IssueOptions("vi", [], [], [], [], datetime.now())

    cache = OptionsCache(Client())
    good = cache.get()
    state["fail"] = True
    with pytest.raises(IssueError):
        cache.get(refresh=True)
    assert cache.peek() is good


class Flaky:
    """Cliente falso: falha enquanto `fail` for verdadeiro; conta as buscas."""

    def __init__(self):
        self.fail, self.calls = True, 0
        self.url = "http://a"
        self.url_fn = lambda: self.url

    def list_options(self):
        self.calls += 1
        if self.fail:
            raise IssueError("fora do ar %d" % self.calls)
        return IssueOptions("vi", [], [], [], [], datetime.now())


def test_options_cache_negative_result_is_kept_for_30s():
    client, now = Flaky(), [1000.0]
    cache = OptionsCache(client, clock=lambda: now[0])
    with pytest.raises(IssueError, match="fora do ar 1"):
        cache.get()
    now[0] += 29
    with pytest.raises(IssueError, match="fora do ar 1"):  # o mesmo erro, sem nova busca
        cache.get()
    assert client.calls == 1
    now[0] += 2
    with pytest.raises(IssueError, match="fora do ar 2"):
        cache.get()
    assert client.calls == 2


def test_options_cache_refresh_always_retries_after_failure():
    client = Flaky()
    cache = OptionsCache(client, clock=lambda: 1000.0)
    with pytest.raises(IssueError):
        cache.get()
    client.fail = False
    assert cache.get(refresh=True).username == "vi"
    assert client.calls == 2
    assert cache.get() is cache.peek() and client.calls == 2  # sucesso limpa o cache negativo


def test_options_cache_negative_result_does_not_survive_url_change():
    client = Flaky()
    cache = OptionsCache(client, clock=lambda: 1000.0)
    with pytest.raises(IssueError):
        cache.get()
    client.url, client.fail = "http://b", False
    assert cache.get().username == "vi" and client.calls == 2


def test_options_cache_different_url_is_a_miss():
    client = Flaky()
    client.fail = False
    cache = OptionsCache(client, clock=lambda: 1000.0)
    first = cache.get()
    assert cache.get() is first and client.calls == 1
    client.url = "http://b"
    assert cache.peek() is None
    second = cache.get()
    assert second is not first and client.calls == 2
    assert cache.get() is second and cache.peek() is second


def test_options_cache_runs_one_fetch_at_a_time():
    import threading
    import time

    running, peak, total = [0], [0], [0]

    class Client:
        def list_options(self):
            running[0] += 1
            peak[0] = max(peak[0], running[0])
            total[0] += 1
            time.sleep(0.2)
            running[0] -= 1
            return IssueOptions("vi", [], [], [], [], datetime.now())

    cache = OptionsCache(Client())
    threads = [threading.Thread(target=cache.get) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1 and total[0] == 1  # as outras três esperaram e reaproveitaram a busca


def test_create_blocked_by_guard_is_not_mistaken_for_login_error(env):
    fields = IssueFields("Corrigir login", "Falha no login do dash", 9, 3, [4], 2, "medium")
    env.scenario(calls=[{"name": TOOL_CREATE, "input": {**fields.as_tool_input(), "title": "Outro"}, "result": "{}"}])
    out = env.client.create_issue(fields)
    assert out.state == "failed" and "Sem login" not in out.error and "title" in out.error
    assert "Argumentos aprovados" not in out.error


def test_create_null_extra_field_in_call_is_not_flagged(env):
    env.scenario(calls=[create_call(input={**FIELDS.as_tool_input(), "environment": None})])
    assert env.client.create_issue(FIELDS) == CreateOutcome("created", 217, "https://issues.example.test/217", None)


# ----- rodada de correção 1 -----


def test_hook_command_is_isolated_and_fail_closed(env):
    env.scenario(calls=[create_call()])
    env.client.create_issue(FIELDS)
    hook = json.loads(env.argv_after("--settings"))["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook["command"].startswith(f"{sys.executable} -I ")
    assert hook["command"].endswith(" || exit 2") and hook["timeout"] == 30


def test_create_fails_closed_when_hook_interpreter_is_missing(env, monkeypatch, tmp_path):
    import types

    import whisper_ditado.issues.client as issues_client

    env.scenario(calls=[create_call()])  # sem o hook funcionando, esta call passaria com o número 217
    monkeypatch.setattr(issues_client, "sys", types.SimpleNamespace(executable=str(tmp_path / "nao-existe" / "python")))
    out = env.client.create_issue(FIELDS)
    assert out.state == "failed" and out.number is None and out.url is None
    assert "não enviada" in out.error


def test_exception_after_approved_call_is_uncertain_not_failed(env, monkeypatch):
    env.scenario(calls=[create_call()])

    def boom(*a, **kw):
        raise RuntimeError("falha ao interpretar")

    monkeypatch.setattr(IssueClient, "_outcome", boom)
    out = env.client.create_issue(FIELDS)
    assert out.state == "uncertain" and out.error == "Pode ter sido criada — confira no MCP de issues"


def test_exception_when_nothing_was_sent_is_failed(env, monkeypatch):
    env.scenario(calls=[create_call(input={**FIELDS.as_tool_input(), "title": "Outro"})])  # o hook bloqueia

    def boom(*a, **kw):
        raise RuntimeError("falha ao interpretar")

    monkeypatch.setattr(IssueClient, "_outcome", boom)
    assert env.client.create_issue(FIELDS).state == "failed"


def test_cleanup_error_does_not_turn_approved_call_into_failure(env, monkeypatch):
    import tempfile

    env.scenario(calls=[create_call()])

    class Exploding(tempfile.TemporaryDirectory):
        def __exit__(self, *a):
            super().__exit__(*a)
            if self.name.rsplit("/", 1)[-1].startswith("whisper-ditado-create-"):
                raise OSError("limpeza falhou")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", Exploding)
    assert env.client.create_issue(FIELDS).state == "created"


@pytest.mark.parametrize("extra,flagged", [
    ({"assignee": 99}, "responsável"),
    ({"team": 4}, "equipe"),
    ({"products": [5]}, "produto"),
    ({"stage": 3}, "etapa"),
    ({"assignee": {"id": 99, "name": "x"}, "stage": {"id": 9}}, "responsável, etapa"),
    ({"products": [{"id": 4}, {"id": 5}]}, "produto"),
    ({"assignee": True}, "responsável"),  # bool nunca é um id válido
    ({"assignee": 9.0}, "responsável"),
])
def test_create_response_field_mismatch_is_flagged(env, extra, flagged):
    env.scenario(calls=[create_call(result=json.dumps({**CREATED, **extra}))])
    out = env.client.create_issue(FIELDS)
    assert out.state == "created" and out.number == 217
    assert out.error == f"Confira no MCP de issues: o MCP de issues devolveu campos diferentes do enviado ({flagged})"


def test_create_response_title_and_other_field_mismatch_lists_both(env):
    env.scenario(calls=[create_call(result=json.dumps({**CREATED, "title": "Outro", "team": 4}))])
    out = env.client.create_issue(FIELDS)
    assert out.state == "created" and out.error.endswith("(título, equipe)")


def test_create_response_nested_dicts_that_match_are_fine(env):
    response = {**CREATED, "assignee": {"id": 9, "username": "miguel"}, "team": {"id": 3}, "stage": {"id": 2},
                "products": [{"id": 4, "name": "dash"}]}
    env.scenario(calls=[create_call(result=json.dumps(response))])
    assert env.client.create_issue(FIELDS) == CreateOutcome("created", 217, "https://issues.example.test/217", None)


def test_create_response_plain_ints_that_match_are_fine(env):
    response = {**CREATED, "assignee": 9, "team": 3, "stage": 2, "products": [4]}
    env.scenario(calls=[create_call(result=json.dumps(response))])
    assert env.client.create_issue(FIELDS).error is None


def test_create_response_absent_or_unrecognized_fields_are_ignored(env):
    response = {"number": 217, "url": "https://issues.example.test/217", "assignee": None, "team": "Desenvolvimento",
                "stage": {"name": "To Do"}, "products": ["dash"]}
    env.scenario(calls=[create_call(result=json.dumps(response))])
    assert env.client.create_issue(FIELDS) == CreateOutcome("created", 217, "https://issues.example.test/217", None)


def test_unconfigured_mcp_fails_without_starting_claude(monkeypatch):
    from whisper_ditado.issues import client
    monkeypatch.setattr(client, "run_stream", lambda *a, **kw: pytest.fail("MCP sem URL não deve executar o Claude"))
    c = IssueClient(ClaudeCli(FAKE, None), lambda: None)
    with pytest.raises(IssueError, match="Configure o endereço"):
        c.list_options()
    outcome = c.create_issue(FIELDS)
    assert outcome.state == "failed" and "nada foi enviado" in outcome.error


def test_custom_mcp_name_is_used_for_options_creation_and_guard(env):
    server = "my-tracker"
    env.client.server_fn = lambda: server
    env.scenario(calls=[{**options_call(), "name": f"mcp__{server}__listar_opcoes"}])
    assert env.client.list_options().people[0].id == 9
    assert json.loads(env.argv_after("--mcp-config"))["mcpServers"] == {
        server: {"type": "http", "url": URL}}
    assert env.argv_after("--allowedTools") == f"mcp__{server}__listar_opcoes"
    env.scenario(calls=[{**create_call(), "name": f"mcp__{server}__criar_issue"}])
    assert env.client.create_issue(FIELDS).state == "created"
    hook = json.loads(env.argv_after("--settings"))["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == f"mcp__{server}__criar_issue"
    # Mesmo no servidor personalizado, mudar o conteúdo aprovado bloqueia a chamada.
    env.scenario(calls=[{**create_call(input={**FIELDS.as_tool_input(), "title": "Não aprovado"}),
                         "name": f"mcp__{server}__criar_issue"}])
    outcome = env.client.create_issue(FIELDS)
    assert outcome.state == "failed" and "diferem" in outcome.error


def test_options_cache_is_invalidated_when_server_name_changes(env):
    server = ["issues"]
    env.client.server_fn = lambda: server[0]
    cache = OptionsCache(env.client)
    env.scenario(calls=[options_call()])
    assert cache.get().username == "vi"
    server[0] = "another-tracker"
    assert cache.peek() is None
    env.scenario(calls=[{**options_call(), "name": "mcp__another-tracker__listar_opcoes"}])
    assert cache.get().username == "vi"


# ----- anexo dos prints -----

ATTACH_TOOL = "mcp__issues__anexar_arquivo"


@pytest.fixture
def jpgs(tmp_path):
    files = []
    for label in ("P1", "P3"):
        path = tmp_path / f"print-{label}.jpg"
        path.write_bytes(b"\xff\xd8 jpeg " + label.encode())
        files.append((label, path))
    return files


def attach_call(label="P1", **kw):
    base = {"name": ATTACH_TOOL, "input": {"issue": 217, "content": f"@@PRINT_B64:{label}@@"}, "result": "ok"}
    base.update(kw)
    return base


def test_attach_unsupported_when_no_calls_and_sem_suporte(env, jpgs):
    env.scenario(final="SEM_SUPORTE")
    out = env.client.attach_prints(217, "https://issues.example.test/217", jpgs)
    assert out == AttachOutcome("unsupported", "O MCP de issues não aceita imagens")


def test_attach_sent_when_all_calls_ok(env, jpgs):
    env.scenario(calls=[options_call(), attach_call("P1"), attach_call("P3")])
    assert env.client.attach_prints(217, "https://issues.example.test/217", jpgs) == AttachOutcome("sent", None)
    stdin = env.log()["stdin"]
    assert "A issue 217 (https://issues.example.test/217)" in stdin
    assert "- P1: print-P1.jpg\n- P3: print-P3.jpg" in stdin and "@@PRINT_B64:<rótulo>@@" in stdin


def test_attach_failed_when_some_call_errors(env, jpgs):
    env.scenario(calls=[attach_call("P1"), attach_call("P3", result="HTTP 413", is_error=True)])
    out = env.client.attach_prints(217, None, jpgs)
    assert out == AttachOutcome("failed", "Alguns prints não foram enviados ao MCP de issues")


def test_attach_failed_when_marker_label_is_unknown(env, jpgs):
    env.scenario(calls=[attach_call("P9")])  # o hook bloqueia: rótulo fora do mapa
    assert env.client.attach_prints(217, None, jpgs).state == "failed"


def test_attach_blocks_create_tool(env, jpgs):
    env.scenario(calls=[create_call()])
    out = env.client.attach_prints(217, None, jpgs)
    assert out.state == "failed"


def test_attach_runs_with_server_tools_and_guard_hook(env, jpgs):
    env.scenario(calls=[attach_call()])
    env.client.attach_prints(217, None, jpgs)
    assert env.argv_after("--allowedTools") == "mcp__issues" and env.argv_after("--tools") == ""
    [entry] = json.loads(env.argv_after("--settings"))["hooks"]["PreToolUse"]
    assert entry["matcher"] == "mcp__issues__.*"
    command = entry["hooks"][0]["command"]
    assert command.startswith(f"{sys.executable} -I ") and command.endswith(" || exit 2")
    assert "attach_guard.py" in command and TOOL_CREATE in command
    assert json.loads(env.argv_after("--mcp-config"))["mcpServers"][SERVER]["url"] == URL


def test_attach_uses_custom_server_name(env, jpgs):
    env.client.server_fn = lambda: "tracker"
    env.scenario(calls=[{**attach_call(label), "name": "mcp__tracker__anexar"} for label in ("P1", "P3")])
    assert env.client.attach_prints(1, None, jpgs).state == "sent"
    assert env.argv_after("--allowedTools") == "mcp__tracker"
    entry = json.loads(env.argv_after("--settings"))["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "mcp__tracker__.*" and "mcp__tracker__criar_issue" in entry["hooks"][0]["command"]
    assert "no tracker" in env.log()["stdin"]


def test_attach_not_logged_in_is_failed(env, jpgs):
    env.scenario(mcp_status="needs-auth")
    out = env.client.attach_prints(217, None, jpgs)
    assert out.state == "failed" and "Sem login" in out.error


def test_attach_never_raises(tmp_path, jpgs):
    client = IssueClient(ClaudeCli(str(tmp_path / "nao-existe"), None), lambda: URL)
    out = client.attach_prints(217, None, jpgs)
    assert out.state == "failed" and "Claude Code não encontrado" in out.error
    assert IssueClient(ClaudeCli(FAKE, None), lambda: None).attach_prints(1, None, jpgs).state == "failed"


def test_attach_read_call_without_markers_and_sem_suporte_is_unsupported(env, jpgs):
    env.scenario(calls=[{"name": "mcp__issues__buscar_issue", "input": {"number": 217}, "result": "{}"}],
                 final="SEM_SUPORTE")
    assert env.client.attach_prints(217, None, jpgs) == AttachOutcome("unsupported", "O MCP de issues não aceita imagens")


def test_attach_read_call_without_markers_and_no_sem_suporte_is_failed(env, jpgs):
    env.scenario(calls=[{"name": "mcp__issues__buscar_issue", "input": {"number": 217}, "result": "{}"}])
    out = env.client.attach_prints(217, None, jpgs)
    assert out.state == "failed" and "não anexou" in out.error


def test_attach_partial_marker_coverage_is_failed(env, jpgs):
    env.scenario(calls=[attach_call("P1")], final="OK")  # P3 nunca foi enviado
    assert env.client.attach_prints(217, None, jpgs) == AttachOutcome(
        "failed", "Alguns prints não foram enviados ao MCP de issues")


def test_attach_retry_after_failure_counts_as_sent(env, jpgs):
    env.scenario(calls=[attach_call("P1", result="HTTP 500", is_error=True), attach_call("P1"), attach_call("P3")])
    assert env.client.attach_prints(217, None, jpgs) == AttachOutcome("sent", None)


def test_attach_marker_inside_data_url_counts(env, jpgs):
    calls = [{"name": ATTACH_TOOL, "input": {"files": [{"url": f"@@PRINT_DATAURL:{label}@@"}]}, "result": "ok"}
             for label in ("P1", "P3")]
    env.scenario(calls=calls)
    assert env.client.attach_prints(217, None, jpgs).state == "sent"


def test_attach_no_calls_and_claude_error_is_failed(env, jpgs):
    env.scenario(final="SEM_SUPORTE", final_is_error=True)
    out = env.client.attach_prints(217, None, jpgs)
    assert out.state == "failed" and "não concluiu" in out.error
