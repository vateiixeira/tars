# Separação de falantes local (sherpa-onnx), com o pyannote como opção — design

Data: 2026-10-27 · Status: aprovado; seção 5 revalidada contra o `main` · Afeta: backend de reuniões
(`src/whisper_ditado/meeting/`), configurações, store, app (Configurações → Reuniões), docs de
instalação.

## 1. Intenção

Hoje a separação de falantes das reuniões depende do `pyannote/speaker-diarization-community-1`,
um modelo com acesso restrito no Hugging Face: o usuário precisa criar conta, aceitar termos, gerar
um token e colar no app. Isso demora e trava quem instala. Sem token, toda reunião sai com um falante
só ("Participantes") e o alerta `no_diarization`.

O objetivo é que a separação de falantes funcione **logo após instalar, sem cadastro nem token**,
com um modelo próprio e grátis (sherpa-onnx), e que o pyannote continue **disponível como opção**
para quem já tem o token ou prefere a qualidade dele.

## 2. Objetivos

- Novo motor de separação **local** com sherpa-onnx, padrão de instalação nova e de quem já usa o
  app sem token (quem já tem token fica no pyannote, ver 6.1). Modelos baixados automaticamente de
  fonte sem login (releases do GitHub do k2-fsa) na primeira necessidade e, depois, lidos do cache
  local sem rede.
- O **pyannote (token)** continua como segundo motor, escolhido explicitamente em Configurações.
- **Seletor explícito** de motor em Configurações → Reuniões: "Local (grátis)" / "pyannote (token
  do Hugging Face)". O campo de token só importa quando o pyannote está escolhido.
- **Vozes salvas preservadas**: nenhuma voz precisa ser recadastrada, nem agora (migração) nem ao
  trocar de motor depois, quantas vezes for.
- **Instalação padrão leve**: `pyannote.audio`, `torch` e `torchaudio` saem das dependências
  obrigatórias e viram um extra opcional.

## 3. Não-objetivos

- Melhorar a qualidade da separação além do que os modelos entregam prontos (sem treino, sem
  ajuste fino).
- Separar falantes do microfone (continua sendo "Eu"; só a trilha do PC é separada, como hoje).
- Diarização em tempo real durante a reunião (continua só na finalização/reprocessamento).
- Mudar o ditado (o modelo Whisper e o fluxo de ditado não são tocados).

## 4. Anti-objetivos (seria falha mesmo funcionando)

- Pedir para o usuário recadastrar as 6 vozes atuais (Alessandro, Evaldo, Will, Lucas Vieira,
  Miguel, Bia - Pegasus) ou perder o vínculo delas com falantes de reuniões antigas.
- Comparar embedding de um modelo com embedding de outro (dá similaridade sem sentido e casa a voz
  errada, silenciosamente).
- Reunião que sai com um falante só porque o pyannote falhou, quando o motor local poderia ter
  separado.
- Instalação padrão que volta a exigir alguns GB de torch/CUDA só por causa da diarização.
- Baixar modelo do Hugging Face no motor local (o ponto é não depender de conta/termos).

## 5. Situação atual (para quem vai construir)

> Revalidado em 2026-10-27 contra o `main` (`/home/vi/dev/whisper-ditado`, commit `9acb7fd`).
> O primeiro levantamento foi feito no branch `fluxo-angulare`, que não tem ancestral comum com o
> `main`; o que segue vale para o `main`.

**Motor atual e ligação**

- `src/whisper_ditado/meeting/diarize.py`: classe `Diarizer` (pyannote 4.x, `PIPELINE_ID =
  "pyannote/speaker-diarization-community-1"`, `pipeline.to(cuda)`), com carga preguiçosa sob lock,
  cache local primeiro (`snapshot_download(local_files_only=True)`) e nova tentativa após falha
  (`RETRY_AFTER_S = 300`, ou na hora com token diferente). `available` tenta carregar.
  `unload()` solta o pipeline sob o lock; o próximo uso recarrega do cache. `run(audio)` devolve
  `DiarizationResult(turns: list[Turn], embeddings: dict[label, np.ndarray])`, usando os turnos de
  `exclusive_speaker_diarization` e descartando centróides nulos/não finitos (`_usable`). É o
  **único** módulo de `src/` que importa `torch`/`pyannote` (`memory.py` só usa o `torch` se já
  estiver em `sys.modules`).
- `src/whisper_ditado/main.py`: `_SettingsDiarizer(Diarizer)` relê o token a cada carga
  (`settings.get("hf_token")` ou a variável de ambiente `HF_TOKEN`), valendo sem reiniciar. Uma
  instância é criada em `serve()` e passada ao `MeetingManager` e ao `api.AppContext(diarizer=...)`.
  `_maintain_meeting_models` chama `meetings.release_idle_models()` a cada 5 s.
- `meeting/session.py`: `MeetingManager` embrulha o diarizer em `_YieldingDiarizer` (que chama
  `gpu_gate()` antes de `run` e repassa `available`/`unload`) **só quando há `gpu_gate`**, ou seja,
  com o ditado ligado. Tudo que usa modelo roda em série na thread única `meeting-gpu`
  (`_finalize_session`, `_reprocess_job`, `_recover_job`). `_release_idle_models` descarrega o
  Whisper das reuniões e chama `diarizer.unload()` depois de `meeting_model_idle_sec` sem uso.
- `meeting/finalize.py::_diarize(pc_audio, pc_words, diarizer)`: sem palavras no PC devolve "separou"
  sem chamar o diarizer; diarizer ausente, `available` falso ou exceção em `run` → um falante só
  ("Participantes"). Em `finalize`, separação bem-sucedida apaga o alerta `no_diarization`;
  falha grava esse alerta cobrindo a reunião inteira (`NO_DIARIZATION = "no_diarization"`).
  `finalize` serve à finalização normal, a `reprocess` e a `recover`.
- `meeting/finalize.py::_build_speakers` grava em cada `Speaker` o embedding do falante e casa com
  `store.list_voices()` via `meeting/voices.py::match_speakers` (cosseno, guloso, limiar
  `voice_match_threshold`). Hoje **não** há herança de vínculos no reprocessamento:
  `store.replace_transcript` apaga e recria os falantes, e só o casamento automático religa vozes.
- `meeting/voices.py` (o módulo fica em `meeting/`, não na raiz): `cosine`, `match_speakers` e
  `enroll` (cria a voz ou atualiza a média normalizada, `n_samples + 1`).
- `api.py` `POST /speakers/{id}/enroll` usa `speaker.embedding` (erro 400 se for `None`) com
  `voice_matching.enroll` e depois `store.upsert_voice` + `update_speaker(voice_id, label)`.
  `AppContext.diarizer` é recebido, mas nenhuma rota o usa hoje. `GET /voices` (`_voice_json`)
  devolve `n_samples`, exibido em Configurações → Vozes (`VoicesSection.vue`, "N amostras").

**Banco**

- `store.py`: migrações por `PRAGMA user_version`, com
  `_MIGRATIONS = [_SCHEMA_V1, _SCHEMA_V2, migration_sql, _SCHEMA_V4, _SCHEMA_V5]` (a V3 é a função
  `issues.legacy.migration_sql`). A próxima é a **V6**. O banco real
  (`~/.local/share/whisper-ditado/ditado.db`) está em `user_version = 5`.
- Tabela `voices` (V1): `id`, `name`, um único `embedding BLOB` (`float32.tobytes()`, 256 floats,
  WeSpeaker ResNet34 do community-1), `n_samples`, `source_meeting_id`, `source_speaker_id`,
  `created_at`, `updated_at`. Tabela `speakers` (V1): `voice_id` (`ON DELETE SET NULL`), `is_me`,
  `color_index`, `embedding BLOB`. `merge_speakers` mantém o embedding e o `voice_id` do destino.
- Configurações ficam na tabela `settings` e seguem a precedência banco > `DITADO_<CHAVE>` > padrão
  (`settings.py::DEFAULTS`, com `voice_match_threshold: 0.6` e `hf_token: None`). `GET /settings`
  omite `hf_token` e devolve `hf_token_set`. Validação por chave em `settings.py`
  (`voice_match_threshold` entre 0 e 1).
- Dados reais hoje: 10 reuniões, todas `ready`, todas com `pc.opus` em
  `~/.local/share/whisper-ditado/meetings/<id>/` (41 MB no total; o áudio não é apagado após a
  finalização). 6 vozes, todas com embedding de 256 floats (`n_samples`: Alessandro 4, Will 2, as
  outras 1). 20 falantes com embedding, 18 deles ligados a vozes, em 9 reuniões. `hf_token` está
  salvo no banco; `voice_match_threshold` não foi editado (vale o padrão 0.6).

**Dependências, CI e release**

- `pyproject.toml`: `pyannote.audio>=4.0,<5`, `torch` e `torchaudio` obrigatórios, com
  `[tool.uv.sources]` apontando os dois para o índice explícito `pytorch-cu128`. Não há
  `[project.optional-dependencies]` hoje. Pytest com `addopts = "-m 'not gpu'"`.
- `uv.lock` (138 pacotes): `pyannote-audio 4.0.7`, `torch`/`torchaudio 2.11.0+cu128`. Tudo que depende
  de torch no lock vem do pyannote (`asteroid-filterbanks`, `julius`, `lightning`,
  `pytorch-lightning`, `pytorch-metric-learning`, `torch-audiomentations`, `torch-pitch-shift`,
  `torchmetrics`). Assim, mover os três para um extra tira o torch do `uv sync` padrão.
  `onnxruntime 1.31.0` já está no lock (via `faster-whisper`); `sherpa-onnx` não está.
- CI (`.github/workflows/ci.yml`, reutilizado pelo `release.yml`): `uv sync --frozen --group dev` e
  `uv run --frozen pytest -q`. **Testes sem marcador `gpu` importam o pyannote**:
  `tests/meeting/test_diarize.py` (fixture `hub` e `_output`, inclusive o teste de
  `main._SettingsDiarizer`). Com o pyannote como extra, esses testes precisam do extra no CI ou de
  `pytest.importorskip`.
- Pacote de release (`scripts/release.py package`): `git archive` do commit + binário do app, sem
  modelos. `docs/INSTALL_RELEASE.md` manda instalar o backend com `uv sync --frozen --no-dev` (na
  instalação e na atualização) e diz só que "Claude Code e o token do Hugging Face são opcionais";
  **não tem** seção de separação de falantes. As notas do `release.yml` dizem que os modelos são
  baixados no primeiro uso. O GPU NVIDIA continua exigido pelo Whisper.
- README: a seção é `### Separação de falantes (token do Hugging Face)` (README.md ~l. 282). A linha
  ~84 diz que a separação "é opcional e usa o token explicado abaixo", e a linha ~276 descreve
  o reprocessamento ("recria a transcrição e os falantes, incluindo nomes editados e mesclagens").
  Os dois trechos mudam com este design.
- `CLAUDE.md`: mudanças em diarização exigem os testes com modelos reais no ambiente NVIDIA antes da
  release.

**App**

- `app/src/components/settings/MeetingsSection.vue`: campo do limiar (`voice_match_threshold`, padrão
  0.6 na tela), campo do token (salvar/remover, `hf_token_set`) e link para o modelo
  (`HF_MODEL_URL` em `settings/constants.ts`). A dica de `meeting_model_idle_sec` cita o "separador
  de vozes".
- `app/src/api/types.ts`: `Settings` com `voice_match_threshold` e `hf_token_set`, `SettingsUpdate`
  com `hf_token`. `AlertKind` **não inclui** `no_diarization`.
- O `no_diarization` aparece hoje só na lista de alertas da reunião (`MeetingDetail.vue` →
  `lib/meeting.ts::alertLines`). Como não há rótulo em `ALERT_LABELS`, ele sai cru
  ("no_diarization de 10:00 a 11:00"). O novo alerta de fallback entra no mesmo lugar e os dois
  precisam de rótulo em pt-BR.
- Testes: `app/src/views/SettingsView.test.ts` (token e limiar).

**Máquina de referência**: AMD Ryzen 7 5700X (8 núcleos / 16 threads), RTX 5070 Ti 16 GB.

## 6. Decisões tomadas

### 6.1 Motores e seleção
- Nova configuração `diarizer` com valores `"local"` e `"pyannote"`. *Seletor explícito: decisão
  minha (o usuário aprovou a descrição, mas não escolheu explicitamente entre seletor e automático).*
- Padrão: **`"local"` em instalação nova**. Instalação que já tem `hf_token` salvo no momento da
  migração fica com `"pyannote"` (não perde qualidade sem perceber). *Decisão minha.*
- `hf_token` continua existindo, com o mesmo tratamento de segredo de hoje (`hf_token_set`).
- A escolha vale a partir da próxima finalização/reprocessamento, sem reiniciar o backend (mesmo
  comportamento do token hoje).

### 6.2 Fallback
- Se o motor escolhido é o pyannote e ele não está disponível (sem token, extra não instalado,
  termos não aceitos, rede, CUDA) ou falha ao rodar, a reunião é separada pelo **motor local** e
  recebe um alerta visível dizendo que o pyannote não foi usado e por quê (texto curto em pt-BR).
- `no_diarization` passa a significar só "nenhum motor conseguiu separar" (ex.: o local também
  falhou, ou o download dos modelos locais falhou sem cache).
- Reprocessar uma reunião que separou com sucesso apaga alertas de diarização antigos, como hoje.

### 6.3 Embeddings por modelo (vozes nunca recadastradas)
- Embeddings de modelos diferentes **nunca** são comparados entre si. Todo embedding gravado (de
  falante ou de voz) fica associado ao identificador do modelo que o gerou (ex.: `pyannote-community-1`,
  `sherpa-<nome-do-modelo-de-embedding>`).
- Uma voz pode ter **um embedding por modelo** (cada um com sua contagem de amostras).
- Ao casar falantes com vozes, só entram vozes que têm embedding do modelo em uso.
- Quando uma voz não tem embedding do modelo em uso, ele é **recalculado a partir do áudio**: para
  cada falante de reunião ligado à voz (`speakers.voice_id`), pega os trechos desse falante na trilha
  do PC (segmentos `source = pc` do falante) e extrai o embedding com o modelo em uso; a voz recebe a
  média normalizada, com `n_samples` = número de falantes que contribuíram.
- Áudio ausente ou trecho curto demais para extrair: aquela amostra é pulada. Se nenhuma sobrar, a
  voz fica sem embedding daquele modelo (nome e vínculos intactos) e volta a ter quando o usuário
  ligar um falante a ela de novo.
- Cadastro/atualização de voz (`/enroll`) atualiza o embedding do mesmo modelo do falante. Se o
  falante não tiver embedding (reunião antiga sem separação), o cadastro continua dando erro como hoje.
- Fonte do recálculo: **todos** os vínculos `speakers.voice_id` atuais, inclusive os casados
  automaticamente e nunca confirmados (o usuário vê e corrige vínculos no app). *Decisão minha.*
- **Reprocessar uma reunião preserva os vínculos falante→voz que ela já tinha**: os falantes novos
  herdam o `voice_id`/nome do falante antigo com maior sobreposição de tempo na trilha do PC; o
  casamento automático só decide os falantes que não herdaram nada. (Decorre do anti-objetivo de não
  perder vínculos.)
- **Migração**: os embeddings existentes (vozes e falantes) são marcados como do pyannote
  community-1. Os embeddings locais das 6 vozes são gerados a partir do áudio das 9 reuniões, sem
  ação do usuário, antes de a primeira reunião ser casada com o motor local.

### 6.4 Limiar de reconhecimento
- O `voice_match_threshold` de hoje (0.6) foi pensado para o pyannote. O motor local tem
  distribuição de similaridade diferente, então cada motor tem seu próprio limiar padrão. O padrão do
  local deve ser **calibrado com os dados reais** (os 18 falantes já ligados a vozes servem de
  verdade: pares mesma-voz vs. vozes diferentes).
- Na tela continua **um campo só**, que edita o limiar do motor escolhido no seletor; um valor
  editado pelo usuário nunca é sobrescrito pelo padrão calibrado. *Decisão minha.*
- Para escolher o modelo local e calibrar, o resultado do pyannote nas reuniões existentes serve de
  gabarito (não há rotulagem manual). *Decisão minha.*

### 6.5 Dependências e instalação
- `sherpa-onnx` vira dependência obrigatória (CPU).
- `pyannote.audio`, `torch`, `torchaudio` saem para um extra opcional (`uv sync --extra pyannote`),
  mantendo o índice cu128 para o torch.
- Com o pyannote escolhido e o extra ausente, vale o fallback (6.2), com mensagem dizendo como
  instalar o extra.
- README: a seção "Separação de falantes (token do Hugging Face)" vira "Separação de falantes",
  explicando que funciona sem nada e que o pyannote é opcional (extra + token + termos).
  `docs/INSTALL_RELEASE.md` não tem essa seção: ajustar a frase sobre o token e indicar o comando
  com o extra (`uv sync --frozen --no-dev --extra pyannote`) para quem quiser o pyannote.

### 6.6 App
- Configurações → Reuniões: seletor do motor; o campo de token e o link para o modelo aparecem
  (ou ficam habilitados) só quando o pyannote está escolhido. Textos em pt-BR.
- O app passa a mostrar o novo alerta de fallback onde já mostra `no_diarization`.

## 7. Restrições

- Tudo local: nenhuma chamada de rede além do download inicial dos modelos (de fonte sem login).
- O download dos modelos locais precisa ser seguro: arquivo temporário + rename, e verificação de
  integridade (hash conhecido) antes de usar.
- O motor local não deve disputar a GPU com o ditado (roda em CPU).
- Padrões do repositório: pt-BR em logs, mensagens e textos de UI; testes sem GPU por padrão (o
  marcador `gpu` continua para testes reais do pyannote); commits no formato do CLAUDE.md global.
- Nada de quebrar reuniões existentes: reprocessar uma reunião antiga com qualquer motor tem que
  funcionar.

## 8. Fica a critério de quem construir

- Qual modelo de segmentação e qual de embedding do sherpa-onnx usar (ex.: pyannote segmentation
  3.0 em ONNX + WeSpeaker/3D-Speaker/TitaNet), escolhido por qualidade em reuniões reais em
  português, usando as reuniões existentes como teste (comparar com o resultado do pyannote).
- Parâmetros de clustering (limiar/número de falantes) e duração mínima de trecho para embedding.
- Formato do armazenamento por modelo (nova tabela ou colunas); a próxima migração é a V6.
- Onde e quando roda o recálculo das vozes (na subida do backend, ao trocar de motor, sob demanda na
  finalização), desde que nunca compare modelos diferentes e não bloqueie a API.
- Pasta do cache dos modelos (sugestão: dentro de `~/.local/share/whisper-ditado/models/`).
- Nome do novo alerta e o texto exato das mensagens.
- Se `_YieldingDiarizer` e a lógica de nova tentativa continuam valendo para os dois motores ou só
  para o pyannote.

## 9. Como saber que deu certo

- Instalação nova sem token: a primeira reunião com duas ou mais pessoas no PC sai com falantes
  separados.
- As 6 vozes atuais são reconhecidas pelo motor local nas reuniões existentes reprocessadas, sem
  recadastro.
- Trocar para pyannote (com token) e voltar para local não perde nem duplica vozes.
- `uv sync` padrão não instala torch.
- Suíte de testes (`pytest`, sem GPU) e testes do app passando.
