# Backend de ditado e reuniões — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend Python que mantém o ditado atual e acrescenta gravação de reuniões (microfone + áudio do PC), transcrição incremental, separação e reconhecimento de vozes, integração com o `claude` CLI e uma API local (HTTP + WebSocket) para o app.

**Architecture:** Pacote `whisper_ditado` (layout `src/`), um processo asyncio que roda o ditado (evdev), as sessões de reunião (subprocessos `pw-record` por trilha), o pipeline na GPU (faster-whisper + pyannote) e um servidor FastAPI/uvicorn no mesmo loop. Estado em SQLite + arquivos de áudio em `~/.local/share/whisper-ditado/`. O `ditado.py` atual e o serviço em produção **não são tocados** neste plano; a troca acontece no plano do app (`2026-10-19-app-reunioes.md`, última tarefa).

**Tech Stack:** Python 3.12 (uv), faster-whisper/CTranslate2 (CUDA 12), pyannote.audio 4.0.x + torch (wheel CUDA 12.x), FastAPI + uvicorn, python-evdev, PipeWire (`pw-record`, `pw-dump`, `pw-cli`), ffmpeg, SQLite (stdlib), pytest.

**Spec:** `docs/superpowers/specs/2026-10-19-app-reunioes-design.md`

## Global Constraints

- Idioma fixo `pt`; reuniões e ditado usam o mesmo prompt de estilo e as mesmas hotwords (`config/vocabulario.txt`) e substituições (`config/substituicoes.txt`); resumo e título em pt-BR.
- Modelo de transcrição: faster-whisper `large-v3-turbo`, `device="cuda"`, `compute_type="float16"`; áudio sempre passado como `np.ndarray` float32 16 kHz mono (o decode por PyAV está quebrado neste ambiente — nunca passar caminho de arquivo ao faster-whisper).
- Sempre `local_files_only=True` primeiro ao carregar modelos; só baixar se não houver cache.
- Ditado: comportamento atual inalterado (Ctrl+Win, Shift+Insert, espera de teclas soltas, detecção de loop, vocabulário com recarga a quente). Latência do ditado não pode piorar perceptivelmente, inclusive durante gravação/processamento de reunião — por isso reuniões usam **uma segunda instância** do WhisperModel (VRAM: 16 GB; Whisper ~3 GB cada + pyannote).
- Chamadas ao Claude só por ação do usuário, exceto o título automático (desligável pela configuração `auto_title`).
- API escuta **somente em 127.0.0.1**, exige token; `api.json` com porta e token gravado com permissão `0600`.
- Dados em `~/.local/share/whisper-ditado/` (respeitar `XDG_DATA_HOME`); banco `ditado.db` com permissão `0600`; nada é apagado automaticamente.
- `config/` do repositório é a fonte da verdade de vocabulário/substituições (`~/.config/whisper-ditado` é symlink para ela).
- Áudio final por trilha em Opus mono (~16 kbps, `mic.opus` e `pc.opus`); durante a gravação, PCM s16le 16 kHz mono gravado incrementalmente em disco (`mic.raw`, `pc.raw`).
- Mensagens de commit: formato `[Tipo] Título` em pt-BR (ver CLAUDE.md global), terminando com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Testes que usam GPU levam `@pytest.mark.gpu` e só rodam com `-m gpu`; **nunca rodar suítes `gpu` em paralelo** (disputam a mesma GPU).

## Review Focus

1. **Backend reinicia no meio de uma gravação** (crash, `systemctl restart`, logout) → o áudio já gravado não se perde; ao subir, reuniões presas em `recording` são finalizadas a partir dos `.raw`. Teste: `test_startup_recovers_orphan_recording` (Task 11).
2. **Headset desconectado no meio da gravação** → a trilha continua alinhada no tempo (preenche com silêncio), alerta `track_dead` e reconexão. Teste: `test_track_pads_gap_and_restarts_after_process_dies` (Task 7).
3. **`claude` ausente, deslogado ou estourando tempo** → erro claro na API, sem derrubar o backend. Teste: `test_claude_errors_are_reported` (Task 12).
4. **Reunião sem ninguém falando no PC** (usuário sozinho, ou áudio do PC mudo) → transcrição só com "Você", sem crash na diarização. Teste: `test_finalize_with_silent_pc_track` (Task 11).
5. **Sem token do Hugging Face** → reunião fica pronta mesmo assim, com um único falante "Participantes" para o áudio do PC e um aviso. Teste: `test_finalize_without_hf_token_uses_single_speaker` (Task 11).

## Paralelização

- **Sequencial primeiro:** Task 1 (dependências, `pyproject.toml`, `uv.lock`) → Task 2 (banco).
- **Grupo paralelo A** (após Task 2; arquivos disjuntos, sem dependências entre si, nenhum toca `pyproject.toml`/`uv.lock`): Tasks 3, 5, 6, 8, 9, 12.
- **Task 4** depende da 3. **Task 7** depende da 6. **Task 10** depende da 3 e da 8.
- **Sequencial no fim:** Task 11 (depende de 2, 4, 5, 7, 8, 9, 10) → Task 13 (depende de 11, 12) → Task 14.
- Tasks com testes `gpu` (3, 10, 11, 14) não rodam suítes `-m gpu` ao mesmo tempo.

## Estrutura de arquivos

```
src/whisper_ditado/
  __init__.py
  paths.py            # diretórios de dados, api.json, pasta de cada reunião
  store.py            # SQLite: schema, migrações, repositório
  settings.py         # padrões + overrides do banco; acesso a vocab/subs
  vocab.py            # Vocabulary, has_loop (movidos do ditado.py)
  transcriber.py      # Transcriber (Whisper), opção de timestamps por palavra
  hotkey.py           # parse_hotkey + HotkeyState (máquina de estados pura)
  dictation.py        # serviço de ditado (evdev, gravação curta, colar), emite eventos
  paste.py            # colar via wl-copy + ydotool
  events.py           # EventBus (asyncio) para o WebSocket
  audio/pwgraph.py    # leitura do grafo do PipeWire, dispositivos, app da chamada
  audio/levels.py     # dBFS e máquina de estados de alertas (HealthMonitor)
  audio/capture.py    # TrackRecorder (pw-record, alinhamento, reconexão, níveis)
  audio/aec.py        # cancelamento de eco (módulo echo-cancel do PipeWire)
  meeting/timeline.py # cortes de bloco, palavra→falante, segmentos, eco, ditado
  meeting/voices.py   # embeddings: similaridade, casamento, cadastro
  meeting/diarize.py  # wrapper do pyannote
  meeting/incremental.py # transcrição por blocos durante a gravação
  meeting/session.py  # MeetingSession: orquestra gravação + finalização
  meeting/finalize.py # pipeline ao parar + reprocessar + recuperação
  claude_cli.py       # resumo, título, sugestão de nomes via `claude -p`
  api.py              # FastAPI: REST + WebSocket + auth
  main.py             # entrypoint: liga tudo
tests/ (espelha os módulos; tests/helpers/tts.py gera fala sintética com libespeak-ng)
run-backend.sh, whisper-ditado-backend.service
```

---

### Task 1: Fundação do pacote e dependências

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `.gitignore`
- Create: `src/whisper_ditado/__init__.py`, `src/whisper_ditado/paths.py`, `tests/conftest.py`, `tests/test_paths.py`, `tests/test_gpu_stack.py`, `tests/helpers/tts.py`

**Interfaces:**
- Produces:
  - `paths.data_dir() -> Path` (`$XDG_DATA_HOME/whisper-ditado` ou `~/.local/share/whisper-ditado`, criado com `0700`)
  - `paths.meeting_dir(meeting_id: str) -> Path` (`data_dir()/meetings/<id>`, criado)
  - `paths.db_path() -> Path`, `paths.api_info_path() -> Path` (`data_dir()/api.json`)
  - `paths.repo_config_dir() -> Path` (pasta `config/` do repositório: `Path(__file__).parents[2] / "config"`)
  - `tests/helpers/tts.py: synth(text: str, voice: str = "pt-br") -> np.ndarray` (float32 16 kHz, via ctypes em `libespeak-ng.so.1`, mesmo método usado na sessão de testes do vocabulário)
  - marcador pytest `gpu` registrado em `pyproject.toml`; por padrão `-m "not gpu"`

- [x] **Step 1:** Em `pyproject.toml`: pacote `whisper_ditado` em `src/` (build backend `hatchling`), dependências de runtime `faster-whisper`, `evdev`, `nvidia-cublas-cu12`, `nvidia-cudnn-cu12==9.*` (já existentes), `fastapi`, `uvicorn`, `pyannote.audio>=4.0,<5`, `torch` vindo do índice PyTorch CUDA 12.x (`[tool.uv.sources]` + `[[tool.uv.index]]` explícito para `torch`/`torchaudio`; preferir `cu128`; o wheel tem que ter kernels `sm_120`); grupo dev `pytest`, `httpx`. Adicionar `data/` e `*.raw` ao `.gitignore`.
- [x] **Step 2:** `uv sync` e verificar a convivência das duas pilhas CUDA no mesmo processo:

Run: `LD_LIBRARY_PATH=$(echo .venv/lib/python3.12/site-packages/nvidia/{cublas,cudnn}/lib | tr ' ' :) .venv/bin/python -c "import ctranslate2, torch; print(ctranslate2.get_cuda_device_count(), torch.cuda.is_available(), torch.cuda.get_device_capability())"`
Expected: `1 True (12, 0)`. Se o torch puxar outra versão de `nvidia-cudnn-cu12`, fixar a versão compatível com ambos.

- [x] **Step 3: Testes que falham**

```python
# tests/test_paths.py
def test_data_dir_respects_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert paths.data_dir() == tmp_path / "whisper-ditado"
    assert (tmp_path / "whisper-ditado").stat().st_mode & 0o777 == 0o700

def test_meeting_dir_is_created(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert paths.meeting_dir("abc").is_dir()

# tests/test_gpu_stack.py
@pytest.mark.gpu
def test_whisper_transcribes_synthetic_speech():
    model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16", local_files_only=True)
    segs, _ = model.transcribe(synth("Bom dia, tudo bem?"), language="pt")
    assert "bom dia" in " ".join(s.text for s in segs).lower()
```

- [x] **Step 4:** Rodar `uv run pytest tests/test_paths.py -v` → FAIL (módulo inexistente). Implementar `paths.py`, `tests/helpers/tts.py`, `conftest.py` (fixture `data_home` que aponta `XDG_DATA_HOME` para `tmp_path`; o `conftest` exporta o `LD_LIBRARY_PATH` das libs CUDA do venv antes de importar ctranslate2 em testes `gpu` — ou documentar `run-tests.sh` que faz isso).
- [x] **Step 5:** `uv run pytest -v` → PASS; `uv run pytest -m gpu tests/test_gpu_stack.py -v` → PASS.
- [x] **Step 6: Commit** — `[Chore] Criar pacote do backend e dependências de reuniões`

---

### Task 2: Banco SQLite e repositório

**Files:**
- Create: `src/whisper_ditado/store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `paths.db_path()`
- Produces (`store.Store(path: Path)`, thread-safe com um `threading.Lock`, `check_same_thread=False`, `PRAGMA journal_mode=WAL`, arquivo `0600`, migrações por `PRAGMA user_version`):
  - Tabelas:
    - `meetings(id TEXT PK, title TEXT, title_auto INTEGER, started_at TEXT, ended_at TEXT, status TEXT CHECK(status IN ('recording','processing','ready','failed')), error TEXT, followed_app TEXT)`
    - `speakers(id TEXT PK, meeting_id TEXT REFERENCES meetings ON DELETE CASCADE, label TEXT, voice_id TEXT REFERENCES voices ON DELETE SET NULL, is_me INTEGER, color_index INTEGER, embedding BLOB)`
    - `segments(id INTEGER PK, meeting_id TEXT REFERENCES meetings ON DELETE CASCADE, speaker_id TEXT REFERENCES speakers ON DELETE CASCADE, source TEXT CHECK(source IN ('mic','pc')), start_s REAL, end_s REAL, text TEXT, dictated INTEGER)`
    - `summaries(meeting_id TEXT PK REFERENCES meetings ON DELETE CASCADE, text TEXT, created_at TEXT)`
    - `voices(id TEXT PK, name TEXT, embedding BLOB, n_samples INTEGER, source_meeting_id TEXT, source_speaker_id TEXT, created_at TEXT, updated_at TEXT)`
    - `alerts(id INTEGER PK, meeting_id TEXT REFERENCES meetings ON DELETE CASCADE, kind TEXT, start_s REAL, end_s REAL)`
    - `dictation_spans(id INTEGER PK, meeting_id TEXT REFERENCES meetings ON DELETE CASCADE, start_s REAL, end_s REAL)`
    - `settings(key TEXT PK, value TEXT)` (JSON)
  - Dataclasses `Meeting`, `Speaker`, `Segment`, `Voice`, `Alert` com os mesmos campos (embeddings como `np.ndarray | None`, serializados `float32.tobytes()`).
  - Métodos: `create_meeting(started_at: datetime) -> Meeting`, `get_meeting(id) -> Meeting | None`, `list_meetings(query: str | None = None) -> list[MeetingSummary]` (`MeetingSummary`: id, title, started_at, duration_s, speakers_count, status; `query` busca com `LIKE` em título e texto dos segmentos, sem diferenciar maiúsculas; ordem `started_at DESC`), `update_meeting(id, **fields)`, `delete_meeting(id)`, `replace_transcript(meeting_id, speakers: list[Speaker], segments: list[Segment])` (transação única), `get_speakers(meeting_id)`, `get_segments(meeting_id)`, `update_speaker(id, **fields)`, `merge_speakers(src_id, dst_id)` (move segmentos e apaga `src`), `set_summary(meeting_id, text)`, `get_summary(meeting_id) -> str | None`, `add_alert(meeting_id, kind, start_s, end_s)`, `get_alerts(meeting_id)`, `add_dictation_span(meeting_id, start_s, end_s)`, `get_dictation_spans(meeting_id)`, `upsert_voice(voice: Voice)`, `list_voices()`, `get_voice(id)`, `delete_voice(id)`, `get_setting(key, default)`, `set_setting(key, value)`, `all_settings() -> dict`, `meetings_with_status(status) -> list[Meeting]`.

- [x] **Step 1: Testes que falham** em `tests/test_store.py`:
  - `test_migrations_create_schema_and_file_is_private` (arquivo `0600`, `user_version == 1`)
  - `test_meeting_crud_and_cascade` (`delete_meeting` apaga speakers, segments, summary, alerts)
  - `test_list_meetings_searches_title_and_transcript` (`list_meetings("pull request")` acha a reunião cujo segmento contém "Pull Request"; ordem por data decrescente; `duration_s` e `speakers_count` corretos)
  - `test_replace_transcript_is_atomic` (exceção no meio não deixa estado parcial)
  - `test_merge_speakers_moves_segments`
  - `test_voice_embedding_roundtrip` (`np.allclose` após salvar/ler)
  - `test_settings_json_roundtrip` (dict, bool, None)
- [x] **Step 2:** `uv run pytest tests/test_store.py -v` → FAIL.
- [x] **Step 3:** Implementar `store.py`.
- [x] **Step 4:** `uv run pytest tests/test_store.py -v` → PASS.
- [x] **Step 5: Commit** — `[Feat] Adicionar banco SQLite das reuniões`

---

### Task 3: Extrair o núcleo do ditado para o pacote

O `ditado.py` continua intocado e em produção. Esta task copia/refatora o código para módulos testáveis.

**Files:**
- Create: `src/whisper_ditado/vocab.py`, `transcriber.py`, `hotkey.py`, `paste.py`, `tests/test_vocab.py`, `tests/test_hotkey.py`, `tests/test_transcriber.py`

**Interfaces:**
- Produces:
  - `vocab.Vocabulary(vocab_path: Path, subs_path: Path, tokenizer)` com `refresh()`, `hotwords: str | None`, `apply(text) -> str`, `is_echo(text) -> bool`; `vocab.has_loop(text) -> bool` — comportamento idêntico ao `ditado.py` atual (UTF-8 com BOM/latin-1, chave de recarga `(st_mtime_ns, st_size, st_ino)`, substituição em passada única, maiúscula inicial, destino vazio remove, `is_echo` exige ≥5 itens e ≥80%, limiar de loop 5× para 1–2 palavras e 3× para 3–6, ignora números).
  - `transcriber.TranscriberOptions` (dataclass: `model="large-v3-turbo"`, `compute_type="float16"`, `language="pt"`, `prompt: str | None`, `beam_size=5`).
  - `transcriber.Transcriber(opts: TranscriberOptions, vocab: Vocabulary)` com:
    - `transcribe_text(audio: np.ndarray) -> str` (fluxo atual do ditado: refresh do vocabulário protegido por try, fallback `no_repeat_ngram_size=3` em loop, descarte de ecos do prompt/vocabulário, substituições)
    - `transcribe_words(audio: np.ndarray, offset_s: float = 0.0) -> list[Word]` com `Word(start_s: float, end_s: float, text: str, prob: float)` em tempo absoluto (`word_timestamps=True`, `without_timestamps=False`, mesmas hotwords/prompt; aplica o descarte de alucinações por segmento; **não** aplica substituições — elas são aplicadas no texto final do segmento).
    - Carrega com `local_files_only=True` primeiro; aquecimento no construtor; `self.vocab` exposto.
  - `hotkey.parse_hotkey(spec) -> tuple[frozenset[int], ...]` e `hotkey.KEY_ALIASES` (idênticos ao atual).
  - `hotkey.HotkeyState(groups)` — máquina de estados pura extraída de `Ditado._on_key`: `on_key(code, value, active_keys: Callable[[], set[int]]) -> Action` onde `Action` ∈ `{NONE, START, FINISH, CANCEL}`; propriedades `pressed: set[int]`, `all_released: bool`.
  - `paste.paste(text: str, mode: str, ydotool: str) -> None` e `paste.PASTE_KEYS` (idênticos ao atual).

- [x] **Step 1: Testes que falham**
  - `tests/test_vocab.py`: portar exatamente os 18 casos verificados na sessão (BOM, latin-1, maiúscula inicial, sem encadear, destino vazio, sem pedaço de palavra, eco longo vs lista curta, arquivo inválido não derruba, arquivo removido → `hotwords is None`; `has_loop` falso para "Não, não, não, isso tá errado", "Muito, muito, muito obrigado", "é isso, é isso, é isso aí", "vai vai vai", "versão 1.1.1", "eu acho que sim, eu acho que não"; verdadeiro para "evisado" ×7 e "o deploy do" ×3).
  - `tests/test_hotkey.py`: portar os 8 cenários do combo `ctrl+super` (só Ctrl não grava; só Super não grava; LCtrl+LSuper grava e soltar finaliza; Super antes + RCtrl e soltar Ctrl primeiro finaliza; Ctrl+Super+Seta cancela; Shift antes impede; Ctrl+A depois Super grava; `parse_hotkey` aceita `ctrl+super`, `KEY_RIGHTCTRL`, `alt+KEY_SPACE`, `win+ctrl` e rejeita `ctrl+banana`).
  - `tests/test_transcriber.py` (`gpu`): `test_transcribe_words_returns_absolute_times` (`offset_s=10` → todas as palavras com `start_s >= 10`, texto de "Preciso fazer o deploy no staging" contém "deploy"); `test_transcribe_text_applies_substitutions`.
- [x] **Step 2:** Rodar → FAIL.
- [x] **Step 3:** Implementar movendo o código do `ditado.py` (copiar, não importar dele).
- [x] **Step 4:** `uv run pytest tests/test_vocab.py tests/test_hotkey.py -v` → PASS; `uv run pytest -m gpu tests/test_transcriber.py -v` → PASS.
- [x] **Step 5: Commit** — `[Refactor] Extrair núcleo do ditado para o pacote do backend`

---

### Task 4: Configurações e serviço de ditado no pacote

**Files:**
- Create: `src/whisper_ditado/settings.py`, `src/whisper_ditado/events.py`, `src/whisper_ditado/dictation.py`, `tests/test_settings.py`, `tests/test_dictation.py`

**Interfaces:**
- Consumes: `Store` (Task 2), `HotkeyState`, `parse_hotkey`, `paste`, `Transcriber` (Task 3)
- Produces:
  - `settings.DEFAULTS: dict` com as chaves e valores:
    `dictation_enabled=True`, `key="ctrl+super"`, `paste="shift+insert"`, `model="large-v3-turbo"`, `compute_type="float16"`, `prompt="Olá! Hoje fiz o deploy no staging, abri um pull request e ajustei o endpoint no backend."`, `trailing_space=True`, `min_sec=0.4`, `max_sec=300.0`, `ignore_devices=["ydotoold"]`, `ydotool="/usr/bin/ydotool"`, `auto_title=True`, `summary_prompt=<texto padrão abaixo>`, `voice_match_threshold=0.6`, `mic_device=None`, `pc_device=None`, `follow_call_app=True`, `aec_enabled=True`, `hf_token=None`, `claude_bin=None`, `claude_model=None`.
  - Texto padrão de `summary_prompt` (pt-BR): "Resuma a reunião abaixo em português do Brasil, em Markdown, com as seções: **Pontos principais**, **Decisões**, **Tarefas** (quem faz o quê; use o nome de quem falou) e **Pendências**. Seja objetivo e não invente nada que não esteja na transcrição."
  - `settings.Settings(store)`: `get(key)` (banco > variável de ambiente `DITADO_<KEY>` > `DEFAULTS`), `update(changes: dict) -> dict` (valida: `key` via `parse_hotkey`, `paste` em `PASTE_KEYS`, limiar entre 0 e 1; erro → `ValueError` com mensagem em pt-BR), `as_dict() -> dict` (sem `hf_token`; expõe `hf_token_set: bool`), `read_text(name: Literal["vocab","subs"]) -> str`, `write_text(name, text)` (escreve em `paths.repo_config_dir()`, escrita atômica por arquivo temporário + `os.replace`).
  - `events.EventBus`: `publish(event: dict)` (thread-safe via `loop.call_soon_threadsafe`), `subscribe() -> AsyncIterator[dict]`; cada evento tem `type`.
  - `dictation.DictationService(settings, transcriber: Transcriber, bus: EventBus)`: `async run()` (scan evdev + leitura, igual ao atual), `apply_settings()` (relê tecla/colagem/limites sem reiniciar; se `model`, `compute_type` ou `prompt` mudaram, cria um novo `Transcriber` numa thread e troca quando estiver aquecido — o ditado segue com o antigo até lá), `paused: bool` (espelha `dictation_enabled`); publica `{"type":"dictation","active":bool}` ao começar/terminar de gravar; chama o callback opcional `on_span(start: float, end: float)` (tempo monotônico) ao finalizar um ditado — usado pela reunião para marcar trechos ditados.

- [x] **Step 1: Testes que falham**
  - `test_settings_precedence` (banco > env `DITADO_KEY` > padrão)
  - `test_settings_validation_rejects_bad_key` (`update({"key":"ctrl+banana"})` → `ValueError`, nada salvo)
  - `test_as_dict_hides_hf_token`
  - `test_write_text_is_atomic_and_targets_repo_config` (usa `monkeypatch` em `paths.repo_config_dir`)
  - `test_dictation_publishes_events_and_span` — com `Recorder`, `transcriber` e `paste` falsos (mesmo padrão dos testes de máquina de estados da sessão): simular combo, verificar eventos `dictation active True/False`, `paste` chamado com "frase " e `on_span` chamado uma vez.
  - `test_dictation_paste_waits_for_keys_released` (porta do cenário "dois ditados seguidos").
- [x] **Step 2:** Rodar → FAIL. **Step 3:** Implementar. **Step 4:** Rodar → PASS.
- [x] **Step 5: Commit** — `[Feat] Adicionar configurações e serviço de ditado no backend`

---

### Task 5: Grafo do PipeWire, dispositivos e app da chamada

**Files:**
- Create: `src/whisper_ditado/audio/__init__.py`, `audio/pwgraph.py`, `tests/audio/test_pwgraph.py`, `tests/fixtures/pwdump_*.json`

**Interfaces:**
- Produces:
  - `pwgraph.snapshot() -> Graph` (roda `pw-dump`, timeout 3 s) e `pwgraph.parse(dump: list[dict]) -> Graph`.
  - `Graph`: `nodes: dict[int, Node]`, `links: list[tuple[int, int]]` (output node → input node), `default_sink: str | None`, `default_source: str | None` (do metadata `default`).
  - `Node`: `id`, `name` (`node.name`), `description`, `media_class`, `app_name`, `app_pid: int | None`, `app_binary`.
  - `devices(graph) -> Devices(sources: list[Device], sinks: list[Device], default_source, default_sink)` com `Device(name, description)`; exclui nós nossos (nome começando com `whisper_ditado`).
  - `call_apps(graph, own_pids: set[int]) -> list[CallApp]`: apps com nó `Stream/Input/Audio` ligado a uma fonte, excluindo `own_pids` e nós cujo `app_name == "whisper-ditado"`; `CallApp(app_name, pid, sinks: set[str])` com as saídas onde os nós `Stream/Output/Audio` do **mesmo pid** (fallback: mesmo `app_name`) estão tocando. Ordem: o mais recente primeiro (maior id de nó de captura).
  - `pick_pc_target(graph, apps, fixed_sink: str | None, follow: bool) -> Target(sink: str | None, followed_app: str | None, mismatch: bool)`: com `fixed_sink`, usa ele e `mismatch = bool(apps) and fixed_sink not in apps[0].sinks`; sem `fixed_sink` e `follow=True` com app de chamada tocando → a primeira saída desse app; senão `default_sink`.

- [x] **Step 1:** Capturar fixtures reais (sem dados pessoais além de nomes de dispositivos): `pw-dump > tests/fixtures/pwdump_idle.json`; com uma chamada de teste aberta no navegador (ou `pw-record` + `pw-play` simulando captura e reprodução do mesmo processo), salvar `pwdump_call_firefox.json`. Se não for possível gerar uma chamada real, construir a fixture à mão a partir de `pwdump_idle.json` acrescentando os nós `Stream/Input/Audio` e `Stream/Output/Audio` com `application.name="Firefox"`, mesmo `application.process.id`, e os links.
- [x] **Step 2: Testes que falham**
  - `test_devices_lists_sources_and_sinks_with_defaults` (a fixture ociosa desta máquina tem as saídas `alsa_output.pci-0000_09_00.4.analog-stereo`, `alsa_output.pci-0000_07_00.1.hdmi-stereo`, `obs_mudo`; padrão `alsa_output.pci-0000_09_00.4.analog-stereo`)
  - `test_call_app_detected_by_mic_capture` (Firefox capturando microfone → `CallApp("Firefox", pid, {"<saída>"})`)
  - `test_browser_playing_without_mic_is_not_a_call_app`
  - `test_own_pw_record_is_ignored`
  - `test_pick_target_follows_call_app_sink` / `test_pick_target_fixed_sink_reports_mismatch` / `test_pick_target_falls_back_to_default`
- [x] **Step 3:** Rodar → FAIL. **Step 4:** Implementar. **Step 5:** Rodar → PASS.
- [x] **Step 6: Commit** — `[Feat] Ler grafo do PipeWire e detectar o app da chamada`

---

### Task 6: Níveis de áudio e alertas de saúde

**Files:**
- Create: `src/whisper_ditado/audio/levels.py`, `tests/audio/test_levels.py`

**Interfaces:**
- Produces:
  - `levels.dbfs(samples: np.ndarray) -> float` (int16 ou float32; silêncio digital → `-inf`).
  - `levels.HealthMonitor(now: Callable[[], float])` com constantes `NO_SIGNAL_DB = -70.0`, `SPEECH_DB = -40.0`, `MIC_SILENT_AFTER_S = 15.0`, `PC_SILENT_AFTER_S = 90.0`.
  - `HealthMonitor.update(mic_db: float, pc_db: float, mic_alive: bool, pc_alive: bool, mismatch: bool) -> list[AlertChange]` com `AlertChange(kind: str, active: bool, at_s: float)`; `kind` ∈ `mic_silent`, `pc_silent`, `pc_wrong_output`, `mic_dead`, `pc_dead`.
  - Regras: `mic_silent` ativa quando o microfone fica abaixo de `NO_SIGNAL_DB` por `MIC_SILENT_AFTER_S` seguidos; `pc_silent` ativa quando o PC fica abaixo de `NO_SIGNAL_DB` por `PC_SILENT_AFTER_S` **e** o microfone passou de `SPEECH_DB` em algum momento dessa janela; `pc_wrong_output` segue `mismatch` imediatamente; `*_dead` seguem `*_alive` imediatamente. Cada alerta desativa assim que a condição some. `active_alerts() -> set[str]`; `history() -> list[tuple[kind, start_s, end_s]]` (intervalos fechados, em segundos desde o início da sessão; alertas ainda abertos são fechados por `close(at)`).

- [x] **Step 1: Testes que falham** (relógio falso):
  - `test_dbfs_of_digital_silence_is_minus_inf` e `test_dbfs_full_scale_sine_is_about_minus_3`
  - `test_mic_silent_after_15s_and_clears`
  - `test_quiet_but_present_mic_is_not_silent` (−55 dBFS por 60 s → nenhum alerta)
  - `test_pc_silent_requires_user_speaking` (PC mudo 120 s com microfone mudo → nada; com o microfone falando → `pc_silent`)
  - `test_wrong_output_and_dead_are_immediate`
  - `test_history_records_intervals`
- [x] **Step 2:** Rodar → FAIL. **Step 3:** Implementar. **Step 4:** Rodar → PASS.
- [x] **Step 5: Commit** — `[Feat] Medir níveis e detectar falhas de captura`

---

### Task 7: Captura por trilha e cancelamento de eco

**Files:**
- Create: `src/whisper_ditado/audio/capture.py`, `src/whisper_ditado/audio/aec.py`, `tests/audio/test_capture.py`

**Interfaces:**
- Consumes: `levels.dbfs` (Task 6)
- Produces:
  - `capture.TrackRecorder(name: Literal["mic","pc"], out_path: Path, target: str | None, capture_sink: bool, spawn: Callable[[list[str]], Popen] = subprocess.Popen, clock: Callable[[], float] = time.monotonic)`:
    - Comando: `pw-record --rate 16000 --channels 1 --format s16 --raw -P '{ application.name = "whisper-ditado" node.name = "whisper_ditado_<name>" stream.capture.sink = <true|false> }' [--target <target>] -`.
    - `start(t0: float)`; thread leitora que anexa PCM a `out_path` (flush a cada ~1 s); `level_db -> float` (últimos 100 ms); `alive -> bool`; `samples_written -> int`.
    - **Alinhamento:** o arquivo sempre corresponde ao tempo de relógio desde `t0`; quando o processo morre ou demora, preenche com zeros (`expected = (clock()-t0)*16000`) antes de anexar dados novos; reinicia o processo automaticamente com backoff (0,5 s, 1 s, 2 s; máx. 5 s).
    - `retarget(target: str | None)` reinicia o processo com novo alvo mantendo o alinhamento.
    - `stop() -> int` (amostras totais).
    - `capture.read_pcm(path, start_s, end_s) -> np.ndarray` (float32) — leitura de trechos do `.raw`.
  - `aec.EchoCanceller(mic: str | None, sink: str | None)`: `start() -> str | None` carrega o módulo de eco do PipeWire num subprocesso `pw-cli -m load-module libpipewire-module-echo-cancel { ... }` mantido vivo, com `monitor.mode = true` (referência = monitor da saída), `aec.method = webrtc`, fonte virtual `node.name = "whisper_ditado_aec"`; retorna o nome da fonte virtual para o `TrackRecorder` do microfone, ou `None` se falhar (log e segue sem AEC). `stop()` encerra o subprocesso.

- [x] **Step 1: Testes que falham** (processo falso que escreve bytes num pipe e pode "morrer"; relógio falso):
  - `test_track_writes_pcm_and_reports_level`
  - `test_track_pads_gap_and_restarts_after_process_dies` (processo morre aos 2 s, volta aos 5 s → arquivo tem 3 s de zeros no lugar certo; `alive` False durante a falha; respawn chamado)
  - `test_retarget_keeps_alignment`
  - `test_command_line_for_pc_track_captures_sink` (`stream.capture.sink = true`, `--target` só quando há alvo)
  - `test_read_pcm_slices_by_time`
- [x] **Step 2:** Rodar → FAIL. **Step 3:** Implementar `capture.py`. **Step 4:** Rodar → PASS.
- [x] **Step 5: Verificação manual do AEC** (não automatizável): com caixa de som, tocar fala por `pw-play` e gravar 10 s com e sem AEC (`EchoCanceller` + `TrackRecorder` num script em `scratch/`, fora do repositório). Esperado: com AEC, o `dbfs` médio da trilha do microfone durante a fala tocada cai ≥ 15 dB. Se `monitor.mode` não existir nesta versão (PipeWire 1.6.2) ou não atingir isso, implementar `aec.EchoCanceller.start()` retornando `None` e definir `aec_enabled=False` em `settings.DEFAULTS` — a rede de segurança da Task 8 continua valendo. Registrar o resultado no commit.
- [x] **Step 6: Commit** — `[Feat] Capturar trilhas de áudio alinhadas com reconexão e eco`

---

### Task 8: Linha do tempo (funções puras)

**Files:**
- Create: `src/whisper_ditado/meeting/__init__.py`, `meeting/timeline.py`, `tests/meeting/test_timeline.py`

**Interfaces:**
- Consumes: `transcriber.Word` (Task 3 — só o tipo; definir um `Protocol` local se a Task 3 ainda não estiver pronta: `start_s`, `end_s`, `text`)
- Produces:
  - `choose_cut(speech: list[tuple[float, float]], start_s: float, available_s: float, min_len_s: float = 30.0, max_len_s: float = 90.0) -> float | None`: escolhe onde cortar o próximo bloco de transcrição — o fim do maior silêncio entre falas dentro de `[start_s+min_len_s, min(available_s-1, start_s+max_len_s)]`; se não houver silêncio e já passou `max_len_s`, corta em `start_s+max_len_s`; se não há áudio suficiente, `None`.
  - `Turn(start_s, end_s, speaker: str)` (saída da diarização).
  - `assign_words(words, turns) -> list[tuple[Word, str | None]]` (falante com maior sobreposição; sem sobreposição → falante do turno mais próximo até 1 s, senão `None`).
  - `group_segments(labeled_words, source: str, max_gap_s: float = 1.5) -> list[DraftSegment]` (`DraftSegment(start_s, end_s, speaker, text, source, dictated=False)`; junta palavras consecutivas do mesmo falante; quebra em pausa > `max_gap_s`).
  - `drop_echo(mic_segments, pc_segments, window_s: float = 1.0, min_ratio: float = 0.6) -> list[DraftSegment]`: remove do microfone segmentos que se sobrepõem (±`window_s`) a um segmento do PC com texto semelhante (`difflib.SequenceMatcher` em minúsculas sem pontuação ≥ `min_ratio`).
  - `mark_dictated(segments, spans: list[tuple[float, float]]) -> list[DraftSegment]` (`dictated=True` quando ≥50% do segmento cai num trecho ditado; só para `source == "mic"`).
  - `merge_tracks(mic, pc) -> list[DraftSegment]` (ordenado por `start_s`).

- [x] **Step 1: Testes que falham** com valores explícitos, por exemplo:

```python
def test_choose_cut_prefers_longest_silence():
    speech = [(0, 20), (21, 35), (38, 50), (50.5, 70)]
    assert choose_cut(speech, start_s=0, available_s=80) == 38.0

def test_drop_echo_removes_mic_copy_of_pc_speech():
    pc = [DraftSegment(10.0, 13.0, "SPEAKER_00", "o endpoint está retornando erro", "pc")]
    mic = [DraftSegment(10.2, 13.1, "me", "o endpoint tá retornando erro", "mic"),
           DraftSegment(20.0, 22.0, "me", "beleza, vou olhar", "mic")]
    assert [s.text for s in drop_echo(mic, pc)] == ["beleza, vou olhar"]
```

  Demais: `test_choose_cut_forces_cut_at_max_len`, `test_choose_cut_none_when_not_enough_audio`, `test_assign_words_by_overlap`, `test_group_segments_splits_on_speaker_and_gap`, `test_mark_dictated_only_mic`, `test_merge_tracks_orders_by_time`.
- [x] **Step 2:** Rodar → FAIL. **Step 3:** Implementar. **Step 4:** Rodar → PASS.
- [x] **Step 5: Commit** — `[Feat] Montar linha do tempo da reunião com remoção de eco`

---

### Task 9: Vozes — casamento e cadastro

**Files:**
- Create: `src/whisper_ditado/meeting/voices.py`, `tests/meeting/test_voices.py`

**Interfaces:**
- Consumes: `store.Voice` (Task 2 — dataclass)
- Produces:
  - `cosine(a, b) -> float`
  - `match_speakers(speaker_embs: dict[str, np.ndarray], voices: list[Voice], threshold: float) -> dict[str, str]` (speaker → voice_id; guloso pela maior similaridade, cada voz no máximo uma vez, só acima do limiar).
  - `enroll(voice: Voice | None, name: str, emb: np.ndarray, source_meeting_id: str, source_speaker_id: str) -> Voice` (nova voz: `n_samples=1`; voz existente: média ponderada `(emb_antigo*n + emb_novo)/(n+1)`, normalizada, `n_samples+1`, `updated_at` agora).

- [x] **Step 1: Testes que falham:** `test_match_assigns_each_voice_once`, `test_match_respects_threshold`, `test_enroll_new_voice`, `test_enroll_updates_running_mean`.
- [x] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS.
- [x] **Step 5: Commit** — `[Feat] Reconhecer e cadastrar vozes por embedding`

---

### Task 10: Diarização e transcrição incremental

**Files:**
- Create: `src/whisper_ditado/meeting/diarize.py`, `meeting/incremental.py`, `tests/meeting/test_incremental.py`, `tests/meeting/test_diarize.py`

**Interfaces:**
- Consumes: `Transcriber.transcribe_words` (Task 3), `choose_cut`, `Turn` (Task 8), `capture.read_pcm` (Task 7 — se ainda não existir, ler o `.raw` direto com `np.fromfile` no mesmo formato)
- Produces:
  - `diarize.Diarizer(hf_token: str | None)`: `available -> bool` (token presente e pipeline carregável); `run(audio: np.ndarray) -> DiarizationResult(turns: list[Turn], embeddings: dict[str, np.ndarray])`. Pipeline `pyannote/speaker-diarization-community-1` (pyannote.audio 4.x) na GPU (`pipeline.to(torch.device("cuda"))`), recebendo `{"waveform": torch.from_numpy(audio)[None], "sample_rate": 16000}`; turnos de `exclusive_speaker_diarization`; embeddings de `speaker_embeddings`. Carregamento preguiçoso no primeiro uso; mantém carregado. Áudio vazio/sem fala → `DiarizationResult([], {})`.
  - `incremental.IncrementalTranscriber(transcriber: Transcriber, vad: Callable[[np.ndarray], list[tuple[float,float]]])` com `feed(track: str, raw_path: Path, available_s: float) -> list[Word]` (transcreve o próximo bloco quando `choose_cut` decidir; guarda `processed_until[track]`), `flush(track, raw_path, end_s) -> list[Word]` (resto), `words[track] -> list[Word]` acumulado. VAD: `faster_whisper.vad.get_speech_timestamps` com `VadOptions` padrão, convertido para segundos.

- [x] **Step 1: Testes que falham**
  - `test_incremental_cuts_at_silence_and_accumulates` (Transcriber falso que devolve uma palavra por bloco; verifica os cortes e `processed_until`)
  - `test_flush_transcribes_remainder`
  - `test_diarizer_unavailable_without_token`
  - `gpu`: `test_diarizer_separates_two_synthetic_voices` — concatenar `synth(..., voice="pt-br")` e `synth(..., voice="pt-br+f3")` (variante feminina do espeak) alternados 4× com pausas de 0,8 s → ≥2 falantes, e os turnos alternam no tempo certo (±1 s). Pular com `skip` se não houver token (`HF_TOKEN` no ambiente ou `hf_token` salvo).
- [x] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** `uv run pytest tests/meeting -v` → PASS; `uv run pytest -m gpu tests/meeting/test_diarize.py -v` → PASS (com token).
- [x] **Step 5: Commit** — `[Feat] Transcrever por blocos e separar vozes com pyannote`

---

### Task 11: Sessão de reunião, finalização e recuperação

**Files:**
- Create: `src/whisper_ditado/meeting/session.py`, `meeting/finalize.py`, `tests/meeting/test_session.py`, `tests/meeting/test_finalize.py`

**Interfaces:**
- Consumes: `Store`, `Settings`, `EventBus`, `pwgraph`, `HealthMonitor`, `TrackRecorder`, `EchoCanceller`, `IncrementalTranscriber`, `Diarizer`, `timeline.*`, `voices.match_speakers`, `ClaudeCli.title` (Task 12 — receber como callable opcional `title_fn: Callable[[str], str] | None`)
- Produces:
  - `session.MeetingManager(store, settings, bus, meeting_transcriber_factory, diarizer, title_fn)`:
    - `state -> Literal["idle","recording","processing"]`, `current_id`, `started_at`, `followed_app`.
    - `start() -> str` (erro se já gravando): cria a reunião `recording`; decide alvos (`settings mic_device`/`pc_device`/`follow_call_app` + `pick_pc_target`); liga AEC se `aec_enabled`; inicia as duas `TrackRecorder`; tarefa periódica de 100 ms que lê níveis → `HealthMonitor.update` → publica `{"type":"levels","mic_db":..,"pc_db":..}` (10/s) e `{"type":"alert","kind":..,"active":..,"message":<pt-BR>}` nas mudanças; a cada 2 s, `pwgraph.snapshot()` → `call_apps` → `retarget` do PC se necessário (só sem dispositivo fixo) e `mismatch`; sem `mic_device` fixo, se `default_source` mudou → `retarget` do microfone; quando o alvo do PC ou do microfone muda com AEC ligado, reinicia o `EchoCanceller` com os novos dispositivos; a cada 10 s, `IncrementalTranscriber.feed` para as duas trilhas numa thread de trabalho.
    - Mensagens dos alertas: `mic_silent` "Microfone sem sinal"; `pc_silent` "Nenhum som do PC há um tempo"; `pc_wrong_output` "Áudio da chamada não está sendo captado"; `mic_dead` "Microfone desconectado"; `pc_dead` "Captura do áudio do PC parou".
    - `register_dictation_span(start_mono, end_mono)` (ligado ao `DictationService.on_span`) → grava `dictation_spans` em segundos relativos ao início.
    - `monitor_levels(on: bool)` — fora de reunião, abre duas `TrackRecorder` gravando em `/dev/null` (mesmos alvos que uma reunião usaria) só para publicar `levels`; desliga sozinho após 60 s; ignorado durante gravação (os níveis já são publicados).
    - `stop() -> str`: para tudo, grava `alerts` do histórico, muda para `processing`, agenda `finalize` numa thread e publica `{"type":"meeting_updated","meeting_id":..,"status":..}` em cada mudança e `{"type":"progress","meeting_id":..,"stage":"transcrevendo"|"separando vozes"|"finalizando"}`.
  - `finalize.finalize(meeting_id, store, settings, transcriber, diarizer, words: dict[str, list[Word]] | None, title_fn) -> None`:
    1. completa a transcrição incremental (ou transcreve tudo, se `words is None` — usado em recuperação/reprocessamento);
    2. diarização do `pc.raw` se `diarizer.available`; senão um único falante "Participantes" e alerta `no_diarization`;
    3. `match_speakers` com `voice_match_threshold` → rótulos (nome da voz ou "Pessoa N" na ordem de primeira fala); falante "Você" (`is_me=1`, `color_index=0`) para o microfone;
    4. `assign_words` → `group_segments` → `drop_echo` → `mark_dictated` → `merge_tracks`; substituições do vocabulário aplicadas ao texto de cada segmento;
    5. `replace_transcript`; `ffmpeg` converte `mic.raw`/`pc.raw` → `mic.opus`/`pc.opus` (`-f s16le -ar 16000 -ac 1 -i X -c:a libopus -b:a 16k`) e apaga os `.raw` só depois de converter com sucesso;
    6. título: se `auto_title` e `title_fn`, título do Claude (erros são ignorados com log); senão "Reunião HH:MM";
    7. status `ready` (ou `failed` com `error` em pt-BR em qualquer exceção, sem apagar o áudio).
  - `finalize.recover(store, ...)`: no início do backend, reuniões em `recording` ou `processing` cujos `.raw` existem são finalizadas com `words=None`.
  - `finalize.reprocess(meeting_id, ...)`: decodifica `mic.opus`/`pc.opus` com `ffmpeg` para PCM em memória e roda `finalize` de novo (preserva título editado e resumo; refaz falantes).

- [x] **Step 1: Testes que falham** (TrackRecorder/pwgraph/diarizer/transcriber falsos; `ffmpeg` real):
  - `test_start_stop_creates_meeting_and_publishes_events`
  - `test_alerts_are_published_and_saved` (relógio falso; microfone mudo 16 s → evento `alert mic_silent active True` e linha em `alerts`)
  - `test_pc_track_follows_call_app` (snapshot falso com Firefox tocando no HDMI → `retarget("alsa_output.pci-0000_07_00.1.hdmi-stereo")`, `followed_app="Firefox"`)
  - `test_finalize_builds_transcript_with_me_and_people` (palavras e turnos falsos → "Você", "Pessoa 1", "Pessoa 2"; voz cadastrada acima do limiar vira o nome)
  - `test_finalize_with_silent_pc_track` (Review Focus 4)
  - `test_finalize_without_hf_token_uses_single_speaker` (Review Focus 5)
  - `test_finalize_marks_dictated_segments`
  - `test_finalize_failure_sets_failed_and_keeps_audio`
  - `test_startup_recovers_orphan_recording` (Review Focus 1: reunião `recording` + `.raw` em disco → `recover` deixa `ready` com segmentos)
  - `gpu`, `test_end_to_end_with_synthetic_tracks`: gravar dois `.raw` com `synth` (microfone: uma frase; PC: duas vozes alternadas) e rodar `finalize` real → segmentos de "Você" e de ≥1 pessoa, texto contém "deploy".
- [x] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS (sem e com `-m gpu`).
- [x] **Step 5: Commit** — `[Feat] Orquestrar gravação e finalização das reuniões`

---

### Task 12: Integração com o `claude` CLI

**Files:**
- Create: `src/whisper_ditado/claude_cli.py`, `tests/test_claude_cli.py`, `tests/fixtures/fake_claude.py`

**Interfaces:**
- Produces:
  - `claude_cli.ClaudeCli(bin_path: str | None, model: str | None, timeout_s: float = 180)`; `bin_path=None` resolve por `shutil.which("claude")` e depois `~/.local/bin/claude` (o serviço systemd pode não ter `~/.local/bin` no `PATH`).
  - `ClaudeError(Exception)` com mensagem em pt-BR: "Claude Code não encontrado", "Claude Code não está logado — rode `claude` no terminal", "O Claude demorou demais para responder", "Resposta inesperada do Claude".
  - Execução: `[bin, "-p", "--output-format", "json", "--no-session-persistence", "--allowedTools", "", (--model X)]` com o prompt no **stdin**; lê `result` do JSON de saída; `is_error` verdadeiro → `ClaudeError`.
  - `transcript_text(segments, speakers) -> str` (linhas `[mm:ss] Nome: texto`; trechos ditados com o sufixo " (ditado)").
  - `summarize(transcript: str, prompt: str) -> str` (Markdown).
  - `title(transcript: str) -> str` (pede no máximo 60 caracteres, sem aspas; corta em 80).
  - `suggest_names(transcript: str, unknown_labels: list[str]) -> list[NameSuggestion]` com `NameSuggestion(label: str, name: str, evidence: str)`: pede JSON `[{"label":..,"name":..,"evidence":..}]`, aceita só rótulos pedidos, extrai o primeiro array JSON da resposta; pede para **não** sugerir quando não houver evidência explícita.

- [x] **Step 1:** `tests/fixtures/fake_claude.py`: script executável que lê o stdin e responde conforme a variável `FAKE_CLAUDE_MODE` (`ok`, `not_logged`, `slow`, `garbage`, `names`).
- [x] **Step 2: Testes que falham:** `test_summarize_returns_result`, `test_command_line_has_no_tools_and_no_persistence`, `test_claude_errors_are_reported` (Review Focus 3: binário inexistente, deslogado, timeout de 1 s com modo `slow`, lixo), `test_suggest_names_filters_unknown_labels`, `test_transcript_text_format`.
- [x] **Step 3:** FAIL. **Step 4:** Implementar. **Step 5:** PASS.
- [x] **Step 6:** Verificação real única: `uv run python -c "from whisper_ditado.claude_cli import ClaudeCli; print(ClaudeCli(None,None).title('[00:01] Você: vamos alinhar o deploy da NFSe de Americana'))"` → um título curto em pt-BR.
- [x] **Step 7: Commit** — `[Feat] Resumir e sugerir nomes com o Claude Code CLI`

---

### Task 13: API local (REST + WebSocket)

**Files:**
- Create: `src/whisper_ditado/api.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: tudo acima (injetado num `AppContext` dataclass: `store, settings, bus, dictation, meetings, claude, diarizer, transcriber_factory`)
- Produces — **contrato consumido pelo app** (todas as rotas sob `/api`, JSON, erros `{"detail": "<mensagem pt-BR>"}`):
  - Auth: `Authorization: Bearer <token>` em REST; WebSocket `/api/events?token=<token>`. Sem token/errado → 401 / fecha com código 4401.
  - CORS: liberar só as origens do app Tauri — `tauri://localhost`, `http://tauri.localhost` e `http://localhost:1420` (dev) — com o header `Authorization`.
  - `GET /status` → `{"dictation":{"enabled":bool,"active":bool,"key":str},"meeting":{"state":"idle|recording|processing","meeting_id":str|null,"started_at":iso|null,"followed_app":str|null,"alerts":[kind]}}`
  - `POST /meetings/start` → `{"meeting_id"}` (409 se já gravando) · `POST /meetings/stop` → `{"meeting_id"}` (409 se não gravando)
  - `GET /meetings?q=` → `[{"id","title","started_at","duration_s","speakers_count","status"}]`
  - `GET /meetings/{id}` → `{"id","title","started_at","ended_at","status","error","followed_app","speakers":[{"id","label","voice_id","is_me","color_index"}],"segments":[{"id","speaker_id","source","start_s","end_s","text","dictated"}],"summary":str|null,"alerts":[{"kind","start_s","end_s"}]}`
  - `PATCH /meetings/{id}` `{"title"}` · `DELETE /meetings/{id}` (apaga a pasta de áudio também) · `POST /meetings/{id}/reprocess`
  - `GET /meetings/{id}/audio?start=&end=` → `audio/ogg` com as duas trilhas mixadas (`ffmpeg ... -filter_complex amix=inputs=2:normalize=0`), recorte opcional em segundos
  - `GET /meetings/{id}/export.md` → `text/markdown` (título, data, participantes, resumo se houver, transcrição no formato de `transcript_text`)
  - `POST /meetings/{id}/summary` → `{"summary"}` (usa `summary_prompt`; salva) · `POST /meetings/{id}/name-suggestions` → `[{"speaker_id","label","name","evidence"}]` (só para falantes sem voz e não `is_me`)
  - `PATCH /speakers/{id}` `{"label"}` (renomeia só nesta reunião) · `POST /speakers/{id}/merge` `{"into_speaker_id"}` · `POST /speakers/{id}/enroll` `{"name"}` ou `{"voice_id"}` → voz (usa o `embedding` do falante; 400 se o falante não tiver embedding)
  - `GET /voices` → `[{"id","name","n_samples","created_at","updated_at"}]` · `PATCH /voices/{id}` `{"name"}` · `DELETE /voices/{id}`
  - `GET /settings` → `Settings.as_dict()` · `PUT /settings` (parcial) → dict atualizado (400 com a mensagem de validação); aplica no ditado via `dictation.apply_settings()`; `hf_token` aceito na escrita
  - `GET /vocab` / `PUT /vocab` `{"text"}` · `GET /subs` / `PUT /subs` `{"text"}`
  - `GET /devices` → `{"sources":[{"name","description"}],"sinks":[...],"default_source","default_sink"}`
  - `POST /levels/monitor` `{"on":bool}` → liga/desliga a medição de níveis fora de reunião (para a tela de configurações; desliga sozinho após 60 s)
  - WebSocket `/events`: eventos `status` (mesmo formato do `GET /status`, enviado ao conectar e a cada mudança), `dictation`, `levels`, `alert`, `meeting_updated`, `progress`.
  - `api.write_api_info(port, token)` grava `paths.api_info_path()` com `{"port":int,"token":str,"pid":int}` em `0600`.

- [x] **Step 1: Testes que falham** (`fastapi.testclient.TestClient`, contexto com fakes): `test_requires_token`, `test_cors_allows_only_tauri_origins`, `test_start_stop_conflicts`, `test_meeting_detail_shape`, `test_search`, `test_rename_merge_enroll`, `test_settings_validation_error_is_400_in_ptbr`, `test_websocket_receives_status_then_events`, `test_export_markdown`, `test_api_info_file_is_private`.
- [x] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS.
- [x] **Step 5: Commit** — `[Feat] Expor API local do backend para o app`

---

### Task 14: Entrypoint, serviço (desligado) e verificação ponta a ponta

**Files:**
- Create: `src/whisper_ditado/main.py`, `run-backend.sh`, `whisper-ditado-backend.service`
- Modify: `README.md` (seção "Backend novo (em desenvolvimento)")

**Interfaces:**
- Consumes: tudo acima
- Produces: `python -m whisper_ditado.main [--port 47321] [--no-dictation]`; `run-backend.sh` (igual ao `run.sh`, ajusta `LD_LIBRARY_PATH` e executa `-m whisper_ditado.main "$@"`); unit `whisper-ditado-backend.service` (mesmas diretivas do atual; `ExecStart=%h/dev/whisper-ditado/run-backend.sh`) **não instalada nem habilitada** — a troca é a última task do plano do app.

- [x] **Step 1:** `main.py`: carrega `Store`, `Settings`, `EventBus`; Transcriber do ditado (modelo carregado e aquecido); fábrica preguiçosa do Transcriber das reuniões (segunda instância, carregada ao iniciar a primeira reunião); `Diarizer`; `ClaudeCli`; `recover(...)`; `DictationService` (a menos de `--no-dictation`); uvicorn em `127.0.0.1:<port>` no mesmo loop (`uvicorn.Server(config).serve()`), token `secrets.token_urlsafe(32)`, `write_api_info`; SIGTERM encerra gravação em andamento chamando `stop()` (a finalização roda no próximo início via `recover`).
- [x] **Step 2: Verificação ponta a ponta sem som audível** (o serviço atual continua rodando; usar `--no-dictation` para não disputar o teclado):
  1. `./run-backend.sh --no-dictation --port 47399 &`
  2. `PUT /api/settings {"pc_device":"obs_mudo","follow_call_app":false,"aec_enabled":false}` (saída virtual muda desta máquina — nada sai na caixa de som)
  3. `POST /api/meetings/start`; tocar `tests/helpers/tts.py` gerando duas vozes alternadas em `pw-play --target obs_mudo` (~40 s); `POST /api/meetings/stop`
  4. Aguardar `status=ready` em `GET /api/meetings/{id}`
  Expected: segmentos do PC com texto reconhecível e ≥1 falante; `pc.opus` existe e `pc.raw` não; `GET /api/meetings/{id}/export.md` contém o texto. Microfone: "Você" pode estar vazio (sem fala) — sem erro.
  5. Matar o processo com `kill -9` durante uma nova gravação de 20 s, subir de novo → a reunião é recuperada (`ready`).
- [x] **Step 3:** Rodar a suíte inteira: `uv run pytest -v` e `uv run pytest -m gpu -v` → PASS.
- [x] **Step 4:** README: como rodar o backend novo, onde ficam os dados, como gerar o token do Hugging Face (aceitar os termos de `pyannote/speaker-diarization-community-1` e criar um token de leitura).
- [x] **Step 5: Commit** — `[Feat] Adicionar entrypoint e serviço do backend novo`
