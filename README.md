# TARS

**Da voz à ação.** Ditado por voz, transcrição do áudio do computador, reuniões e criação de issues.

[GitHub](https://github.com/vateiixeira/tars) · [Licença MIT](LICENSE)

Ditado por voz em qualquer janela: **segure Ctrl + Win (Super), fale e solte**. O texto
transcrito pelo Whisper (faster-whisper `large-v3-turbo`, na GPU) é colado onde
está o cursor.

Feito para GNOME (Wayland ou X11): o teclado é lido via evdev (o usuário precisa estar no
grupo `input`) e a colagem copia o texto com `wl-copy` (Wayland) ou `xclip` (X11, com `xsel`
como alternativa) e simula o atalho de colar com o `ydotool` (serviço `ydotool.service`).

## Estado do projeto

Primeira versão pública, destinada a **Linux com GNOME (Wayland ou X11), PipeWire e GPU NVIDIA**.
O reconhecimento usa CUDA; CPU, macOS e Windows ainda não são suportados pelo backend atual.
Whisper e separação de falantes rodam localmente. Resumos e criação de issues usam o Claude Code
(opcional) e podem enviar a transcrição para esse serviço e os campos aprovados para o MCP.

## Releases

As versões são publicadas na [página de releases](https://github.com/vateiixeira/tars/releases).
O pacote Linux x86_64 inclui o app compilado e o backend Python, acompanhado de checksum SHA-256.
Veja [como instalar o pacote](docs/INSTALL_RELEASE.md) e o [fluxo para publicar versões](docs/RELEASING.md).

## Instalação a partir do código

Pré-requisitos: Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
Node.js 24+, pnpm 12.6.0, Rust estável e driver NVIDIA compatível com CUDA 12.8.
O primeiro uso baixa os modelos; reserve espaço em disco e VRAM para eles. As bibliotecas CUDA
Python são instaladas pelo uv, e `run-backend.sh` configura seus caminhos.

Em Ubuntu 24.04, instale as ferramentas de áudio, teclado e compilação:

```bash
sudo apt update
sudo apt install build-essential python3-dev pkg-config ffmpeg pipewire-bin wl-clipboard xclip ydotool libnotify-bin espeak-ng \
  libwebkit2gtk-4.1-dev libxdo-dev libssl-dev librsvg2-dev libayatana-appindicator3-dev
```

Confira também os [pré-requisitos oficiais do Tauri](https://v2.tauri.app/start/prerequisites/)
para a sua distribuição. O `ydotoold` precisa estar em execução e ter permissão para escrever em
`/dev/uinput`; os atalhos globais precisam de leitura dos dispositivos `/dev/input/event*`.
Em distribuições que usam o grupo `input`, adicione seu usuário e entre novamente na sessão:

```bash
sudo usermod -aG input "$USER"
```

Esse grupo permite ler os eventos do teclado. Verifique a regra de permissões de `/dev/uinput`
da sua distribuição. Quando o pacote fornece a unidade de usuário, ative o daemon com
`systemctl --user enable --now ydotool.service`; em outras distribuições siga a instalação do
[ydotool](https://github.com/ReimuNotMoe/ydotool).

Clone e compile o projeto:

```bash
git clone https://github.com/vateiixeira/tars.git
cd tars
uv sync --frozen --group dev
cd app
pnpm install --frozen-lockfile
pnpm tauri build --no-bundle
cd ..
```

Para testar sem instalar lançadores, rode `./run-backend.sh` em um terminal e abra
`./app/src-tauri/target/release/whisper-ditado-app` em outro. Para instalar o serviço de usuário e
adicionar TARS ao menu de aplicativos, usando o caminho real do clone:

```bash
python3 scripts/install_user.py --dry-run
python3 scripts/install_user.py --enable --start
```

Não execute o instalador com sudo. Os nomes internos `whisper-ditado`, os serviços e os caminhos
de dados foram mantidos para compatibilidade com instalações existentes. Os arquivos `.service`
e `.desktop` da raiz são modelos; o instalador substitui `@PROJECT_ROOT@` pelo caminho do clone.
A pasta do clone deve permanecer no mesmo lugar depois da instalação.

Resumos e criação de issues exigem o [Claude Code](https://code.claude.com/docs/en/setup)
instalado e autenticado. A separação de falantes é opcional e usa o token explicado abaixo.

## Desenvolvimento

Veja [CONTRIBUTING.md](CONTRIBUTING.md) para executar testes e enviar mudanças.
O [CI](https://github.com/vateiixeira/tars/actions) verifica backend, interface e Rust;
os testes com modelos reais na GPU são executados separadamente.

## Uso

- Segure **Ctrl + Win** (qualquer lado, em qualquer ordem), fale e solte. O ícone de microfone do GNOME aparece enquanto grava.
- Soltar qualquer uma das duas encerra a gravação.
- Se você apertar outra tecla durante a combinação (ex.: Ctrl+Win+Seta), a gravação é descartada.
- Gravações com menos de 0,4 s são ignoradas.

## Serviço

O ditado roda no backend novo (`whisper-ditado-backend.service`), que também grava reuniões e
serve a API local do app.

```bash
systemctl --user status whisper-ditado-backend      # estado
journalctl --user -u whisper-ditado-backend -f      # logs
systemctl --user restart whisper-ditado-backend     # reiniciar
systemctl --user disable --now whisper-ditado-backend  # desligar
```

Rodar manualmente (para depurar): `./run-backend.sh` (com o serviço parado).

Ao iniciar, o log informa a sessão usada para colar (`Colagem pelo clipboard da sessão wayland`
ou `x11`). Se aparecer o aviso de que o ditado não vai conseguir colar, o serviço subiu sem as
variáveis da sessão gráfica. No X11 ele precisa de `DISPLAY` e `XAUTHORITY`; importe-as e
reinicie:

```bash
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XAUTHORITY
systemctl --user restart whisper-ditado-backend
```

### Daemon legado

`ditado.py` e `run.sh` continuam disponíveis para compatibilidade. Use apenas um processo de ditado
por vez. As unidades `.service` da raiz são modelos; o instalador configura o backend atual.

## Configuração

As configurações do ditado ficam no app (Configurações → Ditado). As variáveis de ambiente abaixo,
definidas como `Environment=` no `whisper-ditado-backend.service`, valem como padrão:

| Variável | Padrão | Descrição |
|---|---|---|
| `DITADO_KEY` | `ctrl+super` | Combinação de "segurar para falar": apelidos `ctrl`, `super`/`win`, `alt`, `shift` (qualquer lado) ou nomes do evdev, unidos por `+` (ex.: `KEY_RIGHTCTRL`, `alt+KEY_SPACE`) |
| `DITADO_LANG` | `pt` | Idioma; vazio = detecção automática |
| `DITADO_MODEL` | `large-v3-turbo` | Modelo do faster-whisper (`large-v3`, `medium`...) |
| `DITADO_COMPUTE_TYPE` | `float16` | Precisão na GPU (`int8_float16` usa menos VRAM) |
| `DITADO_PASTE` | `shift+insert` | Atalho de colar: `shift+insert`, `ctrl+v` ou `ctrl+shift+v` |
| `DITADO_PROMPT` | frase em pt-BR com termos em inglês | Prompt inicial: ensina o estilo (pontuação, termos técnicos escritos em inglês); vazio desativa |
| `DITADO_TRAILING_SPACE` | `1` | Adiciona espaço no fim, para ditados seguidos não grudarem |
| `DITADO_MIN_SEC` / `DITADO_MAX_SEC` | `0.4` / `300` | Duração mínima e máxima da gravação |
| `DITADO_IGNORE_DEVICES` | `ydotoold` | Dispositivos ignorados (por nome, separados por vírgula) |
| `DITADO_DEBUG` | `0` | `1` mostra o texto transcrito nos logs |
| `DITADO_MEETING_MODEL_IDLE_SEC` | `120` | Segundos sem reunião/processamento antes de liberar os modelos de reunião; `0` mantém carregados |

`shift+insert` é o padrão porque cola em apps GTK, Chrome/Electron e terminais VTE
(o texto também é copiado para a seleção primária).

Em **Configurações → Whisper**, o modelo e a precisão valem para ditado e reuniões:

- **Large v3 Turbo** (`large-v3-turbo`, padrão recomendado): equilíbrio entre qualidade e velocidade.
- **Large v3** (`large-v3`): modelo completo, mais pesado e lento.
- **Medium** (`medium`): tamanho intermediário; pode ser mais lento que o Turbo.
- **Small** (`small`): menor uso de memória, com maior chance de erros em nomes e termos técnicos.
- **Precisão padrão** (`float16`) ou **menor uso de memória** (`int8_float16`). O consumo e a
  velocidade dependem do hardware e do áudio; não são estimativas de memória total do backend.

As opções são multilíngues, adequadas para português. As recomendações seguem as referências do
[Whisper](https://github.com/openai/whisper#available-models-and-languages) e do
[faster-whisper](https://github.com/SYSTRAN/faster-whisper#usage). O primeiro uso de um modelo pode
exigir download. A troca no ditado acontece em segundo plano, mantendo o modelo anterior até o
novo ficar pronto. Reuniões em andamento mantêm o modelo atual até o backend ficar ocioso.

Em **Configurações → Reuniões → Liberar memória dos modelos de reunião**, escolha o prazo de
inatividade (padrão: 2 minutos) ou mantenha os modelos carregados. A manutenção verifica a cada
5 segundos, sem descarregar durante gravação ou processamento. O Whisper das reuniões e o
separador de vozes são descarregados e os caches não utilizados são liberados; o Whisper do
ditado continua pronto. A próxima gravação começa imediatamente, enquanto o transcritor é
recarregado em segundo plano. A separação de vozes recarrega seu modelo ao finalizar. Bibliotecas,
contextos CUDA e o modelo de ditado continuam usando memória mesmo com as reuniões ociosas.

## Termos em inglês

Os dois arquivos são relidos a cada ditado, então não é preciso reiniciar depois de editar.
No backend atual eles ficam na pasta `config/` deste repositório e podem ser editados pelo app.
As variáveis `DITADO_VOCAB` e `DITADO_SUBS` só são usadas pelo daemon antigo (`ditado.py`).

- **`config/vocabulario.txt`**: um termo por linha, escrito como deve sair
  (`pull request`, `Django`, `NFSe`). Ele é passado ao Whisper como `hotwords`, o que puxa o
  reconhecimento para esses termos. A ordem é a prioridade: o limite é de ~220 tokens (o log
  mostra quantos você está usando), e o que passar disso no fim da lista é ignorado.
- **`config/substituicoes.txt`**: correções depois da transcrição, uma por linha
  (`errado => certo`). Troca a expressão inteira, sem diferenciar maiúsculas, numa passada só (as trocas
  não se encadeiam). Um destino vazio (`né =>`) remove a palavra. Use para o que o modelo
  insiste em errar mesmo com o vocabulário.

Se o vocabulário fizer o Whisper entrar em loop ("revisado, revisado, revisado..."), o daemon
detecta e transcreve de novo, bloqueando repetições.

## Avaliador de ditado

Em **Configurações → Ditado → Avaliador de ditado**, ligue a coleta de exemplos. Vem desligada
por padrão; também pode ser configurada com `DITADO_DICTATION_EVALUATION_ENABLED=1` ou
`PUT /api/settings` com `{"dictation_evaluation_enabled": true}`. Não precisa reiniciar para
ligar/desligar pelo app. A opção vale para as próximas gravações; desligar antes de soltar o
atalho também impede que aquela gravação seja coletada.

O ditado continua transcrevendo e colando normalmente. Depois da tentativa de colagem, uma
fila separada salva **todos os ditados válidos**, tanto os acertos quanto os possíveis erros,
incluindo transcrições vazias e falhas de transcrição/colagem. Gravações canceladas ou abaixo
da duração mínima continuam descartadas. Falhas de disco ou fila cheia são registradas nos
logs e não interrompem a colagem.

Os exemplos ficam em `$XDG_DATA_HOME/whisper-ditado/dictation-samples/<id>/` (padrão
`~/.local/share/whisper-ditado/dictation-samples/<id>/`):

- `audio.wav`: áudio original, mono, PCM 16 bits, 16 kHz, sem redução de ruído;
- `sample.json`: texto produzido, horário, duração, sucesso da colagem, eventual erro e
  configuração do transcritor, vocabulário e substituições usados;
- o índice e as avaliações ficam na tabela `dictation_samples` do `ditado.db` existente.

Pastas são privadas (0700), arquivos 0600. Desligar a coleta preserva os exemplos existentes;
não há exclusão automática. A coleta **não envia áudio para serviços externos**.

### Comparação em lote com um modelo que escuta áudio

A ferramenta usa a [entrada de áudio do Gemini](https://ai.google.dev/gemini-api/docs/audio)
para obter uma transcrição independente, sem mostrar ao avaliador o texto do Whisper.
Depois compara as palavras, ignorando pontuação e caixa, e salva a referência, as diferenças,
as dúvidas e a identificação do modelo. Isso reúne evidências para ajustar vocabulário,
substituições ou o reconhecimento; nenhuma correção é aplicada automaticamente.

Na raiz do projeto:

```bash
# Somente operações locais, sem chave nem acesso à rede:
.venv/bin/python -m whisper_ditado.evaluation report
.venv/bin/python -m whisper_ditado.evaluation export > /caminho/privado/ditados.jsonl

# Defina GEMINI_API_KEY no ambiente antes de executar.
# Este comando envia até 20 áudios pendentes ao serviço Gemini, sujeito à cota/cobrança da API:
.venv/bin/python -m whisper_ditado.evaluation evaluate --limit 20

# Para escolher outro modelo que aceite áudio e saída JSON:
.venv/bin/python -m whisper_ditado.evaluation evaluate --limit 10 --model gemini-2.5-flash
```

O modelo padrão é `gemini-2.5-flash`. Cada resultado é salvo antes de passar ao próximo áudio;
uma nova execução continua pelos pendentes. Falhas de rede/API mantêm o exemplo pendente.
O export inclui os caminhos dos WAVs e os resultados em JSONL, permitindo análise posterior.
O relatório separa concordâncias, divergências e casos incertos e agrupa diferenças recorrentes.

**A avaliação por IA é uma estimativa sujeita a revisão humana**, não uma medida de acurácia
real. O modelo também pode errar. Os originais permanecem preservados para conferir e comparar
melhorias futuras; o banco não treina nem modifica o Whisper automaticamente.

## Backend

O processo em `src/whisper_ditado/` reúne o ditado, a gravação de reuniões (microfone + áudio do
PC, transcrição, separação de falantes, resumo pelo Claude) e uma API local para o app desktop.

```bash
./run-backend.sh                          # ditado + reuniões + API em 127.0.0.1:47321
./run-backend.sh --port 47399             # outra porta
./run-backend.sh --no-dictation           # só reuniões/API (não disputa o teclado com o serviço atual)
```

Não rode duas instâncias com o ditado ligado (o serviço e uma manual, ou o backend e o
`whisper-ditado.service` antigo): todas leem o teclado e colam o texto mais de uma vez. Para testar
ao lado do serviço, use `--no-dictation` e outra porta.

- **Dados** em `$XDG_DATA_HOME/whisper-ditado` (padrão `~/.local/share/whisper-ditado`, modo 0700):
  `ditado.db` (reuniões, falantes, vozes, configurações), `meetings/<id>/` (áudio `mic.opus` e
  `pc.opus`) e `api.json`.
- **`api.json`** guarda `{"port", "token", "pid"}` (modo 0600). O token é novo a cada início e vai em
  `Authorization: Bearer <token>` (no WebSocket `/api/events`, em `?token=`). O arquivo é removido ao
  encerrar normalmente (SIGTERM/Ctrl+C); depois de um `kill -9` ele fica velho, então confira o `pid`.
  O log de acesso do servidor fica desligado para o token nunca aparecer nos logs.
- **Encerrar** (SIGTERM/Ctrl+C) fecha a gravação em andamento. Se a reunião ainda estiver sendo
  finalizada, ela termina no próximo início. Uma queda (`kill -9`, falta de energia) também é
  recuperada: ao subir, o backend finaliza as reuniões que ficaram presas.
- **Excluir uma reunião** fica bloqueado enquanto houver tarefas sendo criadas ou aguardando
  conferência no MCP de issues. Confira as tarefas incertas antes de excluir, para preservar o resultado
  de uma criação que pode já ter sido enviada.
- **Reprocessar** recria a transcrição e os falantes, incluindo nomes editados e mesclagens.
  O app pede confirmação quando já existem falantes. Vozes cadastradas, título editado, resumo e
  sugestões de tarefas são mantidos; resumo e levantamento só são refeitos pelos seus botões.
- **Configurações** pela API (`GET/PUT /api/settings`); as variáveis `DITADO_*` acima continuam
  valendo como padrão.

### Separação de falantes (token do Hugging Face)

A separação de falantes usa o `pyannote/speaker-diarization-community-1`, um modelo com acesso
restrito. Sem token a reunião é transcrita normalmente, mas com um único falante ("Participantes") e
um alerta `no_diarization`. Para ativar:

1. Entre em <https://huggingface.co/pyannote/speaker-diarization-community-1> logado e aceite os termos de uso.
2. Em <https://huggingface.co/settings/tokens> crie um token do tipo **Read**.
3. Cole o token no app, em Configurações → Reuniões (vale a partir da próxima reunião, sem
   reiniciar). Também dá por `PUT /api/settings` com `{"hf_token": "hf_..."}` ou pela variável de
   ambiente `HF_TOKEN` ao iniciar o backend. Depois
   do primeiro download o modelo carrega do cache local, sem rede. Se o carregamento falhar (por
   exemplo, termos não aceitos), um token novo é tentado já na próxima reunião; com o mesmo token,
   o backend tenta de novo nas reuniões seguintes a cada 5 minutos, sem reiniciar.

## App

App desktop (Tauri + Vue) em `app/`: janela de reuniões e configurações, ícone na bandeja e a pílula
de gravação no canto superior direito. Ele não carrega modelos, só fala com o backend.

```bash
cd app && pnpm install && pnpm tauri build --no-bundle   # gera app/src-tauri/target/release/whisper-ditado-app
cd app && pnpm tauri dev                                  # desenvolvimento
```

- O app abre no login, minimizado na bandeja (`~/.config/autostart/whisper-ditado-app.desktop`), e
  aparece no menu de apps (`~/.local/share/applications/whisper-ditado-app.desktop`). Os dois apontam
  para `app/src-tauri/target/release/whisper-ditado-app`: depois de um `cargo clean`, rode o build de
  novo, senão o app some do login e do menu.
- Fechar a janela só esconde (o app fica na bandeja). Para sair, use "Sair" na bandeja; com uma
  gravação em andamento, ele pede para parar antes.
- Para identificar uma pessoa, clique na etiqueta dela no cabeçalho da reunião. Em **Ouvir esta
  pessoa**, escolha um dos até três trechos de no máximo dez segundos; os controles permitem
  pausar e repetir enquanto você renomeia ou identifica a voz. Fechar o menu interrompe o áudio.
- A pílula roda num segundo processo forçado em XWayland (`GDK_BACKEND=x11`), onde "sempre por cima"
  e "clique atravessa" funcionam no GNOME.
- No Linux com driver NVIDIA carregado, a janela principal inicia com
  `WEBKIT_DISABLE_DMABUF_RENDERER=1` para contornar áreas pretas ou conteúdo que deixa de ser
  redesenhado pelo WebKitGTK. O ajuste acontece antes de iniciar a interface e não é imposto à
  pílula transparente. Um valor explícito dessa variável é respeitado (`0` permite testar o
  caminho original). É uma mitigação para a [incompatibilidade gráfica documentada pelo Tauri](https://tauri.app/es/develop/debug/linux-graphics/),
  com possível custo de desempenho gráfico; não altera o uso da GPU pelo Whisper.

## Notas

- O texto ditado substitui o conteúdo do clipboard.
- Se outro aplicativo usar o mesmo atalho, os dois podem disparar juntos.
- As libs CUDA (cuBLAS/cuDNN 9) vêm do pip; o `run-backend.sh` ajusta o `LD_LIBRARY_PATH`.

### Ditado do áudio do computador

Segure **Ctrl esquerdo + Shift esquerdo** para capturar a saída de áudio padrão do PC.
Ao soltar, o Whisper transcreve e cola o texto no campo em foco, como no ditado do microfone
(**Ctrl + Super**). Pressionar outra tecla durante a captura descarta o trecho.
O atalho pode ser alterado em **Configurações → Ditado → Atalho do áudio do computador**
(`output_key` / `DITADO_OUTPUT_KEY`). Desligar o ditado pausa ambos os atalhos.
A captura usa o mesmo modelo Whisper, sem criar reunião nem marcar a fala como ditado do microfone.

Ao iniciar o ditado, um sinal curto de 65 ms confirma a captura: um tom para o microfone e
outro, mais agudo, para o áudio do computador. O aviso toca em segundo plano e não carrega modelos.

### Criador de issues e MCP próprio

Em **Configurações → Criador de issues**, ative o levantamento, escolha a pasta raiz dos
projetos e informe o **endereço HTTPS do MCP de issues**. Instalações novas começam sem servidor
configurado. O prompt do levantamento tem regras genéricas e pode ser editado para os seus
projetos; **Restaurar padrão** recupera essas regras. O Claude consulta os projetos em modo de
leitura. As sugestões continuam passando pela revisão antes de qualquer criação.

O servidor é conectado via HTTP pelo Claude Code com o nome local configurável (padrão `issues`).
Use o mesmo nome do servidor já autenticado no Claude Code. Quando o MCP exigir
login, autentique-o pelo Claude Code (`/mcp`). O contrato continua o mesmo, permitindo implementar
um MCP próprio ou um adaptador para outro sistema de issues.
Para registrar um servidor que exige OAuth no Claude Code, use o mesmo nome e URL definidos no app:

```bash
claude mcp add --transport http --scope user issues https://seu-servidor.example/mcp
```

Depois abra `claude` e autentique o servidor em `/mcp`. O contrato das ferramentas é:

- `listar_opcoes()`: retorna um objeto JSON com `voce: {username}`, `pessoas`, `equipes`, `produtos`
  e `etapas`. Cada lista contém objetos com `id` inteiro e `name` (ou `username` para pessoas).
- `criar_issue(title, description, assignee, team, products, stage, priority)`: recebe título e
  descrição como texto, responsável/equipe/etapa como ids inteiros, produtos como lista de ids e
  prioridade `low`, `medium`, `high` ou `urgent`. Retorna ao menos `{number: inteiro, url: texto}`.
  A URL da issue pode pertencer a qualquer provedor HTTPS.

As ferramentas e os campos desse contrato permanecem fixos; o prompt ajusta as regras de
levantamento, sem alterar o formato da integração. O hook de criação continua conferindo os
argumentos aprovados e permitindo uma única chamada por solicitação.

As configurações atuais são `issues_enabled`, `issues_root`, `issues_mcp_url`, `issues_mcp_name` e `tasks_prompt`
(também disponíveis como variáveis `DITADO_<CHAVE>`). A atualização migra as configurações e os
números/links de issues existentes, preserva a pasta, o MCP e seu nome de autenticação, e mantém prompts
personalizados. Os padrões históricos de levantamento passam ao novo padrão genérico.

## Licença

TARS é distribuído sob a [licença MIT](LICENSE), com copyright de Vinicius Alexandre.
As dependências e os modelos utilizados mantêm suas respectivas licenças.
