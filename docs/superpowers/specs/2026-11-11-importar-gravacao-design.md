# Importar gravação ou transcrição — design

Data: 2026-11-11

## 1. Intenção

Hoje o TARS só trata reuniões que ele mesmo gravou. Reuniões gravadas em outro lugar (no Read AI,
por exemplo, ou num vídeo MP4 recebido de alguém) ficam de fora. Elas não aparecem na lista e não
geram resumo nem tarefas.

Com esta funcionalidade, o usuário **importa um arquivo** e ele vira uma reunião comum na lista,
com transcrição separada por pessoa, resumo e tarefas, como as gravadas pelo TARS.

O caso que motivou: uma reunião gravada no Read AI. O usuário baixou, logado, a transcrição em
`.txt` e quer trazê-la para o TARS para ter o resumo e as tarefas no mesmo lugar das outras.

## 2. Objetivos

1. Botão **"Importar"** no topo da janela principal, ao lado de "Iniciar reunião". Ele abre a
   escolha de arquivo; arrastar um arquivo para a janela tem o mesmo efeito.
2. Três tipos de arquivo aceitos:
   - **Transcrição do Read AI (`.txt`)**: importada direto, sem áudio e sem GPU.
   - **Vídeo** (MP4 e outros que o `ffmpeg` lê): só o áudio é usado.
   - **Áudio** (mp3, m4a, wav, ogg, opus, flac etc.).
3. Depois de escolher o arquivo, uma **tela de confirmação** (rascunho aprovado, seção 6) mostra:
   - o arquivo, com tipo, duração e número de pessoas (quando dá para saber);
   - o **título**, preenchido e editável;
   - o **início da reunião (data e hora)**, sugerido e editável;
   - **"Quem é você?"**, só na transcrição: lista os nomes que aparecem no arquivo, mais
     "Nenhuma delas".
4. **Transcrição importada**: cada bloco do arquivo vira uma fala, com a pessoa e o horário. A
   pessoa escolhida como "você" aparece como **"Você"**, igual às reuniões gravadas. A reunião fica
   pronta na hora e segue o fluxo normal pós-reunião (resumo automático, se ligado; tarefas).
5. **Vídeo/áudio importado**: o áudio é convertido e processado **pelo mesmo pipeline de uma
   reunião gravada** (Whisper com o vocabulário do usuário, separação de falantes, casamento com as
   vozes salvas). A reunião fica com player, "Ouvir esta pessoa" e aprendizado de vozes.
6. **"Sou eu"** no menu da etiqueta de uma pessoa, em reuniões importadas de vídeo/áudio: marca
   aquela pessoa como "Você". Num arquivo misturado, o TARS não sabe sozinho qual voz é a do
   usuário (ver §3).

## 3. Não objetivos

- **Colar um link** (Read AI ou qualquer URL). O usuário decidiu que não é necessário. Além disso,
  o link público do Read AI não permite baixar o vídeo sem login, e extrair a gravação do player
  contornaria essa restrição do serviço.
- **Reconhecer automaticamente a voz do usuário** em vídeo/áudio importado. Hoje "Você" vem do
  canal do microfone, e o TARS não guarda uma impressão da voz do usuário. Isso fica para depois.
  Na v1, quem resolve é o "Sou eu" (objetivo 6).
- Outros formatos de transcrição (`.vtt`, `.srt`, `.docx`, exportações de Zoom/Teams/Meet etc.).
  Só o `.txt` do Read AI na v1. O parser deve ficar isolado para outros entrarem depois.
- Importar vídeo **e** transcrição da mesma reunião juntos, ou casar uma transcrição importada com
  um áudio importado depois.
- Detectar importação duplicada.
- **Cancelar uma importação em andamento ou dar prioridade à gravação** (decisão do usuário: o
  mais simples). A importação de vídeo/áudio se comporta como o "Reprocessar" de hoje: enquanto
  está `processing`, não pode ser apagada nem arquivada, e divide a fila única da GPU com a
  transcrição ao vivo. Uma reunião gravada durante uma importação longa não perde áudio, mas a
  transcrição ao vivo e a finalização dela esperam a importação terminar.
- Vincular as pessoas de uma transcrição importada às **vozes salvas** (não há áudio para
  comparar). O nome do arquivo vira o rótulo da pessoa e pronto.
- Guardar o vídeo. Só o áudio convertido fica no TARS; o arquivo original não é alterado nem
  apagado.

## 4. Anti-objetivos (falharia mesmo funcionando)

- A importação trava a interface ou o ditado enquanto converte/transcreve um arquivo longo.
- Uma reunião importada quebra telas que assumem áudio: player com erro, "Reprocessar" que leva a
  reunião a `failed`, "Ouvir esta pessoa" sem som.
- A reunião importada aparece com a data da importação em vez da data real da reunião.
- O arquivo original do usuário é modificado, movido ou apagado.

## 5. Formato do `.txt` do Read AI

Exemplo real: `~/Downloads/Awake e Angular - mudança de mecanismo _ Transcrição.txt` (35 min, 2
pessoas, ~400 linhas, UTF-8).

```
Awake e Angular - mudança de mecanismo de créditos      ← linha 1: título
qua., 11 de nov. de 2026                                ← linha 2: data (pt-BR abreviada)
                                                        ← linha vazia
0:03 - Felipe Iaropoli                                  ← cabeçalho de fala: tempo - nome
Opa, fala ali.                                          ← texto
                                                        ← linha vazia separa blocos
0:12 - Felipe Iaropoli
Deixa eu te mostrar ele. ...                            ← primeiro parágrafo
                                                        ← linha vazia
 Daí você tem o tipo de crédito, ...                    ← continuação: começa com espaço
```

Regras:

- Cabeçalho de fala: `^(\d+:)?\d{1,2}:\d{2} - (.+)$`. O tempo é `M:SS` ou `H:MM:SS` (reuniões
  com mais de uma hora; formato presumido, cobrir com teste).
- Parágrafos de continuação (linhas iniciadas por espaço depois de uma linha vazia) pertencem ao
  bloco anterior e são unidos a ele.
- Fim de cada fala = início da próxima. Na última, o fim é o início mais uma estimativa pelo
  tamanho do texto (por exemplo, ~15 caracteres por segundo, com mínimo de 1 s).
- Data em pt-BR abreviado (`qua., 11 de nov. de 2026`). Se não reconhecer a data, a sugestão cai
  para a data de modificação do arquivo.
- Arquivo que não segue o formato (sem nenhum cabeçalho de fala reconhecido): erro claro na tela
  de confirmação ("Formato de transcrição não reconhecido; hoje só a do Read AI é aceita").

## 6. Interface (rascunho aprovado)

Rascunho em `.superpowers/brainstorm/*/content/importar.html` (pasta ignorada pelo git), aprovado
pelo usuário nas três partes:

1. **Botão "Importar"** no topo, ao lado do bloco "microfone + áudio do PC | Iniciar reunião",
   com estilo secundário (contorno), para não competir com "Iniciar reunião".
2. **Escolha do arquivo**: o diálogo nativo (`@tauri-apps/plugin-dialog` `open`, já usado no app
   com `save`), filtrando os tipos aceitos. Arrastar um arquivo para a janela leva à mesma tela.
3. **Tela de confirmação**, que muda conforme o tipo:
   - **Transcrição**: título; início (data + hora); "Quem é você?" com os nomes do arquivo em
     ordem de primeira fala, mais "Nenhuma delas"; botão **Importar**; aviso de que não há
     player nem aprendizado de vozes.
   - **Vídeo/áudio**: título; início (data + hora); nota explicando que as pessoas só aparecem
     depois da transcrição e que, para marcar a sua voz, basta clicar na etiqueta e escolher
     "Sou eu"; botão **Importar e transcrever**; aviso de que usa a GPU e leva alguns minutos.

**Diferença em relação ao rascunho:** ele dizia que o TARS "tenta reconhecer a sua voz". Isso não
existe hoje (§3), então a nota fala só do "Sou eu".

Sugestões da tela:

| Campo | Transcrição | Vídeo/áudio |
|---|---|---|
| Título | linha 1 do arquivo | nome do arquivo sem extensão |
| Data | linha 2 do arquivo | `creation_time` dos metadados (`ffprobe`); senão, modificação do arquivo − duração |
| Hora | modificação do arquivo − duração, se cair na mesma data do arquivo; senão, vazia e obrigatória | igual à data |
| Quem é você | nome igual ao "seu nome" lembrado (ver abaixo); senão, nenhum marcado | — |

**Lembrar o nome do usuário:** ao importar uma transcrição com uma pessoa marcada como você, o
TARS grava esse nome numa configuração (`my_name`). Nas próximas importações, essa pessoa já
vem marcada.

Depois de confirmar, a janela abre a reunião nova. A de vídeo/áudio aparece como "processando",
com as mesmas etapas de progresso de uma gravada.

## 7. Como funciona por trás

O app e o backend rodam na mesma máquina e com o mesmo usuário: o app manda **o caminho do
arquivo**, e o backend lê direto do disco, sem upload pela API.

### 7.1 API

- `POST /imports/inspect` `{path}` → `{kind: "transcript"|"media", title, started_at_suggestion,
  time_known: bool, duration_s, people: [nomes] | null, my_name_match: nome | null}`, ou 400 com
  a mensagem em pt-BR (arquivo inexistente, formato não reconhecido, mídia sem trilha de áudio).
- `POST /imports` `{path, title, started_at, me: nome | null}` → `{meeting_id}`.

### 7.2 Transcrição

1. Faz o parse (§5) e monta falantes e segmentos:
   - a pessoa escolhida → `Speaker(is_me=True, label="Você")`, segmentos com `source="mic"`;
   - as demais → `Speaker(label=nome, voice_id=None)`, segmentos com `source="pc"`, cores na
     ordem de primeira fala, como em `_build_speakers`.
2. Cria a reunião `ready`, com `started_at` escolhido e `ended_at = started_at + fim da última
   fala`, título manual (`title_auto=0`, porque veio do arquivo ou foi editado). Aplica o
   vocabulário (`vocab.apply`) ao texto? **Não**: o texto já vem transcrito e o vocabulário
   corrige o Whisper, não terceiros.
3. Grava com `store.replace_transcript` e dispara o fluxo pós-reunião (`jobs.on_ready`), o mesmo
   de uma gravação (resumo automático se `auto_summary` estiver ligado).

### 7.3 Vídeo/áudio

1. Cria a reunião `processing` com `started_at` escolhido e título manual.
2. Converte o arquivo para `meetings/<id>/pc.opus` (mono, 16 kHz, Opus 16 kbit/s, os mesmos
   parâmetros de `compress_track`) via `ffmpeg`, gravando em `.part` e renomeando no fim. O
   tempo limite precisa comportar arquivos longos (o `FFMPEG_TIMEOUT_S` atual é 900 s).
3. Agenda na fila da GPU do `MeetingManager` um trabalho que roda `fin.reprocess` (transcreve,
   separa falantes, casa com as vozes) e, ao terminar `ready`, chama `_notify_ready`. Atenção:
   o `_reprocess_job` atual **não** chama `_notify_ready` (só `_finalize_session` e
   `_recover_job` chamam), então o trabalho de importação precisa chamá-lo por conta própria
   para disparar resumo e tarefas.
   Sem a trilha `mic`, todas as falas ficam como `pc`, e não há "Você" até o usuário usar
   "Sou eu".
4. Conversão e processamento rodam fora da thread da requisição. A fila da GPU já serializa com
   ditado, gravação e outros reprocessamentos.
5. Se o backend cair no meio, o `recover` existente cobre: com `pc.opus` presente, a reunião é
   finalizada; sem ele, fica `failed`.

### 7.4 Dados

- Coluna nova `meetings.origin TEXT NOT NULL DEFAULT 'recorded'`, com os valores `recorded`,
  `import_media` e `import_transcript`, numa migração nova no padrão das existentes. A API devolve
  `origin` no detalhe e na lista da reunião.
- Configuração nova `my_name` (texto, vazio por padrão).

### 7.5 Telas que mudam com `origin`

- `import_transcript`: sem player, sem "Reprocessar", sem "Ouvir esta pessoa" nem
  "Cadastrar voz" no menu da pessoa. O backend recusa (409) reprocessar e cadastrar voz nessas
  reuniões, e não só a interface.
- `import_media`: igual a uma gravada, com o "Sou eu" a mais no menu da pessoa.
- "Sou eu" (`import_media`): marca o falante com `is_me=True` e `label="Você"`, desfaz a marca
  de outro falante da mesma reunião que estivesse marcado e remove o vínculo de voz desse
  falante. Um reprocessamento deve **preservar** essa marca, como hoje preserva os vínculos de
  voz (`_previous_links`/`inherit_links`).
- **Desfazer o "Sou eu"** (decisão do Claude): hoje "Você" não tem menu
  (`MeetingDetail.vue:313`). Em `import_media`, "Você" ganha um menu com **"Não sou eu"**, que
  tira a marca e devolve a pessoa como "Pessoa N" (o próximo número livre na reunião). O menu de
  "Você" também traz "Ouvir esta pessoa"; renomear não faz sentido para "Você". Um
  reprocessamento preserva tanto a marca quanto a ausência dela.

## 8. Restrições

- Linux, app Tauri (WebKitGTK) e backend Python. O `ffmpeg` já é dependência.
- Textos da interface e mensagens de erro em português.
- Testes: `pytest` para o parser (incluindo `H:MM:SS`, continuação de parágrafo, data inválida e
  arquivo fora do formato), para a criação da reunião nos dois caminhos e para o bloqueio de
  reprocessar/cadastrar voz; testes de componente (`pnpm test`) para a tela de confirmação e
  para o menu da pessoa. Testes com modelos reais (`pytest -m gpu`) para o caminho de mídia, já
  que ele passa por transcrição e diarização.
- README: seção curta sobre importar.

## 9. Deixado para quem constrói

- Layout exato do botão e da tela, seguindo o visual Ametista e os componentes de `app/src/components/ui`.
- Como o arrastar e soltar entra (evento de drag-drop do Tauri na janela).
- Nome dos módulos (sugestão: `meeting/importer.py` com o parser em `meeting/readai.py`).
- Estimativa da duração da última fala e mensagem de progresso da conversão.
- Se a conversão do `ffmpeg` publica uma etapa própria de progresso ("convertendo") ou entra em
  "transcrevendo".
