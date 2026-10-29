# Aba Tarefas: preenchimento automático, contexto e carregamento — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cartões de tarefa chegam com responsável e equipe preenchidos (Claude ciente de quem cuida de quê + "último usado"), o campo "Projeto" some, a caixa de contexto aparece em destaque antes do primeiro levantamento e há carregamento visível no levantamento e na criação.

**Architecture:** Backend Python: `IssueOptions` passa a guardar equipes/produtos de cada pessoa e expõe `coherent()`; o levantamento usa isso no prompt e na validação; um módulo novo `issues/autofill.py` preenche campos vazios ou marcados "last" a partir da reunião atual e do histórico (`task_suggestions` criadas), chamado pela API (PATCH), pelo job de levantamento e pelo job de criação. A origem de cada valor fica em `suggested["fill"]`. Frontend Vue: remove "Projeto", mostra "(último usado)", ganha `AppSpinner`, estados de carregamento e o componente `ScanContextPrompt`.

**Tech Stack:** Python 3 / FastAPI / SQLite (pytest), Vue 3 + TypeScript (vitest, vue-tsc).

**Spec:** `docs/superpowers/specs/2026-10-29-tarefas-preenchimento-contexto-design.md`

## Global Constraints

- Prioridade por campo (responsável e equipe, independentes): 1) escolha do operador; 2) sugestão válida do Claude; 3) último usado.
- Só se preenche campo **vazio sem marca "manual"** ou **marcado "last"**. Valor manual e sugestão válida do Claude nunca são sobrescritos.
- A reunião atual vale mais que o histórico: escolha manual num cartão substitui "last" nos outros cartões pendentes do mesmo produto.
- Pendente = `discarded=0` e `create_state` em `('none','failed')`.
- Esvaziar à mão marca o campo "manual" (não volta a ser preenchido).
- Preenchimento automático não marca `edited`.
- Marca de origem: `suggested["fill"] = {"assignee_id": "last"|"manual", "team_id": "last"|"manual"}` (chaves opcionais; `suggested` pode ser `None` antes).
- Coerência: pessoa pertence à equipe **ou** ao produto, comparando só com os ids informados; sem listas da pessoa ou sem equipe e produto → coerente.
- Sem opções em cache (`peek()` = `None`), preenche sem validar.
- A coluna `project` fica no banco e em `TaskSuggestion` (Python e TS), sem uso na UI nem no prompt.
- Textos da UI em português, exatamente como nas tarefas 5 e 6.
- Animações respeitam `@media (prefers-reduced-motion: reduce)`.
- Hook `create_guard.py` e argumentos de `criar_issue` não mudam.
- Validação de cada tarefa: `uv run --frozen pytest -q` (backend) e, no app, `pnpm test` e `pnpm build` (este roda `vue-tsc`).

## Review Focus

1. **Corrida entre preenchimento e edição do operador**: o operador edita o cartão B enquanto a propagação de A grava B → a edição do operador vence (CAS por `updated_at` em `Store.fill_task`). Teste em Task 3.
2. **Cartão de outro produto ou já criado/descartado** não recebe propagação. Teste em Task 3.
3. **Produto trocado para `None`**: nada é preenchido e nada quebra. Teste em Task 3.
4. **"Refazer sugestões" depois de preencher**: cartões só preenchidos (não editados) são substituídos e preenchidos de novo; um com escolha manual sobrevive (já `edited`). Teste em Task 3.
5. **MCP sem `equipes`/`produtos` nas pessoas** (outro MCP): parse não quebra, `coherent` devolve `True`. Teste em Task 1.

## Parallel Groups

- **Grupo A (paralelo):** Task 1 (backend opções) ∥ Task 4 (TaskCard/tasks.ts/types) ∥ Task 5 (TasksTab carregamento).
- **Grupo B (paralelo, depois do A):** Task 2 (prompt/scan) ∥ Task 3 (store/autofill/API/jobs) ∥ Task 6 (caixa de contexto).
- Conflitos evitados: Task 4 não mexe em `TasksTab.vue` nem `TasksTab.test.ts`; Task 5 e 6 mexem nos dois e por isso estão em grupos diferentes. Tasks 2 e 3 não compartilham arquivos (`scan.py`/`prompts.py`/`legacy.py` vs `store.py`/`autofill.py`/`api_tasks.py`/`jobs.py`).
- Subagents não tocam neste plano; o controller marca as caixas do grupo num commit ao fim.

---

### Task 1: Equipes e produtos das pessoas em `IssueOptions`

**Files:**
- Modify: `src/whisper_ditado/issues/client.py` (`Option`, `IssueOptions`, `_parse_options`)
- Test: `tests/issues/test_client.py`

**Interfaces:**
- Produces:
  - `Option(id: int, name: str, teams: tuple[int, ...] = (), products: tuple[int, ...] = ())` — dataclass; campos novos com default (construções existentes `Option(9, "Miguel")` continuam valendo).
  - `IssueOptions.person(id: int) -> Option | None`
  - `IssueOptions.coherent(person_id: int, team_id: int | None, product_id: int | None) -> bool`
  - `to_json()` **não muda** (pessoas continuam só `{id, name}`).

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_options_keeps_people_teams_and_products():
    raw = json.dumps({"voce": {"id": 1, "username": "vi"},
        "pessoas": [{"id": 9, "username": "miguel", "equipes": [3, True, "x"], "produtos": [4]},
                    {"id": 10, "username": "ana"}],
        "equipes": [{"id": 3, "name": "Dev"}], "produtos": [{"id": 4, "name": "dash"}], "etapas": []})
    opts = _parse_options(raw)
    assert opts.person(9).teams == (3,) and opts.person(9).products == (4,)
    assert opts.person(10).teams == () and opts.person(10).products == ()
    assert opts.to_json()["people"][0] == {"id": 9, "name": "miguel"}

def test_coherent():
    opts = IssueOptions("vi", people=[Option(9, "M", teams=(3,), products=(4,)), Option(10, "A")],
                        teams=[], products=[], stages=[], fetched_at=datetime.now())
    assert opts.coherent(9, 3, None) and opts.coherent(9, None, 4) and opts.coherent(9, 99, 4)
    assert not opts.coherent(9, 99, 98)
    assert opts.coherent(9, None, None)          # nada para comparar
    assert opts.coherent(10, 99, 98)             # pessoa sem listas (outro MCP)
    assert opts.coherent(77, 3, 4)               # pessoa desconhecida: existência é checada em outro lugar
```

- [ ] **Step 2: Run** `uv run --frozen pytest tests/issues/test_client.py -q -k "people_teams or coherent"` → FAIL.
- [ ] **Step 3: Implement** in `client.py`. Em `_parse_options`, só para `"pessoas"`, ler `equipes`/`produtos` mantendo apenas `int` que não seja `bool`. `coherent`: pessoa desconhecida ou sem listas → `True`; ids informados (`not None`) vazios → `True`; senão `team_id in p.teams or product_id in p.products`.
- [ ] **Step 4: Run** `uv run --frozen pytest -q` → PASS (inclui os testes existentes de opções/`to_json`).
- [ ] **Step 5: Commit** `[Feat] Guardar equipes e produtos das pessoas nas opções de issues`

---

### Task 2: Prompt e validação do levantamento sem "projeto" e com quem cuida de quê

**Files:**
- Modify: `src/whisper_ditado/issues/prompts.py` (`DEFAULT_TASKS_PROMPT`, `SCAN_SYSTEM`)
- Modify: `src/whisper_ditado/issues/scan.py` (`ScanItem`, `as_store_item`, `build_scan_prompt`, `_item`)
- Modify: `src/whisper_ditado/issues/legacy.py` (`LEGACY_PROMPT_HASHES`)
- Test: `tests/issues/test_scan.py`, `tests/issues/test_migration.py` (ou onde `tasks_prompt` legado já é testado — `grep -rln tasks_prompt tests`)

**Interfaces:**
- Consumes: `Option.teams`, `Option.products`, `IssueOptions.coherent` (Task 1).
- Produces: `ScanItem` **sem** o campo `project`; `as_store_item()["fields"]` sem `"project"`.

- [ ] **Step 1: Write the failing tests** (ajuste os testes atuais que usam `project`/`"projeto"`):

```python
def test_prompt_lists_people_with_teams_and_products():
    opts = IssueOptions("vi", people=[Option(9, "Miguel", teams=(3,), products=(4,))],
                        teams=[Option(3, "Dev")], products=[Option(4, "e-commerce")], stages=[], fetched_at=datetime.now())
    prompt = build_scan_prompt("regras", "t", None, opts, [])
    assert '{"id": 9, "name": "Miguel", "equipes": [3], "produtos": [4]}' in prompt

def test_scan_system_has_no_projeto_key():
    assert '"projeto"' not in SCAN_SYSTEM and "## Projeto" not in DEFAULT_TASKS_PROMPT

def test_parse_drops_assignee_outside_team_and_product():
    opts = IssueOptions("vi", people=[Option(9, "M", teams=(3,), products=(4,)), Option(10, "A", teams=(5,), products=(6,))],
                        teams=[Option(3, "Dev"), Option(5, "Ops")], products=[Option(4, "e"), Option(6, "f")],
                        stages=[], fetched_at=datetime.now())
    raw = json.dumps([{"tipo": "tarefa", "titulo": "X", "produto_id": 4, "equipe_id": 3,
                       "responsavel_id": 10, "responsavel_dica": "Ana"}])
    [item] = parse_scan(raw, opts, [], set())
    assert item.assignee_id is None and item.assignee_hint == "Ana" and item.team_id == 3

def test_parse_keeps_assignee_when_no_team_or_product_suggested():
    ...  # mesmo OPTS; produto_id e equipe_id null, responsavel_id 10 → item.assignee_id == 10

def test_previous_default_prompt_is_legacy():
    assert is_legacy_prompt(OLD_DEFAULT)  # OLD_DEFAULT = cópia literal do DEFAULT_TASKS_PROMPT de antes desta task
```

- [ ] **Step 2: Run** `uv run --frozen pytest tests/issues/test_scan.py -q` → FAIL.
- [ ] **Step 3: Implement.**
  - `legacy.py`: antes de mudar o prompt, acrescente a `LEGACY_PROMPT_HASHES` o `sha256(DEFAULT_TASKS_PROMPT.strip())` atual (assim quem estava no padrão migra para o novo).
  - `DEFAULT_TASKS_PROMPT`: tire `## Projeto` da lista de seções; troque "Identifique os projetos ... pasta raiz" por "Use as informações da reunião e os arquivos CLAUDE.md da pasta raiz para entender os projetos e cite na descrição os repositórios envolvidos. Não presuma nomes de repositórios, produtos, pessoas ou equipes."; troque a linha "Use somente os ids ..." por: "Use somente os ids das opções retornadas pelo MCP. Cada pessoa traz as equipes e os produtos de que participa: escolha responsável e equipe entre quem participa do produto da issue (de preferência também da equipe); se alguém foi citado como responsável na conversa, prefira essa pessoa. Deixe o id vazio só quando nenhuma escolha coerente existir e registre a sugestão em texto. Não invente ids."
  - `SCAN_SYSTEM`: remova a chave `"projeto"` da lista de chaves.
  - `build_scan_prompt`: pessoas como `{"id", "name", "equipes": list(p.teams), "produtos": list(p.products)}` (nessa ordem de chaves); equipes e produtos seguem `{id, name}`.
  - `scan.py`: remova `project` de `ScanItem`, `_item` e `as_store_item`; em `_item`, depois de calcular os ids: se `assignee_id` e `options` e `not options.coherent(assignee_id, team_id, product_id)` → `assignee_id = None`.
- [ ] **Step 4: Run** `uv run --frozen pytest -q` → PASS.
- [ ] **Step 5: Commit** `[Feat] Orientar o levantamento por equipes e produtos de cada pessoa`

---

### Task 3: Preenchimento pelo último usado (store, autofill, API, jobs)

**Files:**
- Modify: `src/whisper_ditado/store.py` (`update_task`, novos `tasks_for_product`, `fill_task`)
- Create: `src/whisper_ditado/issues/autofill.py`
- Modify: `src/whisper_ditado/api_tasks.py` (`patch_task`)
- Modify: `src/whisper_ditado/issues/jobs.py` (`_scan`, `_create_one`)
- Test: `tests/test_store_tasks.py`, `tests/issues/test_autofill.py` (novo), `tests/test_api_tasks.py`, `tests/issues/test_jobs.py`

**Interfaces:**
- Consumes: `IssueOptions.has`, `IssueOptions.coherent` (Task 1).
- Produces:
  - `Store.update_task` (mesma assinatura): para cada `assignee_id`/`team_id` presente em `changes`, grava `suggested["fill"][campo] = "manual"` (cria `suggested={}` se `None`); mudar só a marca não marca `edited`.
  - `Store.tasks_for_product(product_id: int, limit: int = 500) -> list[TaskSuggestion]` — `ORDER BY updated_at DESC, rowid DESC`.
  - `Store.fill_task(task_id: str, values: dict[str, int], expected_updated_at: str) -> bool` — grava `values` (só `assignee_id`/`team_id`), marca cada um `"last"` em `suggested["fill"]`, atualiza `updated_at`, **não** toca `edited`; `WHERE id=? AND updated_at=? AND discarded=0 AND create_state IN ('none','failed')`; devolve se gravou.
  - `autofill.FILL_FIELDS = ("assignee_id", "team_id")`
  - `autofill.replaceable(task: TaskSuggestion, field: str) -> bool`
  - `autofill.fill_from_sources(store, options: IssueOptions | None, meeting_id: str, task_ids: Collection[str] | None = None) -> bool`
  - `autofill.propagate(store, options: IssueOptions | None, source: TaskSuggestion, fields: Iterable[str] = FILL_FIELDS) -> bool`
  - `autofill.after_patch(store, options: IssueOptions | None, task: TaskSuggestion, changed: Collection[str]) -> bool`

Regras do `autofill` (o que os testes fixam):
- `replaceable`: pendente **e** (`mark == "last"` ou (`valor is None` e `mark != "manual"`)).
- **Fonte de um campo** para o produto P na reunião M (`fill_from_sources`): percorre `tasks_for_product(P)`; primeiro candidato com valor não nulo entre (a) tarefas de M com marca `"manual"` no campo ou `create_state == "created"`; senão (b) a mais recente com `create_state == "created"` em qualquer reunião. Na regra (b) os dois campos saem da mesma tarefa.
- `fill_from_sources` atua nos cartões pendentes de M (ou só `task_ids`) com `product_id` não nulo e algum campo `replaceable`; não sobrescreve com valor igual.
- `propagate`: valor não nulo de `source` para cada cartão pendente **de M, mesmo produto, id diferente**, com o campo `replaceable` e valor diferente.
- Validação de candidato (com `options` não nulo): o id existe (`has("people"|"teams", id)`); depois de montar os valores finais do cartão, se o responsável final não for coerente (`coherent(resp, equipe, produto)`), o responsável candidato é descartado (a equipe fica).
- `after_patch`: `assignee_id`/`team_id` em `changed` → `propagate` desses campos (com o valor atual de `task`); `product_id` em `changed` → `fill_from_sources(..., [task.id])` e depois `propagate` dos campos manuais de `task` para os outros cartões do novo produto.
- Gravação sempre via `fill_task` com o `updated_at` lido; `False` → ignora o cartão (o operador mexeu nele).

- [ ] **Step 1: Write the failing tests.** Store:

```python
def test_update_task_marks_assignee_and_team_manual(store, mid):
    t = store.add_task(mid, {**BASE}, origin="manual")
    t = store.update_task(t.id, {"assignee_id": 9})
    assert t.suggested["fill"] == {"assignee_id": "manual"} and t.edited
    t = store.update_task(t.id, {"team_id": None})
    assert t.suggested["fill"] == {"assignee_id": "manual", "team_id": "manual"}

def test_fill_task_is_compare_and_set_and_keeps_edited(store, mid):
    t = store.add_task(mid, {**BASE, "product_id": 4}, origin="claude")
    assert store.fill_task(t.id, {"assignee_id": 9}, t.updated_at)
    got = store.get_task(t.id)
    assert got.assignee_id == 9 and got.suggested["fill"] == {"assignee_id": "last"} and not got.edited
    assert not store.fill_task(t.id, {"team_id": 3}, t.updated_at)  # updated_at antigo

def test_tasks_for_product_most_recent_first(store, mid): ...
```

Autofill (`tests/issues/test_autofill.py`, com `Store` real em `tmp_path` e `OPTS` com `Option(9, "Miguel", teams=(3,), products=(4,))`, `Option(10, "Ana", teams=(3,), products=(4,))`, `Option(11, "Zé", teams=(5,), products=(6,))`, equipes 3 e 5, produtos 4 e 6):

```python
def test_scan_fill_uses_history_created_task(...):        # outra reunião, criada com 9/3, produto 4 → novo cartão vazio recebe 9/3, marcas "last"
def test_scan_fill_prefers_current_meeting_manual(...):   # histórico 9/3, cartão desta reunião com 10 "manual" → recebe 10
def test_fill_never_overwrites_claude_or_manual(...):     # cartão com assignee 10 sem marca (Claude) e team None "manual" → nada muda
def test_propagate_replaces_last_but_not_manual(...):     # B "last"=9, C "manual"=10; A muda para 10... e para 9: B troca, C não
def test_propagate_skips_other_product_discarded_created(...)
def test_product_change_fills_from_sources(...)           # trocar produto de None para 4 preenche; para None não faz nada
def test_candidate_validated_against_options(...)         # histórico com pessoa 99 inexistente → não preenche; pessoa 11 com equipe 3/produto 4 → descartada
def test_without_options_fills_unvalidated(...)           # options=None → preenche 99
def test_rescan_replaces_filled_unedited_cards(...)       # Review Focus 4
def test_fill_loses_race_to_operator_edit(...)            # Review Focus 1: fill_task com updated_at velho não grava
```

API (`tests/test_api_tasks.py`): `test_patch_assignee_propagates_and_notifies` — dois cartões do produto 4 na reunião; PATCH `assignee_id=9` no primeiro → o segundo fica com 9 e `suggested.fill.assignee_id == "last"`; um evento `tasks_changed` publicado; resposta do PATCH reflete o próprio cartão relido.
Jobs (`tests/issues/test_jobs.py`): `test_scan_fills_from_history` (resultado do levantamento sem responsável recebe o da tarefa criada) e `test_created_issue_propagates_to_siblings`.

- [ ] **Step 2: Run** os arquivos acima → FAIL.
- [ ] **Step 3: Implement** store (`update_task`, `tasks_for_product`, `fill_task`), depois `autofill.py`, depois os ganchos:
  - `api_tasks.patch_task`: depois de `update_task`, `autofill.after_patch(store, ctx.issue_options.peek(), task, set(body.changes()))`; relê com `store.get_task` antes de responder.
  - `jobs._scan`: dentro do `with self._claim_lock`, logo depois de `replace_scan_tasks`, `autofill.fill_from_sources(self._store, options, meeting_id)`.
  - `jobs._create_one`: depois de `finish_create` com `outcome.state == "created"`, `autofill.propagate(self._store, self._options.peek(), self._store.get_task(task_id))` em `try/except Exception: log.exception(...)` (nunca pode afetar o estado da criação). `_create_all` já publica `tasks_changed`.
- [ ] **Step 4: Run** `uv run --frozen pytest -q` → PASS.
- [ ] **Step 5: Commit** `[Feat] Preencher responsável e equipe pelo último usado do produto`

---

### Task 4: Cartão sem "Projeto" e com "(último usado)"

**Files:**
- Modify: `app/src/api/types.ts` (`TaskFields`, `TaskSuggestion`)
- Modify: `app/src/lib/tasks.ts` (`taskFields`, `tasksMarkdown`)
- Modify: `app/src/components/TaskCard.vue`
- Test: `app/src/components/TaskCard.test.ts`, `app/src/lib/tasks.test.ts`
- **Não** editar `TasksTab.vue` nem `TasksTab.test.ts` (Task 5 está em paralelo).

**Interfaces:**
- Produces (TS):
  - `TaskFields` **sem** `project`; `TaskSuggestion` mantém `project: string | null` (legado, não exibido) — assim fixtures existentes continuam válidas.
  - `TaskSuggestion.suggested`: `{ product_id?: number | null; assignee_id?: number | null; team_id?: number | null; assignee_hint?: string | null; team_hint?: string | null; fill?: Partial<Record<"assignee_id" | "team_id", "last" | "manual">> } | null`.

- [ ] **Step 1: Write the failing tests**
  - TaskCard: não existe `[aria-label="Projeto"]`.
  - TaskCard: com `assignee_id: 2` e `suggested: { fill: { assignee_id: "last" } }`, a opção selecionada do seletor Responsável tem texto `Miguel (último usado)`; com `fill.assignee_id: "manual"` o texto é só `Miguel`; a sugestão do Claude continua `"(sugestão)"`.
  - Mesmo para Equipe (`team_id`).
  - tasks.ts: `taskFields(t)` não tem a chave `project`; `tasksMarkdown` não contém `**Projeto:**`.
- [ ] **Step 2: Run** `cd app && pnpm test -- TaskCard tasks` → FAIL.
- [ ] **Step 3: Implement.** Em `TaskCard.vue`, remover o `<label>Projeto`, `draft.project`, o `watch` e `saveProject`; em `choices()`, para o item selecionado cujo `task.suggested?.fill?.[key] === "last"` (só `assignee_id`/`team_id`), rótulo `${name} (último usado)`; o "(sugestão)" do Claude continua como hoje e tem precedência se coincidir. Ajustar o comentário do topo (tirar "projeto"). Os `assigneeHint`/`teamHint` devem continuar funcionando com `suggested` sem as chaves de dica (use `?? null`).
- [ ] **Step 4: Run** `cd app && pnpm test && pnpm build` → PASS.
- [ ] **Step 5: Commit** `[UI] Remover o campo Projeto e indicar o último usado no cartão`

---

### Task 5: Carregamento no levantamento e na criação

**Files:**
- Create: `app/src/components/ui/AppSpinner.vue`
- Modify: `app/src/components/TasksTab.vue`
- Test: `app/src/components/TasksTab.test.ts`, `app/src/components/ui/AppSpinner.test.ts` (novo, opcional se trivial)

**Interfaces:**
- Produces: `<AppSpinner :size="16" label="..." />` — `size` em px (default 16), `label` opcional vira `aria-label` com `role="status"`; sem label é `aria-hidden="true"`. Cor `currentColor`. Animação desligada em `prefers-reduced-motion`.

Comportamento (mockup aprovado, seções 2 e 3 de `.superpowers/brainstorm/424880-1793290669/content/contexto-e-load.html`):
- **Levantando** (`running`): a `.scan-status` vira caixa (`.scan-box`) com spinner, texto `Levantando tarefas…` (ou `Cancelando levantamento…`), subtítulo `O Claude está lendo a transcrição e os prints. Isso pode levar alguns minutos.`, barra indeterminada (`.scan-bar`) e o botão `Cancelar levantamento` atual.
- Se `running` e `items.length === 0`: mostrar `.skeleton` (3 linhas de lista + 1 cartão) no lugar das `panes`, com `aria-hidden="true"`.
- **Criando:** botão `Criar issue` mostra `<AppSpinner>` + `Criando…` quando ocupado; tag `criando…` da lista ganha spinner; o `TaskCard` aberto fica dentro de `.card-wrap` que, quando `open` está em criação (`create_state === "creating"` ou `isBusy("create:<id>")`), recebe classe `busy` (esmaecido) e um overlay `.card-overlay` com spinner e `Criando a issue no MCP de issues…`.
- Cores só com tokens existentes (`--accent`, `--selected`, `--surface`, `--surface-raised`, `--border`, `--muted`).

- [ ] **Step 1: Write the failing tests**
  - Rodando: `.scan-box` contém `Levantando tarefas…` e `O Claude está lendo a transcrição e os prints. Isso pode levar alguns minutos.` e um `[role="status"]`; sem itens existe `.skeleton`; com itens não existe.
  - Criando: com `create_state: "creating"` no item aberto, existe `.card-wrap.busy` e `.card-overlay` com `Criando a issue no MCP de issues…`; sem criação, não existe.
  - O teste atual "mostra 'Levantando tarefas…' com o levantamento rodando" continua passando.
- [ ] **Step 2: Run** `cd app && pnpm test -- TasksTab` → FAIL.
- [ ] **Step 3: Implement** `AppSpinner.vue` e as mudanças em `TasksTab.vue`.
- [ ] **Step 4: Run** `cd app && pnpm test && pnpm build` → PASS.
- [ ] **Step 5: Commit** `[UI] Mostrar carregamento no levantamento e na criação de issues`

---

### Task 6: Caixa de contexto em destaque antes do primeiro levantamento

**Files:**
- Create: `app/src/components/ScanContextPrompt.vue`
- Modify: `app/src/components/TasksTab.vue`, `app/src/components/ui/AppIcon.vue` (ícone `sparkle`)
- Test: `app/src/components/ScanContextPrompt.test.ts` (novo), `app/src/components/TasksTab.test.ts`

**Interfaces:**
- Consumes: `AppSpinner` não é necessário aqui; `rescan()` e `scanContext` já existentes em `TasksTab.vue`.
- Produces: `ScanContextPrompt` — props `modelValue: string`, `disabled: boolean`; emits `update:modelValue(value: string)`, `generate()`, `dismiss()`.

Conteúdo (opção A do mockup), textos exatos:
- Ícone `sparkle`; título `Quer orientar as sugestões de tarefas?`
- Texto: `Diga ao Claude no que focar, o que agrupar ou ignorar. O texto vai junto com a transcrição e os prints. É opcional: dá para gerar sem escrever nada.`
- Chips (botões): `Foque só nas pendências de um produto` → acrescenta `Foque só nas pendências do produto: `; `Ignore assuntos de infraestrutura` → `Ignore assuntos de infraestrutura.`; `Junte os ajustes relacionados numa tarefa` → `Junte os ajustes relacionados numa tarefa só.` Acrescentar = se o campo não está vazio, `\n` + texto; nunca substitui.
- Textarea com o placeholder atual, `maxlength="10000"`, `rows="3"`.
- Nota `Tarefas editadas, descartadas ou já enviadas são preservadas.`; botões `Agora não` (discreto) e `Gerar tarefas` (primário, com ícone `sparkle`).

Regras em `TasksTab.vue`:
- `contextDismissed = ref(false)` (não persistido).
- Mostrar `ScanContextPrompt` quando `enabled && loaded && scan === null && !running && !contextDismissed`; nesse caso **não** mostrar o `<details class="scan-context">` nem o texto `Revise os prints e clique em Levantar tarefas.`.
- `generate` → `rescan()`; `dismiss` → `contextDismissed = true`.
- Fora disso, o `<details>` atual continua, com summary `Orientar as sugestões (opcional)` / `Orientar as sugestões (preenchido)`. O mesmo `scanContext` alimenta os dois.
- `disabled` = `!connected || !enabled || running || isBusy('scan')`.

- [ ] **Step 1: Write the failing tests**
  - ScanContextPrompt: clicar `Ignore assuntos de infraestrutura` com campo `"Foque no checkout."` emite `update:modelValue` com `"Foque no checkout.\nIgnore assuntos de infraestrutura."`; com campo vazio, só o texto; `Gerar tarefas` emite `generate`; `Agora não` emite `dismiss`; `disabled` desabilita textarea, chips e botões.
  - TasksTab: substituir o teste "antes do primeiro levantamento mostra o botão Levantar tarefas e o campo de contexto" por: com `scan: null` aparece `Quer orientar as sugestões de tarefas?`, não aparece `.scan-context`; preencher `"  Foque no checkout.  "` e clicar `Gerar tarefas` chama `scanTasks("m1", "Foque no checkout.")`.
  - TasksTab: `Agora não` esconde a caixa e mostra `.scan-context` com summary `Orientar as sugestões (preenchido)` quando havia texto, e `Revise os prints e clique em Levantar tarefas.`.
  - TasksTab: com `scan.state === "done"` a caixa não aparece; summary `Orientar as sugestões (opcional)`.
  - TasksTab: com o fluxo desligado a caixa não aparece.
- [ ] **Step 2: Run** `cd app && pnpm test -- ScanContextPrompt TasksTab` → FAIL.
- [ ] **Step 3: Implement** o componente (visual: borda esquerda `--accent`, fundo `--surface-raised`, ícone em quadrado `--selected`/`--selected-text`, chips com borda tracejada `--border-control`), o ícone `sparkle` em `AppIcon.vue` e a integração.
- [ ] **Step 4: Run** `cd app && pnpm test && pnpm build` → PASS.
- [ ] **Step 5: Commit** `[UI] Destacar o contexto do levantamento antes da primeira análise`
