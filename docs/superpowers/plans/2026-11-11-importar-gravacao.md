# Importar gravação ou transcrição — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Importar um `.txt` de transcrição do Read AI, um vídeo ou um áudio e transformá-lo numa reunião comum do TARS, com transcrição por pessoa, resumo e tarefas.

**Architecture:** O app manda ao backend o **caminho** do arquivo (mesma máquina, mesmo usuário). A transcrição `.txt` é lida por um parser isolado e gravada direto como reunião `ready`. Vídeo e áudio são convertidos para `meetings/<id>/pc.opus` e passam pelo pipeline existente de reprocessamento (`fin.reprocess`) na fila da GPU do `MeetingManager`. Uma coluna `meetings.origin` diz às telas se a reunião tem áudio. O novo "Sou eu"/"Não sou eu" marca a pessoa que é o usuário em importações de mídia.

**Tech Stack:** Python 3.12+, FastAPI, SQLite (`store.py`), `ffmpeg`/`ffprobe`; Vue 3 + TypeScript + Vitest; Tauri 2 (`@tauri-apps/plugin-dialog`, eventos de drag-drop do webview).

**Spec:** `docs/superpowers/specs/2026-11-11-importar-gravacao-design.md`. Leia a spec antes de cada tarefa: ela explica o porquê e o que fica de fora.

## Global Constraints

- Textos de interface e mensagens de erro em português (pt-BR).
- `origin` ∈ `recorded` | `import_media` | `import_transcript`, padrão `recorded`.
- Configuração nova `my_name`, string, padrão `""`.
- Áudio importado: `pc.opus` mono, 16 kHz, `libopus`, 16 kbit/s (mesmos parâmetros de `compress_track`), gravado em `.part` e renomeado no fim.
- Transcrição importada: a pessoa escolhida como você → `Speaker(is_me=True, label="Você")` com segmentos `source="mic"`; as demais → `label=<nome do arquivo>`, `voice_id=None`, segmentos `source="pc"`, `color_index` 1, 2, 3… em ordem de primeira fala; "Você" com `color_index=0`.
- O vocabulário (`vocab.apply`) **não** é aplicado ao texto de transcrições importadas.
- O título vindo da importação é manual: `title_auto=False`.
- O arquivo original nunca é alterado, movido ou apagado.
- Importação de mídia sem cancelamento e sem prioridade: comporta-se como "Reprocessar" (`processing` não pode ser apagada nem arquivada).
- Mensagem de formato não reconhecido, exata: `Formato de transcrição não reconhecido; hoje só a do Read AI é aceita`.
- Commits no formato `[Tipo] Título` em português, um por tarefa, terminando com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Verificações do projeto: `uv run --frozen pytest`, `cd app && pnpm test`, `cd app && pnpm build`, `cd app/src-tauri && cargo test`.

## Review Focus

1. **Arquivo com mais de uma hora**: tempos `1:02:03` no `.txt` devem virar `3723.0` s, e não quebrar o parser nem a ordenação (teste na Tarefa 2).
2. **Caminho com espaços e acentos** (o arquivo real é `Awake e Angular - mudança de mecanismo _ Transcrição.txt`): `inspect` e a importação devem funcionar (testes nas Tarefas 3 e 5).
3. **Arquivo `.txt` que não é do Read AI** ou vazio: 400 com a mensagem exata, sem criar reunião (testes nas Tarefas 2 e 5).
4. **Vídeo sem trilha de áudio** (ou arquivo que o `ffprobe` não lê): 400 em `inspect`, sem criar reunião (teste na Tarefa 3).
5. **Reprocessar uma importação de mídia com "Sou eu" marcado**: a marca segue para a pessoa equivalente; sem marca, ninguém vira "Você" (teste na Tarefa 4).

---

### Task 1: Banco e configuração — `origin`, criação flexível de reunião, "Sou eu", `my_name`

**Files:**
- Modify: `src/whisper_ditado/store.py` (migração `_SCHEMA_V9`, `Meeting`, `_MEETING_FIELDS`, `_meeting`, `create_meeting`, novo `set_me`)
- Modify: `src/whisper_ditado/settings.py` (`DEFAULTS["my_name"] = ""`)
- Test: `tests/test_store.py`, `tests/test_settings.py`

**Interfaces:**
- Produces:
  - `Meeting.origin: str = "recorded"`
  - `Store.create_meeting(started_at: datetime, *, status: str = "recording", origin: str = "recorded", title: str | None = None) -> Meeting` (com `title` informado, `title_auto=False`)
  - `Store.set_me(speaker_id: str, on: bool) -> Speaker` → devolve o falante atualizado
  - `PERSON_LABEL = "Pessoa"` em `store.py`; rótulo livre = `f"{PERSON_LABEL} {n}"`

- [ ] **Step 1: Testes que falham** (em `tests/test_store.py`)

```python
def test_meeting_origin_defaults_to_recorded(store):
    m = store.create_meeting(datetime(2026, 11, 11, 16, 30))
    assert store.get_meeting(m.id).origin == "recorded"

def test_create_imported_meeting(store):
    m = store.create_meeting(datetime(2026, 11, 11, 16, 30), status="processing",
                             origin="import_media", title="Awake")
    got = store.get_meeting(m.id)
    assert (got.status, got.origin, got.title, got.title_auto) == ("processing", "import_media", "Awake", False)

def test_set_me_marks_one_speaker_and_frees_label(store):
    # reunião com falantes "Pessoa 1" (sp-a, voice_id="v1") e "Pessoa 2" (sp-b), ambos pc
    ...
    me = store.set_me("sp-a", True)
    assert (me.is_me, me.label, me.voice_id) == (True, "Você", None)
    store.set_me("sp-b", True)            # troca: sp-a deixa de ser você
    a = next(s for s in store.get_speakers(mid) if s.id == "sp-a")
    assert (a.is_me, a.label) == (False, "Pessoa 3")  # próximo número livre (1 e 2 já usados)
    off = store.set_me("sp-b", False)
    assert (off.is_me, off.label) == (False, "Pessoa 4")
```

E em `tests/test_settings.py`: `assert Settings(store).get("my_name") == ""`.

- [ ] **Step 2: Rodar e ver falhar** — `uv run --frozen pytest tests/test_store.py tests/test_settings.py -q`. Esperado: falha por `origin`/`set_me` inexistentes.

- [ ] **Step 3: Implementar**
  - `_SCHEMA_V9 = "ALTER TABLE meetings ADD COLUMN origin TEXT NOT NULL DEFAULT 'recorded';"` adicionada ao fim de `_MIGRATIONS`.
  - `origin` em `Meeting`, `_MEETING_FIELDS` (`None`) e `_meeting`.
  - `set_me` numa transação (`self._tx()`): ao ligar, desmarca o outro `is_me` da mesma reunião, dando a ele o próximo "Pessoa N" livre, e marca este com `is_me=1`, `label="Você"` e `voice_id=NULL`. Ao desligar, `is_me=0` e o próximo "Pessoa N" livre. "Próximo livre" = 1 + o maior N entre os rótulos `^Pessoa (\d+)$` da reunião (contando o próprio rótulo antigo, se houver).

- [ ] **Step 4: Rodar e ver passar** — o mesmo comando; esperado: PASS.

- [ ] **Step 5: Commit** — `[Feat] Guardar a origem da reunião e marcar quem é você`

---

### Task 2: Parser da transcrição do Read AI

**Files:**
- Create: `src/whisper_ditado/meeting/readai.py`
- Test: `tests/meeting/test_readai.py` (texto sintético no próprio teste; **não** versionar o arquivo real do usuário)

**Interfaces:**
- Produces:
  - `class ImportFormatError(ValueError)`, com `FORMAT_ERROR = "Formato de transcrição não reconhecido; hoje só a do Read AI é aceita"`
  - `@dataclass Turn: name: str; start_s: float; end_s: float; text: str`
  - `@dataclass ReadAiTranscript: title: str; date: date | None; turns: list[Turn]` + `people` (property: nomes em ordem de primeira fala, sem repetição) + `duration_s` (property: `turns[-1].end_s`)
  - `parse_readai(text: str) -> ReadAiTranscript` (levanta `ImportFormatError`)

- [ ] **Step 1: Testes que falham**

```python
SAMPLE = (
    "Awake e Angular - mudança de mecanismo de créditos\n"
    "qua., 11 de nov. de 2026\n\n"
    "0:03 - Felipe Iaropoli\nOpa, fala ali.\n\n"
    "0:12 - Alessandro Reichert\nPrimeiro parágrafo.\n\n Segundo parágrafo.\n\n"
    "1:02:03 - Felipe Iaropoli\nValeu, até já.\n"
)

def test_parses_title_date_and_turns():
    t = parse_readai(SAMPLE)
    assert t.title == "Awake e Angular - mudança de mecanismo de créditos"
    assert t.date == date(2026, 11, 11)
    assert [(x.name, x.start_s) for x in t.turns] == [
        ("Felipe Iaropoli", 3.0), ("Alessandro Reichert", 12.0), ("Felipe Iaropoli", 3723.0)]
    assert t.turns[1].text == "Primeiro parágrafo.\n\nSegundo parágrafo."
    assert t.turns[0].end_s == 12.0                       # fim = início da próxima
    assert t.turns[2].end_s > 3723.0                       # última: estimativa pelo texto, ≥ 1 s
    assert t.people == ["Felipe Iaropoli", "Alessandro Reichert"]

def test_unknown_date_is_none():
    assert parse_readai(SAMPLE.replace("qua., 11 de nov. de 2026", "ontem")).date is None

@pytest.mark.parametrize("text", ["", "uma linha qualquer\nsem falas\n"])
def test_rejects_other_formats(text):
    with pytest.raises(ImportFormatError, match="só a do Read AI"):
        parse_readai(text)
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run --frozen pytest tests/meeting/test_readai.py -q`.

- [ ] **Step 3: Implementar `parse_readai`**
  - Cabeçalho de fala: `re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2}) - (.+)$")`.
  - Linhas de texto até o próximo cabeçalho pertencem à fala. Linhas com espaço inicial são parágrafos de continuação: une os parágrafos com `"\n\n"`, depois de `strip()`.
  - Data: `r"(\d{1,2}) de (jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\.? de (\d{4})"` na linha 2.
  - Fim da última fala: `start + max(1.0, len(text) / 15)`.

- [ ] **Step 4: Rodar e ver passar.**

- [ ] **Step 5: Commit** — `[Feat] Ler a transcrição exportada pelo Read AI`

---

### Task 3: Importador — inspeção, transcrição e conversão de mídia

**Files:**
- Create: `src/whisper_ditado/meeting/importer.py`
- Test: `tests/meeting/test_importer.py`

**Interfaces:**
- Consumes: `parse_readai`, `ImportFormatError` (Tarefa 2); `Store.create_meeting(..., status, origin, title)` (Tarefa 1); `finalize._run_ffmpeg`, `SAMPLE_RATE`, `OPUS_BITRATE`.
- Produces:
  - `MEDIA_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac"}`; `TRANSCRIPT_EXTENSIONS = {".txt"}`
  - `class ImportFileError(ValueError)` (mensagem pt-BR, vira 400 na API)
  - `@dataclass Preview: kind: Literal["transcript", "media"]; title: str; date: date | None; time: time | None; duration_s: float; people: list[str] | None; my_name_match: str | None`
  - `inspect(path: Path, *, my_name: str, probe=probe_media) -> Preview`
  - `@dataclass MediaInfo: duration_s: float; created_at: datetime | None` e `probe_media(path: Path) -> MediaInfo` (via `ffprobe -v error -show_entries format=duration:format_tags=creation_time -select_streams a -show_entries stream=index -of json`); sem trilha de áudio → `ImportFileError("O arquivo não tem áudio")`
  - `import_transcript(store: Store, path: Path, *, title: str, started_at: datetime, me: str | None) -> str` (id da reunião, já `ready`, `origin="import_transcript"`, `ended_at = started_at + duration`)
  - `convert_media(src: Path, directory: Path) -> Path` (grava `pc.opus` via `.part`; tempo limite próprio `IMPORT_FFMPEG_TIMEOUT_S = 4 * 3600`)

- [ ] **Step 1: Testes que falham**
  - `test_inspect_transcript_suggests_from_file`: grava `SAMPLE` (Tarefa 2) em `tmp_path / "Reunião com espaço ç.txt"`, com `os.utime` para `2026-11-11 17:05`. Espera `kind == "transcript"`, título da linha 1, `people` na ordem da primeira fala, `date == date(2026, 11, 11)` e `time == (datetime(2026, 11, 11, 17, 5) - timedelta(seconds=duration)).time()` (sem segundos).
  - `test_inspect_transcript_time_unknown_when_mtime_on_other_day`: mtime em `2026-11-12`. Espera `date == date(2026, 11, 11)` (do arquivo) e `time is None` (o usuário preenche).
  - `test_inspect_matches_my_name`: com `my_name="Alessandro Reichert"`, `my_name_match == "Alessandro Reichert"`; com outro nome, `None`.
  - `test_inspect_media_uses_probe`: `.mp4` com `probe` falso devolvendo `MediaInfo(2100.0, datetime(2026,11,11,16,30))`. Espera `kind == "media"`, título = nome sem extensão, `date == date(2026,11,11)`, `time == time(16,30)`, `people is None`.
  - `test_inspect_rejects_unknown_extension_and_missing_file`: `.pdf` e caminho inexistente levantam `ImportFileError`.
  - `test_import_transcript_builds_speakers`: `me="Alessandro Reichert"`. Espera "Você" com `is_me` e `color_index=0` e segmentos `mic`; "Felipe Iaropoli" com `color_index=1` e segmentos `pc`; reunião `ready`, `origin="import_transcript"`, `title_auto=False`.
  - `test_import_transcript_without_me`: `me=None`. Nenhum `is_me`; cores 1 e 2.
  - `@pytest.mark.skipif(shutil.which("ffmpeg") is None, ...) test_convert_media_writes_opus`: gera 2 s de seno com `ffmpeg -f lavfi`, converte e confere que `pc.opus` existe, que não sobra `.part` e que o original continua intacto (mesmo `stat().st_size` e `st_mtime`).

- [ ] **Step 2: Rodar e ver falhar** — `uv run --frozen pytest tests/meeting/test_importer.py -q`.

- [ ] **Step 3: Implementar** as funções acima. O arquivo `.txt` é lido como UTF-8 (com `errors="strict"`; erro de decodificação vira `ImportFileError` com `FORMAT_ERROR`). Se `probe_media` não achar `created_at`, a sugestão é `mtime - duração`.

- [ ] **Step 4: Rodar e ver passar.**

- [ ] **Step 5: Commit** — `[Feat] Importar transcrição do Read AI e converter vídeo ou áudio`

---

### Task 4: Pipeline — trabalho de importação de mídia e "Você" preservado no reprocessamento

**Files:**
- Modify: `src/whisper_ditado/meeting/session.py` (`MeetingManager.import_media`, `_import_job`)
- Modify: `src/whisper_ditado/meeting/finalize.py` (`_previous_me_spans`, parâmetro `me_key` em `_build_speakers`)
- Test: `tests/meeting/test_session.py`, `tests/meeting/test_finalize.py`

**Interfaces:**
- Consumes: `convert_media` (Tarefa 3); `Store.create_meeting` (Tarefa 1).
- Produces:
  - `MeetingManager.import_media(meeting_id: str, source: Path) -> None` (marca `processing`, publica `meeting_updated`, agenda `_import_job` em `self._gpu`)
  - `_import_job(meeting_id, source)`: `convert_media(source, paths.meeting_dir(meeting_id))` → mesmo corpo de `_reprocess_job` → **`self._notify_ready(meeting_id)`** → `finally: self._processing_done(meeting_id)`. Uma falha na conversão vira `fin.mark_failed`.
  - `finalize._previous_me_spans(store, meeting_id) -> list[tuple[float, float]]`: trechos `pc` do falante `is_me` atual.
  - `_build_speakers(..., me_key: str | None = None)`: a chave `me_key` vira `Speaker(is_me=True, label=ME_LABEL, color_index=0)`, mantendo o embedding.

- [ ] **Step 1: Testes que falham**
  - `test_session.py::test_import_media_converts_processes_and_notifies_ready`: usa os fakes do arquivo, com `convert_media` trocado por monkeypatch para gravar um `pc.raw` de 1 s. Espera status final `ready`, `on_ready` chamado com o id e eventos `meeting_updated` `processing` → `ready`.
  - `test_session.py::test_import_media_conversion_failure_marks_failed`: `convert_media` levanta `RuntimeError`. Espera `failed`, `on_ready` não chamado e reunião fora de `_processing`.
  - `test_finalize.py::test_reprocess_keeps_me_on_overlapping_speaker`: reunião `import_media` com falante `is_me` cujos trechos `pc` coincidem com a pessoa B da nova diarização falsa. Depois do `reprocess`, B é "Você" com `is_me` e mais ninguém é.
  - `test_finalize.py::test_reprocess_without_me_marks_nobody`: sem `is_me` antes, nenhum `is_me` depois.

- [ ] **Step 2: Rodar e ver falhar** — `uv run --frozen pytest tests/meeting/test_session.py tests/meeting/test_finalize.py -q`.

- [ ] **Step 3: Implementar.** Em `finalize`, depois de `new_spans`: `me = inherit_links(new_spans, {"__me__": _previous_me_spans(...)})`, com `me_key` sendo a chave cujo valor é `"__me__"`. Essa chave sai de `inherited`/`matches`, para não ganhar também uma voz.

- [ ] **Step 4: Rodar e ver passar** (o arquivo inteiro de cada teste, para pegar regressões).

- [ ] **Step 5: Commit** — `[Feat] Processar vídeo ou áudio importado como uma reunião gravada`

---

### Task 5: API — inspecionar, importar, "Sou eu" e proteções por origem

**Files:**
- Create: `src/whisper_ditado/api_imports.py` (`imports_router(ctx, get_meeting) -> APIRouter`, no padrão de `api_tasks.py`)
- Modify: `src/whisper_ditado/api.py` (incluir o router; `origin` em `meeting_detail`; recusar `reprocess` de `import_transcript`)
- Test: `tests/test_api_imports.py` (reaproveita `ctx`/`client`/`FakeMeetings` de `tests/test_api.py`, importando; acrescentar a `FakeMeetings` `imported: list[tuple[str, Path]]` e `import_media(meeting_id, source)`)

**Interfaces:**
- Consumes: Tarefas 1, 3 e 4.
- Produces (contrato do front, Tarefa 6):
  - `POST /imports/inspect` `{path: str}` → `{kind, title, date: "YYYY-MM-DD" | null, time: "HH:MM" | null, duration_s, people: string[] | null, my_name_match: string | null}`. `ImportFileError`/`ImportFormatError` → 400 com a mensagem.
  - `POST /imports` `{path, title, started_at: ISO local, me: string | null}` → `{meeting_id}`.
    - transcrição: `import_transcript`; se `me`, grava `settings.set("my_name", me)` (usar o método de escrita que `PUT /settings` usa); depois `ctx.jobs.on_ready(id)` quando `ctx.jobs` existe; publica `meeting_updated` com `ready`.
    - mídia: `create_meeting(status="processing", origin="import_media", title=...)` e `ctx.meetings.import_media(id, Path(path))`.
    - título vazio → 400 `"O título não pode ficar vazio"`.
  - `POST /speakers/{id}/me` `{on: bool}` → falante (`id, label, voice_id, is_me, color_index`); 409 `"Só dá para marcar quem é você em reuniões importadas de vídeo ou áudio"` se a origem ≠ `import_media`.
  - `GET /meetings/{id}` passa a ter `"origin"`.
  - `POST /meetings/{id}/reprocess` → 409 `"Esta reunião foi importada de uma transcrição e não tem áudio para reprocessar"` em `import_transcript`.

- [ ] **Step 1: Testes que falham**
  - `test_inspect_transcript` e `test_inspect_bad_txt_is_400`, este com a mensagem exata da spec. Usar arquivo com espaços e acento no nome.
  - `test_import_transcript_creates_ready_meeting_and_remembers_me`: verifica `origin`, `ready`, `settings.get("my_name")`, `on_ready` chamado (com `ctx.jobs` falso) e o evento publicado.
  - `test_import_media_queues_job`: `FakeMeetings.imported == [(id, path)]`, reunião `processing` com `origin="import_media"`.
  - `test_import_empty_title_is_400`.
  - `test_set_me_only_on_import_media` (409 em `recorded`; 200 em `import_media` com `label == "Você"`).
  - `test_reprocess_refuses_transcript_import` (409 com a mensagem).
  - `test_meeting_detail_has_origin`.

- [ ] **Step 2: Rodar e ver falhar** — `uv run --frozen pytest tests/test_api_imports.py -q`.

- [ ] **Step 3: Implementar.** As rotas síncronas que fazem I/O (`inspect`, transcrição) rodam em `run_in_threadpool`. O "cadastrar voz" já recusa falante sem embedding (`/speakers/{id}/enroll` → 400), então não precisa de mudança.

- [ ] **Step 4: Rodar e ver passar** — também `uv run --frozen pytest -q` inteiro.

- [ ] **Step 5: Commit** — `[Feat] Expor a importação de reuniões na API`

---

### Task 6: Interface — botão Importar, tela de confirmação, telas por origem, "Sou eu", README

**Files:**
- Modify: `app/src/api/types.ts` (`MeetingOrigin`, `origin` em `MeetingDetail`, `ImportPreview`)
- Modify: `app/src/api/client.ts` (`inspectImport(path)`, `importFile(body)`, `setSpeakerMe(id, on)`)
- Create: `app/src/components/ImportButton.vue` (botão + `open()` do `@tauri-apps/plugin-dialog` com filtros + drag-drop de `getCurrentWebview().onDragDropEvent`) e `app/src/components/ImportDialog.vue` (tela de confirmação, usando `AppDialog`, `AppButton`)
- Modify: `app/src/App.vue` (`<ImportButton @imported="openMeeting" />` no `TopBar`, depois do `RecordButton`)
- Modify: `app/src/components/MeetingDetail.vue` (sem `MeetingPlayer` nem "Reprocessar" em `import_transcript`; menu para "Você" em `import_media`)
- Modify: `app/src/components/SpeakerMenu.vue` (prop `origin`; "Sou eu" para quem não é você em `import_media`; para "Você", só `SpeakerSamples` e "Não sou eu"; sem `SpeakerSamples` nem identificar voz em `import_transcript`)
- Modify: `README.md` (seção curta "Importar uma gravação ou transcrição")
- Test: `app/src/components/ImportDialog.test.ts`, `app/src/components/SpeakerMenu.test.ts`, `app/src/components/MeetingDetail.test.ts`, `app/src/api/client.test.ts`

**Interfaces:**
- Consumes: contrato HTTP da Tarefa 5.
- Produces: `ImportButton` emite `imported: [meetingId: string]`.

- [ ] **Step 1: Testes que falham**
  - `ImportDialog.test.ts`:
    - **transcrição:** a prévia com `people` mostra as opções, mais "Nenhuma delas", e marca `my_name_match`. Hora `null` deixa o botão "Importar" desabilitado até preencher. Confirmar chama `importFile` com `started_at` montado de data + hora e `me` escolhido (`null` para "Nenhuma delas").
    - **mídia:** mostra "Importar e transcrever" e a nota do "Sou eu"; não mostra "Quem é você?".
    - **erro:** um 400 de `inspectImport` exibe a mensagem do backend.
  - `SpeakerMenu.test.ts`: "Sou eu" chama `setSpeakerMe(id, true)` em `import_media` e não aparece em `recorded`. "Não sou eu" aparece no menu de "Você" e chama `setSpeakerMe(id, false)`.
  - `MeetingDetail.test.ts`: `origin: "import_transcript"` → sem player e sem "Reprocessar"; `import_media` → clicar em "Você" abre o menu.
  - `client.test.ts`: as três chamadas usam método, caminho e corpo da Tarefa 5.

- [ ] **Step 2: Rodar e ver falhar** — `cd app && pnpm test`.

- [ ] **Step 3: Implementar.**
  - **Filtros do diálogo:** "Transcrição do Read AI" `["txt"]` e "Vídeo ou áudio", com as extensões de `MEDIA_EXTENSIONS` sem ponto.
  - **Drag-drop:** só considera o primeiro caminho do evento `drop`.
  - **Visual:** botão secundário com contorno (rascunho aprovado em `.superpowers/brainstorm/*/content/importar.html`), no visual Ametista.
  - **Textos fixos:**
    - nota da transcrição: "Em seguida o TARS gera o resumo e as tarefas. Sem áudio: sem player e sem aprender vozes."
    - nota da mídia: "As pessoas só aparecem depois da transcrição. Para marcar a sua voz, clique na etiqueta da pessoa e escolha “Sou eu”."
    - aviso da GPU: "Transcreve e separa falantes como numa reunião gravada (usa a GPU, alguns minutos)."

- [ ] **Step 4: Rodar e ver passar** — `cd app && pnpm test && pnpm build`.

- [ ] **Step 5: Verificação no app real.**
  1. Compilar com `pnpm tauri build --no-bundle`, reiniciar o backend (`systemctl --user restart whisper-ditado-backend`) e abrir o app.
  2. Importar `~/Downloads/Awake e Angular - mudança de mecanismo _ Transcrição.txt`.
  3. Conferir na reunião criada: "Você" e "Felipe Iaropoli", a data 11/11/2026 e o resumo.
  4. Importar um MP4 curto e conferir que ele termina `ready` com player.

- [ ] **Step 6: Commit** — `[Feat] Importar gravação ou transcrição pela interface`

---

### Task 7: Teste com modelos reais (GPU)

**Files:**
- Test: `tests/meeting/test_model_lifecycle_gpu.py` ou um arquivo novo `tests/meeting/test_import_gpu.py` (marcador `gpu`, no padrão dos testes `gpu` existentes)

- [ ] **Step 1:** Teste `@pytest.mark.gpu`: sintetiza fala com `tests.helpers.tts.synth` (como em `tests/test_transcriber.py`), grava em `.wav` e converte para `.mp3` com `ffmpeg`, roda `create_meeting(origin="import_media")` + `import_media` com o `MeetingManager` real e confere que a reunião fica `ready`, com segmentos `pc` não vazios.
- [ ] **Step 2:** `uv run --frozen pytest -m gpu tests/meeting/test_import_gpu.py -q` no ambiente NVIDIA. Esperado: PASS.
- [ ] **Step 3: Commit** — `[Test] Cobrir a importação de áudio com os modelos reais`
