# Releases do TARS

Usamos versões `MAJOR.MINOR.PATCH` e tags `vMAJOR.MINOR.PATCH`. Correções incrementam patch;
funcionalidades compatíveis incrementam minor; mudanças incompatíveis incrementam major.
Durante a série 0.x, mudanças incompatíveis podem incrementar minor e devem ser explicadas nas notas.

## Preparar uma versão

Na `main`, ou numa branch se quiser revisar por pull request, atualize os seis arquivos de versão
com um só comando:

```bash
python3 scripts/release.py bump patch   # ou minor / major
python3 scripts/release.py check
uv lock --check
```

Revise o diff e envie o commit para `main`, direto ou por pull request. O PR é opcional: ele só
roda o CI antes de o bump entrar em `main`, e o workflow **Release** valida a versão e roda o CI
completo de novo antes de publicar. Depois que o CI passar em `main`:

```bash
git checkout main
git pull --ff-only
python3 scripts/release.py check --tag v0.1.1  # use a versão preparada
git tag -a v0.1.1 -m "TARS v0.1.1"
git push origin v0.1.1
```

A primeira versão já é `0.1.0`; para publicá-la, use `v0.1.0` sem executar o bump.
O script mantém Python, frontend, Tauri, Cargo e os dois lockfiles sincronizados, sem atualizar dependências.

## O que o GitHub faz

O workflow **Release** valida a tag e exige que seu commit pertença ao histórico de `main`.
Em seguida executa o CI completo, compila o desktop em Ubuntu 24.04 x86_64 e monta:

- `tars-X.Y.Z-linux-x86_64.tar.gz`: app compilado, backend Python, código, configurações de exemplo,
  lockfiles e instruções de instalação.
- `SHA256SUMS`: checksum SHA-256 do pacote.

O pacote é verificado antes do envio. A release é preparada como rascunho, recebe os arquivos e
só então é publicada, com notas geradas pelo GitHub. Uma release já publicada não é sobrescrita;
para corrigir um problema, prepare uma nova versão. Se o envio falhar antes da publicação,
reexecute o workflow: ele pode completar o rascunho.

O pacote requer Linux compatível com Ubuntu 24.04, GPU NVIDIA, dependências de áudio/teclado,
Python e uv. Modelos e bibliotecas Python são instalados no computador do usuário. Não contém
modelos, tokens, reuniões ou dados de uso. Veja [INSTALL_RELEASE.md](INSTALL_RELEASE.md).

## Validar sem publicar

No GitHub, abra **Actions → Release → Run workflow**, escolhendo `main`. A execução manual
executa os mesmos testes e build e disponibiliza o pacote nos artifacts do workflow por 14 dias.
A publicação da release ocorre somente ao enviar uma tag válida.

O CI normal também confere a consistência das versões. Os testes que precisam de modelos reais
na GPU continuam separados e devem ser executados no ambiente NVIDIA antes de uma versão que
altere captura, transcrição ou diarização.
