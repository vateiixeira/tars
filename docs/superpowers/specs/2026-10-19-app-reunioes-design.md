# App de ditado e reuniões — design

Data: 2026-10-19 · Status: rascunho para aprovação

## 1. Intenção

Hoje o projeto é um daemon (`ditado.py`) que roda como serviço systemd do usuário: segurar
Ctrl+Win grava o microfone, o faster-whisper transcreve na GPU e o texto é colado na janela ativa.
Funciona bem e o usuário usa no dia a dia.

O objetivo agora é transformar isso num **aplicativo de fato para Linux** que:

1. dá uma tela para configurar o ditado (hoje só por arquivos e variáveis de ambiente);
2. **grava reuniões** de qualquer app de chamada (Zoom, Meet, Discord, ...) só com áudio:
   o usuário liga um botão, o app escuta microfone + áudio do PC e, ao desligar, gera a
   **transcrição completa com quem falou**, que fica guardada no app com ações de copiar e resumir.

O usuário quer essa ferramenta para si: o resumo é para consulta própria, não para mandar a
ninguém agora ("se precisar colar eu trato depois").

## 2. Objetivos

- Gravar reuniões de qualquer app de chamada sem integração com eles: microfone + saída de áudio do PC.
- Transcrição completa da reunião, pronta alguns segundos/poucos minutos depois de parar.
- Diferenciar quem falou: "Você" (microfone), e as demais pessoas separadas por voz no áudio do PC.
- **Reconhecer vozes entre reuniões**: o usuário cadastra uma voz ("essa Pessoa 2 é o João") e as
  próximas reuniões já trazem o nome.
- Ações por reunião: copiar transcrição, resumir, sugerir nomes, exportar, ouvir o áudio.
- Indicador de gravação discreto e sempre visível (overlay) enquanto grava.
- Manter o ditado funcionando exatamente como hoje, agora configurável pela interface.

## 3. Não-objetivos (por agora)

- Gravar tela/vídeo.
- Transcrição ao vivo durante a reunião (o usuário aceitou ficar pronta depois).
- Enviar resumo/transcrição para outras ferramentas (e-mail, Slack, Notion...).
- Windows e macOS (é "Linux primeiro"; a arquitetura não deve impedir portar depois).
- Integração com os projetos/memórias do Claude do usuário.
- Detectar reunião automaticamente — gravar é sempre um ato explícito do usuário.
- Separar várias pessoas falando no **mesmo microfone** (duas pessoas na sala do usuário viram "Você").

## 4. Anti-objetivos

- Ser pior que uma alternativa pronta: o usuário só largaria o app se a Anthropic lançasse algo
  equivalente. A régua é "resolve o meu dia a dia melhor que o que existe".
- Gastar a cota do Claude sem pedido: chamadas ao Claude só por ação do usuário (exceção: título, ver §8).
- Trocar nomes de pessoas sozinho com base em palpite: nome errado numa transcrição é pior que "Pessoa 2".
- Degradar o ditado: a latência do ditado (hoje ~0,1–0,4 s) não pode piorar de forma perceptível,
  nem enquanto uma reunião é gravada ou processada.

## 5. Decisões do usuário

- **Modelo de transcrição:** o mesmo de hoje (faster-whisper `large-v3-turbo`, CUDA, com o vocabulário
  e substituições já existentes).
- **Idioma:** reuniões são **sempre em pt-BR** (com termos técnicos em inglês), como o ditado. Idioma
  fixo `pt`, mesmo prompt de estilo e mesmas hotwords; resumo e título em pt-BR.
- **Dispositivos de áudio:** configuráveis no app (headset, microfone separado, caixa de som...),
  vindo por padrão o padrão do Linux.
- **Saúde da captura é prioridade:** o pior caso é achar que está gravando e não estar. O app tem que
  detectar e avisar durante a reunião quando o microfone ou o áudio da chamada não está chegando.
- **Apps de chamada variados:** Firefox, Chrome, Discord web e app do Discord — sem lista fixa de apps.
- **Resumo e identificação de nomes:** pelo **Claude Code CLI** (`claude`, já instalado, v2.1.304),
  usando a conta/permissões do usuário. O usuário está ciente de que o texto enviado ao Claude sai da
  máquina; áudio e transcrição continuam locais.
- **Reconhecimento de vozes entre reuniões:** entra desde o início.
- **Pronto depois, não ao vivo:** aceitável a transcrição ficar pronta após parar.
- **Overlay de gravação:** estilo "pílula" — ponto vermelho + "Gravando · 12:34", canto superior
  direito, opacidade baixa (~35–40%), sempre por cima, cliques atravessam.
- **Arquitetura híbrida:** app em **Tauri (Rust) + Vue**, backend em **Python** (onde estão os modelos).
  "Qualquer coisa a gente muda depois."
- **Janela principal:** aprovada conforme o mockup (§9).
- **Repositório:** este (`~/dev/whisper-ditado`), branch `main`.

## 6. Ambiente e restrições

- Ubuntu 26.04, **GNOME Shell 50.1 em Wayland**, PipeWire. Extensão `ubuntu-appindicators` ativa
  (ícone na bandeja funciona).
- GPU RTX 5070 Ti, 16 GB VRAM, compute 12.0. Driver 595 (CUDA 13.2). O Whisper turbo ocupa ~3 GB.
- Python 3.12 via `uv` (venv do projeto); libs CUDA do pip; `run.sh` ajusta `LD_LIBRARY_PATH`.
  Atenção: o decode de arquivo do faster-whisper via PyAV está quebrado nesta combinação de versões —
  passar sempre áudio como array numpy.
- Rust 1.99, Node 24, pnpm 12 instalados. **Faltam as libs de sistema do Tauri**
  (`libwebkit2gtk-4.1-dev` e afins) — instalar exige `sudo`, o usuário roda.
- `ydotool`/`ydotoold` (serviço do usuário), `wl-copy`, `pw-record` disponíveis; usuário no grupo `input`.
- Módulo `libpipewire-module-echo-cancel` disponível.
- **Wayland/GNOME não deixa apps se fixarem "sempre por cima"** nem definirem posição livremente.
- Modelo de diarização **pyannote** é "gated" no Hugging Face: o usuário precisa aceitar os termos e
  gerar um token (passo manual, guiado pelo app ou pelo README).

## 7. Arquitetura

```
┌──────────────────────────── app (Tauri: Rust + Vue) ────────────────────────────┐
│ janela principal · configurações · ícone na bandeja · overlay (pílula)          │
└───────────────▲─────────────────────────────────────────────▲──────────────────┘
                │ HTTP (comandos/consultas)                    │ WebSocket (eventos:
                │ 127.0.0.1                                    │ gravando, progresso, ditando)
┌───────────────┴─────────────────────────────────────────────┴──────────────────┐
│ backend Python (serviço systemd do usuário, sempre ligado)                      │
│  ditado (evdev + Whisper + colar)  ·  gravação de reunião  ·  pipeline          │
│  modelos na GPU: Whisper, diarização/embeddings  ·  SQLite + arquivos de áudio  │
│  chamadas ao `claude -p`                                                        │
└─────────────────────────────────────────────────────────────────────────────────┘
```

- **Backend**: evolução do `ditado.py` para um serviço com módulos (ditado, captura, pipeline, voz,
  armazenamento, claude, API). Continua sendo o serviço systemd do usuário e é a fonte da verdade:
  **fechar a janela não interrompe o ditado nem uma gravação.** (escolha minha)
- **API local** (escolha minha): HTTP + WebSocket só em `127.0.0.1`, com um token aleatório gerado
  pelo backend num arquivo do usuário (0600) que o app lê — evita que outro processo/site no navegador
  controle a gravação. O framework (ex.: FastAPI/uvicorn) fica a critério do builder.
- **App Tauri**: janela principal, configurações, bandeja e overlay. O app não carrega modelos.
  Iniciar/parar gravação também pela bandeja.

## 8. Comportamento

### 8.1 Gravação de reunião
- Começa e termina só pelo botão (janela ou bandeja). Ao iniciar, o overlay aparece.
- Captura em **duas trilhas separadas**: microfone padrão e monitor da saída padrão (o que o PC toca).
  O áudio do PC inclui tudo o que tocar (notificações, música) — aceito.
- **Eco** (caixa de som): o microfone capta a voz dos outros. Duas camadas (escolha minha):
  1. cancelamento de eco usando o áudio do PC como referência (módulo echo-cancel do PipeWire ou
     AEC WebRTC no app — builder escolhe, desde que a trilha do microfone gravada saia limpa);
  2. rede de segurança no pós-processamento: trecho de "Você" que repete, no mesmo intervalo, fala de
     outra trilha é descartado como eco.
  Com fone de ouvido nada disso deve atrapalhar.
- **Dispositivos**: por padrão, microfone padrão e monitor da saída padrão do sistema, **acompanhando
  mudanças do padrão** durante a gravação (ex.: plugou o headset e o sistema trocou a saída → a
  captura troca junto, sem perder áudio). Nas configurações o usuário pode fixar um microfone e uma
  saída específicos.
- **Seguir o app da chamada** (escolha minha): no Firefox/Chrome o mesmo app toca de tudo, então o
  critério não é o nome do app e sim **qual app está capturando o microfone** (quem está em chamada
  abre o microfone — Firefox, Chrome, Discord web ou app). O backend observa o grafo do PipeWire,
  identifica a(s) saída(s) onde esse app está tocando e:
  - se a saída monitorada é outra, mostra o alerta "áudio da chamada não está sendo captado" e, quando
    o usuário não fixou dispositivo, passa a monitorar a saída certa automaticamente;
  - se nenhum app usa o microfone (ex.: ouvindo mutado), fica no padrão e usa só o alerta de silêncio;
  - se mais de um app usa o microfone, segue o mais recente e mostra no app qual está sendo seguido.
- **Saúde da captura** (aprovado pelo usuário no mockup): o backend mede o nível das duas trilhas
  continuamente e publica para o overlay. Alertas:
  - **microfone sem sinal** (silêncio digital ou abaixo do ruído de fundo) por ~15 s → alerta;
  - **áudio do PC**: saída errada (regra acima) → alerta imediato; silêncio total prolongado → alerta
    com tolerância maior (silêncio dos outros é normal; o builder define o limiar, ex.: 60–120 s sem
    nenhum sinal enquanto o microfone mostra fala);
  - **trilha parou** (dispositivo sumiu, processo de captura morreu) → alerta imediato e tentativa de
    reconectar sem perder o que já foi gravado.
  Os alertas também ficam registrados na reunião (ex.: "microfone sem sinal de 12:10 a 12:40").
- Áudio salvo compactado (ex.: Opus mono 16–24 kHz, ~30 MB/h no total), uma trilha por fonte.
- Se o backend reiniciar no meio de uma gravação, o que já foi gravado não pode ser perdido
  (gravar em disco de forma incremental). (escolha minha)

### 8.2 Processamento
- **Durante a gravação**: transcrever em segundo plano por blocos (ambas as trilhas), para que ao
  parar falte pouco. Meta: reunião de 1 h pronta em ~1–2 min após parar. (escolha minha)
- **Ao parar**: diarização da trilha do PC (pyannote na GPU), extração de embedding por falante,
  comparação com as vozes cadastradas (similaridade com limiar configurável), montagem da linha do
  tempo intercalando "Você" e as outras pessoas, remoção de eco, título.
- **Título**: sugerido automaticamente pelo Claude ao terminar (escolha minha; é a única chamada
  automática, curta e barata; pode ser desligada nas configurações). Sem Claude, "Reunião HH:MM".
- Vocabulário e substituições do ditado valem também para reuniões.
- **Prioridade da GPU**: ditado tem prioridade sobre o processamento de reunião.

- **Ditado durante a reunião** (escolha minha): os intervalos em que o usuário estava ditando
  (Ctrl+Win) são registrados; esses trechos aparecem na transcrição marcados como "Você (ditado)",
  não são descartados.

### 8.3 Pessoas e vozes
- Rótulos: "Você" para o microfone; nome da voz cadastrada quando reconhecida; senão "Pessoa N".
- Na reunião o usuário pode: **identificar** (vincular "Pessoa 2" a uma voz cadastrada nova ou
  existente — guarda/atualiza o embedding), **renomear só nesta reunião**, **juntar** dois falantes
  que o modelo separou errado.
- **Sugerir nomes (Claude)**: lê a transcrição e propõe "Pessoa 2 → João" com o trecho que justifica;
  o usuário confirma cada sugestão. Nunca troca sozinho. Confirmar uma sugestão **só renomeia nesta
  reunião**; cadastrar a voz é uma ação separada e explícita ("salvar voz do João"), para que um
  palpite do Claude não contamine o reconhecimento das próximas reuniões. (escolha minha)
- O banco registra a origem de cada voz cadastrada (reunião e falante de onde veio o embedding).
- Tela de vozes cadastradas: listar, renomear, apagar.

### 8.4 Resumo (Claude)
- Só por botão. Formato padrão (escolha minha): **pontos principais, decisões, tarefas (quem faz o
  quê), pendências**, em pt-BR.
- O prompt fica editável nas configurações. O resultado é salvo na reunião (aba "Resumo") e pode ser
  refeito.
- Chamada via `claude -p` não interativa, sem ferramentas, sem persistir sessão
  (`--no-session-persistence`), saída estruturada quando útil (`--output-format json`). Mostrar erro
  claro se o CLI não estiver logado ou falhar.

### 8.5 Ditado
- Mantém tudo que já existe (Ctrl+Win, colagem por Shift+Insert, vocabulário, substituições,
  detecção de loop, espera de teclas soltas etc.).
- Configurações passam a ser editáveis pela interface (atalho, idioma, modelo, colagem, prompt,
  vocabulário, substituições), com efeito sem reiniciar quando possível.
- Overlay mostra a pílula "Ditando" enquanto a combinação estiver segurada. (escolha minha)
- O ditado continua funcionando durante uma gravação de reunião.

## 9. Interface

### 9.1 Janela principal (aprovada)
- **Esquerda**: botão grande "Iniciar reunião" (vira "Parar · 12:34" gravando), busca nas reuniões,
  lista agrupada por dia (título, hora, duração, nº de pessoas), rodapé com estado do ditado e
  engrenagem de configurações.
- **Direita (reunião aberta)**: título editável (sugerido pelo Claude), data/hora; etiquetas coloridas
  dos participantes ("Pessoa N" tracejada com "identificar"); ações **Resumir**, **Copiar transcrição**,
  **Sugerir nomes**, **Exportar .md**, **Ouvir áudio**; abas **Transcrição** (hora · quem · texto, com ▶
  para ouvir o trecho) e **Resumo**.
- Enquanto processa: estado visível ("transcrevendo… / separando vozes…").
- Busca: texto da transcrição e títulos.
- Idioma da interface: pt-BR.

### 9.2 Overlay
- Pílula "● mm:ss" com dois medidores mínimos — 🎙 (microfone) e 🔊 (áudio do PC) — que se mexem com
  o volume; canto superior direito, ~35–40% de opacidade, sempre por cima, clique atravessa, sem foco
  nem entrada na barra de tarefas. No ditado: "● Ditando".
- **Alerta**: a pílula fica opaca e laranja, com o texto do problema ("Microfone sem sinal", "Áudio
  da chamada não está sendo captado"), até a condição normalizar.
- Como o GNOME Wayland não permite "sempre por cima" a apps nativos Wayland: rodar a janela do overlay
  via XWayland (onde `_NET_WM_STATE_ABOVE` e input shape funcionam). Se não ficar confiável,
  alternativa: pequena extensão do GNOME Shell que desenha a pílula a partir dos eventos do backend.

### 9.3 Configurações
- Ditado (atalho, idioma, modelo, colagem, prompt), vocabulário, substituições, vozes cadastradas,
  prompt do resumo, título automático liga/desliga, token do Hugging Face, dispositivos de áudio
  (padrão do sistema por default, ou microfone/saída fixos), com **medidores ao vivo** para testar
  antes da reunião.

### 9.4 Bandeja
- Ícone indicando estado (ocioso / gravando / processando); menu: iniciar/parar reunião, abrir app,
  pausar ditado, sair do app (não do backend).
- O app **inicia no login**, minimizado na bandeja (autostart `.desktop`), para o overlay e a bandeja
  estarem sempre disponíveis. (escolha minha)
- "Sair do app" durante uma gravação pede para parar a gravação antes — assim o indicador nunca some
  com uma gravação rodando. (escolha minha)

## 10. Armazenamento

- Dados em `~/.local/share/whisper-ditado/` (escolha minha): SQLite (reuniões, falantes, segmentos,
  vozes com embeddings, resumos, configurações editáveis pela UI) + pasta de áudio por reunião.
- Nada é apagado automaticamente; o usuário pode excluir uma reunião (apaga áudio junto).
- Vocabulário/substituições continuam em `config/` do repositório, que é a fonte da verdade:
  `~/.config/whisper-ditado` é um symlink para essa pasta (o usuário edita por ali no app do Claude).
  A interface edita esses mesmos arquivos; edições aparecem como mudanças no git, o que serve de
  histórico — o usuário commita quando quiser. (escolha minha)

## 10.1 Transição sem quebrar o ditado (escolha minha)

O serviço atual roda direto deste checkout e o usuário dita o dia todo. Durante o desenvolvimento:
- o `ditado.py`, o `run.sh` e o `whisper-ditado.service` atuais ficam **congelados e funcionando**;
- o backend novo nasce ao lado (pacote/entrypoint próprio), reaproveitando a lógica do ditado por
  cópia/refatoração, sem mexer no que está em produção;
- uma tarefa final faz a troca do serviço, com checagem de regressão (latência do ditado, colagem,
  atalho) e caminho de volta documentado.

## 11. Deixado para o builder

- Divisão interna de módulos do backend, framework HTTP, esquema exato do SQLite e migrações.
- Biblioteca de captura (pw-record por trilha, bindings PipeWire, etc.) e método de AEC.
- Versão/pipeline do pyannote (ex.: `speaker-diarization-community-1` ou 3.1) e o modelo de embedding,
  desde que rode local na GPU e caiba junto com o Whisper nos 16 GB.
- Tamanho dos blocos de transcrição incremental e como alinhar com a diarização no final.
- Limiar de reconhecimento de voz e como atualizar o embedding de uma voz com novas amostras.
- Biblioteca de UI em Vue, empacotamento (.deb/AppImage ou só `cargo tauri build` local), ícones.
- Testes: a lógica do ditado hoje não tem testes automatizados; o novo backend deve ter testes para
  a máquina de estados do ditado, o pipeline (com áudio sintético) e a API.
