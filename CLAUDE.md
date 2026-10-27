# TARS

Aplicativo Linux de ditado, transcrição de áudio e reuniões, com backend Python e
interface Vue/Tauri. Consulte [README.md](README.md) para instalação e arquitetura
e [CONTRIBUTING.md](CONTRIBUTING.md) para desenvolvimento e validação.

## Worktrees

É recomendável desenvolver features novas num git worktree separado, numa branch
própria, para não mexer no checkout da `main`. Siga o padrão de diretório irmão
`../whisper-ditado-<nome>`:

```bash
git worktree add ../whisper-ditado-<nome> -b <branch>
```

Correções pequenas e ajustes pontuais podem ser feitos direto no checkout atual.
Remova o worktree com `git worktree remove` depois do merge.

## Versionamento e releases

O fluxo completo está em [docs/RELEASING.md](docs/RELEASING.md).
Use versões estáveis `MAJOR.MINOR.PATCH` e tags `vMAJOR.MINOR.PATCH`:
patch para correções, minor para funcionalidades compatíveis e major para mudanças
incompatíveis. Durante a série 0.x, mudanças incompatíveis podem incrementar minor,
com explicação nas notas da versão.

O dono do projeto trabalha e faz push direto na `main`, sem pull request.
Contribuidores enviam pull requests, e só neles o CI roda automaticamente.

Para publicar uma versão, use um único comando na `main` atualizada:

```bash
python3 scripts/release.py publish patch # ou minor / major
```

Ele confere que a `main` está limpa e em dia com `origin/main` e que não há
arquivos fora do git (que os testes enxergariam mas não seriam publicados; use
`--allow-untracked` para seguir mesmo assim). Depois roda localmente as
verificações do CI (`pytest`, `pnpm test`, `pnpm build` e `cargo test`).
Depois sobe a versão nos seis arquivos abaixo sem atualizar dependências e confere
`uv lock --check`. Por fim, cria o commit `[Release] Publicar versão X.Y.Z` e a
tag anotada `vX.Y.Z` e envia as duas juntas. Se algo falhar antes do push, nada é
commitado e os arquivos de versão voltam ao estado anterior.

- `pyproject.toml`
- `uv.lock`
- `app/package.json`
- `app/src-tauri/tauri.conf.json`
- `app/src-tauri/Cargo.toml`
- `app/src-tauri/Cargo.lock`

Nunca edite esses números à mão nem crie tags `v*` à mão: o workflow Release não
roda testes e confia que o `publish` já os executou. `python3 scripts/release.py
bump` e `check` continuam disponíveis para usos avulsos.

A tag dispara o workflow **Release**. Ele valida a versão e exige que o commit
pertença ao histórico de `main`. Os testes não rodam de novo, porque já rodaram
localmente. O workflow compila o aplicativo em Ubuntu 24.04 x86_64 e publica o
pacote `tars-X.Y.Z-linux-x86_64.tar.gz`, `SHA256SUMS` e as notas geradas pelo
GitHub. A release fica como rascunho até os arquivos serem enviados e verificados.

Releases já publicadas não são sobrescritas: correções exigem uma nova versão.
Uma execução que falhou antes da publicação pode ser reexecutada para completar
o rascunho.

Para validar o build sem publicar, execute **Actions → Release → Run workflow**
em `main`; o pacote fica nos artifacts por 14 dias. O CI também pode ser disparado
à mão em **Actions → CI → Run workflow**.

Mudanças em captura, transcrição ou diarização também exigem os testes com modelos
reais no ambiente NVIDIA antes da release (`uv run --frozen pytest -m gpu`), pois
eles não fazem parte das verificações comuns. O `publish` avisa quando a versão
inclui mudanças nesses caminhos ou nas dependências (`pyproject.toml`, `uv.lock`). O pacote inclui o app compilado e o backend, mas a instalação ainda
requer as dependências descritas em
[docs/INSTALL_RELEASE.md](docs/INSTALL_RELEASE.md). Modelos, tokens e dados de
reuniões não devem entrar no pacote.
