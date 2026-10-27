"""Serviço de ditado: tecla global (evdev) -> gravação curta (pw-record) -> Whisper -> colar.

Reproduz o daemon `ditado.py` sobre o núcleo extraído (`HotkeyState`, `Transcriber`, `paste`).
Tudo roda no loop do asyncio, exceto a transcrição e a colagem, que ficam num worker de uma thread
(os textos colam na ordem falada). `apply_settings()` deve ser chamado na thread do loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime

import evdev
import numpy as np
from evdev import ecodes

from whisper_ditado.audio.capture import build_command
from whisper_ditado.events import EventBus
from whisper_ditado.dictation_samples import DictationSamples
from whisper_ditado.feedback import play_start_sound
from whisper_ditado.hotkey import Action, HotkeyState, parse_hotkey
from whisper_ditado.paste import log_clipboard_session, paste
from whisper_ditado.priority import DictationBusy
from whisper_ditado.settings import DEFAULTS, Settings
from whisper_ditado.transcriber import SAMPLE_RATE, Transcriber, TranscriberOptions
from whisper_ditado.vocab import Vocabulary

log = logging.getLogger("ditado")

SCAN_INTERVAL_SEC = 3


def transcriber_options(settings: Settings) -> TranscriberOptions:
    """Opções do Transcriber do ditado a partir das configurações (modelo, precisão e prompt).

    O prompt de estilo de produção (`settings.DEFAULTS["prompt"]`) só chega ao Whisper por aqui;
    quem constrói o `Transcriber` (ex.: o `main`) deve usar esta função.
    """
    return TranscriberOptions(
        model=settings.get("model"),
        compute_type=settings.get("compute_type"),
        language=os.environ.get("DITADO_LANG", "pt") or None,
        prompt=settings.get("prompt") or None,
    )


def build_vocabulary(settings: Settings) -> Vocabulary:
    """Vocabulário lendo os arquivos de `config/` (editáveis pela API)."""
    return Vocabulary(settings.text_path("vocab"), settings.text_path("subs"))


def make_transcriber(opts: TranscriberOptions, vocab: Vocabulary) -> Transcriber:
    """Cria o `Transcriber` e relê o vocabulário já com o tokenizer, para o aviso de >223 tokens.

    O `Vocabulary` nasce sem tokenizer (só existe depois de carregar o modelo) e a primeira leitura
    não conta tokens; zerar a identidade dos arquivos força a releitura com o tokenizer anexado.
    """
    transcriber = Transcriber(opts, vocab)
    vocab._keys = (None, None)
    vocab.refresh()
    return transcriber


def notify(message: str) -> None:
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "Ditado", "Ditado", message])


# As propriedades identificam o nó como nosso: sem elas o `pw-record` do ditado apareceria como "app
# da chamada" no grafo durante uma reunião (`pwgraph.call_apps` ignora `whisper_ditado*`).
RECORD_CMD = [
    "pw-record", "--rate", str(SAMPLE_RATE), "--channels", "1", "--format", "s16", "--raw",
    "-P", '{ application.name = "whisper-ditado" node.name = "whisper_ditado_dictation" }',
    "-",
]


class Recorder:
    """Grava o microfone ou a saída padrão do PC via pw-record até stop()."""

    def __init__(self, *, capture_sink: bool = False) -> None:
        self._buf = bytearray()
        self._proc = subprocess.Popen(
            build_command("dictation_pc", None, True) if capture_sink else RECORD_CMD,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        while chunk := self._proc.stdout.read1(8192):
            self._buf.extend(chunk)

    def stop(self) -> np.ndarray:
        if self._proc.poll() is None:
            self._proc.send_signal(signal.SIGINT)
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._thread.join(timeout=2)
        data = bytes(self._buf[: len(self._buf) // 2 * 2])
        return np.frombuffer(data, np.int16).astype(np.float32) / 32768.0


@dataclass(frozen=True)
class _Config:
    """Cópia imutável das configurações do ditado; trocada inteira em `apply_settings`."""

    key: str
    groups: tuple[frozenset[int], ...]
    output_key: str
    output_groups: tuple[frozenset[int], ...]
    paste: str
    min_sec: float
    max_sec: float
    trailing_space: bool
    ignore_devices: tuple[str, ...]
    ydotool: str
    evaluation_enabled: bool


class DictationService:
    def __init__(
        self,
        settings: Settings,
        transcriber: Transcriber,
        bus: EventBus,
        *,
        on_span: Callable[[float, float], None] | None = None,
        recorder_factory: Callable[[], Recorder] = Recorder,
        output_recorder_factory: Callable[[], Recorder] | None = None,
        paste_fn: Callable[[str, str, str], None] = paste,
        transcriber_factory: Callable[[TranscriberOptions, Vocabulary], Transcriber] = make_transcriber,
        list_devices: Callable[[], list[str]] = evdev.list_devices,
        open_device: Callable[[str], evdev.InputDevice] = evdev.InputDevice,
        dictation_busy: DictationBusy | None = None,
        samples: DictationSamples | None = None,
    ) -> None:
        self.settings = settings
        self.transcriber = transcriber
        self.bus = bus
        self.samples = samples
        # Recebe (início, fim) em tempo monotônico quando uma gravação válida termina.
        self.on_span = on_span
        self._recorder_factory = recorder_factory
        self._output_recorder_factory = output_recorder_factory or (lambda: Recorder(capture_sink=True))
        self.source = "mic"
        self._paste = paste_fn
        self._transcriber_factory = transcriber_factory
        self._list_devices = list_devices
        self._open_device = open_device
        # Marcado da gravação até a colagem: o processamento de reunião cede a GPU nesse tempo.
        self.busy = dictation_busy or DictationBusy()

        self.devices: dict[str, evdev.InputDevice] = {}
        # Nós ignorados -> identidade do nó; o kernel reaproveita eventN, então o caminho não basta.
        self.skipped: dict[str, tuple[int, int]] = {}
        # Sinaliza ao worker que nenhuma tecla física está segurada: o GNOME soma os
        # modificadores de todos os teclados, então colar com o Ctrl seguro viraria Ctrl+Shift+Insert.
        self.keys_released = threading.Event()
        self.keys_released.set()
        self.stopping = False
        self.paused = False  # espelha `dictation_enabled` (atualizado em `apply_settings`)
        self.recorder: Recorder | None = None
        self.max_timer: asyncio.TimerHandle | None = None
        self.worker = ThreadPoolExecutor(max_workers=1)  # serializa: os textos colam na ordem falada
        self.loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        self._rec_started = 0.0
        self._rec_created_at: str | None = None
        self._collect_current = False

        self._reload_lock = threading.Lock()
        self._wanted_opts = getattr(transcriber, "opts", None)
        self._reloading = False

        self.cfg = self._read_config(previous=None)
        self.hotkey = HotkeyState(self.cfg.groups)
        self.output_hotkey = HotkeyState(self.cfg.output_groups)
        self.paused = not self.settings.get("dictation_enabled")

    # --- configuração -----------------------------------------------------

    def _read_config(self, previous: _Config | None) -> _Config:
        s = self.settings
        key = s.get("key")
        try:
            groups = parse_hotkey(key)
        except SystemExit:  # DITADO_KEY inválida no ambiente: não derruba o serviço
            fallback = previous.key if previous else DEFAULTS["key"]
            log.error("Tecla de ditado inválida: %r; usando %r", key, fallback)
            key = fallback
            groups = previous.groups if previous else parse_hotkey(fallback)
        output_key = s.get("output_key")
        try:
            output_groups = parse_hotkey(output_key)
        except SystemExit:
            output_key = previous.output_key if previous else DEFAULTS["output_key"]
            log.error("Atalho do áudio do PC inválido; usando %r", output_key)
            output_groups = parse_hotkey(output_key)
        paste_mode = s.get("paste")
        return _Config(
            key=key,
            groups=groups,
            output_key=output_key,
            output_groups=output_groups,
            paste=paste_mode,
            min_sec=float(s.get("min_sec")),
            max_sec=float(s.get("max_sec")),
            trailing_space=bool(s.get("trailing_space")),
            ignore_devices=tuple(n for n in s.get("ignore_devices") if n.strip()),
            ydotool=s.get("ydotool"),
            evaluation_enabled=bool(s.get("dictation_evaluation_enabled")),
        )

    def apply_settings(self) -> None:
        """Relê tecla, colagem, limites e pausa sem reiniciar; recarrega o modelo se for preciso."""
        old = self.cfg
        self.cfg = new = self._read_config(previous=old)
        self.paused = not self.settings.get("dictation_enabled")
        if self.paused:
            self._cancel_recording("ditado pausado")
        if new.groups != old.groups or new.output_groups != old.output_groups:
            self._cancel_recording("tecla de ditado alterada")
            pressed = self.hotkey.pressed
            self.hotkey = HotkeyState(new.groups)
            self.hotkey.pressed = set(pressed)
            self.output_hotkey = HotkeyState(new.output_groups)
            self.output_hotkey.pressed = set(pressed)
            self.skipped.clear()  # teclados descartados podem ter a nova combinação
        if new.ignore_devices != old.ignore_devices:
            self.skipped.clear()
            for path, dev in list(self.devices.items()):
                if self._ignored(dev):
                    log.info("Teclado agora ignorado: %s", dev.name)
                    self._drop_device(path)
            self._resync_pressed()
        self._maybe_reload_transcriber()

    def _maybe_reload_transcriber(self) -> None:
        """Se modelo, precisão ou prompt mudaram, carrega um novo Transcriber numa thread.

        O ditado segue com o antigo até o novo estar aquecido (a troca é uma atribuição só).
        Mudanças em sequência são coalescidas: a thread recarrega até alcançar o último pedido.
        """
        if self._wanted_opts is None:  # transcritor sem `opts` (testes)
            return
        wanted = transcriber_options(self.settings)
        with self._reload_lock:
            if wanted == self._wanted_opts:
                return
            self._wanted_opts = wanted
            if self._reloading:
                return
            self._reloading = True
        threading.Thread(target=self._reload_loop, name="transcriber-reload", daemon=True).start()

    def _reload_loop(self) -> None:
        while True:
            with self._reload_lock:
                wanted = self._wanted_opts
                if self.stopping or wanted == self.transcriber.opts:
                    self._reloading = False
                    return
            try:
                log.info("Carregando novo modelo do ditado: %s (%s)", wanted.model, wanted.compute_type)
                new = self._transcriber_factory(wanted, self.transcriber.vocab)
            except Exception:
                log.exception("Falha ao carregar o novo modelo; o ditado segue com o anterior")
                with self._reload_lock:
                    # permite tentar de novo no próximo apply_settings com as mesmas opções
                    self._wanted_opts = self.transcriber.opts
                    self._reloading = False
                return
            with self._reload_lock:
                if self.stopping:  # desligando: não troca (o processo está saindo)
                    self._reloading = False
                    return
                self.transcriber = new
            log.info("Modelo do ditado trocado")

    # --- ciclo de vida ----------------------------------------------------

    async def run(self) -> None:
        """Procura teclados e atende o ditado até `stop()` ou cancelamento da tarefa."""
        self.loop = asyncio.get_running_loop()
        scanner = asyncio.create_task(self._scan_forever())
        log.info("Pronto. Microfone: %s; áudio do PC: %s.", self.cfg.key, self.cfg.output_key)
        log_clipboard_session(log)
        try:
            await self._stop.wait()
        finally:
            self.stopping = True
            self.keys_released.set()  # libera o worker se estiver esperando para colar
            scanner.cancel()
            self._cancel_recording("encerrando")
            for path in list(self.devices):
                self._drop_device(path)
            self.worker.shutdown(wait=False, cancel_futures=True)

    def stop(self) -> None:
        self._stop.set()

    # --- teclados ---------------------------------------------------------

    async def _scan_forever(self) -> None:
        while True:
            try:
                self._scan()
            except Exception:
                log.exception("Falha ao procurar teclados")
            await asyncio.sleep(SCAN_INTERVAL_SEC)

    def _scan(self) -> None:
        paths = self._list_devices()
        for path in set(self.skipped) - set(paths):
            del self.skipped[path]
        for path in paths:
            if path in self.devices:
                continue
            try:
                st = os.stat(path)
                node = (st.st_ino, st.st_ctime_ns)
                if self.skipped.get(path) != node:
                    self._open(path, node)
            except OSError:
                continue  # dispositivo sumiu no meio da varredura

    def _ignored(self, dev) -> bool:
        return any(name in dev.name for name in self.cfg.ignore_devices)

    def _open(self, path: str, node: tuple[int, int]) -> None:
        dev = self._open_device(path)
        try:
            keys = set(dev.capabilities().get(ecodes.EV_KEY, []))
            has_hotkey = any(
                all(group & keys for group in groups)
                for groups in (self.cfg.groups, self.cfg.output_groups)
            )
            if not has_hotkey or ecodes.KEY_A not in keys or self._ignored(dev):
                self.skipped[path] = node
                dev.close()
                return
            self.loop.add_reader(dev.fd, self._on_readable, path)
        except OSError:
            dev.close()
            raise
        self.devices[path] = dev
        log.info("Teclado: %s (%s)", dev.name, path)

    def _drop_device(self, path: str) -> None:
        dev = self.devices.pop(path, None)
        if dev is None:
            return
        try:
            self.loop.remove_reader(dev.fd)
        except (OSError, ValueError):
            pass
        try:
            dev.close()
        except OSError:
            pass

    def _on_readable(self, path: str) -> None:
        dev = self.devices.get(path)
        if dev is None:
            return
        try:
            events = list(dev.read())
        except BlockingIOError:
            return
        except OSError:
            log.info("Teclado removido: %s", path)
            self._drop_device(path)
            self._cancel_recording("teclado removido")
            self._resync_pressed()
            return
        for ev in events:
            if ev.type == ecodes.EV_KEY:
                self._on_key(ev.code, ev.value)
            elif ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_DROPPED:
                self._resync_pressed()

    def _active_keys(self) -> set[int]:
        """Teclas realmente seguradas segundo o kernel, somando todos os teclados abertos."""
        pressed: set[int] = set()
        for dev in list(self.devices.values()):
            try:
                pressed.update(dev.active_keys())
            except OSError:
                pass  # teclado sumindo: o _on_readable dele cuida da remoção
        return pressed

    def _resync_pressed(self) -> None:
        """Relê do kernel quais teclas estão seguradas (corrige eventos de soltar perdidos)."""
        pressed = self._active_keys()
        self.hotkey.resync(lambda: pressed)
        self.output_hotkey.resync(lambda: pressed)
        self._update_keys_released()

    def _update_keys_released(self) -> None:
        if self.hotkey.all_released:
            self.keys_released.set()
        else:
            self.keys_released.clear()

    def _on_key(self, code: int, value: int) -> None:
        # Atualiza as duas máquinas antes de agir: uma tecla extra pode cancelar a fonte atual.
        actions = [
            ("mic", self.hotkey, self.hotkey.on_key(code, value, self._active_keys)),
            ("pc", self.output_hotkey, self.output_hotkey.on_key(code, value, self._active_keys)),
        ]
        for source, _, action in actions:
            if source == self.source and action is Action.CANCEL:
                self._cancel_recording("atalho com outra tecla")
            elif source == self.source and action is Action.FINISH:
                self._finish()
        for source, hotkey, action in actions:
            if action is Action.START:
                if self.paused or self.recorder is not None:
                    hotkey.recording = False
                else:
                    self._start(source)
        self._update_keys_released()

    # --- gravação ---------------------------------------------------------

    def _set_active(self, active: bool) -> None:
        event = {"type": "dictation", "active": active}
        if self.source == "pc":
            event["source"] = "pc"
        self.bus.publish(event)

    def _start(self, source: str = "mic") -> None:
        self.source = source
        try:
            factory = self._output_recorder_factory if source == "pc" else self._recorder_factory
            self.recorder = factory()
        except OSError as exc:
            self.hotkey.recording = self.output_hotkey.recording = False
            log.error("Não foi possível iniciar o pw-record: %s", exc)
            label = "áudio do PC" if source == "pc" else "microfone"
            notify(f"Falha ao abrir o {label}: {exc}")
            return
        active_hotkey = self.output_hotkey if source == "pc" else self.hotkey
        active_hotkey.recording = True
        self.busy.enter()
        self._rec_started = time.monotonic()
        self._rec_created_at = datetime.now().astimezone().isoformat()
        self._collect_current = self.cfg.evaluation_enabled
        self.max_timer = self.loop.call_later(self.cfg.max_sec, self._finish)
        self._set_active(True)
        play_start_sound(source)
        log.info("Gravando %s...", source)

    def _take_audio(self) -> tuple[np.ndarray, float, float]:
        rec, self.recorder = self.recorder, None
        if self.max_timer is not None:
            self.max_timer.cancel()
            self.max_timer = None
        try:
            audio = rec.stop()
        finally:
            self._set_active(False)  # mesmo se o pw-record falhar: a pílula não fica "gravando"
        return audio, self._rec_started, time.monotonic()

    def _cancel_recording(self, reason: str) -> None:
        self.hotkey.recording = self.output_hotkey.recording = False
        if self.recorder is None:
            return
        try:
            self._take_audio()
        finally:
            self.busy.leave()
        log.info("Gravação descartada (%s)", reason)

    def _finish(self) -> None:
        # Também é o callback do limite de tempo: a tecla pode continuar segurada, e a máquina de
        # estados precisa saber que a gravação já acabou.
        self.hotkey.recording = self.output_hotkey.recording = False
        if self.recorder is None:
            return
        # Exatamente um `busy.leave()`: aqui, salvo quando a tarefa foi entregue ao worker (aí o
        # callback dela libera). Um contador vazado faria a GPU das reuniões esperar o prazo
        # inteiro do portão em toda tarefa.
        delivered = False
        try:
            audio, started, ended = self._take_audio()
            secs = len(audio) / SAMPLE_RATE
            if secs < self.cfg.min_sec:
                log.info("Gravação curta demais (%.2fs), ignorada", secs)
                return
            if self.source == "mic" and self.on_span is not None:
                try:
                    self.on_span(started, ended)
                except Exception:
                    log.exception("Falha no callback de trecho ditado")
            try:
                job = self.worker.submit(
                    self._transcribe_and_paste, audio,
                    evaluation_enabled=self._collect_current and self.cfg.evaluation_enabled,
                    created_at=self._rec_created_at,
                )
            except RuntimeError:  # worker já encerrado (desligando)
                return
            delivered = True
        finally:
            if not delivered:
                self.busy.leave()
        # Também quando a tarefa é cancelada na fila (desligamento): a contagem não fica presa.
        job.add_done_callback(lambda _: self.busy.leave())

    # --- transcrição e colagem (thread do worker) -------------------------

    def _wait_keys_released(self, max_sec: float) -> None:
        """Espera o usuário soltar todas as teclas (ex.: já segurando o Ctrl para a próxima frase)."""
        deadline = time.monotonic() + max_sec
        while not self.keys_released.wait(timeout=1.0):
            if self.stopping or time.monotonic() > deadline:
                return
            try:
                self.loop.call_soon_threadsafe(self._resync_pressed)
            except RuntimeError:  # loop fechado
                return

    def _transcribe_and_paste(self, audio: np.ndarray, *, evaluation_enabled: bool | None = None,
                              created_at: str | None = None) -> None:
        started = time.perf_counter()
        transcriber = self.transcriber
        collect = self.cfg.evaluation_enabled if evaluation_enabled is None else evaluation_enabled
        text, pasted, error = "", False, None
        raw_transcript = None
        try:
            transcribe_result = getattr(transcriber, "transcribe_text_result", None)
            if collect and callable(transcribe_result):
                result = transcribe_result(audio)
                text, raw_transcript = result.text, result.raw_text
            else:
                text = transcriber.transcribe_text(audio)
            if not text:
                log.info("Nenhuma fala reconhecida")
                return
            self._wait_keys_released(self.cfg.max_sec)
            if self.stopping:
                return
            cfg = self.cfg  # configurações vigentes na hora de colar
            self._paste(text + " " if cfg.trailing_space else text, cfg.paste, cfg.ydotool)
            pasted = True
            log.info(
                "%.1fs de áudio -> %d caracteres em %.2fs",
                len(audio) / SAMPLE_RATE,
                len(text),
                time.perf_counter() - started,
            )
            log.debug("Texto: %s", text)
        except Exception as exc:
            error = str(exc)
            log.exception("Falha ao transcrever/colar")
            notify("Falha no ditado. Veja: journalctl --user -u whisper-ditado")
        finally:
            if collect and self.samples is not None:
                try:
                    opts = getattr(transcriber, "opts", None)
                    vocab = getattr(transcriber, "vocab", None)
                    self.samples.capture(
                        audio, transcript=text, raw_transcript=raw_transcript,
                        pasted=pasted, error=error, created_at=created_at,
                        config={
                            "transcriber": asdict(opts) if is_dataclass(opts) else {},
                            "hotwords": getattr(vocab, "hotwords", None),
                            "substitutions": dict(getattr(vocab, "subs", {})),
                        },
                    )
                except Exception:
                    log.exception("Falha ao enfileirar exemplo de ditado")
