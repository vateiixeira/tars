# Contribuindo com o TARS

Abra uma issue para bugs ou propostas e envie mudanças por pull request. Para mudanças maiores,
explique o problema e a abordagem antes de começar. Use dados sintéticos em testes e capturas.

## Ambiente e verificações

Siga o README para instalar Python 3.12+, uv, Node.js 24+, pnpm, Rust e as dependências Linux.
Na raiz do projeto:

```bash
uv sync --frozen --group dev --extra pyannote
uv run --frozen pytest -q
```

Sem o extra `pyannote`, os testes do pyannote são pulados. Para rodar os testes que baixam os
modelos do sherpa-onnx, use `uv run --frozen pytest -m models`.

Os testes que exigem GPU são separados e precisam dos modelos no cache. Para rodá-los, configure
as bibliotecas CUDA como em `run-backend.sh` e execute `uv run --frozen pytest -m gpu`.
Não use reuniões reais como fixtures e não publique tokens do Hugging Face ou do MCP.

Na pasta `app/`:

```bash
pnpm install --frozen-lockfile
pnpm test
pnpm build
cd src-tauri
cargo test --locked --lib
```

Descreva o problema, a mudança e as verificações no pull request. Preserve as configurações,
reuniões e vozes existentes ao mudar formatos de dados. A licença das contribuições é MIT.
