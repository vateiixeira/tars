"""Sessão de reunião: gravação em duas trilhas alinhadas e agendamento da finalização.

Modelo de threads (nada disto bloqueia o loop do asyncio):

- `start`, `stop`, `monitor_levels`, `register_dictation_span`, `reprocess` e `recover`
  são chamados na thread do loop e só fazem trabalho rápido (banco, eventos); o resto
  vai para threads próprias.
- `meeting-setup`: inicia já as duas `TrackRecorder` com o mesmo `t0` (PC primeiro; os
  alvos são os dispositivos fixos ou o padrão do sistema), depois escolhe os dispositivos
  (`pw-dump`), redireciona a trilha do PC para o app da chamada, pede o AEC e liga o
  ticker. Se uma trilha não puder ser iniciada, a reunião segue gravando a outra e o
  ticker publica `mic_dead`/`pc_dead` (visível em `active_alerts` e no status): nunca
  fica "gravando" sem aviso.
- `meeting-tick`: chama `tick()` a cada `tick_s` (100 ms). Cada tick publica os níveis
  e alertas; a cada 2 s relê o grafo do PipeWire (app da chamada, padrão do sistema);
  a cada 10 s agenda a transcrição incremental. Com `tick_s=None` não há ticker e
  quem chama `tick()` é o teste, com relógio falso.
- `meeting-aec`: liga o `EchoCanceller` no início e o reinicia quando o microfone ou a
  saída mudam. O microfone grava o dispositivo cru enquanto o AEC carrega e volta para
  ele antes de o AEC antigo ser desligado (nunca aponta para um nó que sumiu). Cada AEC
  tem um nó próprio (`whisper_ditado_aec_<reunião>_<n>`).
- `meeting-stop`: para ticker, gravadores e AEC, grava os alertas e agenda a finalização.
- `meeting-gpu`: uma thread única (daemon) para tudo que usa o modelo — carga do
  Whisper das reuniões, blocos incrementais, finalização, reprocessamento e
  recuperação —, sempre em série. O ditado tem prioridade (spec §8.2): `gpu_gate()`
  (espera limitada enquanto alguém dita) roda antes de cada tarefa, antes de cada
  trecho decodificado pelo Whisper das reuniões (`Transcriber.pause`) e antes da
  diarização.
- `meeting-levels`: medição de níveis fora de reunião (tela de configurações).
- `meeting-print`: uma captura de tela pelo portal (modo `native`), disparada pelo atalho
  global; `capture_print` só agenda e volta na hora (é chamado do loop do evdev). Um
  segundo aperto durante a captura é ignorado (`_print_lock`).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from whisper_ditado import paths
from whisper_ditado.audio import pwgraph
from whisper_ditado.audio.aec import SOURCE_NAME as AEC_PREFIX
from whisper_ditado.audio.aec import EchoCanceller
from whisper_ditado.audio.capture import BYTES_PER_SAMPLE, SAMPLE_RATE, TrackRecorder
from whisper_ditado.audio.levels import KINDS, HealthMonitor
from whisper_ditado.feedback import notify_error, play_print_sound
from whisper_ditado.meeting import finalize as fin
from whisper_ditado.meeting.incremental import IncrementalTranscriber, Vad, default_vad
from whisper_ditado.meeting.voice_sync import VoiceSync
from whisper_ditado.memory import release_unused_memory
from whisper_ditado.prints.files import store_print
from whisper_ditado.prints.flameshot import import_folder, resolve_folder
from whisper_ditado.prints.portal import capture_screen
from whisper_ditado.prints.timing import ClockMarks
from whisper_ditado.settings import Settings
from whisper_ditado.store import Store
from whisper_ditado.worker import SerialWorker

log = logging.getLogger("ditado")

State = Literal["idle", "recording", "processing"]

TICK_S = 0.1
GRAPH_EVERY_S = 2.0
FEED_EVERY_S = 10.0
CLOCK_MARK_S = 10.0  # par (relógio da reunião, relógio de parede) para datar prints do Flameshot
CLOCK_JUMP_S = 1.0  # salto do relógio de parede (suspensão) que antecipa a próxima marca
DEAD_AFTER_S = 1.0  # sem processo de captura por mais que isso = trilha parou
MONITOR_MAX_S = 60.0
LEVEL_FLOOR_DB = -100.0  # -inf não é JSON válido
TRACKS = ("mic", "pc")

ALERT_MESSAGES = {
    "mic_silent": "Microfone sem sinal",
    "pc_silent": "Nenhum som do PC há um tempo",
    "pc_wrong_output": "Áudio da chamada não está sendo captado",
    "mic_dead": "Microfone desconectado",
    "pc_dead": "Captura do áudio do PC parou",
    "print_failed": "Não foi possível tirar um print",
}


class MeetingStateError(RuntimeError):
    """Operação incompatível com o estado atual (ex.: iniciar com outra reunião gravando)."""


@dataclass(eq=False)
class _Session:
    meeting_id: str
    started_at: datetime
    t0: float
    directory: Path
    mic_fixed: str | None
    pc_fixed: str | None
    follow: bool
    aec_enabled: bool
    health: HealthMonitor
    clock: ClockMarks
    last_clock_mark: float  # relógio monotônico da última marca
    last_clock_wall: datetime  # relógio de parede da última marca
    mic_device: str | None = None  # microfone em uso (sem o AEC)
    mic_target: str | None = None  # alvo atual da trilha do microfone (dispositivo ou AEC)
    pc_sink: str | None = None
    followed_app: str | None = None
    mismatch: bool = False
    alerts: tuple[str, ...] = ()  # cópia dos alertas ativos (leitura fora do ticker)
    mic: TrackRecorder | None = None
    pc: TrackRecorder | None = None
    aec: EchoCanceller | None = None
    incremental: IncrementalTranscriber | None = None
    model_failed: bool = False  # carga falhou: não tenta de novo a cada bloco (só na finalização)
    stopping: bool = False
    last_graph: float = 0.0
    last_feed: float = 0.0
    feed: Future | None = None
    dead_since: dict[str, float] = field(default_factory=dict)
    ready: threading.Event = field(default_factory=threading.Event)
    stop_ticker: threading.Event = field(default_factory=threading.Event)
    setup_thread: threading.Thread | None = None
    ticker: threading.Thread | None = None
    aec_lock: threading.Lock = field(default_factory=threading.Lock)
    aec_thread: threading.Thread | None = None
    aec_dirty: bool = False
    aec_count: int = 0


class _YieldingDiarizer:
    """Diarizador que espera o ditado (`gate`) antes de usar a GPU."""

    def __init__(self, inner, gate: Callable[[], None]) -> None:
        self._inner = inner
        self._gate = gate

    @property
    def available(self) -> bool:
        return self._inner.available

    def run(self, audio):
        self._gate()
        result = self._inner.run(audio)
        embed = getattr(result, "embed", None)
        if embed is None:
            return result

        def gated_embed(*args, **kwargs):
            self._gate()
            return embed(*args, **kwargs)

        return replace(result, embed=gated_embed)

    def unload(self) -> None:
        unload = getattr(self._inner, "unload", None)
        if unload is not None:
            unload()


@dataclass(eq=False)
class _LevelMonitor:
    deadline: float
    stop: threading.Event = field(default_factory=threading.Event)


def _db(value: float) -> float:
    return round(max(float(value), LEVEL_FLOOR_DB), 1)


class MeetingManager:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        bus,
        meeting_transcriber_factory: Callable[[], object],
        diarizer,
        title_fn: Callable[[str], str] | None = None,
        *,
        recorder_factory: Callable[[str, Path, str | None, bool], TrackRecorder] | None = None,
        aec_factory: Callable[..., EchoCanceller] = EchoCanceller,  # (mic, sink, name=...)
        snapshot: Callable[[], pwgraph.Graph] = pwgraph.snapshot,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
        tick_s: float | None = TICK_S,
        vad: Vad = default_vad,
        own_pids: set[int] | None = None,
        gpu_gate: Callable[[], None] | None = None,
        on_ready: Callable[[str], None] | None = None,
        capture: Callable[[], Path] = capture_screen,
        print_sound: Callable[[bool], object] = play_print_sound,
        notify_fn: Callable[[str], None] = notify_error,
    ) -> None:
        self._store = store
        self._settings = settings
        self._bus = bus
        self._transcriber_factory = meeting_transcriber_factory
        self._gpu_gate = gpu_gate
        self._on_ready = on_ready  # reunião ficou `ready` (só enfileira; ver issues/jobs.py)
        self._diarizer = (_YieldingDiarizer(diarizer, gpu_gate)
                          if diarizer is not None and gpu_gate is not None else diarizer)
        self._title_fn = title_fn
        self._voice_sync = VoiceSync(fin._load_pc)
        self._recorder_factory = recorder_factory or (
            lambda name, path, target, sink: TrackRecorder(name, path, target, sink, clock=clock)
        )
        self._aec_factory = aec_factory
        self._snapshot = snapshot
        self._clock = clock
        self._now = now
        self._tick_s = tick_s
        self._vad = vad
        self._own_pids = own_pids if own_pids is not None else {os.getpid()}
        self._capture = capture
        self._print_sound = print_sound
        self._notify_fn = notify_fn
        self._print_lock = threading.Lock()  # uma captura por vez

        self._lock = threading.Lock()
        self._session: _Session | None = None
        self._processing: list[str] = []
        self._last_stage: dict[str, str] = {}
        self._monitor: _LevelMonitor | None = None
        self._threads: list[threading.Thread] = []
        self._transcriber = None
        self._models_used_at: float | None = None
        self._reload_requested = False
        self._gpu = SerialWorker("meeting-gpu", before=gpu_gate)

    # -- estado ------------------------------------------------------------------

    @property
    def state(self) -> State:
        with self._lock:
            if self._session is not None:
                return "recording"
            return "processing" if self._processing else "idle"

    @property
    def current_id(self) -> str | None:
        with self._lock:
            if self._session is not None:
                return self._session.meeting_id
            return self._processing[0] if self._processing else None

    @property
    def started_at(self) -> datetime | None:
        with self._lock:
            return self._session.started_at if self._session else None

    @property
    def followed_app(self) -> str | None:
        with self._lock:
            return self._session.followed_app if self._session else None

    @property
    def active_alerts(self) -> list[str]:
        with self._lock:
            s = self._session
        return list(s.alerts) if s is not None else []

    # -- gravação ----------------------------------------------------------------

    def start(self) -> str:
        """Cria a reunião `recording` e começa a gravar (a preparação segue em thread)."""
        with self._lock:
            if self._session is not None:
                raise MeetingStateError("Já existe uma reunião sendo gravada")
            started_at, t0 = self._now(), self._clock()
            meeting = self._store.create_meeting(started_at)
            try:
                directory = paths.meeting_dir(meeting.id)
            except Exception as exc:
                fin.mark_failed(self._store, meeting.id, exc)
                raise
            s = _Session(
                meeting_id=meeting.id, started_at=started_at, t0=t0, directory=directory,
                mic_fixed=self._settings.get("mic_device"),
                pc_fixed=self._settings.get("pc_device"),
                follow=bool(self._settings.get("follow_call_app")),
                aec_enabled=bool(self._settings.get("aec_enabled")),
                health=HealthMonitor(lambda: self._clock() - t0),
                clock=ClockMarks(started_at), last_clock_mark=t0, last_clock_wall=started_at,
            )
            self._session = s
            monitor, self._monitor = self._monitor, None
        if monitor is not None:
            monitor.stop.set()
        self._meeting_updated(meeting.id, "recording")
        s.setup_thread = self._thread(self._setup, s, name="meeting-setup", track=False)
        self._gpu.submit(self._prepare_transcription, s)  # carrega o modelo já no início
        return meeting.id

    def stop(self) -> str:
        """Encerra a gravação e agenda a finalização; devolve o id da reunião."""
        with self._lock:
            s = self._session
            if s is None:
                raise MeetingStateError("Nenhuma reunião sendo gravada")
            self._session = None
            s.stopping = True
            self._processing.append(s.meeting_id)
        self._store.update_meeting(s.meeting_id, status="processing", ended_at=self._now())
        self._meeting_updated(s.meeting_id, "processing")
        self._thread(self._teardown, s, name="meeting-stop")
        return s.meeting_id

    def register_dictation_span(self, start_mono: float, end_mono: float) -> None:
        """Trecho ditado (tempo monotônico) -> `dictation_spans` relativo ao início."""
        with self._lock:
            s = self._session
        if s is None:
            return
        start, end = max(start_mono - s.t0, 0.0), end_mono - s.t0
        if end > start:
            self._store.add_dictation_span(s.meeting_id, start, end)

    def tick(self) -> None:
        """Uma rodada das tarefas periódicas (o ticker chama a cada `tick_s`)."""
        with self._lock:
            s = self._session
        if s is None or s.stopping or not s.ready.is_set():
            return
        now = self._clock()
        if now - s.last_graph >= GRAPH_EVERY_S:
            s.last_graph = now
            self._check_graph(s)
        if now - s.last_feed >= FEED_EVERY_S and (s.feed is None or s.feed.done()):
            s.last_feed = now
            s.feed = self._gpu.submit(self._feed, s)
        wall = self._now()
        mono_delta = now - s.last_clock_mark
        # parede andou mais que o monotônico: o sistema retomou da suspensão, marca já
        if mono_delta >= CLOCK_MARK_S or (
                (wall - s.last_clock_wall).total_seconds() - mono_delta > CLOCK_JUMP_S):
            s.last_clock_mark, s.last_clock_wall = now, wall
            s.clock.mark(now - s.t0, wall)
        self._update_health(s, now)

    # -- prints de tela ----------------------------------------------------------

    def capture_print(self) -> bool:
        """Agenda um print da tela (modo `native`); True se uma captura começou."""
        with self._lock:
            s = self._session
        if s is None or self._settings.get("print_mode") != "native":
            return False
        if not self._print_lock.acquire(blocking=False):
            return False  # já há uma captura em andamento
        at_s = max(self._clock() - s.t0, 0.0)
        try:
            self._thread(self._capture_job, s, at_s, name="meeting-print", track=False)
        except RuntimeError:
            self._print_lock.release()
            log.exception("Não foi possível iniciar a captura do print")
            return False
        return True

    def _capture_job(self, s: _Session, at_s: float) -> None:
        mid = s.meeting_id
        try:
            try:
                shot = self._capture()
                p = store_print(self._store, mid, shot, at_s=at_s, source="native", move=True)
            except Exception as exc:
                log.warning("Falha ao tirar o print da reunião %s: %s", mid, exc)
                self._safely(self._print_sound, False)
                self._safely(self._notify_fn, f"Não foi possível tirar o print: {exc}")
                self._safely(self._store.add_alert, mid, "print_failed", at_s, at_s)
                return
            self._safely(self._print_sound, True)
            if p is not None:
                self._bus.publish({"type": "print_captured", "meeting_id": mid, "print_id": p.id})
        finally:
            self._print_lock.release()

    @staticmethod
    def _safely(fn: Callable, *args) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("Falha ao avisar sobre o print")

    # -- níveis fora de reunião --------------------------------------------------

    def monitor_levels(self, on: bool) -> None:
        """Liga/desliga a publicação de níveis fora de reunião (desliga sozinho após 60 s)."""
        with self._lock:
            if self._session is not None:
                return  # gravando: os níveis já são publicados
            current = self._monitor
            if not on:
                self._monitor = None
                if current is not None:
                    current.stop.set()
                return
            deadline = self._clock() + MONITOR_MAX_S
            if current is not None:
                current.deadline = deadline
                return
            monitor = self._monitor = _LevelMonitor(deadline)
        self._thread(self._run_monitor, monitor, name="meeting-levels", track=False)

    def _run_monitor(self, monitor: _LevelMonitor) -> None:
        recorders: list[TrackRecorder] = []
        try:
            mic_dev, target, _ = self._choose_targets(
                self._settings.get("mic_device"), self._settings.get("pc_device"),
                bool(self._settings.get("follow_call_app")),
            )
            devnull = Path(os.devnull)
            recorders = [
                self._recorder_factory("mic", devnull, mic_dev, False),
                self._recorder_factory("pc", devnull, target.sink, True),
            ]
            t0 = self._clock()
            for rec in recorders:
                rec.start(t0)
            while not monitor.stop.wait(self._tick_s or TICK_S):
                with self._lock:  # sob o lock: um `monitor_levels(True)` agora estende o prazo
                    if self._clock() >= monitor.deadline:
                        if self._monitor is monitor:
                            self._monitor = None
                        break
                self._bus.publish({"type": "levels", "mic_db": _db(recorders[0].level_db),
                                   "pc_db": _db(recorders[1].level_db)})
        except Exception:
            log.exception("Falha na medição de níveis")
        finally:
            for rec in recorders:
                try:
                    rec.stop()
                except Exception:
                    log.exception("Falha ao parar a medição de níveis")
            with self._lock:
                if self._monitor is monitor:
                    self._monitor = None

    # -- reprocessar e recuperar -------------------------------------------------

    def reprocess(self, meeting_id: str) -> None:
        """Refaz a transcrição a partir do áudio salvo, em segundo plano."""
        meeting = self._store.get_meeting(meeting_id)
        if meeting is None:
            raise KeyError(meeting_id)
        with self._lock:
            busy = meeting_id in self._processing or (
                self._session is not None and self._session.meeting_id == meeting_id)
            if busy or meeting.status in ("recording", "processing"):
                raise MeetingStateError("A reunião ainda está sendo gravada ou processada")
            self._processing.append(meeting_id)
        self._store.update_meeting(meeting_id, status="processing", error=None)
        self._meeting_updated(meeting_id, "processing")
        self._gpu.submit(self._reprocess_job, meeting_id)

    def recover(self) -> None:
        """Agenda a finalização das reuniões que ficaram presas (backend caiu)."""
        self._gpu.submit(self._recover_job)

    def release_idle_models(self) -> Future:
        """A manutenção roda na própria fila da GPU, sem disputar uma inferência."""
        return self._gpu.submit(self._release_idle_models, gated=False)

    def apply_settings(self) -> None:
        """Troca modelo/precisão/prompt só depois que as reuniões em andamento terminarem."""
        with self._lock:
            self._reload_requested = True
        self.release_idle_models()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Espera o fim do trabalho em segundo plano já agendado (encerramento, GPU)."""
        deadline = None if timeout is None else time.monotonic() + timeout

        def remaining() -> float | None:
            return None if deadline is None else max(deadline - time.monotonic(), 0.0)

        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(remaining())
            if thread.is_alive():
                return False
        try:
            self._gpu.submit(lambda: None, gated=False).result(remaining())
        except TimeoutError:
            return False
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
        return True

    # -- internos: preparação e encerramento -------------------------------------

    def _thread(self, fn: Callable, *args, name: str, track: bool = True) -> threading.Thread:
        thread = threading.Thread(target=fn, args=args, name=name, daemon=True)
        if track:
            with self._lock:
                self._threads.append(thread)
        thread.start()
        return thread

    def _choose_targets(
        self, mic_fixed: str | None, pc_fixed: str | None, follow: bool
    ) -> tuple[str | None, pwgraph.Target, bool]:
        """(microfone, alvo do PC, saída errada?) a partir do grafo atual."""
        try:
            graph = self._snapshot()
        except Exception as exc:
            log.warning("pw-dump indisponível; usando os dispositivos padrão: %s", exc)
            return mic_fixed, pwgraph.Target(pc_fixed, None, False), False
        apps = pwgraph.call_apps(graph, self._own_pids)
        target = pwgraph.pick_pc_target(graph, apps, pc_fixed, follow)
        return mic_fixed or graph.default_source, target, self._mismatch(target, apps)

    @staticmethod
    def _mismatch(target: pwgraph.Target, apps: list[pwgraph.CallApp]) -> bool:
        playing_elsewhere = bool(apps and apps[0].sinks and target.sink not in apps[0].sinks)
        return target.mismatch or playing_elsewhere

    def _setup(self, s: _Session) -> None:
        try:
            # Grava desde já (o começo da reunião não vira silêncio): PC primeiro, microfone no
            # dispositivo cru; os alvos são refinados com o grafo e o AEC logo em seguida.
            s.pc = self._start_track(s, "pc", s.pc_fixed, True)
            s.mic = self._start_track(s, "mic", s.mic_fixed, False)
            s.mic_target = s.mic_fixed
            try:
                mic_dev, target, mismatch = self._choose_targets(s.mic_fixed, s.pc_fixed, s.follow)
            except Exception:
                log.exception("Falha ao ler os dispositivos; usando os padrões")
                mic_dev, target, mismatch = s.mic_fixed, pwgraph.Target(s.pc_fixed, None, False), False
            with s.aec_lock:
                s.mic_device, s.pc_sink = mic_dev, target.sink
            s.mismatch = mismatch
            self._set_followed_app(s, target.followed_app)
            if target.followed_app and s.pc is not None:
                s.pc.retarget(target.sink)
            s.last_graph = s.last_feed = self._clock()
            log.info("gravando reunião %s (mic=%s, pc=%s, app=%s)",
                     s.meeting_id, mic_dev, target.sink, target.followed_app)
            if s.aec_enabled and s.mic is not None:
                self._request_aec_restart(s)
        except Exception:
            log.exception("Falha ao preparar a gravação da reunião %s", s.meeting_id)
        finally:
            s.ready.set()
        if self._tick_s:  # também sem trilhas: é o ticker que publica mic_dead/pc_dead
            s.ticker = self._thread(self._run_ticker, s, name="meeting-tick", track=False)

    def _start_track(self, s: _Session, name: str, target: str | None,
                     capture_sink: bool) -> TrackRecorder | None:
        rec = None
        try:
            rec = self._recorder_factory(name, s.directory / f"{name}.raw", target, capture_sink)
            rec.start(s.t0)
            return rec
        except Exception:
            log.exception("Falha ao iniciar a trilha %s da reunião %s", name, s.meeting_id)
            if rec is not None:
                try:
                    rec.stop()
                except Exception:
                    pass
            return None

    def _retarget_mic(self, s: _Session, target: str | None) -> None:
        with s.aec_lock:
            if s.mic is None or target == s.mic_target:
                return
            s.mic_target = target
            s.mic.retarget(target)

    def _start_aec(self, s: _Session, mic: str | None, sink: str | None) -> str | None:
        with s.aec_lock:
            s.aec_count += 1
            name = f"{AEC_PREFIX}_{s.meeting_id[:8]}_{s.aec_count}"
        aec = self._aec_factory(mic, sink, name=name)
        try:
            source = aec.start()
        except Exception:
            log.exception("Falha ao ligar o cancelamento de eco")
            source = None
        with s.aec_lock:
            if s.stopping:
                aec.stop()
                return None
            s.aec = aec if source else None
        return source

    def _run_ticker(self, s: _Session) -> None:
        while not s.stop_ticker.wait(self._tick_s):
            try:
                self.tick()
            except Exception:
                log.exception("Falha na rodada periódica da reunião")

    def _teardown(self, s: _Session) -> None:
        try:
            if s.setup_thread is not None:
                s.setup_thread.join()
            s.stop_ticker.set()
            if s.ticker is not None:
                s.ticker.join(timeout=10.0)
            duration = max(self._clock() - s.t0, 0.0)
            with s.aec_lock:
                s.stopping = True
                aec_thread = s.aec_thread
            if aec_thread is not None:
                aec_thread.join(timeout=15.0)
            for rec in (s.mic, s.pc):
                if rec is not None:
                    try:
                        rec.stop()
                    except Exception:
                        log.exception("Falha ao parar a trilha %s", rec.name)
            if s.aec is not None:
                try:
                    s.aec.stop()
                except Exception:
                    log.exception("Falha ao desligar o cancelamento de eco")
            s.alerts = ()
            for change in s.health.close(duration):
                self._publish_alert(change.kind, change.active)
            for kind, start_s, end_s in s.health.history():
                self._store.add_alert(s.meeting_id, kind, start_s, end_s)
        except Exception:
            log.exception("Falha ao encerrar a gravação da reunião %s", s.meeting_id)
        finally:
            self._gpu.submit(self._finalize_session, s)

    # -- internos: rodada periódica ----------------------------------------------

    def _alive(self, s: _Session, name: str, alive: bool, now: float) -> bool:
        if alive:
            s.dead_since.pop(name, None)
            return True
        since = s.dead_since.setdefault(name, now)
        return now - since < DEAD_AFTER_S

    def _update_health(self, s: _Session, now: float) -> None:
        # Trilha que nem iniciou conta como morta (alerta `*_dead` depois de DEAD_AFTER_S).
        mic_db = s.mic.level_db if s.mic is not None else float("-inf")
        pc_db = s.pc.level_db if s.pc is not None else float("-inf")
        changes = s.health.update(
            mic_db, pc_db,
            self._alive(s, "mic", s.mic is not None and s.mic.alive, now),
            self._alive(s, "pc", s.pc is not None and s.pc.alive, now),
            s.mismatch,
        )
        active = s.health.active_alerts()
        s.alerts = tuple(kind for kind in KINDS if kind in active)
        self._bus.publish({"type": "levels", "mic_db": _db(mic_db), "pc_db": _db(pc_db)})
        for change in changes:
            self._publish_alert(change.kind, change.active)

    def _check_graph(self, s: _Session) -> None:
        try:
            graph = self._snapshot()
        except Exception as exc:  # FileNotFoundError, CalledProcessError, TimeoutExpired, JSON
            log.debug("pw-dump falhou: %s", exc)
            return
        apps = pwgraph.call_apps(graph, self._own_pids)
        target = pwgraph.pick_pc_target(graph, apps, s.pc_fixed, s.follow)
        s.mismatch = self._mismatch(target, apps)
        self._set_followed_app(s, target.followed_app)

        devices_changed = False
        with s.aec_lock:
            new_sink = target.sink if not s.pc_fixed and target.sink and target.sink != s.pc_sink else None
            if new_sink:
                s.pc_sink = new_sink
            default = graph.default_source
            new_mic = default if not s.mic_fixed and default and default != s.mic_device else None
            if new_mic:
                s.mic_device = new_mic
            devices_changed = bool(new_sink or new_mic)
        if new_sink:
            log.info("trilha do PC: seguindo %s (%s)", new_sink, target.followed_app or "padrão")
            if s.pc is not None:
                s.pc.retarget(new_sink)
        if new_mic:
            log.info("microfone padrão mudou para %s", new_mic)
            if not s.aec_enabled:
                self._retarget_mic(s, new_mic)
        if devices_changed and s.aec_enabled:
            self._request_aec_restart(s)

    def _set_followed_app(self, s: _Session, app: str | None) -> None:
        if app == s.followed_app:
            return
        s.followed_app = app
        try:
            self._store.update_meeting(s.meeting_id, followed_app=app)
        except Exception:
            log.exception("Falha ao gravar o app seguido")

    def _request_aec_restart(self, s: _Session) -> None:
        with s.aec_lock:
            s.aec_dirty = True
            if s.aec_thread is not None or s.stopping:
                return
            s.aec_thread = threading.Thread(
                target=self._restart_aec, args=(s,), name="meeting-aec", daemon=True)
            s.aec_thread.start()

    def _restart_aec(self, s: _Session) -> None:
        """Reinicia o AEC com os dispositivos atuais até não haver mudança pendente."""
        while True:
            with s.aec_lock:
                if not s.aec_dirty or s.stopping:
                    s.aec_thread = None
                    return
                s.aec_dirty = False
                mic, sink = s.mic_device, s.pc_sink
                old, s.aec = s.aec, None
            if old is not None:
                # Volta ao microfone cru antes de derrubar o AEC antigo: a trilha nunca fica
                # apontando para um nó que sumiu enquanto o novo carrega (até 5 s).
                self._retarget_mic(s, mic)
                try:
                    old.stop()
                except Exception:
                    log.exception("Falha ao desligar o cancelamento de eco")
            source = self._start_aec(s, mic, sink)
            self._retarget_mic(s, source or mic)

    def _feed(self, s: _Session) -> None:
        if s.stopping:
            return
        inc = self._prepare_transcription(s)
        if inc is None:
            return
        for track in TRACKS:
            path = s.directory / f"{track}.raw"
            try:
                available = path.stat().st_size / BYTES_PER_SAMPLE / SAMPLE_RATE
            except FileNotFoundError:
                continue
            try:
                inc.feed(track, path, available)
            except Exception:
                log.exception("Falha na transcrição incremental (%s)", track)

    # -- internos: GPU -------------------------------------------------------------

    def _release_idle_models(self) -> bool:
        with self._lock:
            if self._session is not None or self._processing:
                return False
            if self._transcriber is None:
                self._reload_requested = False
                return False
            delay = float(self._settings.get("meeting_model_idle_sec"))
            if not self._reload_requested and (
                delay == 0 or self._models_used_at is None
                or self._clock() - self._models_used_at < delay
            ):
                return False
            transcriber, self._transcriber = self._transcriber, None
            self._models_used_at = None
            self._reload_requested = False
        try:
            unload = getattr(transcriber, "unload", None)
            if unload is not None:
                unload()
        except Exception:
            log.exception("Falha ao descarregar o Whisper das reuniões")
        finally:
            del transcriber
        try:
            unload = getattr(self._diarizer, "unload", None)
            if unload is not None:
                unload()
        except Exception:
            log.exception("Falha ao descarregar o modelo de separação de vozes")
        release_unused_memory()
        log.info("Modelos de reunião descarregados; o ditado continua pronto")
        return True

    def _get_transcriber(self):
        if self._transcriber is None:
            transcriber = self._transcriber_factory()
            if self._gpu_gate is not None:
                transcriber.pause = self._gpu_gate  # cede a GPU ao ditado entre os trechos
            self._transcriber = transcriber
        self._models_used_at = self._clock()
        return self._transcriber

    def _prepare_transcription(self, s: _Session) -> IncrementalTranscriber | None:
        if s.incremental is None and not s.model_failed:
            try:
                s.incremental = IncrementalTranscriber(self._get_transcriber(), self._vad)
            except Exception:
                s.model_failed = True
                log.exception("Modelo das reuniões indisponível; transcrição só ao finalizar")
        return s.incremental

    def _flush_words(self, s: _Session) -> dict | None:
        """Completa a transcrição incremental; None faz a finalização transcrever tudo."""
        inc = s.incremental
        if inc is None:
            return None
        try:
            for track in TRACKS:
                path = s.directory / f"{track}.raw"
                end = path.stat().st_size / BYTES_PER_SAMPLE / SAMPLE_RATE if path.exists() else 0.0
                inc.flush(track, path, end)
        except Exception:
            log.exception("Falha ao completar a transcrição incremental; transcrevendo tudo")
            return None
        return {track: list(inc.words.get(track, [])) for track in TRACKS}

    def _import_flameshot(self, s: _Session) -> None:
        if self._settings.get("print_mode") != "flameshot":
            return
        try:
            meeting = self._store.get_meeting(s.meeting_id)
            end = (meeting.ended_at if meeting is not None else None) or self._now()
            import_folder(self._store, s.meeting_id, resolve_folder(self._settings.get("print_folder")),
                          s.clock, s.started_at, end)
        except Exception:
            log.exception("Falha ao importar os prints do Flameshot da reunião %s", s.meeting_id)

    def _finalize_session(self, s: _Session) -> None:
        mid = s.meeting_id
        try:
            # Antes da transcrição: os prints ficam salvos mesmo se o modelo falhar.
            self._import_flameshot(s)
            self._progress(mid, fin.STAGE_TRANSCRIBING)
            try:
                transcriber = self._get_transcriber()
            except Exception as exc:
                log.exception("Modelo das reuniões indisponível")
                fin.mark_failed(self._store, mid, exc)
                return
            words = self._flush_words(s)
            fin.finalize(mid, self._store, self._settings, transcriber, self._diarizer, words,
                         self._title_fn, on_progress=lambda stage: self._progress(mid, stage),
                         voice_sync=self._voice_sync)
        finally:
            self._processing_done(mid)
        self._notify_ready(mid)

    def _reprocess_job(self, meeting_id: str) -> None:
        try:
            transcriber = self._get_transcriber()
        except Exception as exc:
            log.exception("Modelo das reuniões indisponível")
            fin.mark_failed(self._store, meeting_id, exc)
        else:
            fin.reprocess(meeting_id, self._store, self._settings, transcriber, self._diarizer,
                          self._title_fn, on_progress=lambda st: self._progress(meeting_id, st),
                          voice_sync=self._voice_sync)
        finally:
            self._processing_done(meeting_id)

    def _recover_job(self) -> None:
        def busy(meeting_id: str) -> bool:
            with self._lock:
                recording = self._session is not None and self._session.meeting_id == meeting_id
                return recording or meeting_id in self._processing

        def on_status(meeting_id: str, status: str) -> None:
            if status == "processing":
                with self._lock:
                    self._processing.append(meeting_id)
                self._meeting_updated(meeting_id, status)
            else:
                self._processing_done(meeting_id)
                if status == "ready":
                    self._notify_ready(meeting_id)

        fin.recover(self._store, self._settings, self._get_transcriber, self._diarizer,
                    self._title_fn, on_status=on_status, on_progress=self._progress, skip=busy,
                    voice_sync=self._voice_sync)

    # -- internos: eventos ---------------------------------------------------------

    def _processing_done(self, meeting_id: str) -> None:
        meeting = self._store.get_meeting(meeting_id)
        with self._lock:
            if meeting_id in self._processing:
                self._processing.remove(meeting_id)
            self._last_stage.pop(meeting_id, None)
            self._models_used_at = self._clock()
        self._meeting_updated(meeting_id, meeting.status if meeting else "failed")

    def _notify_ready(self, meeting_id: str) -> None:
        """Avisa `on_ready` se a reunião terminou `ready`; erro do callback não afeta o processamento."""
        if self._on_ready is None:
            return
        try:
            meeting = self._store.get_meeting(meeting_id)
            if meeting is not None and meeting.status == "ready":
                self._on_ready(meeting_id)
        except Exception:
            log.exception("Falha no on_ready da reunião %s", meeting_id)

    def _meeting_updated(self, meeting_id: str, status: str) -> None:
        self._bus.publish({"type": "meeting_updated", "meeting_id": meeting_id, "status": status})

    def _progress(self, meeting_id: str, stage: str) -> None:
        with self._lock:
            if self._last_stage.get(meeting_id) == stage:
                return
            self._last_stage[meeting_id] = stage
        self._bus.publish({"type": "progress", "meeting_id": meeting_id, "stage": stage})

    def _publish_alert(self, kind: str, active: bool) -> None:
        self._bus.publish({"type": "alert", "kind": kind, "active": active,
                           "message": ALERT_MESSAGES.get(kind, kind)})
