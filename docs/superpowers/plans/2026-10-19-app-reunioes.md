# App de ditado e reuniões (Tauri + Vue) e troca do serviço — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** App desktop para Linux (Tauri 2 + Vue 3) com janela principal de reuniões, configurações, ícone na bandeja e overlay de gravação, conversando com o backend Python; ao final, trocar o serviço do ditado antigo pelo backend novo.

**Architecture:** O app não carrega modelos: lê `~/.local/share/whisper-ditado/api.json` (porta + token) por um comando Rust e fala com o backend por HTTP/WebSocket em `127.0.0.1`. O overlay roda num **segundo processo** do mesmo binário (`--overlay`) forçado em XWayland (`GDK_BACKEND=x11`), onde "sempre por cima" e "clique atravessa" funcionam no GNOME; a janela principal fica nativa em Wayland.

**Tech Stack:** Tauri 2.12 (Rust 1.99), plugins `single-instance`, `autostart`, `dialog`, `fs`, `clipboard-manager`; Vue 3.5 + TypeScript + Vite; Vitest; pnpm.

**Spec:** `docs/superpowers/specs/2026-10-19-app-reunioes-design.md` · **Depende de:** `docs/superpowers/plans/2026-10-19-backend-reunioes.md` concluído (o contrato da API está na Task 13 dele).

## Global Constraints

- Interface em pt-BR.
- Overlay: pílula no canto superior direito, ~35–40% de opacidade, sempre por cima, clique atravessa, sem foco e sem entrada na barra de tarefas; mostra "● mm:ss" + medidores 🎙 e 🔊 gravando, "● Ditando" ditando; em alerta fica opaca e laranja com o texto do alerta.
- Fechar a janela principal só esconde (o app fica na bandeja). "Sair" com gravação em andamento é bloqueado com a mensagem "Pare a gravação antes de sair".
- App inicia no login, minimizado na bandeja.
- Nada de chamadas ao Claude sem clique do usuário (o título automático é do backend).
- Confirmar sugestão de nome do Claude **só renomeia nesta reunião**; cadastrar voz é ação separada.
- Cores dos falantes por `color_index`: `["#4f8cff","#66bb6a","#ab47bc","#ffb74d","#ef5350","#26c6da","#d4e157","#8d6e63"]` (índice 0 = "Você").
- Commits: `[Tipo] Título` em pt-BR, com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Backend fora do ar / reiniciando** (app abre antes do serviço, ou o serviço reinicia) → o app mostra "Conectando ao serviço…" e reconecta sozinho (WebSocket com backoff, relendo `api.json` porque o token muda a cada início). Teste: `reconnects and rereads api info after close` (Task 1).
2. **Reunião ainda processando ou que falhou** é aberta → mostra o estágio/erro e o botão "Reprocessar", sem quebrar a tela. Teste: `MeetingDetail shows progress and failed state` (Task 4).
3. **Transcrição longa** (2 h, milhares de segmentos) → a lista rola sem travar. Teste: `TranscriptView renders only visible rows for 5000 segments` (Task 4).
4. **Overlay com o app principal fechado/escondido** → continua aparecendo durante gravação e ditado. Verificação manual na Task 2.
5. **Erro do Claude** (deslogado/timeout) ao resumir ou sugerir nomes → mensagem do backend exibida, botão volta a funcionar. Teste: `Summary shows backend error message` (Task 5).

## Paralelização

- **Pré-requisito (usuário, com sudo):** `sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev build-essential`
- Task 1 sozinha (cria o projeto, `package.json`, `pnpm-lock.yaml`, `Cargo.toml` com todas as dependências).
- Depois: **Task 2 → Task 3** em sequência (ambas mexem em `src-tauri/src/lib.rs`), **em paralelo** com **Task 4 → Task 5** (mesma tela de reunião) e com **Task 6** (configurações, arquivos próprios).
- Task 7 (troca do serviço) por último, sozinha.

## Estrutura de arquivos

```
app/
  package.json, vite.config.ts (duas entradas: index.html e overlay.html), tsconfig.json, index.html, overlay.html
  src/
    main.ts, App.vue                 # shell: barra lateral + área principal + rota simples (reuniões | configurações)
    api/types.ts                     # tipos do contrato da API
    api/client.ts                    # fetch com token, erros em pt-BR, reconexão
    api/events.ts                    # WebSocket com reconexão e assinantes por tipo
    composables/useStatus.ts         # estado global (status, alertas, níveis)
    lib/format.ts                    # mm:ss, datas, agrupamento por dia, cores
    views/MeetingsView.vue           # lista + detalhe
    components/MeetingList.vue, RecordButton.vue, MeetingDetail.vue, TranscriptView.vue,
               SpeakerChips.vue, SpeakerMenu.vue, NameSuggestionsDialog.vue, SummaryTab.vue
    views/SettingsView.vue
    components/settings/DictationSettings.vue, TextFileEditor.vue, VoicesSettings.vue,
               MeetingSettings.vue, AudioSettings.vue, LevelMeter.vue
    overlay/main.ts, overlay/Overlay.vue
  src-tauri/
    Cargo.toml, tauri.conf.json, capabilities/default.json, icons/ (app + bandeja: idle, recording, processing)
    src/main.rs, src/lib.rs          # modos normal/overlay, comando api_info, bandeja, plugins
whisper-ditado-app.desktop           # entrada de menu
```

---

### Task 1: Projeto Tauri + Vue e cliente da API

**Files:**
- Create: tudo em `app/` acima exceto views/componentes de tarefas futuras; `app/src/api/*.test.ts`, `app/src/lib/format.test.ts`
- Modify: `.gitignore` (`app/node_modules/`, `app/dist/`, `app/src-tauri/target/`)

**Interfaces:**
- Produces:
  - Comando Rust `api_info() -> Result<ApiInfo, String>` com `ApiInfo { port: u16, token: String }`, lido de `$XDG_DATA_HOME/whisper-ditado/api.json` (fallback `~/.local/share/...`); erro "Serviço do ditado não está rodando" se não existir.
  - `api/types.ts`: `Status`, `MeetingListItem`, `MeetingDetail`, `Speaker`, `Segment`, `Alert`, `Voice`, `Settings`, `Devices`, `NameSuggestion`, `ServerEvent` (união por `type`: `status|dictation|levels|alert|meeting_updated|progress`) — campos idênticos ao contrato da Task 13 do plano do backend.
  - `api/client.ts`: `createClient(getInfo: () => Promise<ApiInfo>, fetchFn = fetch)` → objeto com um método por rota do contrato (`status()`, `startMeeting()`, `stopMeeting()`, `listMeetings(q?)`, `getMeeting(id)`, `renameMeeting(id,title)`, `deleteMeeting(id)`, `reprocess(id)`, `audioBlob(id, start?, end?)`, `exportMarkdown(id)`, `summarize(id)`, `nameSuggestions(id)`, `renameSpeaker(id,label)`, `mergeSpeaker(id,into)`, `enrollSpeaker(id, {name}|{voice_id})`, `listVoices()`, `renameVoice(id,name)`, `deleteVoice(id)`, `getSettings()`, `putSettings(partial)`, `getText('vocab'|'subs')`, `putText(kind,text)`, `devices()`, `monitorLevels(on)`); lança `ApiError(status, message)` com o `detail` do backend; em 401 relê `api_info` uma vez e repete.
  - `api/events.ts`: `connectEvents(getInfo, WebSocketImpl = WebSocket)` → `{ on(type, cb): () => void, state: Ref<'connecting'|'open'|'closed'> }`; reconecta com backoff 0,5 s → 5 s, relendo `api_info` a cada tentativa.
  - `lib/format.ts`: `mmss(seconds)`, `groupByDay(items, now)` ("Hoje", "Ontem", data longa pt-BR), `speakerColor(index)`.
- Backend: o FastAPI precisa liberar CORS para as origens `tauri://localhost`, `http://tauri.localhost` e `http://localhost:1420` (dev) — está incluído na Task 13 do plano do backend.

- [x] **Step 1:** `pnpm create tauri-app` (Vue + TypeScript, pnpm) em `app/`; adicionar `vitest`, `@vue/test-utils`, `jsdom`; plugins Tauri `single-instance`, `autostart`, `dialog`, `fs`, `clipboard-manager` (crates + pacotes JS) já nesta task; `vite.config.ts` com as duas entradas HTML.
- [x] **Step 2: Testes que falham** (`pnpm vitest run`):
  - `client sends bearer token and parses json`
  - `client turns backend detail into ApiError message`
  - `client rereads api info once on 401`
  - `reconnects and rereads api info after close` (Review Focus 1; WebSocket falso)
  - `events dispatches by type`
  - `mmss formats 3725 as 1:02:05 and 65 as 01:05`
  - `groupByDay labels Hoje and Ontem`
- [x] **Step 3:** Implementar `api_info` (Rust), `client.ts`, `events.ts`, `format.ts`, `App.vue` com o shell vazio (barra lateral com o botão, área principal, link para Configurações) e o estado "Conectando ao serviço…".
- [x] **Step 4:** `pnpm vitest run` → PASS; `pnpm tauri dev` abre a janela e, com o backend novo rodando (`./run-backend.sh --no-dictation`), o shell mostra o estado conectado.
- [x] **Step 5: Commit** — `[Feat] Criar app Tauri com cliente da API do backend`

---

### Task 2: Overlay de gravação e ditado

**Files:**
- Create: `app/overlay.html`, `app/src/overlay/main.ts`, `app/src/overlay/Overlay.vue`, `app/src/overlay/Overlay.test.ts`
- Modify: `app/src-tauri/src/lib.rs`, `app/src-tauri/tauri.conf.json`, `app/src-tauri/capabilities/default.json`

**Interfaces:**
- Consumes: `connectEvents`, `api_info`, `mmss`
- Produces:
  - Modo overlay: o binário rodado com `--overlay` cria só a janela `overlay` (`overlay.html`): `transparent: true`, `decorations: false`, `alwaysOnTop: true`, `skipTaskbar: true`, `focusable: false`, `shadow: false`, `resizable: false`, ~300×40 px, posicionada no canto superior direito do monitor principal com 16 px de margem (abaixo da barra superior do GNOME), `set_ignore_cursor_events(true)`.
  - O processo principal inicia o overlay com `std::process::Command::new(current_exe).arg("--overlay").env("GDK_BACKEND","x11")`, reinicia se ele morrer e o encerra ao sair.
  - `Overlay.vue` decide a visibilidade pelos eventos: visível quando `meeting.state == 'recording'` ou `dictation.active`; senão chama `getCurrentWindow().hide()`.
  - Estados visuais (conforme o mockup aprovado): normal = `opacity: .38`, "● mm:ss" + 🎙 + 🔊 com 3 barras cada (altura proporcional a `dbfs` entre −60 e −10); alerta = `opacity: 1`, fundo `rgba(120,60,0,.95)`, texto do alerta mais recente ativo; ditando = "● Ditando". Gravando e ditando ao mesmo tempo → mostra a gravação com o ponto piscando.

- [x] **Step 1: Testes que falham** (componente com eventos falsos): `hidden when idle`, `shows timer and meters while recording`, `shows Ditando while dictating`, `alert makes pill opaque with message`, `alert clears when inactive event arrives`.
- [x] **Step 2:** FAIL. **Step 3:** Implementar componente + modo overlay em Rust. **Step 4:** PASS.
- [x] **Step 5: Verificação manual no GNOME (Wayland)** com o backend novo rodando:
  1. iniciar uma gravação pela API → a pílula aparece no canto superior direito, por cima de uma janela maximizada do Firefox;
  2. clicar em cima da pílula → o clique chega na janela de baixo;
  3. a pílula não aparece no Alt+Tab nem na dock;
  4. desconectar o microfone (ou `PUT /settings {"mic_device":"inexistente"}` antes de iniciar) → pílula laranja "Microfone sem sinal" em ~15 s;
  5. fechar a janela principal → a pílula continua (Review Focus 4).
  Se 1–3 não funcionarem via XWayland, **parar e reportar**: o caminho alternativo do spec é uma extensão do GNOME Shell que desenha a pílula a partir do WebSocket — vira uma task nova, não improvisar.
- [x] **Step 6: Commit** — `[Feat] Adicionar overlay de gravação e ditado`

---

### Task 3: Bandeja, início no login e proteção ao sair

**Files:**
- Modify: `app/src-tauri/src/lib.rs`, `app/src-tauri/tauri.conf.json`, `app/src-tauri/icons/`
- Create: `whisper-ditado-app.desktop`

**Interfaces:**
- Consumes: `api_info`; rotas `status`, `meetings/start|stop`, `PUT /settings {"dictation_enabled"}` (chamadas do Rust com `reqwest` ou repassadas ao front por evento — o Rust lê o status pelo mesmo WebSocket/HTTP)
- Produces:
  - Ícone de bandeja com 3 estados (ocioso, gravando, processando), atualizado pelo `status`.
  - Menu: "Iniciar reunião"/"Parar reunião" (alternando), "Abrir", "Pausar ditado"/"Retomar ditado", "Sair".
  - `single-instance`: abrir o app de novo só mostra a janela existente.
  - `autostart` habilitado com o argumento `--minimized` (não mostra a janela principal ao iniciar).
  - Fechar a janela principal → esconder. "Sair" com `meeting.state == 'recording'` → diálogo "Pare a gravação antes de sair" e não sai.
  - `whisper-ditado-app.desktop` para o menu de apps (instalado em `~/.local/share/applications/` no Step 4).

- [x] **Step 1:** Implementar bandeja, plugins e a regra de saída.
- [x] **Step 2: Verificação manual:** ícone aparece na barra superior (extensão AppIndicator ativa); iniciar/parar pela bandeja muda o ícone e a pílula; "Sair" gravando mostra o aviso; abrir o app duas vezes não cria segunda janela; após logout/login o app sobe minimizado (ou conferir o arquivo em `~/.config/autostart/`).
- [x] **Step 3: Commit** — `[Feat] Adicionar bandeja, início no login e proteção ao sair`

---

### Task 4: Janela principal — lista e transcrição

**Files:**
- Create: `app/src/views/MeetingsView.vue`, `components/MeetingList.vue`, `RecordButton.vue`, `MeetingDetail.vue`, `TranscriptView.vue`, `SpeakerChips.vue`, testes `*.test.ts` correspondentes
- Modify: `app/src/App.vue`

**Interfaces:**
- Consumes: `client`, `connectEvents`, `format.ts`
- Produces (layout do mockup aprovado):
  - `RecordButton`: "● Iniciar reunião / microfone + áudio do PC"; gravando: "■ Parar · mm:ss" e, embaixo, "Seguindo: <followed_app>" quando houver (ou "Saída padrão do sistema"); processando: desabilitado "Processando…".
  - `MeetingList`: busca (debounce 300 ms → `listMeetings(q)`), grupos por dia, item com título, hora · duração · nº de pessoas; atualiza com `meeting_updated`.
  - `MeetingDetail`: título editável no lugar (Enter salva com `renameMeeting`), data/hora, `SpeakerChips` (cor por `color_index`; falante sem `voice_id` e não `is_me` com borda tracejada), ações **Resumir** (Task 5), **Copiar transcrição** (clipboard com o mesmo formato de `export.md` sem o cabeçalho), **Sugerir nomes** (Task 5), **Exportar .md** (diálogo de salvar + `fs`), **Ouvir áudio** (player com `audioBlob(id)` → `URL.createObjectURL`); abas Transcrição/Resumo; estados `processing` (estágio do evento `progress`) e `failed` (mensagem + "Reprocessar"); alertas da reunião listados discretamente acima da transcrição ("Microfone sem sinal de 12:10 a 12:40").
  - `TranscriptView`: linhas `mm:ss · nome · texto`, "(ditado)" em itálico para `dictated`, ▶ por linha toca `audioBlob(id, start_s, end_s)`; **virtualizada** (só renderiza as linhas visíveis + margem).
  - Excluir reunião: menu "⋯" no cabeçalho com confirmação.

- [x] **Step 1: Testes que falham:** `MeetingList groups by day and filters with search`, `RecordButton toggles start and stop`, `MeetingDetail shows progress and failed state` (Review Focus 2), `TranscriptView renders only visible rows for 5000 segments` (Review Focus 3: < 200 elementos de linha no DOM), `title edit saves on Enter`, `copy uses transcript format`.
- [x] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS; conferir visualmente com uma reunião real gerada pela verificação da Task 14 do backend.
- [x] **Step 5: Commit** — `[Feat] Mostrar lista de reuniões e transcrição`

---

### Task 5: Pessoas, sugestão de nomes e resumo

**Files:**
- Create: `app/src/components/SpeakerMenu.vue`, `NameSuggestionsDialog.vue`, `SummaryTab.vue`, testes correspondentes
- Modify: `app/src/components/MeetingDetail.vue`, `SpeakerChips.vue`

**Interfaces:**
- Consumes: rotas de speakers, voices, summary, name-suggestions
- Produces:
  - `SpeakerMenu` (clique numa etiqueta): "Renomear nesta reunião" (`renameSpeaker`), "Identificar como…" (lista de vozes cadastradas → `enrollSpeaker(id,{voice_id})`, ou "Nova voz…" → nome → `enrollSpeaker(id,{name})`), "Juntar com…" (outra pessoa da reunião → `mergeSpeaker`). Não aparece para "Você".
  - `NameSuggestionsDialog`: lista "Pessoa 2 → João" com a evidência; cada uma com "Aplicar" (só `renameSpeaker`) e uma caixa "também salvar a voz" desmarcada por padrão (se marcada, `enrollSpeaker(id,{name})`); vazio → "O Claude não encontrou nomes no contexto".
  - `SummaryTab`: sem resumo → botão "Resumir"; com resumo → Markdown renderizado (sem HTML bruto), "Copiar" e "Refazer"; carregando → "O Claude está resumindo…"; erro → mensagem do backend.

- [x] **Step 1: Testes que falham:** `applying a suggestion only renames`, `checking save voice also enrolls`, `merge calls API and refreshes`, `Summary shows backend error message` (Review Focus 5), `summary markdown is rendered without raw html`.
- [x] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS.
- [x] **Step 5: Commit** — `[Feat] Identificar pessoas e resumir reuniões`

---

### Task 6: Configurações

**Files:**
- Create: `app/src/views/SettingsView.vue`, `components/settings/*.vue`, testes correspondentes

**Interfaces:**
- Consumes: `getSettings/putSettings`, `getText/putText`, `listVoices/renameVoice/deleteVoice`, `devices`, `monitorLevels`, eventos `levels`
- Produces (seções):
  - **Ditado**: ligado/desligado, atalho (texto `ctrl+super`), colagem (`shift+insert`/`ctrl+v`/`ctrl+shift+v`), prompt de estilo, espaço no fim, mín./máx. de segundos. Erro de validação do backend aparece embaixo do campo.
  - **Vocabulário** e **Substituições**: `TextFileEditor` (textarea monoespaçada, "Salvar", aviso "vale a partir do próximo ditado").
  - **Vozes**: lista com renomear e apagar (confirmação).
  - **Reuniões**: título automático (liga/desliga), prompt do resumo (com "Restaurar padrão"), limiar de reconhecimento de voz (0,40–0,90), token do Hugging Face (campo senha; mostra "configurado" quando `hf_token_set`; link para a página do modelo).
  - **Áudio**: microfone e saída ("Padrão do sistema" + lista de `devices`), "Seguir o app da chamada" (liga/desliga), cancelamento de eco (liga/desliga), e `LevelMeter` ao vivo para 🎙 e 🔊 (`monitorLevels(true)` ao abrir a seção, `false` ao sair).

- [x] **Step 1: Testes que falham:** `shows validation error under field`, `text editor saves vocab`, `audio section starts and stops level monitor`, `device select sends null for system default`, `hf token is never displayed back`.
- [x] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS.
- [x] **Step 5: Commit** — `[Feat] Adicionar tela de configurações`

---

### Task 7: Trocar o serviço do ditado pelo backend novo

**Files:**
- Modify: `README.md`
- Delete (só no Step 6, após confirmação do usuário): `ditado.py`, `run.sh`, `whisper-ditado.service`

- [x] **Step 1: Medir o antes:** com o serviço atual, ditar 5 frases curtas e anotar a latência do log (`journalctl --user -u whisper-ditado | grep "caracteres em"`).
- [x] **Step 2: Trocar:**

```bash
systemctl --user disable --now whisper-ditado.service
systemctl --user link ~/dev/whisper-ditado/whisper-ditado-backend.service
systemctl --user enable --now whisper-ditado-backend.service
```

- [x] **Step 3: Checklist de regressão (manual, com o usuário):** Ctrl+Win dita e cola em GTK, Chrome e terminal; acentos corretos; dois ditados seguidos colam na ordem; Ctrl+Win+Seta cancela; latência das 5 frases ≤ a medida no Step 1 + 0,1 s; ditar **durante uma gravação de reunião** mantém a latência e o trecho aparece como "Você (ditado)"; app sobe pela bandeja após logout/login.
- [x] **Step 4: Caminho de volta** (documentar no README):

```bash
systemctl --user disable --now whisper-ditado-backend.service
systemctl --user enable --now whisper-ditado.service
```

- [x] **Step 5: Commit** — `[Chore] Trocar serviço do ditado pelo backend novo`
- [ ] **Step 6:** Só depois do usuário confirmar que usou por um tempo sem problema: remover `ditado.py`, `run.sh`, `whisper-ditado.service` e o trecho antigo do README. Commit `[Chore] Remover daemon antigo do ditado`.
