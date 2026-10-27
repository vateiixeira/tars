# TARS

Aplicativo Linux de ditado, transcrição de áudio e reuniões, com backend Python e
interface Vue/Tauri. Consulte [README.md](README.md) para instalação e arquitetura
e [CONTRIBUTING.md](CONTRIBUTING.md) para desenvolvimento e validação.

## Versionamento e releases

O fluxo completo está em [docs/RELEASING.md](docs/RELEASING.md).
Use versões estáveis `MAJOR.MINOR.PATCH` e tags `vMAJOR.MINOR.PATCH`:
patch para correções, minor para funcionalidades compatíveis e major para mudanças
incompatíveis. Durante a série 0.x, mudanças incompatíveis podem incrementar minor,
com explicação nas notas da versão.

Sempre use o script para atualizar versões; ele sincroniza os seis arquivos abaixo
sem atualizar dependências:

- `pyproject.toml`
- `uv.lock`
- `app/package.json`
- `app/src-tauri/tauri.conf.json`
- `app/src-tauri/Cargo.toml`
- `app/src-tauri/Cargo.lock`

Na `main` (ou numa branch, se quiser revisar por pull request):

```bash
python3 scripts/release.py bump patch # ou minor / major
python3 scripts/release.py check
uv lock --check
```

Revise o diff e envie o commit para `main`, direto ou por pull request; o PR é
opcional. Depois que o CI passar em `main`, a publicação usa uma tag anotada com
a versão preparada.
Exemplo para `0.1.1` (substitua pela versão que será publicada):

```bash
git checkout main
git pull --ff-only
python3 scripts/release.py check --tag v0.1.1
git tag -a v0.1.1 -m "TARS v0.1.1"
git push origin v0.1.1
```

Enviar a tag dispara a publicação automática. O workflow **Release** valida a
versão e exige que o commit pertença ao histórico de `main`, executa o CI completo
e compila o aplicativo em Ubuntu 24.04 x86_64. Publica o pacote
`tars-X.Y.Z-linux-x86_64.tar.gz`, `SHA256SUMS` e notas geradas pelo GitHub.
A release fica como rascunho até os arquivos serem enviados e verificados.

Releases já publicadas não são sobrescritas: correções exigem uma nova versão.
Uma execução que falhou antes da publicação pode ser reexecutada para completar
o rascunho.

Para validar sem publicar, execute **Actions → Release → Run workflow** em `main`.
Essa execução realiza os testes e o build e mantém o pacote nos artifacts por
14 dias; a publicação acontece apenas ao enviar uma tag válida.

Mudanças em captura, transcrição ou diarização também exigem os testes com modelos
reais no ambiente NVIDIA antes da release, pois esses testes não fazem parte do
CI comum. O pacote inclui o app compilado e o backend, mas a instalação ainda
requer as dependências descritas em
[docs/INSTALL_RELEASE.md](docs/INSTALL_RELEASE.md). Modelos, tokens e dados de
reuniões não devem entrar no pacote.
