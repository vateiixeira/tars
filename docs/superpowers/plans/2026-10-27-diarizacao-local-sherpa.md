# Separação de falantes local (sherpa-onnx) — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** separar falantes das reuniões logo após instalar, com um motor local grátis (sherpa-onnx, CPU),
mantendo o pyannote (token) como opção, sem perder nem recadastrar vozes.

**Architecture:** dois motores com o mesmo protocolo (`available`, `run`, `embed`, `unload`,
`model_id`, `engine`) atrás de um `DiarizerRouter`, que lê a configuração `diarizer` a cada uso e cai
para o local quando o pyannote não roda. Todo embedding (de falante e de voz) é gravado junto com o
identificador do modelo que o gerou. Uma voz tem um embedding por modelo, em `voice_embeddings` (V6).
O que faltar para o modelo em uso é recalculado a partir do `pc.opus` das reuniões ligadas, antes do
casamento.

**Tech Stack:** Python 3.12, sherpa-onnx 2.0.1 (onnxruntime CPU), numpy, SQLite, pyannote.audio 4 +
torch cu128 (extra opcional), FastAPI, Vue 3 + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-27-diarizacao-local-sherpa-design.md` (ler junto com este
plano; a seção 5 descreve o código atual do `main`).

## Global Constraints

- Branch de trabalho: `diarizacao-local-sherpa`, criada a partir do `main` atual (já contém o spec).
- Textos de log, mensagens de erro e UI em pt-BR. Commits no formato `[Tipo] Título` do CLAUDE.md global,
  com a linha `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `sherpa-onnx>=2.0.1,<3` obrigatório. `pyannote.audio>=4.0,<5`, `torch` e `torchaudio` vão para o extra
  `pyannote` (`uv sync --extra pyannote`), mantendo o índice `pytorch-cu128`.
- O `.venv` deste checkout pode ser o mesmo que o backend do usuário usa. Sincronize sempre com
  `uv sync --frozen --group dev --extra pyannote`, nunca sem o extra.
- Motor local só em CPU. Ele nunca baixa nada do Hugging Face: modelos só das releases do GitHub do
  k2-fsa, com SHA-256 conferido, via arquivo temporário + `os.replace`.
- Identificadores de modelo: `pyannote-community-1` e `sherpa-<nome do modelo de embedding sem .onnx>`.
  Nunca comparar embeddings de modelos diferentes. Embedding sem modelo conhecido não é gravado.
- Testes sem GPU e sem rede por padrão: `addopts = "-m 'not gpu and not models'"`. Nenhuma reunião real
  vira fixture, e nenhum teste lê o banco real `~/.local/share/whisper-ditado/ditado.db`.
- Comandos: `uv run --frozen pytest -q` (backend) e `cd app && pnpm test && pnpm build` (app).

## Review Focus

1. **Uma pessoa só no PC (o caso mais comum):** o motor local não pode dividir uma voz em várias
   "Pessoa N" por causa de clusters minúsculos. Teste em Task 4
   (`test_run_merges_small_clusters_into_most_similar`).
2. **Reunião longa (2 h):** o embedding por falante não pode crescer com a duração. Teste em Task 3
   (`test_speech_chunks_caps_total_and_splits_long_spans`).
3. **Voz cujas reuniões perderam o áudio ou têm só trechos curtos:** o recálculo pula a amostra, a voz
   continua com nome e vínculos, e a mesma tentativa não se repete a cada reunião. Testes em Task 8.
4. **pyannote escolhido mas o local separou (fallback):** o limiar usado é o do motor que rodou, e o
   embedding fica marcado com o modelo local. Teste em Task 7 (`test_fallback_uses_local_threshold_and_model`).
5. **Reprocessar quando um falante ligado vira dois clusters:** só um herda a voz, sem rótulo duplicado.
   Teste em Task 9 (`test_inherit_links_is_one_to_one`).

## Ordem e paralelismo

| Grupo | Tasks | Observação |
|---|---|---|
| — | 1 | Dependências e lockfile (sozinha: mexe em `pyproject.toml`/`uv.lock`) |
| A | 2 ∥ 3 | Arquivos disjuntos |
| B | 4 ∥ 5 ∥ 6 | 4: `local_diarize.py`; 5: `diarize.py`; 6: store/settings/voices/finalize/api |
| — | 7 | Roteador, fallback e ligação em `main.py` (depende de 4, 5 e 6) |
| C | (8 → 9) ∥ 10 ∥ 11 | 8 e 9 editam `finalize.py`, então são sequenciais entre si; 10 é só `app/`; 11 edita constantes de `local_diarize.py`/`settings.py`, que 8, 9 e 10 não tocam |
| — | 12 | Documentação e validação final em cópia dos dados reais |

O controller marca as caixas de um grupo paralelo num único commit ao fim do grupo.

---

### Task 1: sherpa-onnx obrigatório, pyannote como extra

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `CONTRIBUTING.md`
- Modify: `tests/meeting/test_diarize.py`
- Create: `tests/meeting/test_diarize_pyannote.py`

**Interfaces:**
- Produces: marcador pytest `models`; extra `pyannote`; testes que importam `pyannote`/`torch` isolados em
  `tests/meeting/test_diarize_pyannote.py` (que começa com `pytest.importorskip("pyannote.audio")`).

- [x] **Step 1: Mover os testes que importam pyannote/torch.** Em `tests/meeting/test_diarize.py` ficam só
  os que não importam pyannote nem torch (`test_diarizer_unavailable_without_token`,
  `test_diarizer_empty_audio_returns_empty_result`, `test_diarizer_unloads_and_reloads_with_current_token`)
  e os fakes que eles usam. Todo o resto (fixture `hub`, `_output`, `_with_pipeline`, os testes `run_*`,
  `test_settings_diarizer_recovers_after_token_change` e o teste `gpu`) vai para
  `tests/meeting/test_diarize_pyannote.py`, com `pytest.importorskip("pyannote.audio")` logo após os imports
  de stdlib.
- [x] **Step 2: `pyproject.toml`.** Tirar `pyannote.audio>=4.0,<5`, `torch` e `torchaudio` de `dependencies`,
  incluir `"sherpa-onnx>=2.0.1,<3"` e criar:
  ```toml
  [project.optional-dependencies]
  pyannote = ["pyannote.audio>=4.0,<5", "torch", "torchaudio"]
  ```
  `[tool.uv.sources]` e `[[tool.uv.index]]` ficam como estão. Em `[tool.pytest.ini_options]`:
  `addopts = "-m 'not gpu and not models'"` e o marcador
  `"models: testes que baixam e usam os modelos reais do sherpa-onnx (rodar com -m models)"`.
- [x] **Step 3: Travar.** Run: `uv lock && uv lock --check`. Expected: `sherpa-onnx 2.0.1` e
  `sherpa-onnx-core 2.0.1` no lock, torch só atrás do extra.
- [x] **Step 4: Conferir que o padrão não traz torch.**
  Run: `uv export --frozen --no-dev --no-hashes | grep -cE '^(torch|torchaudio|pyannote-audio)=='`
  Expected: `0`. Run: `uv export --frozen --no-dev --no-hashes --extra pyannote | grep -cE '^torch=='`
  Expected: `1`.
- [x] **Step 5: CI.** Em `.github/workflows/ci.yml` (job `backend`): trocar a sincronização por
  `uv sync --frozen --group dev --extra pyannote --python 3.12` e acrescentar, antes dela, o passo
  `name: Instalação padrão sem torch` com
  `run: "! uv export --frozen --no-dev --no-hashes | grep -E '^(torch|torchaudio|pyannote-audio)=='"`.
- [x] **Step 6: `CONTRIBUTING.md`.** Comando de desenvolvimento: `uv sync --frozen --group dev --extra pyannote`.
  Uma linha dizendo que sem o extra os testes do pyannote são pulados e que `-m models` roda os testes que
  baixam os modelos do sherpa-onnx.
- [x] **Step 7: Rodar com e sem o extra.**
  Run: `uv sync --frozen --group dev --extra pyannote && uv run --frozen pytest -q` → Expected: PASS.
  Run, num venv separado para não mexer no `.venv`:
  `UV_PROJECT_ENVIRONMENT=$(mktemp -d)/venv uv run --frozen --group dev pytest -q tests/meeting/test_diarize.py tests/meeting/test_diarize_pyannote.py`
  Expected: `test_diarize.py` passa e `test_diarize_pyannote.py` aparece como skipped.
- [x] **Step 8: Commit** `[Chore] Tornar o pyannote um extra opcional e exigir o sherpa-onnx`

---

### Task 2: Cache verificado dos modelos locais  *(grupo A, paralela à 3)*

**Files:**
- Modify: `src/whisper_ditado/paths.py`
- Create: `src/whisper_ditado/meeting/sherpa_models.py`
- Test: `tests/meeting/test_sherpa_models.py`, `tests/test_paths.py`

**Interfaces:**
- Produces:
  - `paths.models_dir() -> Path`: `data_dir() / "models" / "sherpa-onnx"`, criado com 0700 (mesmo padrão de
    `meeting_dir`).
  - `@dataclass(frozen=True) class ModelFile: name: str; url: str; sha256: str; member: str | None = None; member_sha256: str | None = None`.
    `name` é o arquivo final no cache. `sha256` é o hash do arquivo baixado. `member` é o caminho do arquivo
    dentro de um `.tar.bz2`, e `member_sha256` é o hash desse arquivo extraído.
  - `SEGMENTATION: ModelFile`, `EMBEDDINGS: dict[str, ModelFile]` (chave = nome sem `.onnx`).
  - `class ModelDownloadError(RuntimeError)`.
  - `ensure_model(model: ModelFile, cache_dir: Path, *, timeout_s: float = 60.0) -> Path`.

Valores fixos (baixados e conferidos em 2026-10-27; base
`https://github.com/k2-fsa/sherpa-onnx/releases/download`; o tag `speaker-recongition-models` tem mesmo
esse erro de grafia):

| Chave / arquivo final | URL (após a base) | sha256 do download | membro / sha256 do membro |
|---|---|---|---|
| `SEGMENTATION` → `pyannote-segmentation-3-0.onnx` | `/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2` | `24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488` | `sherpa-onnx-pyannote-segmentation-3-0/model.onnx` / `220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079` |
| `wespeaker_en_voxceleb_resnet34_LM` | `/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx` | `e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012` | — |
| `wespeaker_en_voxceleb_CAM++` | `/speaker-recongition-models/wespeaker_en_voxceleb_CAM++.onnx` | `c46fad10b5f81e1aa4a60c162714208577093655076c5450f8c469e522ec54ef` | — |
| `3dspeaker_speech_campplus_sv_en_voxceleb_16k` | `/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx` | `357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b` | — |
| `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k` | `/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` | `1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b` | — |
| `3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common` | `/speaker-recongition-models/3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx` | `bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239` | — |
| `nemo_en_titanet_small` | `/speaker-recongition-models/nemo_en_titanet_small.onnx` | `ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e` | — |

Comportamento de `ensure_model`:
1. Se `cache_dir/name` existe e o SHA-256 bate (`member_sha256` quando há membro, senão `sha256`), devolve o
   caminho sem rede.
2. Se não existe ou o hash não bate, registra `log.warning("modelo %s ausente ou corrompido no cache; baixando", name)`
   e baixa com `urllib.request.urlopen(url, timeout=timeout_s)` em blocos de 1 MiB para
   `name + ".download.part"`, calculando o hash ao mesmo tempo.
3. Hash do download diferente: `ModelDownloadError(f"hash inválido ao baixar {name}")`.
4. Com `member`: abre o `.tar.bz2` com `tarfile.open(..., "r:bz2")` e confere que `getmember(member).isfile()`.
   Extrai só esse membro via `extractfile` para `name + ".part"` (nunca `extractall`) e confere o
   `member_sha256`.
5. `os.replace(part, final)`. Em qualquer falha, apaga os `.part` e levanta
   `ModelDownloadError(f"falha ao baixar {name}: {exc}")` encadeada.

- [x] **Step 1: Testes que falham.** Os testes usam URLs `file://`, que o `urlopen` aceita (`Path.as_uri()`):
  - `test_models_dir_is_private` (em `tests/test_paths.py`, com a fixture `data_home`): o caminho termina em
    `models/sherpa-onnx` e `stat().st_mode & 0o777 == 0o700`.
  - `test_ensure_model_downloads_verifies_and_reuses_cache`: arquivo `src.onnx` com `b"abc"` e `ModelFile`
    com o sha256 dele. O primeiro `ensure_model` devolve `cache/"m.onnx"` com o conteúdo `b"abc"`. Depois de
    apagar `src.onnx`, o segundo `ensure_model` ainda devolve o mesmo caminho (não foi à rede), e não sobra
    nenhum `*.part`.
  - `test_ensure_model_rejects_wrong_hash_and_cleans_up`: com sha256 errado → `ModelDownloadError`; o
    arquivo final não existe e não sobra `*.part`.
  - `test_ensure_model_redownloads_corrupted_cache`: `cache/"m.onnx"` com `b"lixo"` → é substituído por `b"abc"`.
  - `test_ensure_model_extracts_only_the_member_from_tar_bz2`: `.tar.bz2` com `pkg/model.onnx` (`b"seg"`) e
    `pkg/README.md`. O resultado é `cache/"seg.onnx"` com `b"seg"`, e o README não aparece no cache.
  - `test_ensure_model_rejects_member_with_wrong_hash`: `member_sha256` errado → `ModelDownloadError`, sem
    arquivo final.
  - `test_registry_urls_and_hashes_are_pinned`: todos os `url` começam com a base acima, todos os sha256 têm
    64 caracteres hexadecimais e `SEGMENTATION.member` termina em `/model.onnx`.
- [x] **Step 2:** Run: `uv run --frozen pytest -q tests/meeting/test_sherpa_models.py tests/test_paths.py`.
  Expected: FAIL (`ModuleNotFoundError: whisper_ditado.meeting.sherpa_models`).
- [x] **Step 3:** Implementar `paths.models_dir` e `meeting/sherpa_models.py` conforme acima.
- [x] **Step 4:** Rodar o mesmo comando. Expected: PASS.
- [x] **Step 5: Commit** `[Feat] Baixar modelos do sherpa-onnx com verificação de hash`

---

### Task 3: Trechos de fala e resultado marcado com o modelo  *(grupo A, paralela à 2)*

**Files:**
- Create: `src/whisper_ditado/meeting/speech.py`
- Modify: `src/whisper_ditado/meeting/diarize.py` (só `DiarizationResult`, constantes e `_usable` → `speech.usable`)
- Test: `tests/meeting/test_speech.py`, `tests/meeting/test_diarize.py`

**Interfaces:**
- Produces (`meeting/speech.py`):
  - `SAMPLE_RATE = 16000`, `MIN_SPAN_S = 0.5`, `CHUNK_S = 10.0`, `MAX_EMBED_S = 120.0`, `MIN_EMBED_S = 1.5`
  - `usable(emb) -> np.ndarray | None` (o atual `_usable` de `diarize.py`, movido sem mudança; `diarize.py`
    passa a importá-lo).
  - `speech_chunks(audio: np.ndarray, spans: Sequence[tuple[float, float]], *, sample_rate=SAMPLE_RATE, min_span_s=MIN_SPAN_S, chunk_s=CHUNK_S, max_total_s=MAX_EMBED_S) -> list[np.ndarray]`.
    Corta os trechos aos limites do áudio e descarta os menores que `min_span_s`. Ordena por duração
    (maior primeiro) e acumula até `max_total_s`, truncando o último. Divide cada trecho em pedaços de no
    máximo `chunk_s` e descarta sobras menores que `min_span_s`.
  - `mean_embedding(chunks: Sequence[np.ndarray], embed_chunk: Callable[[np.ndarray], np.ndarray], *, sample_rate=SAMPLE_RATE, min_total_s=MIN_EMBED_S) -> np.ndarray | None`.
    Devolve `None` se a soma dos pedaços for menor que `min_total_s` ou se nenhum vetor passar em `usable`.
    Senão, faz a média dos vetores normalizados, ponderada pelo número de amostras, e devolve normalizada,
    em float32.
- Produces (`meeting/diarize.py`):
  - `PYANNOTE_MODEL = "pyannote-community-1"`
  - `Embed = Callable[[np.ndarray, Sequence[tuple[float, float]]], np.ndarray | None]`
  - ```python
    @dataclass
    class DiarizationResult:
        turns: list[Turn] = field(default_factory=list)
        embeddings: dict[str, np.ndarray] = field(default_factory=dict)
        model: str = ""          # id do modelo dos embeddings ("" = desconhecido: não grava nem casa)
        engine: str = ""         # "local" | "pyannote" | ""
        fallback: str | None = None   # tipo do alerta de fallback (Task 7)
        embed: Embed | None = field(default=None, compare=False, repr=False)
    ```

- [x] **Step 1: Testes que falham** em `tests/meeting/test_speech.py`:
  - `test_speech_chunks_drops_short_and_clips_to_audio`: áudio de 5 s, trechos `[(0.0, 0.3), (1.0, 2.0), (4.5, 9.0)]`
    → pedaços com durações `[1.0, 0.5]` (o primeiro é curto demais e o último foi cortado em 5 s).
  - `test_speech_chunks_caps_total_and_splits_long_spans`: áudio de 600 s, trechos `[(0, 300), (300, 310)]`.
    Com `max_total_s=25.0, chunk_s=10.0`, as durações são `[10.0, 10.0, 5.0]` (cada uma ≤ 10 s, soma 25).
  - `test_mean_embedding_weights_by_length_and_normalizes`: pedaços de 1 s e 3 s, com `embed_chunk` devolvendo
    `[1,0]` para o de 1 s e `[0,5]` para o de 3 s. O resultado é `normalize([0.25, 0.75])`, com norma 1 e
    dtype float32.
  - `test_mean_embedding_none_when_too_short_or_unusable`: total de 1.0 s com `min_total_s=1.5` → `None`;
    todos os vetores zerados → `None`.
  - Em `tests/meeting/test_diarize.py`: `test_result_defaults_and_embed_not_compared`:
    `DiarizationResult([], {}) == DiarizationResult([], {}, embed=lambda a, s: None)` e
    `DiarizationResult().model == ""`.
- [x] **Step 2:** Run: `uv run --frozen pytest -q tests/meeting/test_speech.py tests/meeting/test_diarize.py`.
  Expected: FAIL.
- [x] **Step 3:** Implementar conforme as interfaces.
- [x] **Step 4:** Run: `uv run --frozen pytest -q`. Expected: PASS (a suíte inteira continua verde).
- [x] **Step 5: Commit** `[Feat] Extrair trechos de fala e marcar o resultado com o modelo`

---

### Task 4: Motor local (sherpa-onnx)  *(grupo B, paralela à 5 e à 6)*

**Files:**
- Create: `src/whisper_ditado/meeting/local_diarize.py`
- Test: `tests/meeting/test_local_diarize.py`

**Interfaces:**
- Consumes: `sherpa_models.SEGMENTATION`, `EMBEDDINGS`, `ensure_model`, `paths.models_dir()` (Task 2);
  `speech.speech_chunks`, `speech.mean_embedding`, `DiarizationResult`, `Turn` (Task 3).
- Produces:
  - Constantes **provisórias**, que a Task 11 calibra: `DEFAULT_EMBEDDING = "wespeaker_en_voxceleb_resnet34_LM"`,
    `DEFAULT_CLUSTER_THRESHOLD = 0.9`, `MIN_SPEAKER_S = 8.0`. Também `RETRY_AFTER_S = 300.0` e `ENGINE = "local"`.
  - `default_threads() -> int`: `max(1, min(8, (os.cpu_count() or 2) // 2))`.
  - `class SherpaBackend(Protocol): def segments(self, audio: np.ndarray) -> list[tuple[float, float, int]]: ...; def embed_chunk(self, chunk: np.ndarray) -> np.ndarray: ...`
  - `load_backend(segmentation: Path, embedding: Path, cluster_threshold: float, num_threads: int) -> SherpaBackend`:
    a implementação real. Usa `OfflineSpeakerDiarization` com
    `OfflineSpeakerDiarizationConfig(segmentation=OfflineSpeakerSegmentationModelConfig(pyannote=OfflineSpeakerSegmentationPyannoteModelConfig(model=...), num_threads=n), embedding=SpeakerEmbeddingExtractorConfig(model=..., num_threads=n), clustering=FastClusteringConfig(num_clusters=-1, threshold=cluster_threshold))`.
    Chama `config.validate()`, e falso → `RuntimeError`. `segments` faz
    `process(audio).sort_by_start_time()` → `(r.start, r.end, r.speaker)`. Para `embed_chunk`, cria um
    `SpeakerEmbeddingExtractor` separado: `create_stream()`, `accept_waveform(16000, chunk)`,
    `input_finished()`, `compute(stream)`.
  - ```python
    class LocalDiarizer:
        engine = "local"
        def __init__(self, *, embedding: str = DEFAULT_EMBEDDING, cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
                     min_speaker_s: float = MIN_SPEAKER_S, num_threads: int | None = None,
                     loader: Callable[[], SherpaBackend] | None = None,
                     clock: Callable[[], float] = time.monotonic) -> None
        model_id: str                      # property: f"sherpa-{embedding}"
        available: bool                    # property: carrega na 1ª vez (baixa se preciso)
        last_error: str | None             # property: texto da última falha de carga (None se carregou)
        def run(self, audio: np.ndarray) -> DiarizationResult
        def embed(self, audio: np.ndarray, spans: Sequence[tuple[float, float]]) -> np.ndarray | None
        def unload(self) -> None
    ```
    O loader padrão chama `ensure_model(SEGMENTATION, models_dir())`, `ensure_model(EMBEDDINGS[embedding], models_dir())`
    e depois `load_backend`. Carga preguiçosa sob `threading.Lock`; uma falha registra
    `log.warning("separação local indisponível: %s", exc)` e só tenta de novo depois de `RETRY_AFTER_S`
    (mesmo padrão de `Diarizer._may_try`, sem a parte do token). `run` e `embed` usam o mesmo lock.

Algoritmo de `run(audio)`:
1. Áudio vazio → `DiarizationResult(model=..., engine="local", embed=self.embed)`. Motor indisponível →
   `RuntimeError("separação local indisponível")`.
2. `segs = backend.segments(audio)`; agrupa os trechos por cluster e soma a duração de cada um.
3. Calcula o embedding de cada cluster com `mean_embedding(speech_chunks(audio, trechos), backend.embed_chunk)`.
4. Os "grandes" são os clusters com duração ≥ `min_speaker_s`. Se nenhum for grande, fica só o de maior
   duração. Cada cluster pequeno vai para o grande de maior cosseno; se ele ou os grandes não tiverem
   embedding, vai para o grande de maior duração.
5. Rótulos `SPEAKER_00`, `SPEAKER_01`… na ordem do primeiro início; `turns` ordenados por início.
6. Recalcula o embedding de cada rótulo final sobre todos os seus trechos; só os não `None` entram em
   `embeddings`.
7. Devolve `DiarizationResult(turns, embeddings, model=self.model_id, engine="local", embed=self.embed)`.

- [x] **Step 1: Testes que falham** em `tests/meeting/test_local_diarize.py`, com um `FakeBackend(segments, vectors_by_amp)`
  cujo `embed_chunk` devolve o vetor pela amplitude do pedaço (mesma ideia do `FakeTranscriber`):
  - `test_run_labels_by_first_appearance_and_tags_model`: dois clusters longos (ids 1 e 0, com o 1 primeiro).
    Os rótulos são `SPEAKER_00` para o cluster 1 e `SPEAKER_01` para o 0; `result.model == "sherpa-wespeaker_en_voxceleb_resnet34_LM"`,
    `result.engine == "local"` e `result.embed is not None`.
  - `test_run_merges_small_clusters_into_most_similar`: clusters A (30 s, vetor `[1,0]`), B (30 s, `[0,1]`) e
    C (2 s, `[0.1, 0.9]`). C vai para B, e o resultado tem 2 falantes.
  - `test_run_single_voice_split_into_tiny_clusters_becomes_one_speaker`: três clusters de 3 s, 2 s e 1 s
    (todos < 8 s) → um único rótulo `SPEAKER_00` cobrindo todos os trechos.
  - `test_run_empty_audio_and_unavailable`: áudio vazio → `turns == []` e `model` preenchido. Com um loader
    que levanta: `available is False`, `last_error` contém a mensagem e `run` levanta `RuntimeError`.
  - `test_load_failure_retries_after_delay`: loader falha uma vez. Com relógio falso, `available` continua
    `False` aos +10 s (sem chamar o loader de novo) e vira `True` aos +301 s; o loader foi chamado 2 vezes.
  - `test_embed_uses_speech_chunks_and_unload_releases`: `embed` com um trecho de 0.3 s → `None`. `unload()`
    seguido de `available` chama o loader de novo.
  - `@pytest.mark.models test_local_diarizer_separates_two_synthetic_voices`: igual ao teste `gpu` do pyannote
    (`tests.helpers.tts.synth`, vozes `pt-br` e `pt-br+f3`), mas com `LocalDiarizer()` real e
    `XDG_DATA_HOME` apontando para `tmp_path`. Espera ≥ 2 falantes e as falas de vozes diferentes em
    rótulos diferentes.
- [x] **Step 2:** Run: `uv run --frozen pytest -q tests/meeting/test_local_diarize.py`. Expected: FAIL (módulo inexistente).
- [x] **Step 3:** Implementar `meeting/local_diarize.py`.
- [x] **Step 4:** Run: `uv run --frozen pytest -q tests/meeting/test_local_diarize.py` → PASS. Depois
  `uv run --frozen pytest -q -m models tests/meeting/test_local_diarize.py` → PASS (baixa ~33 MB uma vez).
- [x] **Step 5: Commit** `[Feat] Adicionar motor local de separação de falantes com sherpa-onnx`

---

### Task 5: pyannote com modelo, motivo de indisponibilidade e `embed`  *(grupo B)*

**Files:**
- Modify: `src/whisper_ditado/meeting/diarize.py`
- Test: `tests/meeting/test_diarize.py`, `tests/meeting/test_diarize_pyannote.py`

**Interfaces:**
- Consumes: `PYANNOTE_MODEL`, `DiarizationResult`, `speech.speech_chunks`, `speech.mean_embedding` (Task 3).
- Produces:
  - Códigos de motivo: `NO_TOKEN = "no_token"`, `MISSING_EXTRA = "missing_extra"`, `LOAD_FAILED = "failed"`.
  - `Diarizer.engine = "pyannote"` e `Diarizer.model_id = PYANNOTE_MODEL` (atributos de classe).
  - `Diarizer.__init__(self, hf_token, *, clock=time.monotonic, device: str = "cuda")`. O `device` é usado
    em `pipeline.to(torch.device(device))` e no `embed`.
  - `Diarizer.unavailable_reason -> str | None` (property): `None` se o pipeline carrega. Senão: sem token →
    `NO_TOKEN`; pyannote não instalado → `MISSING_EXTRA`; falha de carga → `LOAD_FAILED`.
  - `pyannote_installed() -> bool`: `importlib.util.find_spec("pyannote.audio") is not None`, tratando
    `ModuleNotFoundError` como `False`. Com o extra ausente, `_ensure_loaded` devolve `None` sem tentar
    carregar e sem registrar falha. Ele registra uma vez por processo:
    `log.warning("pyannote não instalado; instale com: uv sync --extra pyannote")`.
  - `run` devolve `DiarizationResult(..., model=PYANNOTE_MODEL, engine="pyannote", embed=self.embed)`,
    também com áudio vazio.
  - `embed(audio, spans) -> np.ndarray | None`: carrega ou levanta `RuntimeError("diarização indisponível")`.
    Faz `mean_embedding(speech_chunks(audio, spans), f)`, em que `f(chunk)` é
    `pipeline._embedding(torch.from_numpy(chunk)[None, None].to(device))[0]`, sob o lock.

- [x] **Step 1: Testes que falham.**
  - Em `test_diarize.py` (sem pyannote): `test_unavailable_reason_without_token` (`Diarizer(None).unavailable_reason == "no_token"`)
    e `test_unavailable_reason_missing_extra` (monkeypatch de `diarize.pyannote_installed` → `False`;
    `Diarizer("tok").unavailable_reason == "missing_extra"`, e `_load` não é chamado).
  - Em `test_diarize.py`: `test_load_failure_reason_is_failed` (monkeypatch de `Diarizer._load` levantando
    `OSError` e de `pyannote_installed` → `True`; motivo `"failed"`).
  - Em `test_diarize_pyannote.py`: atualizar as comparações para `DiarizationResult([], {}, model="pyannote-community-1", engine="pyannote")`.
    Novo `test_embed_averages_chunks_with_pipeline_embedding`: `FakePipeline` com `_embedding` que devolve
    `np.array([[1.0, 0.0]])`. `Diarizer("tok", device="cpu")` com `_pipeline` injetado:
    `embed(np.ones(16000*3, np.float32), [(0.0, 3.0)])` dá `[1, 0]`, e `embed(..., [(0.0, 0.2)])` dá `None`.
- [x] **Step 2:** Run: `uv run --frozen pytest -q tests/meeting/test_diarize.py tests/meeting/test_diarize_pyannote.py`. Expected: FAIL.
- [x] **Step 3:** Implementar.
- [x] **Step 4:** Rodar de novo → PASS; `uv run --frozen pytest -q` → PASS.
- [x] **Step 5: Commit** `[Feat] Expor modelo, motivo de indisponibilidade e embed no pyannote`

---

### Task 6: Embeddings por modelo no banco (V6) e configurações por motor  *(grupo B)*

**Files:**
- Modify: `src/whisper_ditado/store.py`, `src/whisper_ditado/settings.py`, `src/whisper_ditado/meeting/voices.py`,
  `src/whisper_ditado/meeting/finalize.py`, `src/whisper_ditado/api.py`
- Test: `tests/test_store.py`, `tests/test_settings.py`, `tests/meeting/test_voices.py`,
  `tests/meeting/test_finalize.py` (incluindo o `FakeDiarizer` compartilhado com `test_session.py`), `tests/test_api.py`

**Interfaces:**
- Consumes: `DiarizationResult.model/engine`, `PYANNOTE_MODEL` (Task 3).
- Produces (`store.py`):
  - `@dataclass class VoiceEmbedding: vector: np.ndarray; n_samples: int = 1; updated_at: datetime | None = None`
  - `Voice`: os campos `embedding` e `n_samples` saem e entra
    `embeddings: dict[str, VoiceEmbedding] = field(default_factory=dict)`. Os demais campos ficam iguais.
  - `Speaker.embedding_model: str | None = None` (último campo; também em `_SPEAKER_FIELDS`).
  - `upsert_voice(voice) -> Voice`: grava a identidade e faz upsert de cada item de `voice.embeddings`, sem
    apagar modelos ausentes. As colunas legadas `voices.embedding`/`voices.n_samples` não são mais lidas
    nem escritas.
  - `set_voice_embedding(voice_id: str, model: str, emb: VoiceEmbedding) -> None` (upsert de uma linha).
  - `linked_speakers(voice_id: str) -> list[Speaker]`: falantes com esse `voice_id` e `is_me = 0`, por
    `meetings.started_at`, depois `speakers.rowid`.
  - `list_voices()`/`get_voice()` carregam os embeddings de `voice_embeddings`.
  - Migração `_SCHEMA_V6`, acrescentada em `_MIGRATIONS` (o comentário "Índice = versão destino" volta para
    cima de `_MIGRATIONS`):
    ```sql
    CREATE TABLE voice_embeddings (
        voice_id TEXT NOT NULL REFERENCES voices(id) ON DELETE CASCADE,
        model TEXT NOT NULL,
        embedding BLOB NOT NULL,
        n_samples INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT,
        PRIMARY KEY (voice_id, model)
    );
    INSERT INTO voice_embeddings(voice_id, model, embedding, n_samples, updated_at)
        SELECT id, 'pyannote-community-1', embedding, n_samples, updated_at FROM voices WHERE embedding IS NOT NULL;
    ALTER TABLE speakers ADD COLUMN embedding_model TEXT;
    UPDATE speakers SET embedding_model = 'pyannote-community-1' WHERE embedding IS NOT NULL;
    INSERT OR IGNORE INTO settings(key, value) SELECT 'diarizer', '"pyannote"'
        WHERE EXISTS (SELECT 1 FROM settings WHERE key = 'hf_token' AND value NOT IN ('null', '""'));
    INSERT OR IGNORE INTO settings(key, value)
        SELECT 'voice_match_threshold_pyannote', value FROM settings WHERE key = 'voice_match_threshold';
    DELETE FROM settings WHERE key = 'voice_match_threshold';
    ```
- Produces (`settings.py`):
  - `DIARIZERS = ("local", "pyannote")`, `DEFAULT_LOCAL_VOICE_THRESHOLD = 0.5` (provisório; a Task 11 calibra).
  - Em `DEFAULTS`, `voice_match_threshold` sai e entram `"diarizer": "local"`,
    `"voice_match_threshold_local": DEFAULT_LOCAL_VOICE_THRESHOLD` e `"voice_match_threshold_pyannote": 0.6`.
  - `_validate`: `diarizer` fora de `DIARIZERS` → `ValueError(f"Motor de separação inválido: {value!r} (opções: local, pyannote)")`.
    Os dois limiares usam a mesma validação de hoje ("Limiar de reconhecimento de voz deve estar entre 0 e 1").
    Em `_from_env`, `diarizer` passa por `_validate(key, raw.strip())`.
  - `threshold_key(engine: str) -> str`: `f"voice_match_threshold_{engine}"`; motor fora de `DIARIZERS` →
    `ValueError`.
- Produces (`meeting/voices.py`):
  - `match_speakers(speaker_embs, voices, threshold, model: str) -> dict[str, str]`: só considera
    `voice.embeddings[model]`.
  - `enroll(voice: Voice | None, name: str, emb: np.ndarray, model: str, source_meeting_id: str, source_speaker_id: str) -> Voice`:
    a média corrente de hoje, aplicada só a `embeddings[model]` (`n_samples` vem desse item). Os outros
    modelos ficam intactos.
- Produces (`finalize.py`):
  - `_diarize(pc_audio, pc_words, diarizer) -> DiarizationResult | None`: `None` quando não separou;
    `DiarizationResult()` quando não há fala no PC. O fluxo de alertas de hoje fica igual nesta task.
  - `_build_speakers(meeting_id, segments, result: DiarizationResult, voices, threshold: float | None)`:
    grava `embedding` + `embedding_model = result.model` só se `result.model` não for vazio. Casa só com
    `result.model` não vazio e `threshold` não `None`.
  - O limiar em `finalize` é `float(settings.get(threshold_key(result.engine)))` quando
    `result.engine in DIARIZERS`; senão `None`, com `log.warning`.
- Produces (`api.py`):
  - `_voice_json(v: Voice, model: str | None = None) -> dict`, com as mesmas chaves de hoje. `n_samples` é o
    de `v.embeddings[model]` se existir; senão o maior entre os modelos (0 se não houver nenhum).
  - `_active_model(ctx) -> str | None`: `ctx.diarizer.chosen_model()` se o diarizer tiver o método; senão `None`.
  - `/enroll`: `sp.embedding is None or not sp.embedding_model` → 400 com a mesma mensagem de hoje.
    Chama `voice_matching.enroll(..., sp.embedding_model, ...)`.
  - O renomear usa `dataclasses.replace(voice, name=..., embeddings={})` + `upsert_voice` (não reescreve
    embeddings).
  - `GET /voices`, `/enroll` e o renomear passam `_active_model(ctx)` para `_voice_json`.

- [x] **Step 1: Testes que falham.**
  - `tests/test_store.py`: `user_version` passa a 6 e `voice_embeddings` entra no conjunto de tabelas.
    `test_migration_v6_tags_existing_embeddings_and_settings`: cria um banco em V5 aplicando
    `store._MIGRATIONS[:5]` à mão (str ou callable, com `PRAGMA user_version`). Insere uma voz com
    embedding de 256 floats e `n_samples=4`, uma voz sem embedding, um falante com embedding, um sem, e as
    configurações `hf_token="hf_x"` e `voice_match_threshold=0.7` (JSON). Ao abrir com `Store`:
    `get_voice(...).embeddings == {"pyannote-community-1": VoiceEmbedding(vec, 4, ...)}`, a voz sem embedding
    tem `{}`, os falantes têm `embedding_model` `"pyannote-community-1"`/`None`,
    `get_setting("diarizer") == "pyannote"`, `get_setting("voice_match_threshold_pyannote") == 0.7` e
    `get_setting("voice_match_threshold") is None`.
  - `test_migration_v6_without_token_keeps_local_default`: `hf_token` gravado como `null` → nenhuma linha
    `diarizer`.
  - `test_voice_embeddings_per_model_roundtrip_and_cascade`: `set_voice_embedding` para dois modelos →
    `list_voices()[0].embeddings` tem os dois. `upsert_voice(replace(v, name="X", embeddings={}))` mantém os
    dois. `delete_voice` apaga as linhas de `voice_embeddings`. `linked_speakers` ignora `is_me`.
  - Ajustar `test_voice_embedding_roundtrip` ao novo formato.
  - `tests/test_settings.py`: `diarizer` aceita `"local"`/`"pyannote"` e rejeita `"gpu"` com a mensagem
    acima. Os padrões dos limiares são 0.5 e 0.6; `voice_match_threshold_local=1.5` → erro;
    `threshold_key("x")` → `ValueError`.
  - `tests/meeting/test_voices.py`: `test_match_ignores_voices_without_that_model` (voz só com `"m1"` não
    casa com falantes de `"m2"`) e `test_enroll_updates_only_the_given_model`.
  - `tests/meeting/test_finalize.py`: o `FakeDiarizer` ganha `model="fake-model", engine="local"` e carimba o
    resultado quando ele vier sem esses campos. As vozes dos testes passam a
    `Voice(..., embeddings={"fake-model": VoiceEmbedding(vec)})`. Novo
    `test_finalize_tags_speaker_embeddings_with_model`: `embedding_model == "fake-model"` nos falantes do PC.
    Novo `test_finalize_skips_match_and_embedding_when_model_unknown`: `FakeDiarizer(model="")` → nenhum
    `voice_id` e nenhum embedding gravado.
  - `tests/test_api.py`: em `test_rename_merge_enroll`, o falante `sp-a` ganha `embedding_model="m"`. Depois
    do enroll, `get_voice(...).embeddings["m"].n_samples` vale 1 e depois 2. Novo caso: falante com
    embedding e `embedding_model=None` → 400. Novo `test_voices_report_samples_of_active_model`: com
    `ctx.diarizer` fake de `chosen_model() == "m2"` e uma voz com `m1: 3` e `m2: 1`, `GET /voices` devolve
    `n_samples == 1`; sem `ctx.diarizer`, devolve 3.
- [x] **Step 2:** Run: `uv run --frozen pytest -q tests/test_store.py tests/test_settings.py tests/meeting/test_voices.py tests/meeting/test_finalize.py tests/test_api.py`.
  Expected: FAIL.
- [x] **Step 3:** Implementar store, settings, voices, finalize e api conforme as interfaces.
- [x] **Step 4:** Run: `uv run --frozen pytest -q`. Expected: PASS (inclusive `test_session.py`, que usa o `FakeDiarizer`).
- [x] **Step 5: Commit** `[Feat] Guardar embeddings de voz por modelo e limiar por motor`

---

### Task 7: Escolha do motor, fallback com alerta e ligação no backend

**Files:**
- Create: `src/whisper_ditado/meeting/engines.py`
- Modify: `src/whisper_ditado/meeting/finalize.py`, `src/whisper_ditado/main.py`
- Test: `tests/meeting/test_engines.py`, `tests/meeting/test_finalize.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `LocalDiarizer` (Task 4); `Diarizer.unavailable_reason`, códigos de motivo (Task 5);
  `DIARIZERS`, `threshold_key` (Task 6).
- Produces (`meeting/engines.py`):
  - `FALLBACK_ALERTS = {NO_TOKEN: "pyannote_no_token", MISSING_EXTRA: "pyannote_missing_extra", LOAD_FAILED: "pyannote_failed"}`
  - ```python
    class DiarizerRouter:
        def __init__(self, settings, local, pyannote) -> None
        def chosen(self) -> str            # settings.get("diarizer"); valor fora de DIARIZERS → "local"
        def chosen_model(self) -> str      # model_id do motor escolhido, sem carregar nada
        available: bool                    # property
        def run(self, audio: np.ndarray) -> DiarizationResult
        def unload(self) -> None           # descarrega os dois
    ```
    `available`: `True` se o escolhido for o pyannote e `pyannote.available`; senão `local.available`.
    `run`: com o pyannote escolhido, `reason = pyannote.unavailable_reason`. Se o motivo for `None`, tenta
    `pyannote.run`; uma exceção vira `reason = LOAD_FAILED` com `log.exception`. Sem retorno do pyannote,
    `result = local.run(audio)` (a exceção dele sobe). Se houve motivo, devolve
    `dataclasses.replace(result, fallback=FALLBACK_ALERTS[reason])` e registra
    `log.warning("pyannote não usado (%s); falantes separados pelo motor local", reason)`.
- Produces (`finalize.py`): `DIARIZATION_ALERTS = (NO_DIARIZATION, *FALLBACK_ALERTS.values())`.
  - Separação bem-sucedida: apaga todos os `DIARIZATION_ALERTS` e, se `result.fallback`, grava
    `add_alert(meeting_id, result.fallback, 0.0, duration)`.
  - Falha: apaga os de fallback e grava `no_diarization` se ainda não houver (como hoje).
- Produces (`main.py`): `build_diarizer(settings: Settings) -> DiarizerRouter`, que devolve
  `DiarizerRouter(settings, LocalDiarizer(), _SettingsDiarizer(settings))`; `serve()` passa a usá-lo.
  Atualizar a docstring do módulo (o CTranslate2/pyannote/sherpa-onnx em thread daemon).

- [x] **Step 1: Testes que falham** em `tests/meeting/test_engines.py`, com fakes de motor (`available`,
  `unavailable_reason`, `run`, `model_id`, `engine`, contador de chamadas e de `unload`):
  - `test_local_chosen_runs_local_only`: o pyannote nem é consultado e `fallback is None`.
  - `test_pyannote_chosen_and_available_runs_pyannote`.
  - `test_pyannote_without_token_falls_back_with_alert` (`"no_token"` → `fallback == "pyannote_no_token"`, `engine == "local"`).
  - `test_pyannote_missing_extra_falls_back_with_alert` (`"pyannote_missing_extra"`).
  - `test_pyannote_run_error_falls_back_with_failed_alert` (`"pyannote_failed"`).
  - `test_both_fail_raises`: local levanta → `run` levanta.
  - `test_choice_is_read_on_each_run`: `settings.update({"diarizer": "pyannote"})` entre duas chamadas muda o motor.
  - `test_chosen_model_does_not_load`: `chosen_model()` devolve o `model_id` sem chamar `available`.
  - Em `test_finalize.py`: `test_fallback_alert_replaces_previous_diarization_alerts`: reunião com
    `no_diarization`; reprocessar com resultado `fallback="pyannote_no_token"` deixa só esse alerta, em
    `(0.0, duração)`; reprocessar de novo sem fallback não deixa nenhum alerta de diarização.
    `test_fallback_uses_local_threshold_and_model`: `voice_match_threshold_local=0.9`,
    `voice_match_threshold_pyannote=0.1`, resultado `engine="local"` e similaridade 0.5 → não casa; o
    falante tem `embedding_model` igual ao modelo do resultado.
  - Em `tests/test_main.py`: `test_build_diarizer_follows_settings`: `build_diarizer(settings).chosen() == "local"`;
    depois de `settings.update({"diarizer": "pyannote"})`, vale `"pyannote"`. `chosen_model()` é
    `"pyannote-community-1"` com o pyannote e começa com `"sherpa-"` com o local.
- [x] **Step 2:** Run: `uv run --frozen pytest -q tests/meeting/test_engines.py tests/meeting/test_finalize.py tests/test_main.py`. Expected: FAIL.
- [x] **Step 3:** Implementar `engines.py`, os alertas no `finalize` e `build_diarizer`.
- [x] **Step 4:** Run: `uv run --frozen pytest -q`. Expected: PASS.
- [x] **Step 5: Smoke manual.** Run: `uv run --frozen python -c "from whisper_ditado.meeting.local_diarize import LocalDiarizer; d=LocalDiarizer(); print(d.available, d.model_id)"`.
  Expected: `True sherpa-wespeaker_en_voxceleb_resnet34_LM` (o cache vai para `~/.local/share/whisper-ditado/models/sherpa-onnx`).
- [x] **Step 6: Commit** `[Feat] Escolher o motor de separação com fallback para o local`

---

### Task 8: Recalcular vozes a partir do áudio  *(grupo C; antes da 9)*

**Files:**
- Create: `src/whisper_ditado/meeting/voice_sync.py`
- Modify: `src/whisper_ditado/meeting/finalize.py`, `src/whisper_ditado/meeting/session.py`
- Test: `tests/meeting/test_voice_sync.py`, `tests/meeting/test_finalize.py`, `tests/meeting/test_session.py`

**Interfaces:**
- Consumes: `store.linked_speakers`, `store.set_voice_embedding`, `VoiceEmbedding` (Task 6); `Embed` (Task 3).
- Produces:
  - ```python
    class VoiceSync:
        def __init__(self, load_pc: Callable[[str], np.ndarray | None]) -> None
        def ensure(self, store: Store, model: str, embed: Embed) -> list[str]   # ids de voz atualizados
    ```
    `ensure` percorre cada voz sem `embeddings[model]`. Para cada falante de `linked_speakers(voice.id)`, os
    trechos são os segmentos `source == "pc"` desse falante (`store.get_segments(meeting_id)`), e o áudio é
    `load_pc(meeting_id)`, decodificado uma vez por reunião dentro de cada chamada. Cada `embed` não `None`
    é uma amostra. Exceções de `load_pc`/`embed` são registradas e a amostra é pulada. Com amostras, grava
    `set_voice_embedding(voice.id, model, VoiceEmbedding(normalize(média das amostras normalizadas), n_samples=len(amostras)))`.
    Sem amostras, guarda `(voice.id, model, tuple(sorted(ids dos falantes)))` num conjunto interno e não
    repete essa tentativa enquanto os vínculos forem os mesmos.
  - `finalize(..., *, on_progress=None, voice_sync: VoiceSync | None = None)` (também em `reprocess` e
    `recover`). Depois do `_diarize` bem-sucedido, com `result.model` e `result.embed`, e **antes** de
    `store.list_voices()`/`_build_speakers`/`replace_transcript`: chama
    `(voice_sync or VoiceSync(_load_pc)).ensure(store, result.model, result.embed)` dentro de
    `try/except Exception: log.exception(...)`; a reunião nunca falha por isso. `_load_pc(mid)` =
    `load_track(paths.meeting_dir(mid), "pc")`.
  - `MeetingManager` cria `self._voice_sync = VoiceSync(fin._load_pc)` e o repassa nas três chamadas a `fin.*`.
    `_YieldingDiarizer.run` devolve o resultado com `embed` embrulhado: `gate()` antes de cada chamada,
    quando `embed` não for `None`.
  - O `FakeDiarizer` de `test_finalize.py` ganha o parâmetro `embed: Embed | None = None`, copiado para o
    resultado.

- [ ] **Step 1: Testes que falham** em `tests/meeting/test_voice_sync.py` (store real em `data_home`; `load_pc`
  e `embed` fakes):
  - `test_ensure_builds_missing_model_from_linked_speakers`: voz com só `"pyannote-community-1"`, ligada a
    falantes de duas reuniões. `embed` devolve `[1,0]` e `[0,1]` → `embeddings["m-local"]` é
    `normalize([0.5, 0.5])` com `n_samples == 2`; o embedding do pyannote continua igual.
  - `test_ensure_uses_only_pc_segments_of_that_speaker`: os trechos passados ao `embed` são exatamente os
    `(start_s, end_s)` dos segmentos `pc` do falante (sem os de `mic` nem os de outro falante).
  - `test_ensure_skips_missing_audio_and_short_samples`: uma reunião com `load_pc → None` e outra com
    `embed → None` → a voz fica sem `"m-local"`, com nome e vínculos intactos. Uma segunda chamada não chama
    `load_pc` de novo. Depois de ligar um novo falante (outro `voice_id` em outra reunião), a terceira chamada
    tenta de novo.
  - `test_ensure_never_touches_voices_that_already_have_the_model`.
  - Em `test_finalize.py`: `test_finalize_computes_voice_embedding_before_matching`. Uma voz "João" só com
    `"other-model"`, ligada a um falante de uma reunião antiga com `pc.raw` gravado e segmento `pc`.
    `FakeDiarizer(model="fake-model")` com `embed` devolvendo o vetor `e3`. A reunião nova casa o falante de
    embedding `e3` com João.
  - Em `test_session.py`: `test_voice_embedding_recompute_waits_for_dictation`. Com `gpu_gate` registrando
    `"gate"` e um diarizer cujo resultado tem `embed` registrando `"embed"`, cada `"embed"` vem logo depois
    de um `"gate"`.
- [ ] **Step 2:** Run: `uv run --frozen pytest -q tests/meeting/test_voice_sync.py tests/meeting/test_finalize.py tests/meeting/test_session.py`. Expected: FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Run: `uv run --frozen pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `[Feat] Recalcular embeddings de voz a partir do áudio das reuniões`

---

### Task 9: Reprocessar preserva os vínculos falante → voz  *(grupo C; depois da 8)*

**Files:**
- Modify: `src/whisper_ditado/meeting/voices.py`, `src/whisper_ditado/meeting/finalize.py`
- Test: `tests/meeting/test_voices.py`, `tests/meeting/test_finalize.py`

**Interfaces:**
- Produces:
  - `inherit_links(new_spans: dict[str, list[tuple[float, float]]], old_spans: dict[str, list[tuple[float, float]]]) -> dict[str, str]`
    em `meeting/voices.py`, devolvendo chave nova → `voice_id`. `old_spans` é indexado por `voice_id`. Calcula
    a sobreposição total de tempo para cada par, ordena os pares com sobreposição > 0 da maior para a menor
    e atribui de forma gulosa, um para um (cada chave nova e cada voz no máximo uma vez).
  - Em `finalize`, antes de `replace_transcript`: `old_spans` vem dos segmentos `source == "pc"` dos
    falantes atuais da reunião com `voice_id`, e `new_spans` dos `drafts` `pc` por chave, sem
    `_PARTICIPANTS`. `_build_speakers(..., inherited: dict[str, str])`: chave herdada recebe esse `voice_id`
    e `label = nome da voz` (se a voz ainda existir). O casamento automático só considera as chaves não
    herdadas e as vozes não herdadas. Na finalização normal não há falantes antigos, então nada muda.

- [ ] **Step 1: Testes que falham.**
  - `test_voices.py`: `test_inherit_links_picks_largest_overlap`: `new = {"A": [(0,10)], "B": [(10,20)]}`,
    `old = {"v1": [(8,20)]}` → `{"B": "v1"}`. `test_inherit_links_is_one_to_one`: um cluster antigo `v1`
    `[(0,20)]` virou `A [(0,12)]` e `B [(12,20)]` → só `A` herda. `test_inherit_links_ignores_no_overlap`.
  - `test_finalize.py`: `test_reprocess_keeps_voice_links_by_overlap`. A reunião tem falantes "Ana" (voz
    `v-ana`, segmentos pc em 2–4 s) e "Pessoa 1" (sem voz, em 6–7 s). O reprocessamento usa
    `FakeDiarizer` com turnos `X` em 2–4 s e `Y` em 6–7 s, e embeddings que **não** casariam com a voz
    (limiar 0.99). Resultado: o falante novo em 2–4 s tem `voice_id == "v-ana"` e rótulo "Ana", e o de 6–7 s
    é "Pessoa 1" sem voz. `test_reprocess_without_diarization_does_not_inherit`: com o diarizer
    indisponível, só existe "Participantes", sem `voice_id`.
- [ ] **Step 2:** Run: `uv run --frozen pytest -q tests/meeting/test_voices.py tests/meeting/test_finalize.py`. Expected: FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Run: `uv run --frozen pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `[Feat] Preservar vínculos de voz ao reprocessar a reunião`

---

### Task 10: App — seletor de motor, limiar por motor e alertas  *(grupo C, paralela)*

**Files:**
- Modify: `app/src/api/types.ts`, `app/src/components/settings/MeetingsSection.vue`, `app/src/lib/meeting.ts`
- Test: `app/src/views/SettingsView.test.ts`, `app/src/lib/meeting.test.ts`

**Interfaces:**
- Consumes (contrato do backend, Tasks 6–7): chaves `diarizer` (`"local" | "pyannote"`),
  `voice_match_threshold_local` e `voice_match_threshold_pyannote` em `GET/PUT /settings`
  (`voice_match_threshold` deixa de existir). Tipos de alerta `no_diarization`, `pyannote_no_token`,
  `pyannote_missing_extra` e `pyannote_failed`.
- Produces:
  - `types.ts`: `export type Diarizer = "local" | "pyannote";`,
    `export type DiarizationAlertKind = "no_diarization" | "pyannote_no_token" | "pyannote_missing_extra" | "pyannote_failed";`.
    `Alert.kind: AlertKind | DiarizationAlertKind`. Em `Settings`, `voice_match_threshold` sai e entram
    `diarizer: Diarizer`, `voice_match_threshold_local: number` e `voice_match_threshold_pyannote: number`.
    `AlertKind` (alertas ao vivo) e `ALERT_MESSAGES` não mudam.
  - `lib/meeting.ts`: `ALERT_LABELS: Record<AlertKind | DiarizationAlertKind, string>` com estes textos:
    - `no_diarization`: "Falantes não separados: nenhum motor de separação funcionou"
    - `pyannote_no_token`: "pyannote não usado: falta o token do Hugging Face. Falantes separados pelo motor local"
    - `pyannote_missing_extra`: "pyannote não usado: o extra não está instalado (uv sync --extra pyannote). Falantes separados pelo motor local"
    - `pyannote_failed`: "pyannote não usado: falhou ao carregar ou rodar. Falantes separados pelo motor local"

    Em `alertLines`, os alertas de diarização saem só com o rótulo, sem "de HH:MM a HH:MM".
  - `MeetingsSection.vue`:
    - Novo campo "Motor de separação de falantes" (`<select id="meet-diarizer" class="s-select" data-test="diarizer-select">`,
      no padrão de `DictationSection.vue`), com as opções `local` "Local (grátis)" e `pyannote`
      "pyannote (token do Hugging Face)". O `@change` salva `diarizer`. Dica: "O motor local funciona sem
      cadastro; os modelos são baixados na primeira reunião. A escolha vale a partir da próxima reunião ou
      reprocessamento."
    - O limiar continua num campo só (`#meet-threshold`). Ele lê e salva
      `voice_match_threshold_${diarizer}` e ganha a frase "Vale para o motor escolhido." na dica. O range
      passa a `min="0.1" max="0.95" step="0.05"`, e os erros/`clearError` usam a chave do motor.
    - O bloco do token e o link do modelo só aparecem (`v-if`) com `diarizer === 'pyannote'`. A dica do
      token também diz: "Exige o extra pyannote instalado (uv sync --extra pyannote)."

- [ ] **Step 1: Testes que falham.**
  - `SettingsView.test.ts`: a fixture de configurações troca `voice_match_threshold` por
    `diarizer: "local", voice_match_threshold_local: 0.5, voice_match_threshold_pyannote: 0.6`.
    Os testes de token passam a começar com `diarizer: "pyannote"`. Novos:
    `it("hides the token field while the local engine is selected")` (sem `[data-test="token-save"]` e sem
    `[data-test="hf-model-link"]`) e `it("saves the engine choice")` (selecionar `pyannote` →
    `putSettings({ diarizer: "pyannote" })` e o campo do token aparece). Em
    `it("saves the threshold of the selected engine")`: com local, range 0.35 →
    `putSettings({ voice_match_threshold_local: 0.35 })`; com pyannote, range 0.75 →
    `putSettings({ voice_match_threshold_pyannote: 0.75 })`. A expectativa antiga
    `{ voice_match_threshold: 0.75 }` é substituída.
  - `meeting.test.ts`: `it("labels diarization alerts without a time range")`: um alerta
    `pyannote_no_token` em `(0, 1800)` gera exatamente o texto do rótulo.
- [ ] **Step 2:** Run: `cd app && pnpm test`. Expected: FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Run: `cd app && pnpm test && pnpm build`. Expected: PASS (o `vue-tsc` também passa).
- [ ] **Step 5: Commit** `[UI] Adicionar seletor de motor de separação de falantes`

---

### Task 11: Escolher o modelo local e calibrar com os dados reais  *(grupo C, paralela)*

**Files:**
- Create: `src/whisper_ditado/meeting/calibration.py`, `scripts/eval_diarization.py`,
  `docs/superpowers/specs/2026-10-27-diarizacao-local-sherpa-calibracao.md`
- Modify: `src/whisper_ditado/meeting/local_diarize.py` (só as três constantes), `src/whisper_ditado/settings.py`
  (só `DEFAULT_LOCAL_VOICE_THRESHOLD`)
- Test: `tests/meeting/test_calibration.py`

**Interfaces:**
- Produces (`meeting/calibration.py`, funções puras):
  - `agreement(ref: list[tuple[float, float, str]], hyp: list[tuple[float, float, str]]) -> float`: fração do
    tempo de `ref` coberta por `hyp` com o mesmo rótulo, depois de mapear rótulos de `hyp` para `ref` de
    forma gulosa e um para um pela maior sobreposição. Devolve 0.0 se `ref` estiver vazio.
  - `pick_threshold(positives: list[float], negatives: list[float]) -> float`: se `min(positives) > max(negatives)`,
    o ponto médio entre os dois; senão, o limiar entre os valores observados que maximiza `TPR − FPR`
    (empate → o maior). Arredonda para 2 casas.
  - `leave_one_out_scores(samples: dict[str, list[np.ndarray]]) -> tuple[list[float], list[float]]`. `samples`
    mapeia `voice_id` para os embeddings dos falantes ligados. Para cada amostra, o positivo é o cosseno com
    a média normalizada das outras amostras da mesma voz (só se houver outras), e os negativos são os
    cossenos com a média de cada outra voz.
- Produces (`scripts/eval_diarization.py`, executado com `uv run --frozen python scripts/eval_diarization.py <modo>`):
  - Lê o banco **só em leitura** (`sqlite3.connect(f"file:{db}?mode=ro", uri=True)`, padrão
    `paths.db_path()`) e os `pc.opus` via `finalize.decode_opus`. Nunca escreve no banco nem na pasta de
    reuniões.
  - `voices`: para cada modelo de `EMBEDDINGS`, extrai o embedding de cada falante ligado a uma voz
    (segmentos `pc` dele) com `LocalDiarizer(embedding=nome).embed`. Imprime por modelo a separação
    (`min(pos) − max(neg)`), o limiar de `pick_threshold`, TPR e FPR. Faz o mesmo para os embeddings do
    pyannote já gravados (`speakers.embedding`), só como referência.
  - `clusters --embedding <nome> --thresholds 0.6,0.7,0.8,0.9,1.0,1.1,1.2 --min-speaker 0,4,8,12 --max-minutes 20`.
    Para cada reunião, a referência são os segmentos `pc` gravados (falante do pyannote). Roda
    `LocalDiarizer(embedding, cluster_threshold, min_speaker_s).run` nos primeiros `max-minutes` de áudio e
    imprime `agreement` médio e o erro médio do número de falantes por combinação.
  - Saída sem nomes de pessoas nem texto de reunião (vozes como "voz 1…6", reuniões como "reunião 1…10").

Sondagem feita no planejamento: o `3dspeaker eres2net` com limiar 0.5 deu 115 clusters onde o pyannote
achou 3. Com o `wespeaker resnet34`, uma reunião de 16 min deu 19 clusters com 0.5 e 3 com 0.9 (um deles
dominante). Uma reunião de 31 min levou ~150 s com 4 threads. Por isso a busca vai até 1.2 e usa
`min_speaker`.

- [ ] **Step 1: Testes que falham** em `tests/meeting/test_calibration.py`:
  `test_agreement_maps_labels_one_to_one` (`ref [(0,10,"a"),(10,20,"b")]`, `hyp [(0,10,"x"),(10,20,"x")]` → `0.5`;
  `hyp` com rótulos trocados `y`/`x` correspondentes → `1.0`),
  `test_pick_threshold_separable_midpoint` (`[0.8, 0.9]`, `[0.2, 0.4]` → `0.6`),
  `test_pick_threshold_overlap_maximizes_youden`, e
  `test_leave_one_out_scores_skips_single_sample_voices` (voz com 1 amostra não gera positivo, mas gera negativos).
- [ ] **Step 2:** Run: `uv run --frozen pytest -q tests/meeting/test_calibration.py`. Expected: FAIL.
- [ ] **Step 3:** Implementar `calibration.py` e `scripts/eval_diarization.py`. Run: `uv run --frozen pytest -q tests/meeting/test_calibration.py` → PASS.
- [ ] **Step 4: Rodar `voices`** (rodar em segundo plano; baixa os 6 modelos, ~250 MB):
  `uv run --frozen python scripts/eval_diarization.py voices`. Escolher o modelo pela maior separação. Em
  empate técnico (diferença < 0.02), ficar com o mais rápido.
- [ ] **Step 5: Rodar `clusters`** com o modelo escolhido (dezenas de minutos de CPU) e escolher a
  combinação de maior `agreement`; em empate, a de menor erro no número de falantes.
- [ ] **Step 6: Gravar os padrões.** Em `local_diarize.py`: `DEFAULT_EMBEDDING`, `DEFAULT_CLUSTER_THRESHOLD`
  e `MIN_SPEAKER_S`. Em `settings.py`: `DEFAULT_LOCAL_VOICE_THRESHOLD` = limiar do `pick_threshold` do modelo
  escolhido. Valor gravado pelo usuário continua prevalecendo, porque o padrão só vale sem linha no banco.
  Ajustar os testes que comparam com os valores provisórios (`test_settings.py`,
  `test_run_labels_by_first_appearance_and_tags_model`, `test_build_diarizer_follows_settings`).
- [ ] **Step 7: Registrar** em `docs/superpowers/specs/2026-10-27-diarizacao-local-sherpa-calibracao.md` as
  tabelas das duas rodadas (anônimas), a escolha e o porquê.
- [ ] **Step 8:** Run: `uv run --frozen pytest -q` → PASS; `uv run --frozen pytest -q -m models` → PASS.
- [ ] **Step 9: Commit** `[Feat] Calibrar o motor local com as reuniões existentes`

---

### Task 12: Documentação e validação final

**Files:**
- Modify: `README.md`, `docs/INSTALL_RELEASE.md`, `.github/workflows/release.yml` (texto das notas)

- [ ] **Step 1: README.**
  - A seção `### Separação de falantes (token do Hugging Face)` vira `### Separação de falantes`. Ela explica:
    o motor local (padrão, grátis, CPU, modelos baixados uma vez das releases do sherpa-onnx para
    `~/.local/share/whisper-ditado/models/sherpa-onnx`); o pyannote opcional (`uv sync --extra pyannote`,
    termos, token, os passos de hoje); o seletor em Configurações → Reuniões; o fallback com alerta; e que
    as vozes valem para os dois motores sem recadastro.
  - A frase da linha ~84 passa a dizer que a separação funciona sem configuração e que o pyannote é opcional.
  - O item "Reprocessar" (~276) passa a dizer que os vínculos com vozes cadastradas são mantidos, e que
    nomes editados sem voz e mesclagens são refeitos.
  - Os comandos `uv sync` de desenvolvimento passam a incluir `--extra pyannote` onde já há `--group dev`.
- [ ] **Step 2: `docs/INSTALL_RELEASE.md`.** Trocar "Claude Code e o token do Hugging Face são opcionais" por
  uma frase dizendo que a separação de falantes funciona sem token e que, para usar o pyannote, a
  instalação é `uv sync --frozen --no-dev --extra pyannote` + token (ver README). Na atualização, citar o
  mesmo `--extra pyannote` para quem usa o pyannote.
- [ ] **Step 3: Notas do `release.yml`.** Acrescentar ao texto de `--notes`: "Separação de falantes local
  por padrão; pyannote opcional (extra)."
- [ ] **Step 4: Validação em cópia dos dados reais** (nunca no diretório real):
  ```bash
  V=$(mktemp -d) && mkdir -p "$V/whisper-ditado"
  sqlite3 ~/.local/share/whisper-ditado/ditado.db ".backup '$V/whisper-ditado/ditado.db'"
  cp -r ~/.local/share/whisper-ditado/meetings "$V/whisper-ditado/"
  cp -r ~/.local/share/whisper-ditado/models "$V/whisper-ditado/" 2>/dev/null || true
  XDG_DATA_HOME="$V" uv run --frozen python -m whisper_ditado.main --no-dictation --port 47399
  ```
  Em outro terminal, com o token de `$V/whisper-ditado/api.json`:
  - [ ] `sqlite3 "$V/whisper-ditado/ditado.db" "PRAGMA user_version; SELECT model, count(*) FROM voice_embeddings GROUP BY model; SELECT value FROM settings WHERE key='diarizer'"`
    → `6`, `pyannote-community-1|6`, `"pyannote"`.
  - [ ] `PUT /api/settings {"diarizer": "local"}` e `POST /api/meetings/<id>/reprocess` em cada uma das 10
    reuniões, uma de cada vez, esperando `ready`. Depois, `voice_embeddings` tem `sherpa-…|6` (ou o número
    de vozes com áudio aproveitável, explicado se for menor). As 18 ligações falante→voz continuam
    (`SELECT count(*) FROM speakers WHERE voice_id IS NOT NULL` ≥ 18) e não há alerta `no_diarization`.
  - [ ] Reconhecimento sem herança: o resultado `voices` da Task 11 para o modelo escolhido, com o limiar
    gravado, tem TPR = 1 e FPR = 0 (no máximo 1 erro, explicado). Isso cobre "as 6 vozes são reconhecidas
    pelo motor local".
  - [ ] Ida e volta: `PUT {"diarizer": "pyannote"}`, reprocessar 1 reunião, `PUT {"diarizer": "local"}`,
    reprocessar a mesma. `GET /api/voices` continua com 6 vozes, os mesmos ids e nenhuma duplicada.
  - [ ] Fallback: num venv sem o extra (`UV_PROJECT_ENVIRONMENT=$(mktemp -d)/venv uv run --frozen --no-dev python -m whisper_ditado.main --no-dictation --port 47398`
    com o mesmo `XDG_DATA_HOME`), `diarizer=pyannote`, reprocessar 1 reunião → alerta `pyannote_missing_extra`
    e falantes separados.
  - [ ] Encerrar os backends de teste e apagar `$V`.
- [ ] **Step 5: Testes com modelos reais** (exigência do CLAUDE.md para diarização):
  `uv run --frozen pytest -q -m gpu` e `uv run --frozen pytest -q -m models` → PASS. Também
  `uv run --frozen pytest -q` e `cd app && pnpm test && pnpm build` → PASS.
- [ ] **Step 6: Commit** `[Docs] Documentar a separação de falantes local e o pyannote opcional`
