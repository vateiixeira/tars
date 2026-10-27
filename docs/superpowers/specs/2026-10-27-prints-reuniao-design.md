# Prints de tela nas reuniões — design

Data: 2026-10-27

## 1. Intenção

Numa reunião, boa parte do que importa aparece na tela e não é dito em voz alta: o bug que
alguém mostrou, a tela que precisa mudar, um log, um dashboard. Hoje o TARS só registra a fala,
então tudo o que é mostrado com "olha isso aqui" se perde.

Com esta funcionalidade, a reunião passa a guardar também **o que estava na tela, no minuto em
que foi mostrado**. Os prints chegam a três lugares:

- **Revisão da reunião:** galeria de prints com horário e o trecho falado naquele momento.
- **Documentação:** o resumo do Claude enxerga os prints, e o export os inclui.
- **Levantamento de issues** (Claude Code + MCP de issues): o Claude recebe os prints com
  horário, relaciona cada um com a fala e escreve issues melhores. Os prints usados como
  evidência são anexados à issue quando o MCP permite.

## 2. Objetivos

1. Configuração **"Prints de tela"** em Configurações → Reuniões, com três modos:
   **Desligado** (padrão), **Flameshot** e **Nativo**.
2. **Modo Flameshot.** O usuário escolhe a pasta de onde o TARS lê os prints (padrão: a pasta
   de Imagens do XDG). Na **finalização** da reunião, o TARS copia para a pasta da reunião
   os arquivos de imagem (PNG/JPG) criados entre o início e o fim da gravação. A janela inclui
   uma pequena folga depois do fim para pegar o print tirado bem na hora de parar. Os
   originais não são alterados nem apagados. Durante a reunião, nada fica visível.
3. **Modo Nativo.** Um **atalho global de teclado** configurável e um item **"Tirar print"**
   no menu da bandeja capturam **a tela inteira, com todos os monitores numa imagem só**. A
   captura acontece por trás: não abre janela nem tira o foco da reunião. Cada captura toca
   um **som curto** de confirmação. Fora de uma reunião em gravação, o atalho não faz nada.
4. Cada print guarda o **horário relativo ao início da reunião**, na mesma escala do
   `start_s` dos segmentos.
5. **Aba "Prints (N)"** no detalhe da reunião, entre Transcrição e Tarefas. É uma galeria em
   que cada print mostra o horário e o trecho de fala daquele momento. Clicar amplia a
   imagem. Dá para **apagar** o print, com confirmação: ele some da reunião e a cópia em
   disco é removida. A aba só aparece quando a reunião tem prints.
6. **Na transcrição**, a fala correspondente ao minuto do print ganha um selo 📷 que leva
   ao print na aba Prints.
7. **O resumo continua automático e passa a considerar os prints**, que vão ao Claude junto
   com a transcrição, cada um com seu horário.
8. **O levantamento de issues passa a ser sempre manual**, em todas as reuniões, com ou sem
   prints. Ele deixa de rodar sozinho quando a reunião fica pronta. O campo de contexto
   opcional da aba Tarefas (hoje "Contexto para refazer sugestões") fica disponível já antes
   do primeiro levantamento e passa a ter um nome que sirva para as duas situações.
9. No levantamento, o Claude recebe os prints com horário e pode **citá-los como evidência**
   em cada sugestão, do mesmo jeito que já cita trechos da transcrição. A aba Tarefas mostra
   os prints citados em cada sugestão.
10. **Anexo na issue, só se o MCP permitir.** O MCP de issues é genérico: pode ser do
    usuário ou de qualquer outro fornecedor. Se o MCP configurado oferecer uma forma de enviar
    imagem (por exemplo, um parâmetro de anexo no `criar_issue` ou uma ferramenta de upload),
    os prints citados na sugestão seguem com a issue. Se não oferecer, a issue é criada
    normalmente sem eles, e a sugestão mostra um aviso discreto de que o MCP não aceita
    imagens.
11. **Export.** Uma reunião sem prints continua sendo exportada como `.md`, como hoje. Com
    prints, o export vira um `.zip` com o `.md` e uma pasta de imagens referenciadas no texto
    no ponto certo.
12. Excluir a reunião apaga os prints dela, como já acontece com os áudios.

## 3. Não-objetivos

- Ver os prints **durante** a reunião. Eles aparecem só depois da finalização.
- Editar, recortar ou anotar prints dentro do TARS.
- Recorte de área no modo nativo. Para isso, o usuário usa o Flameshot.
- Integrar com outros apps de captura além do Flameshot. Como o modo Flameshot só lê uma
  pasta, ele funciona na prática com qualquer ferramenta que salve arquivos lá, mas não há
  suporte específico.
- OCR ou interpretação local das imagens. Quem interpreta é o Claude.
- Ativar o Flameshot pelo TARS ou mudar a configuração dele.
- Mudar o MCP de issues para aceitar imagens. O TARS só usa o que o MCP já oferece.

## 4. Anti-objetivos

- **Um print sensível ir para uma issue ou para outra pessoa sem chance de revisão.** O
  levantamento manual existe para isso: o usuário revisa e apaga os prints na aba Prints
  antes de levantar. Como o resumo é automático e enxerga os prints, ele recebe as imagens
  antes da revisão. Isso foi aceito pelo usuário.
- **Atrapalhar a reunião.** A captura nativa não pode abrir janela, tirar o foco nem
  aparecer no próprio print. Também não pode degradar a gravação nem o ditado.
- **Alterar ou apagar arquivos na pasta de prints do usuário.**
- **Print no minuto errado.** Um print encaixado num momento diferente do real é pior do que
  não ter print.
- **Ficar em silêncio quando algo falha.** Se o modo nativo não conseguir capturar (falta de
  permissão no GNOME, portal indisponível), o usuário precisa ficar sabendo: um som de erro
  na hora e uma mensagem clara depois.

## 5. Restrições e contexto

- **Ambiente-alvo:** Ubuntu com GNOME em **Wayland** (ambiente do usuário: Flameshot 13.3). No
  Wayland, um app não pode capturar a tela por conta própria. A captura nativa tem de passar
  pelo GNOME, via xdg-desktop-portal Screenshot. O GNOME pode pedir permissão na primeira
  vez. Validar na implementação se as capturas seguintes ficam silenciosas. O X11 também é
  suportado pelo app (ver `paste.py`) e deve funcionar, mesmo que por outro caminho.
- **Tempo:** os segmentos usam `start_s`, em segundos desde `t0` (`time.monotonic`). A reunião
  guarda `started_at` (relógio de parede local, sem fuso) em `meeting/session.py`.
  - Print nativo: o horário é `monotonic() - t0`, no momento da captura.
  - Print do Flameshot: o horário vem do mtime do arquivo, que é relógio de parede, menos
    `started_at`. Os dois relógios podem divergir, por exemplo se o computador suspender
    durante a reunião. O builder deve tratar isso para não violar o anti-objetivo do "minuto
    errado".
- **Armazenamento:** cada reunião tem a pasta `~/.local/share/whisper-ditado/meetings/<id>/`
  (modo 0700). Os prints ficam numa subpasta dela. O registro dos prints vai para o SQLite
  (`store.py`) por uma **nova migração de schema** (hoje a última é a V5). A branch
  `diarizacao-local-sherpa` também mexe em `store.py`. Coordenar o número da migração ao
  integrar.
- **Atalho global:** reaproveitar o mecanismo dos atalhos de ditado (`hotkey.py`, evdev), que
  já funciona com qualquer janela em foco. O atalho de print precisa ser diferente dos atalhos
  de ditado e ser validado como eles (`settings.py`).
- **Som:** reaproveitar `feedback.py` (`pw-play`).
- **Bandeja:** `app/src-tauri/src/tray.rs` já tem o item de reunião. O item "Tirar print" só
  fica habilitado com uma reunião em gravação.
- **Configurações:** o padrão já existente é tabela `settings` mais `DEFAULTS`/`_validate`
  em `settings.py`, mais `types.ts` (`Settings`/`SettingsUpdate`), mais
  `components/settings/MeetingsSection.vue`.
- **Resumo:** hoje usa `ClaudeCli._run` com as ferramentas desligadas e manda só texto pelo
  stdin. Ele precisa passar a enviar as imagens sem abrir acesso amplo a arquivos. O
  isolamento atual (`--tools ""`, `--setting-sources ""`, `--strict-mcp-config`, diretório
  neutro) deve continuar equivalente.
- **Levantamento:** `issues/scan.py` + `issues/runner.py` rodam o Claude Code em modo restrito
  (`Read,Grep,Glob` nos projetos). O disparo automático fica hoje em `issues/jobs.py`
  (`_after_ready`) e deve ser removido. O resumo automático continua lá. Os prints são
  **dados de terceiros**, como a transcrição: o texto dentro de uma imagem nunca pode ser
  tratado como instrução (ver `SCAN_SYSTEM`).
- **Criação da issue:** hoje o Claude chama `criar_issue` com exatamente os campos que o
  operador viu (`CREATE_PROMPT`, `issues/client.py`). Os anexos seguem a mesma regra: vão só
  os prints que o operador viu na sugestão e não apagou.
- **Branch `fluxo-angulare`:** existe em paralelo e mexe na mesma área (Tarefas e levantamento).
  Esta funcionalidade é feita sobre a `main` (versão genérica do MCP de issues). Espere
  conflitos ao integrar as duas.
- **Comunicação frontend ↔ backend:** REST em `/api` com token e WebSocket `/api/events`. As
  imagens chegam ao frontend pela API autenticada, como o áudio (`client.audioBlob`).
- Seguir `CONTRIBUTING.md`. Captura e transcrição são áreas que exigem testes no ambiente
  NVIDIA antes de uma release (ver `CLAUDE.md`).

## 6. Decisões do usuário

- Dois modos de captura, escolhidos nas configurações: Flameshot (com pasta configurável) e
  nativo (tela inteira).
- Flameshot: os prints só são recolhidos e encaixados na finalização.
- Nativo: disparo por trás, sem a janela do TARS na frente, com aviso sonoro. Com vários
  monitores, captura todos.
- Os prints aparecem numa aba própria "Prints", com o selo 📷 na transcrição (opção C do
  esboço).
- Dá para apagar prints depois da finalização.
- O resumo continua automático e enxerga os prints.
- O levantamento de issues passa a ser sempre manual, com o campo de contexto disponível
  antes da primeira rodada.
- As imagens são anexadas à issue só quando o MCP aceitar. Caso contrário, não são enviadas.

## 7. Decisões tomadas por mim (Claude), revisáveis

- O padrão do modo de prints é **Desligado**.
- A pasta padrão do Flameshot é a de Imagens do XDG (`xdg-user-dir PICTURES`).
- A folga depois do fim da reunião no modo Flameshot é curta: alguns segundos. O valor exato
  fica com o builder.
- Apagar print pede confirmação.
- Na bandeja, o item "Tirar print" é uma alternativa ao atalho.
- O Claude cita prints como evidência nas sugestões, e são esses os anexados à issue.
- O export com prints vira `.zip`.
- A aba Prints só aparece quando há prints.

## 8. O que fica com o builder

- Formato da tabela e dos arquivos dos prints (nome, formato de gravação, miniaturas).
- Como mandar imagens ao `claude -p`, tanto no resumo quanto no levantamento, mantendo o
  isolamento atual.
- Como detectar se o MCP aceita imagens (inspecionar o schema das ferramentas na
  inicialização) e como enviar o arquivo.
- Como o Claude referencia um print na resposta JSON do levantamento (campo novo ao lado de
  `trechos`).
- Biblioteca e caminho para falar com o portal de Screenshot (D-Bus) e o fallback para X11.
- Como associar um print à fala mais próxima para mostrar o trecho e o selo 📷.
- Valor da folga, extensões aceitas e filtro de arquivos (ignorar arquivos ocultos ou
  temporários).
- Desenho visual da aba Prints e do visualizador ampliado, seguindo o padrão atual do app.
- Estratégia de testes. A captura nativa real e o portal do GNOME precisam de validação
  manual.
