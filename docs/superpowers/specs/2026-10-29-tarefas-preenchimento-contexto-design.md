# Aba Tarefas: preenchimento automático, contexto em destaque e carregamento

## Intenção

Revisar as tarefas levantadas de uma reunião demora demais. Uma reunião costuma gerar várias
tarefas do mesmo produto, e em cada cartão o operador precisa escolher **responsável** e
**equipe** à mão, um por um. Além disso, o cartão mostra um campo "Projeto" que não vai para o
MCP de issues (só existe no TARS) e que outros MCPs de issues também não teriam.

Ao mesmo tempo, a opção de orientar o levantamento com um contexto ("Contexto para o
levantamento") está escondida num `<details>` fechado. O dono do projeto quer que o operador
perceba que pode orientar as sugestões. E os momentos de espera (levantamento e criação da
issue) hoje só mostram texto, sem indicação visual de que algo está acontecendo.

## Objetivos

1. Cartões chegam com responsável e equipe preenchidos sempre que houver base para isso.
2. O campo "Projeto" deixa de existir na interface e no levantamento.
3. Com a reunião pronta e sem levantamento feito, a caixa de contexto aparece aberta, em
   destaque, com as ações "Gerar tarefas" e "Agora não".
4. Levantamento e criação de issue têm indicação de carregamento visível.

## Não objetivos

- Botão "aplicar a todas" para copiar responsável e equipe entre cartões.
- Etapa diferente de "To Do" (continua fixa em `STAGE_NAME`).
- Suporte genérico a outros MCPs de issues: a criação continua presa ao `criar_issue` do kbgen.
- Mudar o disparo do levantamento: ele continua manual (não começa sozinho quando a reunião fica
  pronta).

## Anti-objetivos (falharia mesmo funcionando)

- Sobrescrever um valor que o operador escolheu à mão, ou uma sugestão válida do Claude
  (um valor "último usado" pode ser substituído; esses dois, não).
- Sugerir como responsável alguém que não pertence à equipe nem ao produto da tarefa.
- Criar uma issue sem o operador revisar: preencher não é criar; o botão "Criar issue" continua
  sendo o único gatilho.
- A caixa de contexto em destaque virar um obstáculo (modal, bloqueio da aba) para quem quer só
  revisar os prints antes.

## Parte 1: preenchimento de responsável e equipe

### 1.1 Remover "Projeto"

- Sai do `TaskCard.vue`, do export Markdown (`tasksMarkdown` em `app/src/lib/tasks.ts`), do
  `SCAN_SYSTEM` (chave `"projeto"`) e da seção `## Projeto` do `DEFAULT_TASKS_PROMPT`
  (`issues/prompts.py`). A orientação "Identifique os projetos pelos CLAUDE.md..." deixa de pedir
  o campo, mas o Claude continua podendo citar repositórios na descrição.
- **Decisão minha:** a coluna `project` continua no banco e no modelo (`TaskSuggestion`), sem
  uso, para evitar migração. O parser (`scan.py`) para de ler `"projeto"`; a API pode continuar
  aceitando o campo para não quebrar clientes, mas a UI não o envia.
- Atenção: regras salvas pelo usuário em Configurações que já tenham o texto antigo do prompt
  continuam com `## Projeto`; isso é aceitável (vira só uma seção a mais na descrição).

### 1.2 O levantamento passa a saber quem cuida de quê

- `listar_opcoes` do kbgen já devolve, para cada pessoa, `equipes` e `produtos` (ids). Hoje
  `_parse_options` (`issues/client.py`) descarta isso. Passa a guardar: a opção de pessoa ganha
  `teams: list[int]` e `products: list[int]` (vazias quando o MCP não informar; o código não
  pode depender delas existirem, já que outro MCP pode não mandar).
- `build_scan_prompt` inclui essas listas no bloco de opções das pessoas.
- O prompt muda de "se não houver correspondência, deixe vazio" para: escolha responsável e
  equipe entre as pessoas que participam do produto da tarefa (e, de preferência, da equipe); se
  alguém foi citado na conversa, prefira essa pessoa; deixe vazio só quando nenhuma escolha
  coerente existir. Continua proibido inventar ids.
- Validação (`scan.py`): além de o id existir, se a pessoa sugerida tiver listas de equipes/
  produtos informadas e não pertencer nem à equipe nem ao produto sugeridos, o `responsavel_id`
  vira vazio (a dica em texto é preservada). Sem listas informadas, só a checagem de existência.
- **Decisão minha:** a comparação usa só os ids que vieram de fato. Se o Claude não sugeriu
  produto nem equipe, o responsável é mantido (só a checagem de existência vale).

### 1.3 Preenchimento pelo último usado

Ordem de prioridade por campo (responsável e equipe, independentes):
1. valor escolhido pelo operador;
2. sugestão válida do Claude;
3. último valor usado para aquele produto.

Só se preenche campo **vazio ou marcado como "último usado"**. Valor escolhido à mão e
sugestão válida do Claude nunca são sobrescritos. **Decisão do dono do projeto:** a reunião
atual vale mais que o histórico. Uma escolha manual num cartão substitui o "último usado" nos
outros cartões pendentes do mesmo produto.

- **Entre reuniões (histórico):** ao gravar o resultado do levantamento
  (`replace_scan_tasks`), cartões com produto definido e responsável e/ou equipe vazios recebem
  os valores da última tarefa com `create_state='created'` daquele produto (em qualquer
  reunião), pela mais recente `updated_at`. Sai das próprias `task_suggestions`; nenhuma tabela
  nova. Os dois campos vêm da mesma tarefa histórica.
- **Na mesma reunião (sequência):** quando o operador muda responsável, equipe ou produto de um
  cartão (`update_task`), ou cria uma issue, os outros cartões **pendentes** da mesma reunião com
  o mesmo produto e com responsável/equipe vazios ou marcados como "último usado" recebem
  esses valores (e ficam marcados como "último usado"). Pendente = não
  descartado e `create_state` em `none`/`failed`.
- **Ao escolher o produto:** se o operador escolhe/troca o produto de um cartão e responsável ou
  equipe estão vazios, aplica-se o histórico (regra de entre reuniões) a esse cartão.
- **Decisão minha:** esvaziar um campo à mão conta como escolha do operador. O campo fica marcado
  como manual e nenhum preenchimento automático o preenche de novo.
- **Decisão minha:** valores do histórico e da sequência passam pela mesma validação da 1.2
  (o id existe nas opções atuais e a pessoa pertence à equipe/produto quando as listas existem),
  usando o cache de opções. Sem opções em cache, preenche sem validar: o operador revisa antes
  de criar. Por isso o preenchimento fica numa camada que enxerga as opções (API/jobs), não só no
  `Store`.
- **Decisão minha:** preenchimento automático não marca `edited` nos cartões que só receberam
  valores. Assim um "Refazer sugestões" continua podendo substituí-los, como hoje.
- Depois de preencher outros cartões, o backend publica `tasks_changed` para a aba recarregar.

### 1.4 Origem visível

Cada valor preenchido automaticamente indica a origem no seletor (como o "(sugestão)" atual):
- "(sugestão)" — veio do Claude (já existe, via `suggested`).
- "(último usado)" — veio do histórico ou da sequência na mesma reunião.

A origem precisa ser guardada por campo (sugestão: em `suggested_json`, numa chave como
`auto: {"assignee_id": "last", "team_id": "last"}`; o formato fica a critério de quem
implementa). Quando o operador muda o campo à mão, a marca daquele campo some.

## Parte 2: caixa de contexto em destaque (opção A do mockup)

- **Quando aparece aberta:** criador de issues ligado, aba carregada, nenhum levantamento feito
  nesta reunião (`scan === null`) e o operador não clicou "Agora não" nesta visita à aba.
- **Decisão minha:** "Agora não" vale enquanto a aba estiver aberta; se a reunião for reaberta e
  ainda não tiver levantamento, a caixa volta a aparecer. Não é persistido.
- **Visual (opção A escolhida):** cartão com borda de destaque à esquerda, ícone, título "Quer
  orientar as sugestões de tarefas?", texto explicando que vai junto com a transcrição e os prints
  e que é opcional, três sugestões clicáveis ("chips") que inserem um texto de exemplo no campo,
  textarea, nota "Tarefas editadas, descartadas ou já enviadas são preservadas." e os botões
  **Agora não** (discreto) e **Gerar tarefas** (primário). Fica inline na aba, no lugar do
  `<details>`, nunca em modal.
- **Decisão minha:** os chips são exemplos genéricos fixos (ex.: "Foque só no produto …",
  "Ignore assuntos de infraestrutura", "Junte os ajustes relacionados numa tarefa"); clicar
  acrescenta o texto ao campo, não substitui o que já foi escrito.
- **Gerar tarefas:** dispara o mesmo `rescan()` de hoje, com o contexto se preenchido.
- **Agora não:** fecha a caixa; a aba fica no fluxo atual (botão "Levantar tarefas" na barra,
  texto "Revise os prints e clique em Levantar tarefas.").
- **Depois do primeiro levantamento ou do "Agora não":** o contexto continua disponível recolhido
  (o `<details>` atual), com o rótulo "Orientar as sugestões (opcional)", para "Refazer
  sugestões". O texto digitado é mantido entre a caixa e o recolhido.
- Desabilitado sem conexão ou com levantamento em andamento, como hoje.

## Parte 3: carregamento

Mockup aprovado (seções 2 e 3 de `contexto-e-load.html`, em `.superpowers/brainstorm/`).

- **Levantando tarefas:** a linha de status vira uma caixa com spinner, "Levantando tarefas…",
  subtítulo "O Claude está lendo a transcrição e os prints. Isso pode levar alguns minutos.",
  barra indeterminada e o botão "Cancelar levantamento". Quando ainda não há tarefas, aparecem
  cartões-esqueleto (lista e detalhe) no lugar da área vazia. Quando já há tarefas (refazer),
  basta a caixa de status.
- **Criando a issue:** spinner no botão "Criando…", spinner na tag "criando…" da linha na lista,
  e o cartão aberto fica esmaecido com a mensagem "Criando a issue no MCP de issues…" por cima,
  enquanto o item estiver `creating` ou ocupado (`create:<id>`).
- Spinners e barras respeitam `prefers-reduced-motion` (sem animação, só o estado estático).
- Usa os tokens de cor do app (`app/src/styles/tokens`), claro e escuro.

## Restrições

- A saída do Claude continua sendo entrada não confiável: só ids presentes nas opções passam.
- O hook `create_guard.py` e o formato de `criar_issue` não mudam.
- Testes: os existentes (`tests/issues/`, `tests/test_store_tasks.py`, `tests/test_api_tasks.py`,
  `TaskCard.test.ts`, `TasksTab.test.ts`, `tasks.test.ts`) precisam ser ajustados à remoção do
  projeto e cobrir as regras novas de preenchimento (prioridade, só campo vazio, mesma reunião,
  histórico, troca de produto) e os estados da caixa de contexto.

## Fica a critério de quem implementa

- Formato exato da marca de origem por campo.
- Componentização (ex.: extrair a caixa de contexto e o spinner em componentes próprios).
- Texto exato do prompt novo, respeitando as regras acima.
- Como dividir o preenchimento entre `Store` (consultas e gravação atômica) e a camada que
  conhece as opções (API/jobs), desde que publique `tasks_changed`.
