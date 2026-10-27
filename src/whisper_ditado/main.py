"""Entrypoint do backend novo: `python -m whisper_ditado.main [--port 47321] [--no-dictation]`.

Monta e liga todos os módulos num único processo e num único loop do asyncio:
banco/configurações/barramento de eventos, ditado (tecla global), reuniões (gravação, transcrição,
diarização, Claude) e a API local (uvicorn em 127.0.0.1).

- `api.json` (porta, token, pid) é gravado quando o servidor já está ouvindo e removido no
  encerramento limpo (só se ainda for o nosso pid). Após `kill -9` ele fica velho: o app confere o
  pid antes de usá-lo.
- SIGTERM/SIGINT: primeiro fecha a API (ninguém mais inicia reunião), depois encerra a gravação em
  andamento (`meetings.stop()`) e espera os arquivos `.raw` fecharem; a finalização que não couber
  no prazo roda no próximo início (`recover`).
- O uvicorn não instala handlers de sinal (`capture_signals` desligado): os handlers são do loop.
- A saída é por `os._exit` depois da limpeza: a finalização que passou do prazo (ou uma recarga do
  modelo do ditado) pode estar dentro do CTranslate2/pyannote/sherpa-onnx numa thread daemon, e a
  finalização normal do interpretador abortaria o processo (código 134 + core dump). O banco já
  está fechado e a reunião interrompida continua `processing`, que o `recover` refaz no próximo
  início.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import signal
import sys
import threading

import uvicorn

from whisper_ditado import api, dictation, paths
from whisper_ditado.issues.jobs import PostMeetingJobs
from whisper_ditado.issues.client import IssueClient, OptionsCache
from whisper_ditado.claude_cli import ClaudeCli, sync_summary_cli
from whisper_ditado.claude_models import ClaudeModels
from whisper_ditado.events import EventBus
from whisper_ditado.dictation_samples import DictationSamples
from whisper_ditado.meeting.diarize import Diarizer
from whisper_ditado.meeting.engines import DiarizerRouter
from whisper_ditado.meeting.local_diarize import LocalDiarizer
from whisper_ditado.meeting.session import MeetingManager
from whisper_ditado.priority import DictationBusy
from whisper_ditado.settings import Settings
from whisper_ditado.store import Store

log = logging.getLogger("ditado")

DEFAULT_PORT = 47321
HOST = "127.0.0.1"
SERVER_STOP_S = 10.0  # espera do encerramento gracioso da API antes de forçar
SHUTDOWN_WAIT_S = 30.0  # quanto esperar o fechamento/finalização da reunião ao encerrar
DICTATION_GRACE_S = 30.0  # além de `max_sec`: transcrever e colar o último ditado
MODEL_IDLE_CHECK_S = 5.0


class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield  # os sinais são tratados no loop (ver `serve`)


class _SettingsDiarizer(Diarizer):
    """Diarizer que relê o token das configurações a cada uso (sem reiniciar após `PUT /settings`)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        super().__init__(self._token())

    def _token(self) -> str | None:
        return self._settings.get("hf_token") or os.environ.get("HF_TOKEN") or None

    def _ensure_loaded(self):
        if self._pipeline is None:
            self.hf_token = self._token()
        return super()._ensure_loaded()


def build_diarizer(settings: Settings) -> DiarizerRouter:
    """Roteador que escolhe, a cada separação, o motor da configuração `diarizer`."""
    return DiarizerRouter(settings, LocalDiarizer(), _SettingsDiarizer(settings))


def _meeting_options(settings: Settings):
    """Opções do Whisper das reuniões: as do ditado, mas sempre em português (spec)."""
    return dataclasses.replace(dictation.transcriber_options(settings), language="pt")


def _dictation_gate(busy: DictationBusy, settings: Settings):
    """Portão da GPU das reuniões: espera o ditado acabar, no máximo uma gravação inteira."""

    def gate() -> None:
        limit = float(settings.get("max_sec")) + DICTATION_GRACE_S
        if not busy.wait_idle(limit):
            log.warning("Ditado ocupado há mais de %.0fs; seguindo com a reunião", limit)

    return gate


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m whisper_ditado.main", description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"porta da API local (padrão {DEFAULT_PORT})")
    parser.add_argument("--no-dictation", action="store_true",
                        help="não liga o ditado (sem teclado e sem carregar o modelo do ditado)")
    return parser.parse_args(argv)


def _remove_api_info() -> None:
    path = paths.api_info_path()
    try:
        if json.loads(path.read_text()).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


async def _run_server(server: uvicorn.Server) -> None:
    """`server.serve()` sem deixar um `SystemExit` (porta ocupada) escapar do loop."""
    try:
        await server.serve()
    except SystemExit as exc:
        raise RuntimeError(f"servidor encerrou com código {exc.code} (porta em uso?)") from None


async def _stop_server(server: uvicorn.Server, task: asyncio.Task) -> None:
    server.should_exit = True
    done, _ = await asyncio.wait({task}, timeout=SERVER_STOP_S)
    if not done:
        server.force_exit = True
        done, _ = await asyncio.wait({task}, timeout=SERVER_STOP_S)
        if not done:
            task.cancel()
            await asyncio.wait({task}, timeout=1)


async def _maintain_meeting_models(meetings: MeetingManager, shutdown: asyncio.Event) -> None:
    """Verifica ociosidade sem carregar modelos; a liberação é serializada com a GPU."""
    while not shutdown.is_set():
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=MODEL_IDLE_CHECK_S)
            return
        except TimeoutError:
            pass
        try:
            await asyncio.wrap_future(meetings.release_idle_models())
        except Exception:
            log.exception("Falha na liberação de memória das reuniões")


async def serve(args: argparse.Namespace) -> int:
    """Roda o backend até SIGTERM/SIGINT; devolve o código de saída (1 se a API não subiu/caiu)."""
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    store = Store(paths.db_path())
    settings = Settings(store)
    bus = EventBus()

    service = None
    if not args.no_dictation:
        opts, vocab = dictation.transcriber_options(settings), dictation.build_vocabulary(settings)
        transcriber = await loop.run_in_executor(None, dictation.make_transcriber, opts, vocab)
    if shutdown.is_set():  # SIGTERM durante o carregamento do modelo
        store.close()
        return 0

    claude = ClaudeCli(settings.get("claude_bin"), settings.get("claude_model"),
                       effort=settings.get("claude_effort"))

    def title_fn(transcript: str) -> str:
        sync_summary_cli(claude, settings)
        return claude.title(transcript)

    # Criador de issues (levantamento, opções e criação): modelo/effort próprios, independentes do resumo.
    tasks_claude = ClaudeCli(settings.get("claude_bin"), settings.get("tasks_model"),
                             effort=settings.get("tasks_effort"))
    issues_client = IssueClient(tasks_claude, lambda: settings.get("issues_mcp_url"),
                                lambda: settings.get("issues_mcp_name"))
    issue_options = OptionsCache(issues_client)
    jobs = PostMeetingJobs(store, settings, bus, claude, issues_client, issue_options, tasks_claude)
    stopping = threading.Event()  # o desligamento avisa as threads de fundo (ex.: catálogo de modelos)
    claude_models = ClaudeModels(store, lambda: settings.get("claude_bin"), stopping=stopping)

    def meeting_transcriber():  # segunda instância, carregada ao iniciar a primeira reunião
        return dictation.make_transcriber(_meeting_options(settings), dictation.build_vocabulary(settings))

    diarizer = build_diarizer(settings)
    # Ditado tem prioridade na GPU (spec §8.2): a reunião espera enquanto alguém dita.
    busy = None if args.no_dictation else DictationBusy()
    gate = _dictation_gate(busy, settings) if busy is not None else None
    # `on_span` passa time.monotonic(), o mesmo relógio padrão do MeetingManager.
    meetings = MeetingManager(store, settings, bus, meeting_transcriber, diarizer, title_fn,
                              gpu_gate=gate, on_ready=jobs.on_ready)
    samples = DictationSamples(store)
    if not args.no_dictation:
        service = dictation.DictationService(settings, transcriber, bus, on_span=meetings.register_dictation_span,
                                             dictation_busy=busy, samples=samples)
        # O serviço é o dono do modelo: a coroutine não deve reter a primeira instância
        # depois que o usuário escolher outro Whisper nas configurações.
        del transcriber

    store.recover_interrupted()  # criações/levantamentos que o último desligamento deixou no meio
    meetings.recover()

    token = api.generate_token()
    ctx = api.AppContext(store=store, settings=settings, bus=bus, dictation=service,
                         meetings=meetings, claude=claude, diarizer=diarizer,
                         jobs=jobs, issue_options=issue_options, tasks_claude=tasks_claude,
                         claude_models=claude_models)
    # access_log desligado: a URL do WebSocket leva `?token=` e nunca pode ir para o log.
    server = _Server(uvicorn.Config(api.create_app(ctx, token), host=HOST, port=args.port,
                                    access_log=False, log_level="warning"))

    dictation_task = None
    server_task = None
    memory_task = None
    try:
        if shutdown.is_set():  # SIGTERM durante o carregamento/recuperação
            return 0
        if service is not None:
            dictation_task = asyncio.create_task(service.run())
            dictation_task.add_done_callback(
                lambda t: t.cancelled() or t.exception() is None
                or log.error("Ditado parou", exc_info=t.exception()))
        server_task = asyncio.create_task(_run_server(server))
        while not server.started and not server_task.done():
            await asyncio.sleep(0.05)
        if server.started:
            api.write_api_info(args.port, token)
            log.info("API em http://%s:%d", HOST, args.port)
            claude_models.start_auto_refresh(stopping)  # cache ausente/velho: atualiza em segundo plano
            memory_task = asyncio.create_task(_maintain_meeting_models(meetings, shutdown))
        waiter = asyncio.create_task(shutdown.wait())
        await asyncio.wait({waiter, server_task}, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        if shutdown.is_set():
            log.info("Encerrando...")
            return 0
        # A API caiu sozinha (ex.: porta em uso): deixa a causa no log e sai com erro.
        exc = server_task.exception() if server_task.done() and not server_task.cancelled() else None
        log.error("API parou antes do desligamento", exc_info=exc)
        return 1
    finally:
        stopping.set()  # a atualização do catálogo de modelos não começa mais nenhuma sonda
        if memory_task is not None:
            memory_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await memory_task
        _remove_api_info()
        # 0) Abre o portão do ditado: a finalização abaixo tem prazo e não pode ficar esperando um
        # Ctrl+Win segurado (ou um contador preso) por até `max_sec + DICTATION_GRACE_S`.
        if busy is not None:
            busy.open()
        # 1) Fecha a API: depois disso ninguém mais inicia reunião (nada fica órfão).
        if server_task is not None:
            await _stop_server(server, server_task)
        # 2) Encerra a gravação (fecha os .raw) e espera a finalização, fora do loop.
        for _ in range(2):  # confere de novo o estado depois da espera
            if meetings.state == "recording":
                try:
                    meetings.stop()
                except Exception:
                    log.exception("Falha ao encerrar a gravação")
            if not await loop.run_in_executor(None, meetings.wait_idle, SHUTDOWN_WAIT_S):
                log.warning("Reunião ainda em processamento; será finalizada no próximo início")
                break
            if meetings.state != "recording":
                break
        # 3) Ditado e banco.
        if dictation_task is not None:
            service.stop()  # na thread do loop
            with contextlib.suppress(Exception):
                await dictation_task
        jobs.shutdown()  # mata os `claude` do resumo/levantamento/criação; as threads são daemon
        await loop.run_in_executor(None, samples.shutdown)
        store.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    log.setLevel(logging.DEBUG if os.environ.get("DITADO_DEBUG") == "1" else logging.INFO)
    logging.getLogger("whisper_ditado").setLevel(log.level)
    return asyncio.run(serve(args))


def _hard_exit(rc: int) -> None:
    """Esvazia logs e saídas e encerra sem finalizar o interpretador (ver docstring do módulo)."""
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    os._exit(rc)


def _entrypoint() -> None:
    try:
        code = main()
    except Exception:  # erro inesperado no loop: registra e sai do mesmo jeito (sem abortar)
        log.exception("Backend encerrou com erro")
        code = 1
    _hard_exit(code)


if __name__ == "__main__":
    _entrypoint()
