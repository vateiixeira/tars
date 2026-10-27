# Instalar o pacote Linux do TARS

Baixe o arquivo `tars-X.Y.Z-linux-x86_64.tar.gz` e `SHA256SUMS` da mesma release. Em uma pasta
que contenha somente esses arquivos da versão, confira e extraia:

```bash
sha256sum -c SHA256SUMS
tar -xzf tars-X.Y.Z-linux-x86_64.tar.gz
cd tars-X.Y.Z
```

Substitua `X.Y.Z` pela versão baixada. O app já vem compilado; Node.js, pnpm e Rust não são
necessários para usar esse pacote. O backend Python e os modelos são instalados separadamente.

O pacote é compilado em Ubuntu 24.04 para x86_64. Você precisa de GPU NVIDIA com driver
compatível com CUDA 12.8, Python 3.12+ e [uv](https://docs.astral.sh/uv/getting-started/installation/).
Em Ubuntu 24.04, as dependências de execução são:

```bash
sudo apt update
sudo apt install ffmpeg pipewire-bin wl-clipboard xclip ydotool libnotify-bin \
  libwebkit2gtk-4.1-0 libayatana-appindicator3-1 librsvg2-2
uv sync --frozen --no-dev
python3 scripts/install_user.py --dry-run
python3 scripts/install_user.py --enable --start
```

Os atalhos e a colagem também exigem as permissões de `/dev/input` e `/dev/uinput` e o daemon
`ydotoold` em execução, conforme a seção de instalação do [README](../README.md).
Abra **TARS** pelo menu. Mantenha a pasta extraída no mesmo lugar após instalar os lançadores.
Os modelos são baixados no primeiro uso. Claude Code e o token do Hugging Face são opcionais,
conforme as funcionalidades descritas no README.

Para atualizar, extraia a nova versão em outra pasta e prepare suas dependências com `uv sync --frozen --no-dev`.
Encerre o app e pare o backend quando não houver reunião ou ditado em andamento. Execute o
instalador da nova pasta e inicie o app novamente. Reuniões, vozes e configurações ficam na
mesma pasta de dados do usuário. Não edite ou substitua os arquivos do programa enquanto ele grava.
