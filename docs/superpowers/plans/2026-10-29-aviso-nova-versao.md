# Aviso de nova versão e versão no rodapé — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O app mostra a versão em execução no rodapé, avisa quando há release mais nova e aponta para um comando único (`scripts/update.py`) que leva o clone à última release.

**Architecture:** Um módulo novo do backend (`updates.py`) consulta a última release no GitHub uma vez por dia numa thread daemon, guarda o resultado na tabela `settings` (mesmo padrão do `ClaudeModels`) e o expõe em `GET /api/status` → `version`. O app lê esse campo pelo `useStatus`, compara com a própria versão embutida no build e mostra uma linha no rodapé com diálogo. O `scripts/update.py` (só biblioteca padrão) faz fetch das tags, para o serviço, faz checkout da tag, sincroniza dependências, recompila e sobe o serviço, com marca de retomada em `.git/`.

**Tech Stack:** Python 3.12 (stdlib `urllib`, `threading`), FastAPI, Vue 3 + Vite + Vitest, Tauri 2 (`plugin-opener`, `plugin-clipboard-manager`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-29-aviso-nova-versao-design.md`

## Global Constraints

- Repositório: `vateiixeira/tars`; endpoint `https://api.github.com/repos/vateiixeira/tars/releases/latest`, sem token.
- Versões `MAJOR.MINOR.PATCH`, tags `vMAJOR.MINOR.PATCH`; comparação numérica, nunca de string.
- Há atualização só se a última release for **estritamente maior** que a instalada.
- Verificação no máximo a cada 24 h, em segundo plano, nunca bloqueia a inicialização; falhas só vão para o log e mantêm o cache.
- Versão instalada do backend = `version` do `pyproject.toml` da raiz do projeto em execução.
- `install` = `"git"` se existe `.git` na raiz do projeto, senão `"package"`.
- Texto do rodapé: `TARS 0.2.0`; com atualização: `TARS 0.2.0 · atualizar para 0.3.0`; versões divergentes: `app 0.2.0 · serviço 0.3.0`.
- Comando mostrado ao usuário: `python3 scripts/update.py`.
- `scripts/update.py` usa só a biblioteca padrão; testes sem rede e sem rodar `uv`/`pnpm`/`systemctl`/`git fetch` reais.
- Unidade systemd: `whisper-ditado-backend.service` (`systemctl --user`).
- Textos da interface e mensagens em pt-BR.

## Review Focus

1. Tag com formato inesperado no GitHub ou no `git tag` (`v1.0.0-rc1`, `latest`, `v01.2.0`) → ignorada, nunca vira "versão nova" nem quebra a comparação.
2. GitHub responde 403 (rate limit) ou JSON sem `tag_name` → sem aviso, cache anterior mantido, nenhuma exceção sobe para a thread/status.
3. Clone com commits à frente da última tag (o dono na `main`, versão igual) → nenhum aviso no app e `update.py` diz "já está atualizado" sem mexer em nada.
4. `update.py` rodado de novo depois de falhar no build → retoma a mesma tag (marca em `.git/`), não diz "atualizado".
5. Backend rodando via `./run-backend.sh` (responde na API mas a unidade systemd não está ativa) → `update.py` recusa antes de mexer em qualquer coisa.

## Dependências e paralelismo

- **Grupo A, em paralelo:** Tasks 1, 2 e 3. Arquivos distintos (`src/` + `tests/test_updates.py`/`tests/test_api.py`; `app/`; `scripts/update.py` + `tests/test_update_script.py` + `README.md`). Nenhuma mexe em `pyproject.toml`, `uv.lock`, `package.json` ou `pnpm-lock.yaml`. A Task 2 depende só do formato JSON definido no Interfaces da Task 1, não do código.
- Em grupo paralelo, os subagents não tocam no plano; o controller marca as caixas do grupo num commit único ao final.

---

### Task 1: Backend — verificação de versão e `version` no status

**Files:**
- Create: `src/whisper_ditado/updates.py`
- Modify: `src/whisper_ditado/api.py` (`AppContext` ganha `updates: Any = None`; `build_status` ganha a chave `version`)
- Modify: `src/whisper_ditado/main.py` (cria `UpdateChecker`, passa no `AppContext`, chama `start_auto_check(stopping)` junto do `claude_models.start_auto_refresh`)
- Test: `tests/test_updates.py`, `tests/test_api.py`

**Interfaces:**
- Produces (Python, `whisper_ditado.updates`):
  - `REPO = "vateiixeira/tars"`, `LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"`, `CACHE_KEY = "_update_check_cache"`, `CHECK_EVERY = timedelta(hours=24)`, `AUTO_CHECK_DELAY_S = 30.0`
  - `parse_version(text: str) -> tuple[int, int, int] | None` — aceita `"0.2.0"` e `"v0.2.0"`; `None` para qualquer outra coisa (pré-release, zeros à esquerda, lixo).
  - `installed_version(root: Path = PROJECT_ROOT) -> str` — lê `[project].version` do `pyproject.toml` com `tomllib`.
  - `install_kind(root: Path = PROJECT_ROOT) -> Literal["git", "package"]`
  - `fetch_latest(timeout: float = 10.0) -> dict` — devolve `{"version": "0.3.0", "url": "<html_url>"}`; levanta `UpdateCheckError` em erro de rede/HTTP/JSON/tag inválida.
  - `class UpdateChecker(store, root: Path = PROJECT_ROOT, fetch=fetch_latest, clock=..., stopping: threading.Event | None = None)` com `public() -> dict`, `needs_check() -> bool`, `check() -> dict`, `start_auto_check(stopping=None, delay_s=AUTO_CHECK_DELAY_S) -> threading.Thread | None`.
- Produces (JSON, `GET /api/status` → `version`; a Task 2 consome):
  ```json
  {"current": "0.2.0", "latest": "0.3.0", "url": "https://github.com/vateiixeira/tars/releases/tag/v0.3.0",
   "update_available": true, "install": "git"}
  ```
  `latest` e `url` são `null` antes da primeira verificação bem-sucedida; `update_available` é `false` nesse caso.

- [ ] **Step 1: Escrever os testes que falham em `tests/test_updates.py`**

Use `Store(tmp_path / "t.db")` e um `pyproject.toml` mínimo em `tmp_path` (`[project]\nversion = "0.2.0"`). `fetch` e `clock` falsos, nunca rede.

```python
@pytest.mark.parametrize("text,expected", [
    ("0.2.0", (0, 2, 0)), ("v1.10.3", (1, 10, 3)),
    ("v1.0.0-rc1", None), ("latest", None), ("v01.2.0", None), ("", None),
])
def test_parse_version(text, expected): ...

def test_update_available_only_when_latest_is_strictly_greater(store, root):
    # fetch -> 0.3.0: update_available True; fetch -> 0.2.0 e 0.1.9: False
    # comparação numérica: instalada 0.10.0, latest 0.9.0 -> False

def test_public_before_any_check(store, root):
    assert UpdateChecker(store, root, fetch=boom).public() == {
        "current": "0.2.0", "latest": None, "url": None,
        "update_available": False, "install": "package"}

def test_install_kind_git_when_dot_git_exists(store, root):
    (root / ".git").mkdir(); assert install_kind(root) == "git"

def test_failed_check_keeps_previous_cache(store, root):
    # 1º check ok (0.3.0); 2º fetch levanta UpdateCheckError -> check() não propaga
    # para a thread (start_auto_check) e public()["latest"] continua "0.3.0"

def test_needs_check_after_24h(store, root):
    # clock avança 23h -> False; 25h -> True

def test_auto_check_does_not_run_when_stopping_is_set(store, root):
    # stopping.set() antes; thread termina sem chamar fetch

def test_fetch_latest_rejects_bad_payloads(monkeypatch):
    # urlopen falso devolvendo {"tag_name": "v1.0.0-rc1"}, {} e HTTPError 403 -> UpdateCheckError
    # payload {"tag_name": "v0.3.0", "html_url": "https://x"} -> {"version": "0.3.0", "url": "https://x"}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run --frozen pytest tests/test_updates.py -q`
Expected: FAIL (`ModuleNotFoundError: whisper_ditado.updates`)

- [ ] **Step 3: Implementar `src/whisper_ditado/updates.py`**

`PROJECT_ROOT = Path(__file__).resolve().parents[2]`. `fetch_latest` usa `urllib.request.Request` com headers `Accept: application/vnd.github+json` e `User-Agent: tars-update-check`. O cache guarda `{"checked_at", "latest", "url"}`; `public()` sempre lê `installed_version`/`install_kind` na hora (barato) e compara com o cache. `start_auto_check` segue `ClaudeModels.start_auto_refresh`/`_auto_refresh` (thread daemon `update-check`, `stopping.wait(delay_s)`, revalida `needs_check()`, captura `UpdateCheckError` com `log.info` e qualquer outra exceção com `log.exception`).

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run --frozen pytest tests/test_updates.py -q`
Expected: PASS

- [ ] **Step 5: Teste do status em `tests/test_api.py`**

```python
def test_status_has_version(client, ctx):
    assert client.get("/api/status").json()["version"] is None  # sem checker no contexto

def test_status_version_from_checker(client, ctx):
    ctx.updates = FakeUpdates({"current": "0.2.0", "latest": "0.3.0", "url": "https://x",
                               "update_available": True, "install": "git"})
    assert client.get("/api/status").json()["version"]["update_available"] is True
```

`test_status_shape` continua passando (só verifica chaves específicas).

- [ ] **Step 6: Implementar a ligação**

`build_status`: `"version": ctx.updates.public() if ctx.updates is not None else None`. Em `main.py`: `updates = UpdateChecker(store, stopping=stopping)` ao lado do `ClaudeModels`, `updates=updates` no `AppContext`, e `updates.start_auto_check(stopping)` logo após `claude_models.start_auto_refresh(stopping)`.

- [ ] **Step 7: Rodar a suíte Python**

Run: `uv run --frozen pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/whisper_ditado/updates.py src/whisper_ditado/api.py src/whisper_ditado/main.py tests/test_updates.py tests/test_api.py
git commit -m "[Feat] Verificar nova versão no GitHub e expor no status"
```

---

### Task 2: App — versão no rodapé e diálogo de atualização

**Files:**
- Modify: `app/vite.config.ts` (`define: { __APP_VERSION__: JSON.stringify(<version de package.json>) }`)
- Modify: `app/src/vite-env.d.ts` (`declare const __APP_VERSION__: string;`)
- Modify: `app/src/api/types.ts` (`VersionInfo` e `Status.version?: VersionInfo | null`)
- Create: `app/src/components/VersionLine.vue`
- Modify: `app/src/App.vue` (`<VersionLine />` logo abaixo do `.conn` no `.sidebar-footer`)
- Test: `app/src/components/VersionLine.test.ts`

**Interfaces:**
- Consumes: o JSON `version` do `GET /api/status` definido na Task 1 (via `useStatus().status`).
- Produces (TS):
  ```ts
  export interface VersionInfo {
    current: string; latest: string | null; url: string | null;
    update_available: boolean; install: "git" | "package";
  }
  ```
  `VersionLine.vue` sem props; lê `useStatus()` e `__APP_VERSION__`.

- [ ] **Step 1: Escrever os testes que falham em `VersionLine.test.ts`**

Montar com o status falso no padrão de `App.test.ts`/`useStatus.test.ts` (`setStatusStore`). Mockar `@tauri-apps/plugin-opener` (`openUrl`) e `@tauri-apps/plugin-clipboard-manager` (`writeText`). `__APP_VERSION__` = `"0.2.0"` via `vi.stubGlobal`.

```ts
it("mostra só a versão quando não há atualização", ...)
  // version = {current:"0.2.0", latest:"0.2.0", update_available:false, install:"git"}
  // texto === "TARS 0.2.0"; nenhum link
it("mostra o link e abre o diálogo com o comando numa instalação git", ...)
  // update_available:true, latest:"0.3.0" -> texto "TARS 0.2.0 · atualizar para 0.3.0"
  // clicar no link abre AppDialog com "python3 scripts/update.py"; "Copiar" chama writeText com esse texto
  // "Ver novidades" chama openUrl(url)
it("instalação por pacote mostra download em vez do comando", ...)
  // install:"package" -> diálogo sem "scripts/update.py"; botão "Baixar a versão 0.3.0" chama openUrl(url)
it("versões divergentes mostram app e serviço", ...)
  // current:"0.3.0" com __APP_VERSION__ "0.2.0" -> texto "app 0.2.0 · serviço 0.3.0"
  // e um link que abre o diálogo explicando para recompilar (rodar python3 scripts/update.py) e reabrir o app
it("sem backend mostra só a versão do app", ...)
  // status null -> "TARS 0.2.0"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd app && pnpm exec vitest run VersionLine`
Expected: FAIL (componente não existe)

- [ ] **Step 3: Implementar `VersionLine.vue`, tipos e `define`**

Linha com o estilo de `.conn` (`font-size: 0.6875rem; color: var(--muted)`), link como `<button type="button">` com `color: var(--accent)`. Diálogo com `AppDialog` (`components/ui/AppDialog.vue`, título `TARS 0.3.0 disponível`), comando em `<code>` com `AppButton` "Copiar". Erro de `openUrl`/`writeText` vai para `console.error`, como em `MeetingsSection.vue`. Versões divergentes têm precedência sobre o aviso de atualização na linha (o diálogo cobre os dois casos).

- [ ] **Step 4: Rodar e ver passar**

Run: `cd app && pnpm exec vitest run VersionLine`
Expected: PASS

- [ ] **Step 5: Inserir no `App.vue` e rodar tudo**

Run: `cd app && pnpm test && pnpm build`
Expected: PASS e build sem erro de tipo.

- [ ] **Step 6: Commit**

```bash
git add app/vite.config.ts app/src/vite-env.d.ts app/src/api/types.ts app/src/components/VersionLine.vue app/src/components/VersionLine.test.ts app/src/App.vue
git commit -m "[UI] Mostrar versão no rodapé e avisar sobre nova versão"
```

---

### Task 3: `scripts/update.py` e seção "Atualizando" no README

**Files:**
- Create: `scripts/update.py`
- Test: `tests/test_update_script.py` (carrega o script com `importlib.util.spec_from_file_location`, como `tests/test_release.py`)
- Modify: `README.md` (seção `## Atualizando` logo após "Instalação a partir do código")

**Interfaces:**
- Consumes: nada das outras tasks. Lê `$XDG_DATA_HOME/whisper-ditado/api.json` (ou `~/.local/share/whisper-ditado/api.json`), formato `{"port", "token", "pid"}`, e `GET http://127.0.0.1:<port>/api/status` com `Authorization: Bearer <token>`; `meeting.state` ∈ `"idle" | "recording" | "processing"`.
- Produces: `python3 scripts/update.py` (sem argumentos). Funções testáveis:
  - `latest_tag(tags: list[str]) -> str | None` — maior `vX.Y.Z` válida.
  - `update(root: Path, run=<executor>, api=<consulta status>, out=print) -> str` — devolve a tag aplicada ou a versão atual; levanta `UpdateError(mensagem)` nas recusas.
  - `PENDING = ".git/tars-update-pending"` (conteúdo: a tag alvo).

Fluxo de `update()` (spec, seção `scripts/update.py`):
1. Sem `.git` → `UpdateError` com `https://github.com/vateiixeira/tars/releases`.
2. `git status --porcelain --untracked-files=no` não vazio → `UpdateError` listando os arquivos.
3. `api()` devolve status com `meeting.state != "idle"` → `UpdateError` ("Há uma reunião gravando ou processando"). API responde mas `systemctl --user is-active whisper-ditado-backend.service` não é `active` → `UpdateError` pedindo para encerrar o `./run-backend.sh`. API não responde → segue.
4. `git fetch --tags origin`; alvo = marca `PENDING` se existir, senão `latest_tag(git tag --list "v*")`. Sem marca e versão do `pyproject.toml` ≥ alvo → diz "já está na versão X" e devolve sem mais comandos.
5. Grava `PENDING`; avisa que ditado e reuniões ficam fora do ar; `systemctl --user stop whisper-ditado-backend.service` (só se a unidade existir: `systemctl --user cat` com sucesso).
6. `git checkout <tag>` (avisa se HEAD estava numa branch: "saiu da branch X").
7. `uv sync --frozen` + `--extra pyannote` se `.venv` tiver `pyannote.audio` instalado (procure `.venv/lib/python*/site-packages/pyannote` ou `pyannote.audio-*.dist-info`) + `--group dev` se `pytest` estiver instalado no `.venv`.
8. Em `app/`: `pnpm install --frozen-lockfile` e `pnpm tauri build --no-bundle`.
9. `systemctl --user start whisper-ditado-backend.service` se a unidade existir, senão orienta a iniciar `./run-backend.sh`; apaga `PENDING`; diz a versão nova e pede para fechar e reabrir o app.
10. Falha em 6–8: tenta o passo 9 sem apagar `PENDING`, informa se o serviço subiu, qual passo falhou e que basta rodar `python3 scripts/update.py` de novo.

- [ ] **Step 1: Escrever os testes que falham**

Repositório git real em `tmp_path` (`git init`, `pyproject.toml` com `version = "0.2.0"`, commit, tags `v0.2.0` e, num segundo commit com `0.3.0`, `v0.3.0`), `origin` apontando para um bare repo local (como `tests/test_release.py` faz). `run` falso registra os comandos e só executa de verdade os `git`; `uv`, `pnpm` e `systemctl` são simulados.

```python
@pytest.mark.parametrize("tags,expected", [
    (["v0.2.0", "v0.10.0", "v0.9.1"], "v0.10.0"),
    (["v1.0.0-rc1", "latest", "v0.2.0"], "v0.2.0"),
    ([], None),
])
def test_latest_tag(tags, expected): ...

def test_refuses_without_git(tmp_path):            # UpdateError contém ".../releases"
def test_refuses_with_local_changes(repo):          # mensagem lista "pyproject.toml"; nenhum systemctl/checkout
def test_refuses_while_meeting_records(repo):       # api -> {"meeting": {"state": "recording"}}
def test_refuses_when_backend_runs_outside_systemd(repo)  # api responde, is-active -> "inactive"
def test_already_up_to_date_runs_nothing(repo_at_latest): # sem stop/checkout/uv/pnpm
def test_ahead_of_latest_tag_is_up_to_date(repo):   # commit extra na main após v0.3.0 com versão 0.3.0
def test_full_update_order(repo):
    # ordem exata: stop -> checkout v0.3.0 -> uv sync --frozen -> pnpm install --frozen-lockfile
    #              -> pnpm tauri build --no-bundle -> start ; PENDING apagado; devolve "v0.3.0"
def test_keeps_pyannote_and_dev_extras(repo):       # .venv falso com pyannote e pytest -> args incluem os dois
def test_build_failure_restarts_service_and_resumes(repo):
    # pnpm tauri build falha -> UpdateError cita o passo; start foi chamado; PENDING == "v0.3.0"
    # 2ª execução (build ok) refaz stop..start para v0.3.0 apesar de pyproject já em 0.3.0; PENDING apagado
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run --frozen pytest tests/test_update_script.py -q`
Expected: FAIL (arquivo `scripts/update.py` não existe)

- [ ] **Step 3: Implementar `scripts/update.py`**

Só biblioteca padrão (`subprocess`, `tomllib`, `urllib.request`, `json`, `pathlib`). `main()` captura `UpdateError`, imprime em stderr e sai com código 1. Cada passo imprime `==> <nome>` como `scripts/release.py`. O `api()` padrão usa timeout de 3 s e trata `URLError`/`ConnectionRefusedError`/arquivo ausente como "backend parado".

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run --frozen pytest tests/test_update_script.py -q`
Expected: PASS

- [ ] **Step 5: README**

Seção `## Atualizando`: o app avisa no rodapé; rodar `python3 scripts/update.py` na pasta do clone; o que ele faz (vai para a última release, para o serviço por alguns minutos, sincroniza dependências, recompila, sobe o serviço); que recusa com arquivos alterados ou reunião em andamento; que pode ser rodado de novo se falhar; quem instalou pelo pacote segue `docs/INSTALL_RELEASE.md`.

- [ ] **Step 6: Rodar a suíte Python**

Run: `uv run --frozen pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/update.py tests/test_update_script.py README.md
git commit -m "[Feat] Adicionar comando único para atualizar o clone para a última release"
```
