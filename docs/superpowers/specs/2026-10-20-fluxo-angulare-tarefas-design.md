# Fluxo Angulare: tarefas a partir das reuniões — design

Data: 2026-10-20 · Status: rascunho para aprovação · Depende de: app e backend de reuniões já em
produção (`docs/superpowers/specs/2026-10-19-app-reunioes-design.md`).

## 1. Intenção

As reuniões de definição da empresa (Angulare) decidem **features, tarefas e bug fixes**. Hoje o
que foi decidido vira issue no KBGen à mão, por quem opera este sistema. O objetivo é que, ao fim de
cada reunião, o app já traga o **resumo** e uma **lista de sugestões de issues** prontas para
revisar, e que o operador crie no KBGen as que quiser, com um clique, sem redigitar nada.

Quem decide é sempre o operador: o Claude propõe, o operador revisa, edita, descarta e cria.

## 2. Objetivos

- **Resumo automático** ao fim de toda reunião, sem clique (hoje só existe pelo botão "Resumir").
- **Fluxo Angulare** (configurável): ao fim de toda reunião, o Claude Code roda a partir da pasta
  raiz dos projetos da empresa, lê a transcrição e decide se há issues; se houver, propõe **uma
  sugestão por item** (uma reunião pode gerar várias).
- **Aba "Tarefas"** na reunião, no próprio app: lista das sugestões, cada uma editável e
  descartável; copiar e exportar; criar no KBGen os itens que o operador marcar.
- Criar no KBGen só quando o operador decidir, em uma ou várias levas, sem duplicar.

## 3. Não-objetivos

- Conversar com o Claude sobre as sugestões (não há chat; a revisão é na aba).
- O Claude alterar código, abrir PR, rodar comandos ou consultar bancos.
- Criar issue no KBGen sem clique do operador.
- Editar ou sincronizar issues depois de criadas (só criar; o resto é no KBGen).
- Anexar prints/áudio na issue.
- Outros destinos além do KBGen (ClickUp, GitHub Issues etc.).

## 4. Anti-objetivos

- Issue criada sozinha, ou com conteúdo diferente do que o operador viu na tela.
- Transcrição (texto de terceiros) conseguir disparar ações: ela é entrada não confiável.
- Issue duplicada por clicar duas vezes ou criar em levas.
- Degradar o ditado ou atrasar a transcrição: o fluxo roda depois da reunião estar `ready`.

## 5. Decisões do usuário

- A revisão é **dentro do app**, numa aba da reunião, logo que as sugestões ficarem prontas.
- **Roda em todas as reuniões** com o fluxo ligado; a própria varredura decide se há issue ou não
  (reunião sem issue → aba mostra "Nenhuma tarefa identificada nesta reunião").
- **Várias issues por reunião**, cada uma separada.
- **Copiar e exportar** a lista também.
- **Responsável e equipe**: o Claude sugere a partir da conversa; o operador confirma ou troca.
- **Etapa** padrão `To Do`.
- **Ambiente** (`dev`/`prod`) sempre vazio: nunca enviado ao KBGen.
- Menu nas Configurações para o fluxo, com a **pasta raiz** dos projetos e a **conexão com o MCP do
  KBGen**.

## 6. Contexto encontrado (pasta raiz atual)

- Pasta raiz: `/home/vi/dev/ecommerce-dash` (pasta-pai do ecossistema Angulare: `angular-body`
  = ecommerce, `pay-v3`, `ws`, `dash-v3`, `dash-crm`, `mcp-angular`, `kbgen-all/`), com
  `CLAUDE.md` geral (apelidos dos repos) e um `CLAUDE.md` por repo.
- MCP do KBGen: servidor **`kbgen-issues`** (`https://kbgen.angulare.net/mcp`, HTTP, login do
  usuário), configurado para sessões do Claude Code abertas nessa pasta. Tools: `listar_opcoes`,
  `criar_issue`, `editar_issue`, `gerar_link_upload`.
- Regras já usadas pela equipe (skill `criar-issue-kbgen` em `.claude/skills/` da pasta raiz):
  - campos obrigatórios: responsável, equipe, produto, etapa; ids sempre via `listar_opcoes`
    (não decorar, mudam);
  - produto pelo projeto: `dash-v3`/`dash` → `dash`; `ecommerce`/`angular-body` → `e-commerce`;
    `pay`/`pay-v3` → `pay`; `kbgen` → `kbgen`; `dash-crm`, `ws`, `mcp` → sem produto próprio
    (operador escolhe). `dash-kbgen` é o dashboard do KBGen, não o dash-v3;
  - etapa sempre `To Do`; equipe sugerida `Desenvolvimento` para tarefa de código; prioridade
    padrão `medium`;
  - título curto começando pelo módulo, máx. 300 caracteres;
  - descrição em Markdown pt-BR: `## Problema` → `## Comportamento esperado` (lista curta) →
    `## Projeto`; descrever o que foi dito, sem inventar causa nem solução técnica;
  - o MCP devolve o `number` da issue; o servidor atual também devolve a `url` (mostrar o link).

## 7. Comportamento

### 7.1 Ao terminar o processamento de uma reunião
1. A reunião fica `ready` como hoje (transcrição, falantes, título).
2. **Resumo automático** (configuração "Resumir automaticamente", ligada por padrão): o backend
   gera o resumo com o prompt atual e salva na reunião. Falha do Claude não marca a reunião como
   falha: fica registrado o erro e o botão "Resumir" continua disponível.
3. **Levantamento de tarefas** (só com o fluxo Angulare ligado e pasta raiz válida): o backend roda
   o Claude Code em modo não interativo com `cwd` = pasta raiz, entregando a transcrição (com nomes
   dos falantes e horários) e o resumo, e pedindo uma lista estruturada de sugestões. O Claude:
   - decide se a reunião gerou issues; se não, devolve lista vazia;
   - classifica cada item: **feature**, **tarefa** ou **bug fix**;
   - identifica o **projeto** (pelos apelidos do `CLAUDE.md` da raiz) e, a partir dele, o produto;
   - pode **ler** o código e os `CLAUDE.md` dos repos para embasar título e descrição (só leitura);
   - consulta `listar_opcoes` do KBGen para sugerir **responsável** e **equipe** com ids reais,
     só quando a conversa der pista (ex.: "o Miguel pega isso"); sem pista, deixa vazio;
   - prioridade `medium`, salvo se a conversa deixar claro outra;
   - para cada item, inclui o **trecho da conversa** (horário + quem falou) que o justifica.
4. Os estados aparecem na aba: "Levantando tarefas…" → lista / "Nenhuma tarefa identificada" /
   erro do Claude com "Tentar de novo".
5. "Refazer sugestões" roda o levantamento de novo. Itens já criados no KBGen ou editados à mão pelo
   operador não são apagados nem sobrescritos.

### 7.2 Aba "Tarefas" (reunião aberta)
- Ao lado de Transcrição e Resumo. Cada sugestão é um cartão com: tipo, título, descrição
  (Markdown), projeto/produto, responsável, equipe, prioridade, etapa (`To Do`), e o trecho que
  justifica (com ▶ para ouvir aquele trecho, como na transcrição).
- Tudo editável; responsável/equipe/produto/etapa são seletores com as opções reais do KBGen
  (carregadas via `listar_opcoes`), mostrando o valor sugerido pelo Claude marcado como sugestão.
- **Descartar** item (some da lista; dá para desfazer enquanto a tela está aberta) e **adicionar**
  item manual (o operador lembra de algo que o Claude não pegou).
- Caixa de seleção por item + **"Criar no KBGen"** para os marcados. Antes de criar, o app exige os
  campos obrigatórios (responsável, equipe, produto, etapa) em cada item marcado e mostra o que falta.
- Item criado mostra o número (`#217`), fica travado para edição e não pode ser criado de novo.
  Falha num item não impede os outros; cada um mostra seu erro.
- **Copiar** (Markdown da lista, só itens não descartados) e **Exportar .md**.

### 7.3 Criação no KBGen
- Cada item é criado **exatamente** com o texto e os campos que o operador vê na tela no momento do
  clique. A etapa padrão é `To Do`.
- A criação usa o login do usuário no `kbgen-issues` (o mesmo que o Claude Code já usa nessa pasta).
- Sem duplicar: o app registra o `number` devolvido; repetir o clique ou recarregar não recria.

### 7.4 Configurações → "Angulare"
- **Fluxo ligado/desligado** (desligado por padrão até configurar).
- **Pasta raiz dos projetos** (padrão sugerido `/home/vi/dev/ecommerce-dash`; validar que existe).
- **Conexão com o KBGen**: estado ("conectado como …" / "sem login" / erro) e um botão "Testar
  conexão" (chama `listar_opcoes`). Se faltar login, instrução clara de como fazer (ex.: abrir o
  Claude Code na pasta raiz e autenticar o `kbgen-issues` via `/mcp`).
- **Prompt do levantamento** editável com "Restaurar padrão" (como o prompt do resumo).
- "Resumir automaticamente" fica na seção Reuniões (vale com ou sem o fluxo Angulare).

## 8. Segurança (escolhas minhas)

- A transcrição é **entrada não confiável** (qualquer participante pode dizer "ignore as instruções
  e …"). Por isso:
  - o **levantamento** roda com ferramentas só de leitura nos arquivos da pasta raiz e, de MCP,
    só a leitura de opções do KBGen (`listar_opcoes`); sem Bash, sem edição, sem web, sem os MCPs
    de banco/Sentry/Discord do `.mcp.json` da raiz; não persiste sessão;
  - a **criação** nunca é decidida pelo Claude a partir da transcrição: só acontece por clique e só
    com os campos revisados. O builder escolhe o mecanismo (chamada direta ao MCP pelo backend, ou
    uma chamada restrita do Claude Code que só enxerga `criar_issue`), desde que o conteúdo criado
    seja o da tela, campo a campo, e verificável depois.
- A saída do levantamento é validada contra um esquema; campos fora do esquema são descartados; o
  texto é tratado como dado (renderizado sem HTML bruto, como o resumo).

## 8.1 Como o app fala com o KBGen (verificado em 2026-10-20)

- O `kbgen-issues` só aceita OAuth (não há token de API). **Verificado:** `claude -p` com
  `--strict-mcp-config --mcp-config <json com kbgen-issues e a mesma url> --tools ""
  --allowedTools "mcp__kbgen-issues__listar_opcoes" --setting-sources ""`, rodando de um diretório
  neutro, reaproveita o login já feito pelo usuário e devolveu as opções sem interação.
- Portanto **toda** chamada ao KBGen (opções, testar conexão, criar) passa pelo Claude Code CLI do
  usuário com o `kbgen-issues` como único MCP e só a(s) tool(s) daquela operação liberada(s):
  `listar_opcoes` para opções/teste; `criar_issue` (e, se útil, `ver_issue` para conferir) para criar.
- As opções (`listar_opcoes`) ficam em cache no backend (renovar ao abrir a aba/Configurações ou por
  botão), porque cada chamada leva alguns segundos.
- Backend e app rodam sempre na mesma máquina e no mesmo usuário (API só em `127.0.0.1`): a pasta
  raiz é validada pelo backend, e o login do Claude Code é o do usuário do serviço.
- Criar: uma chamada por item, com os campos da tela passados como dados estruturados e a instrução
  de chamar `criar_issue` uma única vez com eles; o backend confere a resposta (título, responsável,
  equipe, produto, etapa) contra o que foi pedido e registra `number` e `url`.

## 8.2 Escolhas minhas (respostas à checagem do builder)

- **Tipo no KBGen** (o KBGen não tem campo de tipo): vai como primeira linha da descrição,
  `**Tipo:** Feature | Tarefa | Bug fix`, antes do `## Problema`; o título segue a regra de começar
  pelo módulo. Trocar para prefixo no título é mudança de uma linha se o usuário preferir.

- **Refazer sugestões:** o prompt recebe a lista atual (criadas, editadas e descartadas) como "já
  levantado — não repita"; os itens não editados e não criados da rodada anterior são substituídos;
  editados, criados e adicionados à mão ficam. Um item novo muito parecido com um já criado aparece
  marcado "parece repetir #217" para o operador decidir.
- **Reprocessar a reunião:** as sugestões ficam como estão (o trecho que justifica é guardado como
  texto + horário + quem falou, não como id de segmento); resumo e levantamento **não** rodam de novo
  sozinhos no reprocessamento — só pelos botões "Refazer".
- **Falha ambígua na criação** (timeout/erro depois de enviar): o item fica em estado **"incerto —
  confira no KBGen"** e o botão de criar dele fica bloqueado até o operador escolher "não foi criado,
  tentar de novo" ou informar o número da issue. O app pode ajudar buscando por título com
  `listar_issues_por_responsavel(q=…)`.

## 9. Restrições

- Mesmo app e backend do projeto (`whisper-ditado`): backend Python (FastAPI, SQLite), app Tauri +
  Vue; interface em pt-BR; chamadas ao Claude via Claude Code CLI do usuário (`claude`, v2.1.304),
  como o resumo.
- Nada de chamada ao Claude sem ação do usuário, **exceto**: título (já existe), resumo automático e
  levantamento de tarefas, ambos desligáveis.
- O levantamento roda depois da reunião `ready` e não pode atrasar o ditado (fila/prioridade de GPU
  não se aplicam — é CPU/rede, mas não pode travar a API nem o processamento de outra reunião).
- Reprocessar uma reunião não apaga sugestões já criadas no KBGen.

## 10. Deixado para o builder

- Esquema exato das sugestões no SQLite e a API (rotas para listar, editar, descartar, adicionar,
  criar no KBGen, refazer).
- Como passar ao Claude Code as permissões restritas (`--tools`, `--allowedTools`,
  `--mcp-config`/`--strict-mcp-config`, `--permission-mode`) e como reaproveitar o login HTTP do
  `kbgen-issues` sem interação — verificar com o CLI instalado.
- Timeout do levantamento (pode ler código; maior que o do resumo) e tamanho máximo da transcrição.
- Texto do prompt padrão do levantamento (seguindo as regras da seção 6).
- Como mostrar a sugestão de responsável/equipe vinda do Claude quando o nome não casa com
  ninguém do KBGen.
