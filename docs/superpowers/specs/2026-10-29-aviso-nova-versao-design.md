# Aviso de nova versão e versão no rodapé

## Intenção

Quem usa o TARS clonando o repositório não tem como saber que saiu uma versão nova: o app
não consulta nada e a pessoa teria de acompanhar o GitHub por conta própria. Hoje há pelo
menos uma pessoa nessa situação. O objetivo é que o próprio app diga qual versão está
rodando e avise quando houver release mais nova, com um único comando para atualizar —
atualizar a partir do código exige baixar o código, sincronizar dependências Python e Node,
recompilar o executável Tauri e reiniciar o serviço, e quem faz isso à mão esquece passos
(principalmente o build).

## Objetivos

1. O rodapé da barra lateral mostra a versão em execução.
2. Quando existir release publicada mais nova, o rodapé avisa e leva a um diálogo com o
   comando de atualização e o link para as notas da versão.
3. Um comando único, `python3 scripts/update.py`, leva o clone à última release e deixa tudo
   pronto (dependências, build, serviço).

## Não objetivos

- Botão que atualiza pelo app ou atualização automática.
- Seguir a ponta da `main`: o aviso e o comando trabalham com releases (tags `vX.Y.Z`).
- Renderizar as notas da versão dentro do app: o diálogo só leva à página da release.
- Atualizar instalações feitas pelo pacote `.tar.gz` (veja "Instalação pelo pacote").

## Anti-objetivos (falharia mesmo funcionando)

- Aviso que aparece offline como erro, ou que trava/atrasa a inicialização do backend.
- Aviso para quem já está na última versão ou à frente dela (o dono do projeto na `main`).
- `update.py` que derruba uma reunião em gravação, apaga mudanças locais ou deixa o clone
  pela metade sem dizer qual passo falhou.

## Decisões do dono do projeto

- **Layout (opção C do mockup):** uma linha abaixo do status da conexão no rodapé da barra
  lateral (`.sidebar-footer` em `app/src/App.vue`): `TARS 0.2.0`. Com versão nova:
  `TARS 0.2.0 · atualizar para 0.3.0`, em que "atualizar para 0.3.0" é um link que abre um
  diálogo. O diálogo mostra a versão nova, o comando `python3 scripts/update.py` com botão de
  copiar e um link "Ver novidades" para a página da release no GitHub.
- **Só avisar:** o app não executa a atualização.
- **Comando único:** `scripts/update.py` faz todos os passos.
- **Destino é a última release:** `update.py` faz checkout da tag da última release, não
  `git pull` na `main`.

## Comportamento

### Verificação de versão (backend)

- Versão instalada: a do `pyproject.toml` do projeto em execução (é o que a release sobe;
  na `main` entre releases continua igual à última release, então não gera aviso falso).
- Última release: `GET https://api.github.com/repos/vateiixeira/tars/releases/latest`
  (repositório público, sem token). Ignora rascunhos e pré-releases (a API já faz isso).
  Usa `tag_name` (sem o `v`) e `html_url`.
- Frequência: no máximo uma vez a cada 24 h, em segundo plano, começando depois que a API
  sobe — mesmo padrão do `ClaudeModels.start_auto_refresh` (`src/whisper_ditado/claude_models.py`):
  resultado em cache na tabela `settings` com chave interna fora de `settings.DEFAULTS`.
- Falhas (offline, timeout, rate limit, JSON inesperado): registram no log em nível baixo e
  mantêm o último resultado em cache; nunca viram erro na interface.
- Há atualização só se a última release for estritamente maior que a instalada
  (comparação semântica `MAJOR.MINOR.PATCH`).
- O resultado vai para o payload de `/api/status` (que o app já consome via `useStatus`),
  algo como `version: {current, latest, url, update_available, install}`, em que `install`
  é `"git"` (há `.git` na raiz do projeto) ou `"package"`.

### Rodapé e diálogo (app)

- A linha da versão sempre aparece quando o backend está conectado.
- *Decisão minha:* o app também conhece a própria versão (a do `app/package.json`, embutida
  no build). Se ela diferir da versão do backend, a linha mostra as duas
  (`app 0.2.0 · serviço 0.3.0`) e o diálogo explica que falta recompilar/reabrir o app — é o
  caso "atualizou o código mas esqueceu o build".
- Instalação git: o diálogo mostra o comando para copiar. Instalação por pacote: o diálogo
  mostra o link de download da release no lugar do comando.
- Sem conexão com o backend: a linha mostra só a versão do app.

### `scripts/update.py`

Ordem, parando no primeiro erro com mensagem dizendo qual passo falhou e como retomar:

1. **Pré-condições.** Recusa se não for um clone git (aponta para a página de releases). Recusa
   se houver arquivos rastreados alterados, listando-os (*decisão minha*: não faz stash nem
   descarta nada). Recusa se houver reunião gravando ou processando, consultando o
   `/api/status` do backend local pela porta e token que o backend grava ao subir
   (`api.write_api_info`). Se o backend responder mas não for a unidade systemd (rodando via
   `./run-backend.sh`), recusa e pede para encerrá-lo antes. Backend que não responde é
   tratado como parado.
2. **Descobrir a release.** `git fetch --tags origin`; escolhe a maior tag `vX.Y.Z`. Se a
   versão atual já for igual ou maior e não houver atualização pendente (abaixo), diz que
   está atualizado e sai sem mexer em nada.
3. **Parar o serviço.** *Decisão do dono, confirmada por mim:* `systemctl --user stop
   whisper-ditado-backend.service` antes de trocar o código, para que nada rode sobre
   arquivos e `.venv` pela metade e ninguém comece uma reunião durante o update. Ditado e
   reuniões ficam indisponíveis por alguns minutos; o script avisa isso antes de parar.
4. **Código.** `git checkout vX.Y.Z` (fica em detached HEAD; a próxima execução pula para a
   tag seguinte).
5. **Python.** `uv sync --frozen`, preservando os extras e grupos que a pessoa já usa
   (em especial `--extra pyannote` se o pyannote estiver instalado no `.venv`).
6. **App.** `pnpm install --frozen-lockfile` e `pnpm tauri build --no-bundle` em `app/`.
7. **Subir o serviço.** `systemctl --user start` se a unidade estiver instalada; senão, avisa
   para iniciar o `./run-backend.sh`.
8. **Fim.** Diz a versão nova e pede para fechar e reabrir o app.

**Retomada (*decisão minha*).** Antes do passo 3, o script grava uma marca de atualização
pendente com a tag alvo dentro de `.git/` (fora da árvore de trabalho e do git). A marca só é
apagada depois do passo 7. Se o script rodar de novo com a marca presente, refaz os passos 3
a 7 para aquela tag em vez de dizer "está atualizado" — sem isso, uma falha no build depois
do checkout deixaria o clone pela metade para sempre.

**Falha no meio (*decisão minha*).** Se algum passo de 4 a 6 falhar, o script tenta subir o
serviço de novo mesmo assim (para não deixar a pessoa sem ditado), diz se ele subiu, qual
passo falhou e que basta rodar `python3 scripts/update.py` de novo depois de corrigir.

Se a pessoa estava na `main` com commits locais não enviados, o checkout da tag não os perde
(continuam na branch), mas o script avisa que ela saiu da branch.

## Restrições

- Linux, mesmos pré-requisitos do README ("Instalação a partir do código").
- O script de update só usa a biblioteca padrão do Python (roda antes do `uv sync`).
- A verificação não pode bloquear o loop do servidor nem a inicialização.
- Testes sem rede: a consulta ao GitHub e os comandos externos do `update.py` precisam ser
  injetáveis/mockáveis, no estilo de `scripts/release.py` e seus testes.
- README ganha uma seção "Atualizando" que aponta para `scripts/update.py`;
  `docs/INSTALL_RELEASE.md` mantém o fluxo do pacote.

## Instalação pelo pacote

O `.tar.gz` não tem `.git`. O app detecta isso e mostra o link de download no diálogo; o
`update.py` recusa com a mesma orientação. Automatizar essa atualização fica fora do escopo.

## Deixado para quem implementa

- Nome exato dos campos no `/api/status`, chave de cache e onde mora o módulo
  (ex.: `src/whisper_ditado/updates.py`).
- Cliente HTTP (biblioteca padrão ou o que o backend já usa), timeouts e `User-Agent`.
- Como embutir a versão do app no build (ex.: `define` do Vite lendo `package.json`).
- Componente do diálogo (reaproveitar os de `app/src/components/ui/` se houver).
- Como detectar os extras instalados para o `uv sync`.
- Se `update.py` também roda a verificação de versão do backend ou só lê as tags.
