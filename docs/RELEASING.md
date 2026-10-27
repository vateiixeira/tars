# Releases do TARS

Usamos versões `MAJOR.MINOR.PATCH` e tags `vMAJOR.MINOR.PATCH`. Correções incrementam patch;
funcionalidades compatíveis incrementam minor; mudanças incompatíveis incrementam major.
Durante a série 0.x, mudanças incompatíveis podem incrementar minor e devem ser explicadas nas notas.

## Fluxo de trabalho

O mantenedor trabalha e faz push direto na `main`. Contribuidores enviam pull requests para a
`main`; o CI do GitHub roda neles e também pode ser disparado em **Actions → CI → Run workflow**.

## Publicar uma versão

Na `main`, sem alterações pendentes e em dia com `origin/main`:

```bash
python3 scripts/release.py publish patch   # ou minor / major
```

O comando:

1. confere a branch, as alterações pendentes e se a `main` local está atrás de `origin/main`;
2. avisa se há mudanças em captura, transcrição ou diarização desde a última tag (veja abaixo);
3. roda `uv run --frozen pytest -q`, `pnpm install --frozen-lockfile`, `pnpm test`, `pnpm build`
   e `cargo test --locked --lib`, como o CI;
4. sobe a versão em Python, frontend, Tauri, Cargo e nos dois lockfiles, sem atualizar
   dependências, e confere `uv lock --check`;
5. cria o commit `[Release] Publicar versão X.Y.Z` e a tag anotada `vX.Y.Z`;
6. envia `main` e a tag num único `git push --atomic`.

Se uma verificação falhar, nada é commitado e os arquivos de versão voltam ao estado anterior.
Se só o push falhar, o commit e a tag ficam locais e o comando mostra como reenviar.

Mudanças em captura, transcrição ou diarização exigem os testes com modelos reais na GPU antes
da versão. Configure as bibliotecas CUDA como em `run-backend.sh` e rode
`uv run --frozen pytest -m gpu`.

`python3 scripts/release.py bump` e `python3 scripts/release.py check` continuam disponíveis para
preparar ou conferir versões à mão.

## O que o GitHub faz

A tag dispara o workflow **Release**. Ele valida a tag e exige que seu commit pertença ao histórico
de `main`; os testes não rodam de novo, porque o `publish` já os executou. Em seguida compila o
desktop em Ubuntu 24.04 x86_64 e monta:

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
compila e disponibiliza o pacote nos artifacts do workflow por 14 dias. A publicação da release
ocorre somente ao enviar uma tag válida.
