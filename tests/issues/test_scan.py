import json
from datetime import datetime
from pathlib import Path

import pytest

from whisper_ditado.issues.client import IssueOptions, Option
from whisper_ditado.issues.prompts import DEFAULT_TASKS_PROMPT, PRINTS_BLOCK, SCAN_SYSTEM
from whisper_ditado.issues.scan import (
    MAX_ITEMS,
    SCAN_TIMEOUT_S,
    TRANSCRIPT_MAX_CHARS,
    Evidence,
    ScanItem,
    build_scan_prompt,
    parse_scan,
    run_scan,
)
from whisper_ditado.claude_cli import ClaudeCli, ClaudeError
from whisper_ditado.prints.parts import ImagePart
from whisper_ditado.store import Segment, TaskSuggestion

FAKE = str(Path(__file__).parent.parent / "fixtures" / "fake_claude.py")

OPTS = IssueOptions(
    "vi",
    people=[Option(9, "Miguel"), Option(10, "Ana")],
    teams=[Option(3, "Desenvolvimento")],
    products=[Option(4, "e-commerce")],
    stages=[Option(2, "To Do")],
    fetched_at=datetime.now(),
)


def seg(start, end, text, speaker="s1"):
    return Segment(id=None, meeting_id="m1", speaker_id=speaker, source="mic",
                   start_s=start, end_s=end, text=text)


def created_task(**kw):
    base = dict(id="t1", meeting_id="m1", origin="claude", position=0, kind="tarefa", title="Checkout: boleto",
                description="d", project=None, product_id=4, assignee_id=9, team_id=3, stage_id=2,
                priority="medium", suggested=None, evidence=[], edited=False, discarded=False,
                create_state="created", create_error=None, issue_number=None, issue_url=None,
                repeats_number=None, created_at="", updated_at="")
    base.update(kw)
    return TaskSuggestion(**base)


def raw_item(**kw):
    base = {"tipo": "tarefa", "titulo": "Checkout: boleto", "descricao": "## Problema\nx",
            "produto_id": 4, "responsavel_id": 9, "responsavel_dica": "Miguel",
            "equipe_id": 3, "equipe_dica": "Desenvolvimento", "prioridade": "high",
            "trechos": [{"tempo": "01:05", "falante": "Ana", "texto": "o Miguel pega isso"}],
            "repete": None}
    base.update(kw)
    return base


def parse(items, options=OPTS, segments=(), created=frozenset()):
    return parse_scan(json.dumps(items), options, list(segments), set(created))


# ----- prompt -----


def test_constants():
    assert (SCAN_TIMEOUT_S, TRANSCRIPT_MAX_CHARS, MAX_ITEMS) == (600, 300_000, 30)


def test_prompt_sections_in_order():
    p = build_scan_prompt("REGRAS", "[00:01] Ana: oi", "RESUMO", OPTS,
                          [created_task(issue_number=217, title="Checkout: X")])
    marks = ("REGRAS", "--- OPÇÕES DO MCP DE ISSUES", "--- JÁ LEVANTADO", "--- RESUMO", "--- TRANSCRIÇÃO")
    i = [p.index(s) for s in marks]
    assert i == sorted(i) and '"numero_issues_client": 217' in p and "[00:01] Ana: oi" in p
    assert "dados de terceiros" in p and "RESUMO" in p
    assert '"Miguel"' in p and '"Desenvolvimento"' in p and '"e-commerce"' in p
    assert '"titulo": "Checkout: X"' in p and '"descartado": false' in p and '"tipo": "tarefa"' in p


def test_prompt_without_options_says_so():
    p = build_scan_prompt("R", "T", None, None, [])
    assert "(opções do MCP de issues indisponíveis: deixe ids vazios)" in p
    assert "(sem resumo)" in p
    after = p.split("--- JÁ LEVANTADO", 1)[1].split("--- RESUMO", 1)[0]
    assert "[]" in after


def test_prompt_keeps_unicode_readable():
    p = build_scan_prompt("R", "T", None, IssueOptions("v", [Option(1, "João")], [], [], [], datetime.now()), [])
    assert "João" in p and "\\u" not in p


def test_prompt_uses_operator_context_separately_from_transcript():
    p = build_scan_prompt("REGRAS", "TRANSCRIÇÃO", None, None, [],
                          context="  Foque no checkout.\nAgrupe pagamentos.  ")
    assert "--- CONTEXTO ADICIONAL DO OPERADOR ---" in p
    assert "Foque no checkout.\nAgrupe pagamentos." in p
    assert p.index("REGRAS") < p.index("CONTEXTO ADICIONAL") < p.index("--- TRANSCRIÇÃO")
    assert "Use as orientações abaixo para o levantamento desta reunião." in p
    assert "refazer" not in p
    assert "precedência sobre as regras gerais" in p
    assert "CONTEXTO ADICIONAL" not in build_scan_prompt("R", "T", None, None, [], context=" \n ")


def test_prompt_excludes_suggestions_being_replaced_and_preserves_reviewed_items():
    existing = [
        created_task(title="Refazer esta", create_state="none"),
        created_task(title="Manual", create_state="none", origin="manual"),
        created_task(title="Editada", create_state="none", edited=True),
        created_task(title="Descartada", create_state="none", discarded=True),
        *[created_task(title=state, create_state=state)
          for state in ("creating", "created", "uncertain", "failed")],
    ]
    p = build_scan_prompt("R", "T", None, None, existing, context="Refaça o agrupamento.")
    assert "Refazer esta" not in p
    for title in ("Manual", "Editada", "Descartada", "creating", "created", "uncertain", "failed"):
        assert f'"titulo": "{title}"' in p


def test_prompt_lists_people_with_teams_and_products():
    opts = IssueOptions("vi", people=[Option(9, "Miguel", teams=(3,), products=(4,))],
                        teams=[Option(3, "Dev")], products=[Option(4, "e-commerce")], stages=[],
                        fetched_at=datetime.now())
    prompt = build_scan_prompt("regras", "t", None, opts, [])
    assert '{"id": 9, "name": "Miguel", "equipes": [3], "produtos": [4]}' in prompt
    assert '"equipes": [{"id": 3, "name": "Dev"}]' in prompt
    assert '"produtos": [{"id": 4, "name": "e-commerce"}]' in prompt


def test_scan_system_has_no_projeto_key():
    assert '"projeto"' not in SCAN_SYSTEM and "## Projeto" not in DEFAULT_TASKS_PROMPT


def test_default_prompt_guides_assignee_by_team_and_product():
    assert "escolha responsável e equipe entre quem participa do produto da issue" in DEFAULT_TASKS_PROMPT
    assert "arquivos CLAUDE.md da pasta raiz para entender os projetos" in DEFAULT_TASKS_PROMPT


# ----- parse -----


def test_parse_valid_item_maps_evidence_to_segment():
    items = parse([raw_item()], segments=[seg(60, 70, "x"), seg(70, 80, "y")])
    assert len(items) == 1
    it = items[0]
    assert it.evidence[0] == Evidence(65.0, 70.0, "Ana", "o Miguel pega isso")
    assert (it.kind, it.title, it.product_id, it.assignee_id, it.team_id, it.priority) == (
        "tarefa", "Checkout: boleto", 4, 9, 3, "high")
    assert (it.assignee_hint, it.team_hint, it.repeats_number) == ("Miguel", "Desenvolvimento", None)


def test_evidence_end_falls_back_to_last_segment_then_plus_20():
    ev = lambda t, segs: parse([raw_item(trechos=[{"tempo": t, "falante": "A", "texto": "x"}])],
                               segments=segs)[0].evidence[0]
    # t dentro do segmento: fim dele; t depois do fim do último que começou antes: t + 20
    assert ev("01:05", [seg(60, 90, "x")]).end_s == 90.0
    assert ev("01:20", [seg(60, 70, "x")]).end_s == 100.0
    # lacuna entre segmentos (o último que começou já terminou): t + 20
    assert ev("01:05", [seg(60, 63, "x"), seg(70, 80, "y")]).end_s == 85.0
    # nenhum segmento: t + 20
    assert ev("01:05", []).end_s == 85.0
    # só segmentos que começam depois: t + 20
    assert ev("00:10", [seg(60, 70, "x")]).end_s == 30.0
    # horário com hora
    assert ev("1:02:03", []).start_s == 3723.0


def test_evidence_end_matches_segment_starting_within_the_truncated_second():
    ev = lambda t, segs: parse([raw_item(trechos=[{"tempo": t, "falante": "A", "texto": "x"}])],
                               segments=segs)[0].evidence[0]
    # a transcrição mostra o horário truncado ("00:44" para 44.43): o segmento que começa nesse segundo vale
    assert ev("00:44", [seg(44.43, 52, "b")]).end_s == 52.0
    # e vence o resto do segmento anterior que termina logo depois de t (não gera trecho de 0,13 s)
    assert ev("19:43", [seg(1170, 1183.13, "a"), seg(1183.5, 1190, "b")]).end_s == 1190.0
    # sobra de menos de 1 s do anterior, sem segmento novo no segundo: t + 20
    assert ev("19:43", [seg(1170, 1183.13, "a")]).end_s == 1203.0


def test_evidence_with_bad_time_dropped():
    trechos = [{"tempo": "ontem", "falante": "A", "texto": "x"},
               {"tempo": 65, "falante": "A", "texto": "x"},
               "texto solto",
               {"tempo": "00:10", "falante": "A", "texto": 7},
               {"tempo": "00:10", "falante": "B", "texto": "ok"}]
    ev = parse([raw_item(trechos=trechos)])[0].evidence
    assert [e.text for e in ev] == ["ok"]


def test_evidence_text_truncated_and_limited_to_five():
    trechos = [{"tempo": "00:%02d" % i, "falante": "A", "texto": "y" * 900} for i in range(7)]
    ev = parse([raw_item(trechos=trechos)])[0].evidence
    assert len(ev) == 5 and all(len(e.text) == 500 for e in ev)


def test_evidence_accepts_five_excerpts_for_a_grouped_issue():
    trechos = [{"tempo": "00:%02d" % i, "falante": "A", "texto": f"pedido {i}"} for i in range(5)]
    ev = parse([raw_item(trechos=trechos)])[0].evidence
    assert [e.text for e in ev] == [f"pedido {i}" for i in range(5)]


def test_default_prompt_groups_requests_by_area():
    from whisper_ditado.issues.prompts import DEFAULT_TASKS_PROMPT

    assert "Cada área de um projeto vira uma issue só" in DEFAULT_TASKS_PROMPT
    assert "Backend e front de um mesmo pedido são a mesma issue" in DEFAULT_TASKS_PROMPT
    assert "nunca agrupe por cliente" in DEFAULT_TASKS_PROMPT
    assert "Uma issue por item" not in DEFAULT_TASKS_PROMPT
    assert "de 1 a 5 trechos" in DEFAULT_TASKS_PROMPT


def test_default_scan_instructions_use_sdd(env, tmp_path):
    from whisper_ditado.issues.prompts import DEFAULT_TASKS_PROMPT

    env(final="[]")
    run_scan(ClaudeCli(FAKE, None), tmp_path, DEFAULT_TASKS_PROMPT, "T", None, OPTS, [], [])
    prompt = env.log()["stdin"]
    assert "Spec Driven Development (SDD)" in prompt
    for heading in ("## Especificação", "## Critérios de aceite", "## Plano de implementação", "## Validação"):
        assert heading in prompt
    assert "Não preencha lacunas por suposição" in prompt


def test_parse_tolerates_text_around_json():
    text = "Segue:\n" + json.dumps([raw_item()]) + "\nPronto."
    assert len(parse_scan(text, OPTS, [], set())) == 1


def test_parse_empty_list():
    assert parse_scan("[]", OPTS, [], set()) == []
    assert parse_scan("Nada a levantar: []", OPTS, [], set()) == []


def test_parse_no_array_raises():
    for text in ("", "sem lista nenhuma", "{\"a\": 1}", "[1, 2]"):
        with pytest.raises(ClaudeError, match="o Claude não devolveu uma lista"):
            parse_scan(text, OPTS, [], set())


def test_parse_skips_non_dict_items():
    text = json.dumps(["x", 3, None, raw_item()])
    assert len(parse_scan(text, OPTS, [], set())) == 1


def test_parse_drops_invalid_kind_and_empty_title():
    items = parse([raw_item(tipo="epic"), raw_item(titulo="   "), raw_item(titulo=None), raw_item(tipo=None),
                   raw_item(titulo="Bom", tipo="bugfix")])
    assert [(i.kind, i.title) for i in items] == [("bugfix", "Bom")]


def test_parse_unknown_ids_become_none_keep_hint():
    it = parse([raw_item(responsavel_id=999, responsavel_dica="Miguel", equipe_id=77, produto_id=55)])[0]
    assert (it.assignee_id, it.team_id, it.product_id) == (None, None, None)
    assert (it.assignee_hint, it.team_hint) == ("Miguel", "Desenvolvimento")


def test_parse_ids_must_be_real_ints():
    it = parse([raw_item(responsavel_id=True, equipe_id="3", produto_id=4.0)])[0]
    assert (it.assignee_id, it.team_id, it.product_id) == (None, None, None)


def test_parse_without_options_all_ids_none():
    it = parse([raw_item()], options=None)[0]
    assert (it.assignee_id, it.team_id, it.product_id) == (None, None, None)
    assert it.assignee_hint == "Miguel"


def test_parse_hints_only_str_stripped_and_limited():
    it = parse([raw_item(responsavel_dica="  " + "m" * 300 + " ", equipe_dica=12)])[0]
    assert it.assignee_hint == "m" * 200 and it.team_hint is None
    it = parse([raw_item(responsavel_dica="   ", equipe_dica=None)])[0]
    assert it.assignee_hint is None and it.team_hint is None


def test_parse_clamps_and_defaults():
    it = parse([raw_item(titulo="T" * 400, prioridade="altíssima")])[0]
    assert len(it.title) == 300 and it.priority == "medium"
    items = parse([raw_item(titulo="i%d" % n) for n in range(200)])
    assert len(items) == MAX_ITEMS
    assert [i.title for i in items][:2] == ["i0", "i1"]
    it = parse([raw_item(descricao="d" * 30_000)])[0]
    assert len(it.description) == 20_000


def test_parse_description_type():
    it = parse([raw_item(descricao=["x"])])[0]
    assert it.description == ""


def test_parse_ignores_projeto_key():
    it = parse([raw_item(projeto="ecommerce")])[0]
    assert not hasattr(it, "project")
    assert "project" not in it.as_store_item(2)["fields"]


def test_parse_repeats_only_known_created_numbers():
    assert parse([raw_item(repete=217)], created={217})[0].repeats_number == 217
    assert parse([raw_item(repete=5)], created={217})[0].repeats_number is None
    assert parse([raw_item(repete=True)], created={1})[0].repeats_number is None
    assert parse([raw_item(repete="217")], created={217})[0].repeats_number is None


def test_parse_drops_unknown_keys():
    it = parse([raw_item(comando="rm -rf")])[0]
    assert not hasattr(it, "comando")
    assert "rm -rf" not in json.dumps(it.as_store_item(2))


def test_parse_scan_keeps_known_print_labels():
    it = parse([raw_item(prints=["P2", "P9", 3, "P2"])])[0]
    assert it.as_store_item(2, {"P1": "a", "P2": "b"})["print_ids"] == ["b"]
    assert it.as_store_item(2)["print_ids"] == []


def test_parse_prints_dedup_strings_only_max_five():
    labels = [f"P{i}" for i in range(1, 9)]
    it = parse([raw_item(prints=["P1", "P1", 7, None, *labels])])[0]
    assert it.prints == ["P1", "P2", "P3", "P4", "P5"]
    assert parse([raw_item(prints="P1")])[0].prints == []
    assert parse([raw_item()])[0].prints == []


def test_scan_system_asks_for_prints_key_after_repete():
    assert '"prints"' in SCAN_SYSTEM
    assert SCAN_SYSTEM.index('"repete"') < SCAN_SYSTEM.index('"prints"')
    assert SCAN_SYSTEM.index('"trechos"') < SCAN_SYSTEM.index('"repete"')


def test_build_scan_prompt_lists_prints_block_only_when_present():
    without = build_scan_prompt("R", "T", None, None, [])
    assert "PRINTS DE TELA" not in without
    p = build_scan_prompt("R", "T", None, None, [], print_labels=["P1", "P2"])
    assert PRINTS_BLOCK in p and '["P1", "P2"]' in p
    assert p.index("--- RESUMO") < p.index("--- PRINTS DE TELA") < p.index("--- TRANSCRIÇÃO")


def test_as_store_item_format():
    it = parse([raw_item(repete=217)], segments=[seg(60, 70, "x")], created={217})[0]
    assert it.as_store_item(2) == {
        "fields": {"kind": "tarefa", "title": "Checkout: boleto", "description": "## Problema\nx",
                   "product_id": 4, "assignee_id": 9, "team_id": 3,
                   "stage_id": 2, "priority": "high"},
        "suggested": {"product_id": 4, "assignee_id": 9, "team_id": 3,
                      "assignee_hint": "Miguel", "team_hint": "Desenvolvimento"},
        "evidence": [{"start_s": 65.0, "end_s": 70.0, "speaker": "Ana", "text": "o Miguel pega isso"}],
        "repeats_number": 217,
        "print_ids": [],
    }
    assert it.as_store_item(None)["fields"]["stage_id"] is None


def test_as_store_item_is_accepted_by_store(tmp_path):
    from datetime import datetime as dt

    from whisper_ditado.store import Store
    store = Store(tmp_path / "t.db")
    try:
        mid = store.create_meeting(dt(2026, 10, 20, 10, 0, 0)).id
        stored = store.replace_scan_tasks(mid, [parse([raw_item(repete=217)], created={217})[0].as_store_item(2)])
    finally:
        store.close()
    t = stored[0]
    assert (t.title, t.stage_id, t.assignee_id, t.repeats_number) == ("Checkout: boleto", 2, 9, 217)
    assert t.suggested["assignee_hint"] == "Miguel" and t.evidence[0]["end_s"] == 85.0


# ----- run_scan -----


@pytest.fixture
def env(monkeypatch, tmp_path):
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "stream")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    def scenario(**kw):
        monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", json.dumps(kw))

    scenario.log = lambda: json.loads(log.read_text())
    return scenario


def scan(root, transcript="[00:01] Ana: oi", **kw):
    return run_scan(ClaudeCli(FAKE, None), root, "REGRAS", transcript, None, OPTS, [], kw.get("segments", []))


def test_run_scan_uses_root_and_read_only_tools(env, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    env(final="[]")
    assert scan(root) == []
    log = env.log()
    argv = log["argv"]
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert argv[argv.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert "--mcp-config" not in argv
    assert argv[argv.index("--append-system-prompt") + 1] == SCAN_SYSTEM
    assert log["cwd"] == str(root)
    assert "REGRAS" in log["stdin"] and "[00:01] Ana: oi" in log["stdin"]


def test_run_scan_with_images_sends_stream_json_with_labels(env, tmp_path):
    env(final="[]")
    images = [ImagePart("P1", 65.0, "image/jpeg", "QUJD"), ImagePart("P2", 70.0, "image/jpeg", "REVG")]
    run_scan(ClaudeCli(FAKE, None), tmp_path, "REGRAS", "[00:01] Ana: oi", None, OPTS, [], [], images=images)
    log = env.log()
    assert log["argv"][:3] == ["-p", "--input-format", "stream-json"]
    content = json.loads(log["stdin"])["message"]["content"]
    assert PRINTS_BLOCK in content[0]["text"] and '["P1", "P2"]' in content[0]["text"]
    assert [b["type"] for b in content[1:]] == ["text", "image", "text", "image"]


def test_run_scan_denies_reading_secret_files(env, tmp_path):
    env(final="[]")
    scan(tmp_path)
    argv = env.log()["argv"]
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings == {"permissions": {"deny": [
        "Read(./**/.env*)", "Read(./.env*)", "Read(./**/.mcp.json)", "Read(./.mcp.json)"]}}
    assert "hooks" not in settings


def test_run_scan_returns_items(env, tmp_path):
    env(final="Segue:\n" + json.dumps([raw_item()]))
    items = scan(tmp_path, segments=[seg(60, 70, "x")])
    assert len(items) == 1 and isinstance(items[0], ScanItem) and items[0].evidence[0].end_s == 70.0


def test_run_scan_passes_created_numbers(env, tmp_path):
    env(final=json.dumps([raw_item(repete=217), raw_item(repete=5)]))
    existing = [created_task(issue_number=217), created_task(issue_number=None, create_state="none")]
    items = run_scan(ClaudeCli(FAKE, None), tmp_path, "R", "T", None, OPTS, existing, [])
    assert [i.repeats_number for i in items] == [217, None]


def test_run_scan_sends_operator_context_to_claude(env, tmp_path):
    env(final="[]")
    run_scan(ClaudeCli(FAKE, None), tmp_path, "R", "T", None, OPTS, [], [],
             context="Foque nas pendências de pagamento.")
    assert "Foque nas pendências de pagamento." in env.log()["stdin"]


def test_run_scan_rejects_missing_root(tmp_path):
    missing = tmp_path / "nao-existe"
    with pytest.raises(ClaudeError) as e:
        scan(missing)
    assert str(e.value) == f"Pasta raiz não encontrada: {missing}"
    f = tmp_path / "arquivo"
    f.write_text("x")
    with pytest.raises(ClaudeError, match="Pasta raiz não encontrada"):
        scan(f)


def test_run_scan_rejects_huge_transcript(env, tmp_path):
    with pytest.raises(ClaudeError, match="longa demais.*300 mil caracteres"):
        scan(tmp_path, transcript="x" * (TRANSCRIPT_MAX_CHARS + 1))
    assert not (tmp_path / "log.json").exists()  # o Claude nem foi chamado


def test_run_scan_accepts_transcript_at_limit(env, tmp_path):
    env(final="[]")
    assert scan(tmp_path, transcript="x" * TRANSCRIPT_MAX_CHARS) == []


def test_run_scan_reports_login_error(env, tmp_path):
    env(final="Not logged in · Please run /login", final_is_error=True)
    with pytest.raises(ClaudeError) as e:
        scan(tmp_path)
    assert str(e.value) == "Claude Code não está logado — rode `claude` no terminal"


def test_run_scan_reports_other_error(env, tmp_path):
    env(final="boom", final_is_error=True)
    with pytest.raises(ClaudeError) as e:
        scan(tmp_path)
    assert str(e.value) == "O levantamento falhou: resposta inesperada do Claude"


def test_run_scan_no_array(env, tmp_path):
    env(final="Não consegui.")
    with pytest.raises(ClaudeError) as e:
        scan(tmp_path)
    assert str(e.value) == "O levantamento falhou: o Claude não devolveu uma lista"


def test_run_scan_timeout(env, tmp_path, monkeypatch):
    monkeypatch.setattr("whisper_ditado.issues.scan.SCAN_TIMEOUT_S", 1)
    env(final="[]", sleep_after_calls=30)
    with pytest.raises(ClaudeError) as e:
        scan(tmp_path)
    assert str(e.value) == "O levantamento demorou demais (10 min)"


# ----- saída hostil -----


@pytest.mark.parametrize("text", ["[" * 100_000, "[1," * 50_000, "[{\"a\":" * 50_000, "x" * 10 + "[" * 3000])
def test_parse_deeply_nested_or_truncated_output_is_claude_error(text):
    import time
    t0 = time.monotonic()
    with pytest.raises(ClaudeError, match="o Claude não devolveu uma lista"):
        parse_scan(text, OPTS, [], set())
    assert time.monotonic() - t0 < 5


def test_parse_deep_nesting_before_valid_array_still_finds_it():
    text = "[" * 50 + "\n" + json.dumps([raw_item()])
    assert len(parse_scan(text, OPTS, [], set())) == 1


def test_parse_output_over_cap_is_claude_error():
    from whisper_ditado.issues.scan import OUTPUT_MAX_CHARS
    with pytest.raises(ClaudeError, match="resposta grande demais do Claude"):
        parse_scan("[]" + " " * OUTPUT_MAX_CHARS, OPTS, [], set())


COHERENCE_OPTS = IssueOptions(
    "vi",
    people=[Option(9, "M", teams=(3,), products=(4,)), Option(10, "A", teams=(5,), products=(6,))],
    teams=[Option(3, "Dev"), Option(5, "Ops")], products=[Option(4, "e"), Option(6, "f")],
    stages=[], fetched_at=datetime.now(),
)


def test_parse_drops_assignee_outside_team_and_product():
    raw = json.dumps([{"tipo": "tarefa", "titulo": "X", "produto_id": 4, "equipe_id": 3,
                       "responsavel_id": 10, "responsavel_dica": "Ana"}])
    [item] = parse_scan(raw, COHERENCE_OPTS, [], set())
    assert item.assignee_id is None and item.assignee_hint == "Ana" and item.team_id == 3


def test_parse_keeps_assignee_when_coherent():
    raw = json.dumps([{"tipo": "tarefa", "titulo": "X", "produto_id": 4, "equipe_id": 5,
                       "responsavel_id": 9}])
    [item] = parse_scan(raw, COHERENCE_OPTS, [], set())
    assert item.assignee_id == 9


def test_parse_keeps_assignee_when_no_team_or_product_suggested():
    raw = json.dumps([{"tipo": "tarefa", "titulo": "X", "produto_id": None, "equipe_id": None,
                       "responsavel_id": 10}])
    [item] = parse_scan(raw, COHERENCE_OPTS, [], set())
    assert item.assignee_id == 10
