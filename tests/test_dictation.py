import asyncio
import os
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from evdev import ecodes as e

from whisper_ditado import dictation
from whisper_ditado.dictation import DictationService, transcriber_options
from whisper_ditado.events import EventBus
from whisper_ditado.settings import DEFAULTS, Settings
from whisper_ditado.store import Store
from whisper_ditado.transcriber import TranscriberOptions

LC, RC = e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL
LW, RW = e.KEY_LEFTMETA, e.KEY_RIGHTMETA
A, SH, LEFT = e.KEY_A, e.KEY_LEFTSHIFT, e.KEY_LEFT


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in list(os.environ):
        if name.startswith("DITADO_"):
            monkeypatch.delenv(name)
    notes = []
    monkeypatch.setattr(dictation, "play_start_sound", lambda source: None)
    monkeypatch.setattr(dictation, "notify", notes.append)  # nunca chamar notify-send de verdade
    return notes


class FakeRecorder:
    def __init__(self):
        self.started = time.monotonic()
        self.stopped = False

    def stop(self):
        self.stopped = True
        return np.zeros(int((time.monotonic() - self.started) * 16000), np.float32)


@pytest.mark.parametrize("enabled", [False, True])
def test_evaluation_is_optional_and_collects_after_pasting(tmp_path, enabled):
    captured = []
    h = Harness(tmp_path, FakeTranscriber(delay=0, opts=TranscriberOptions(prompt=DEFAULTS["prompt"])), samples=SimpleNamespace(
        capture=lambda audio, **metadata: captured.append((len(h.pasted), audio, metadata)),
    ))
    try:
        h.settings.update({"dictation_evaluation_enabled": enabled})
        h.service.apply_settings()
        audio = np.zeros(16000, np.float32)
        h.service._transcribe_and_paste(audio)
        assert h.texts == ["frase "]
        assert len(captured) == int(enabled)
        if enabled:
            count, saved_audio, metadata = captured[0]
            assert count == 1  # colar acontece antes de enfileirar
            assert saved_audio is audio
            assert metadata["transcript"] == "frase"
            assert metadata["pasted"] is True and metadata["error"] is None
            assert metadata["config"]["transcriber"]["model"] == "large-v3-turbo"
    finally:
        asyncio.run(h.close())


@pytest.mark.parametrize("failure", ["empty", "transcribe", "paste", "collect"])
def test_evaluation_preserves_failures_and_never_breaks_pasting(tmp_path, failure):
    captured = []

    def capture(audio, **metadata):
        if failure == "collect":
            raise OSError("coleta indisponível")
        captured.append(metadata)

    h = Harness(tmp_path, FakeTranscriber(text="" if failure == "empty" else "frase", delay=0,
                                         opts=TranscriberOptions(prompt=DEFAULTS["prompt"])),
                samples=SimpleNamespace(capture=capture))
    try:
        h.settings.update({"dictation_evaluation_enabled": True})
        h.service.apply_settings()

        def boom(*args):
            raise RuntimeError("falha simulada")

        if failure == "transcribe":
            h.transcriber.transcribe_text = boom
        if failure == "paste":
            h.service._paste = boom
        h.service._transcribe_and_paste(np.zeros(16000, np.float32))
        if failure == "collect":
            assert h.texts == ["frase "]
        else:
            metadata, = captured
            assert metadata["pasted"] is False
            assert metadata["transcript"] == ("frase" if failure == "paste" else "")
            assert metadata["error"] == (None if failure == "empty" else "falha simulada")
    finally:
        asyncio.run(h.close())


class FakeTranscriber:
    def __init__(self, text="frase", delay=0.3, opts=None, vocab=None):
        self.text, self.delay = text, delay
        self.opts = opts or TranscriberOptions()
        self.vocab = vocab or SimpleNamespace()
        self.calls = 0

    def transcribe_text(self, audio):
        self.calls += 1
        time.sleep(self.delay)
        return self.text


class FakeDevice:
    """Teclado falso: `held` é o que o kernel diria estar seguro."""

    def __init__(self, name="Teclado", keys=(A, LC, RC, LW, RW, SH)):
        self.name = name
        self.fd, self._w = os.pipe()
        self.keys = list(keys)
        self.held: set[int] = set()
        self.queue: list = []
        self.fail_active_keys = False
        self.closed = False

    def capabilities(self):
        return {e.EV_KEY: self.keys}

    def active_keys(self):
        if self.fail_active_keys:
            raise OSError("sumiu")
        return set(self.held)

    def read(self):
        events, self.queue = self.queue, []
        if events == "OSError":
            raise OSError("No such device")
        return events

    def close(self):
        if not self.closed:
            self.closed = True
            os.close(self.fd)
            os.close(self._w)


class Harness:
    def __init__(self, tmp_path, transcriber=None, **service_kwargs):
        self.store = Store(tmp_path / "ditado.db")
        self.settings = Settings(self.store)
        self.bus = EventBus()
        self.pasted: list[tuple[float, str, str, str]] = []
        self.spans: list[tuple[float, float]] = []
        self.recorders: list[FakeRecorder] = []
        # Opções iguais às das configurações: `apply_settings()` não vê mudança nem recarrega.
        self.transcriber = transcriber or FakeTranscriber(opts=transcriber_options(self.settings))
        self.kb = FakeDevice()
        # Nunca o `make_transcriber` real (carregaria o Whisper na GPU numa thread daemon).
        service_kwargs.setdefault(
            "transcriber_factory", lambda opts, vocab: FakeTranscriber(opts=opts, vocab=vocab))
        service_kwargs.setdefault("output_recorder_factory", self._output_recorder)
        self.output_recorders = []
        self.service = DictationService(
            self.settings,
            self.transcriber,
            self.bus,
            on_span=lambda s, t: self.spans.append((s, t)),
            recorder_factory=self._recorder,
            paste_fn=lambda text, mode, ydotool: self.pasted.append((time.monotonic(), text, mode, ydotool)),
            **service_kwargs,
        )
        self.service.devices["/dev/input/fake"] = self.kb
        self.sub = None

    def _output_recorder(self):
        rec = FakeRecorder()
        self.output_recorders.append(rec)
        return rec

    def _recorder(self):
        rec = FakeRecorder()
        self.recorders.append(rec)
        return rec

    def start(self):
        self.service.loop = asyncio.get_running_loop()
        self.sub = self.bus.subscribe()

    def press(self, code):
        self.kb.held.add(code)
        self.service._on_key(code, 1)

    def release(self, code):
        self.kb.held.discard(code)
        self.service._on_key(code, 0)

    @property
    def texts(self):
        return [p[1] for p in self.pasted]

    async def events(self):
        out = []
        while True:
            try:
                out.append(await asyncio.wait_for(self.sub.__anext__(), 0.05))
            except TimeoutError:
                return out

    async def close(self):
        self.service.stopping = True
        self.service.keys_released.set()
        self.service.worker.shutdown(wait=True)
        self.kb.close()
        self.store.close()


def run(tmp_path, scenario, **kwargs):
    async def main():
        h = Harness(tmp_path, **kwargs)
        h.start()
        try:
            await scenario(h)
        finally:
            await h.close()

    asyncio.run(main())


def test_dictation_publishes_events_and_span(tmp_path):
    async def scenario(h):
        before = time.monotonic()
        h.press(LC)
        assert h.service.recorder is None
        h.press(LW)
        assert h.service.recorder is not None
        await asyncio.sleep(0.6)
        h.release(LW)
        h.release(LC)
        await asyncio.sleep(0.7)
        assert await h.events() == [
            {"type": "dictation", "active": True},
            {"type": "dictation", "active": False},
        ]
        assert [p[1:] for p in h.pasted] == [("frase ", "shift+insert", "/usr/bin/ydotool")]
        assert len(h.spans) == 1
        start, end = h.spans[0]
        assert before <= start < end <= time.monotonic()
        assert 0.55 < end - start < 1.0
        assert h.recorders[0].stopped

    run(tmp_path, scenario)


def test_dictation_paste_waits_for_keys_released(tmp_path):
    async def scenario(h):
        h.settings.update({"key": "KEY_RIGHTCTRL"})
        h.service.apply_settings()
        # frase 1 e, logo em seguida, segura a tecla de novo: a colagem só pode ocorrer após soltar
        h.press(RC)
        await asyncio.sleep(0.6)
        h.release(RC)
        await asyncio.sleep(0.05)
        h.press(RC)
        await asyncio.sleep(1.5)
        t_release = time.monotonic()
        h.release(RC)
        await asyncio.sleep(1.2)
        assert h.texts == ["frase ", "frase "]
        assert h.pasted[0][0] >= t_release
        assert h.pasted[1][0] > h.pasted[0][0]

    run(tmp_path, scenario)


def test_wait_resyncs_when_a_release_event_was_lost(tmp_path):
    async def scenario(h):
        h.settings.update({"key": "KEY_RIGHTCTRL"})
        h.service.apply_settings()
        h.press(RC)
        await asyncio.sleep(0.6)
        h.release(RC)
        h.kb.held.add(SH)  # tecla "presa" no estado, mas o kernel já não a vê
        h.service.hotkey.pressed.add(SH)
        h.service._update_keys_released()
        assert not h.service.keys_released.is_set()
        h.kb.held.discard(SH)
        await asyncio.sleep(2.0)  # o worker relê do kernel a cada 1 s
        assert h.texts == ["frase "]

    run(tmp_path, scenario)


def test_combo_scenarios(tmp_path):
    async def scenario(h):
        s = asyncio.sleep
        # só Ctrl / só Super não gravam
        h.press(LC)
        assert h.service.recorder is None
        await s(0.5)
        h.release(LC)
        h.press(LW)
        assert h.service.recorder is None
        h.release(LW)
        await s(0.3)
        assert h.texts == []

        # Super antes + RCtrl, solta o Ctrl primeiro: cola
        h.press(RW)
        h.press(RC)
        await s(0.6)
        h.release(RC)
        await s(0.1)
        h.release(RW)
        await s(0.6)
        assert len(h.texts) == 1

        # Ctrl+Super+Seta vira outro atalho: descarta
        h.press(LC)
        h.press(LW)
        await s(0.5)
        h.press(LEFT)
        assert h.service.recorder is None and not h.service.hotkey.recording
        h.release(LEFT)
        h.release(LW)
        h.release(LC)
        await s(0.6)
        assert len(h.texts) == 1

        # Shift já segurado: não grava
        h.press(SH)
        h.press(LC)
        h.press(LW)
        assert h.service.recorder is None
        await s(0.5)
        h.release(LW)
        h.release(LC)
        h.release(SH)
        await s(0.5)
        assert len(h.texts) == 1

        # Ctrl+A e depois Super: grava
        h.press(LC)
        h.press(A)
        h.release(A)
        h.press(LW)
        assert h.service.recorder is not None
        await s(0.6)
        h.release(LW)
        h.release(LC)
        await s(0.8)
        assert len(h.texts) == 2

        # toque curto é ignorado
        h.press(LC)
        h.press(LW)
        h.release(LW)
        h.release(LC)
        await s(0.6)
        assert len(h.texts) == 2
        assert h.spans.__len__() == 2  # só as gravações válidas viram trecho ditado
        events = await h.events()
        assert [ev["active"] for ev in events].count(True) == [ev["active"] for ev in events].count(False)

    run(tmp_path, scenario)


def test_trailing_space_can_be_disabled(tmp_path):
    async def scenario(h):
        h.settings.update({"trailing_space": False, "paste": "ctrl+shift+v", "ydotool": "/x/ydotool"})
        h.service.apply_settings()
        h.press(LC)
        h.press(LW)
        await asyncio.sleep(0.6)
        h.release(LW)
        h.release(LC)
        await asyncio.sleep(0.6)
        assert [p[1:] for p in h.pasted] == [("frase", "ctrl+shift+v", "/x/ydotool")]

    run(tmp_path, scenario)


def test_empty_transcription_pastes_nothing(tmp_path):
    async def scenario(h):
        h.press(LC)
        h.press(LW)
        await asyncio.sleep(0.6)
        h.release(LW)
        h.release(LC)
        await asyncio.sleep(0.6)
        assert h.pasted == []
        assert h.transcriber.calls == 1

    run(tmp_path, scenario, transcriber=FakeTranscriber(text=""))


def test_mic_failure_resets_recording_flag(tmp_path, isolated):
    async def scenario(h):
        attempts = []

        def broken():
            attempts.append(1)
            if len(attempts) == 1:
                raise FileNotFoundError("pw-record")
            return h._recorder()

        h.service._recorder_factory = broken
        h.press(LC)
        h.press(LW)
        assert h.service.recorder is None
        assert h.service.hotkey.recording is False
        assert h.service.max_timer is None
        assert "Falha ao abrir o microfone" in isolated[0]
        assert await h.events() == []  # nada começou
        h.release(LW)
        h.release(LC)
        # a tecla continua funcionando na próxima tentativa
        h.press(LC)
        h.press(LW)
        assert h.service.recorder is not None and h.service.hotkey.recording
        await asyncio.sleep(0.5)
        h.release(LW)
        h.release(LC)
        await asyncio.sleep(0.6)
        assert len(h.texts) == 1

    run(tmp_path, scenario)


def test_max_duration_timer_resets_recording_flag(tmp_path):
    async def scenario(h):
        h.settings.update({"min_sec": 0.1, "max_sec": 0.5})
        h.service.apply_settings()
        h.press(LC)
        h.press(LW)
        await asyncio.sleep(0.8)  # o limite de tempo encerra com a tecla ainda segurada
        assert h.service.recorder is None
        assert h.service.hotkey.recording is False
        assert len(h.spans) == 1
        h.release(LW)
        h.release(LC)
        await asyncio.sleep(0.6)
        assert h.texts == ["frase "]  # soltar depois não finaliza de novo
        assert len(h.recorders) == 1
        # e a próxima gravação funciona
        h.press(LC)
        h.press(LW)
        assert h.service.recorder is not None
        h.release(LW)
        h.release(LC)

    run(tmp_path, scenario)


def test_keyboard_removed_cancels_recording(tmp_path):
    async def scenario(h):
        h.press(LC)
        h.press(LW)
        await asyncio.sleep(0.5)
        h.kb.queue = "OSError"
        h.service._on_readable("/dev/input/fake")
        assert h.service.recorder is None
        assert h.service.hotkey.recording is False
        assert "/dev/input/fake" not in h.service.devices
        assert h.service.keys_released.is_set()  # sem teclados, nada segurado
        await asyncio.sleep(0.5)
        assert h.pasted == [] and h.spans == []
        assert [ev["active"] for ev in await h.events()] == [True, False]

    run(tmp_path, scenario)


def test_resync_swallows_per_device_oserror(tmp_path):
    async def scenario(h):
        second = FakeDevice("Outro")
        second.held = {SH}
        h.service.devices["/dev/input/outro"] = second
        h.kb.fail_active_keys = True
        h.kb.held = {LC}
        h.service._resync_pressed()  # não levanta; usa só o dispositivo que respondeu
        assert h.service.hotkey.pressed == {SH}
        assert not h.service.keys_released.is_set()
        second.close()

    run(tmp_path, scenario)


def test_syn_dropped_resyncs_pressed_keys(tmp_path):
    async def scenario(h):
        h.press(LC)
        h.kb.held.clear()  # o evento de soltar se perdeu
        assert not h.service.keys_released.is_set()
        h.kb.queue = [SimpleNamespace(type=e.EV_SYN, code=e.SYN_DROPPED, value=0)]
        h.service._on_readable("/dev/input/fake")
        assert h.service.hotkey.pressed == set()
        assert h.service.keys_released.is_set()

    run(tmp_path, scenario)


def test_on_readable_dispatches_key_events(tmp_path):
    async def scenario(h):
        h.kb.held = {LC, LW}
        h.kb.queue = [
            SimpleNamespace(type=e.EV_KEY, code=LC, value=1),
            SimpleNamespace(type=e.EV_KEY, code=LW, value=1),
        ]
        h.service._on_readable("/dev/input/fake")
        assert h.service.recorder is not None
        h.kb.held.clear()
        h.kb.queue = [SimpleNamespace(type=e.EV_KEY, code=LW, value=0)]
        h.service._on_readable("/dev/input/fake")
        assert h.service.recorder is None

    run(tmp_path, scenario)


def test_pause_blocks_recording_and_resume_restores(tmp_path):
    async def scenario(h):
        h.settings.update({"dictation_enabled": False})
        h.service.apply_settings()
        assert h.service.paused is True
        h.press(LC)
        h.press(LW)
        assert h.service.recorder is None and h.service.hotkey.recording is False
        h.release(LW)
        h.release(LC)

        h.settings.update({"dictation_enabled": True})
        h.service.apply_settings()
        assert h.service.paused is False
        h.press(LC)
        h.press(LW)
        assert h.service.recorder is not None
        # pausar no meio da gravação a descarta
        h.settings.update({"dictation_enabled": False})
        h.service.apply_settings()
        assert h.service.recorder is None and h.service.hotkey.recording is False
        assert (await h.events())[-1] == {"type": "dictation", "active": False}
        await asyncio.sleep(0.5)
        assert h.pasted == []

    run(tmp_path, scenario)


def test_apply_settings_changes_key_without_restart(tmp_path):
    async def scenario(h):
        h.settings.update({"key": "KEY_RIGHTCTRL"})
        h.service.apply_settings()
        assert h.service.cfg.key == "KEY_RIGHTCTRL"
        h.press(LC)
        h.press(LW)
        assert h.service.recorder is None  # a combinação antiga já não vale
        h.release(LW)
        h.release(LC)
        h.press(RC)
        assert h.service.recorder is not None
        h.release(RC)

    run(tmp_path, scenario)


def test_invalid_key_in_environment_keeps_previous_combo(tmp_path, monkeypatch):
    async def scenario(h):
        assert h.service.cfg.key == "ctrl+super"  # a inválida do ambiente não derrubou o serviço
        monkeypatch.setenv("DITADO_PASTE", "ctrl+v")
        h.service.apply_settings()
        assert h.service.cfg.paste == "ctrl+v"

    monkeypatch.setenv("DITADO_KEY", "ctrl+banana")
    run(tmp_path, scenario)


def test_transcriber_reload_swaps_only_when_ready(tmp_path):
    started, release = threading.Event(), threading.Event()
    built = []

    def factory(opts, vocab):
        built.append((opts, vocab))
        started.set()
        assert release.wait(5)
        return FakeTranscriber(text="novo", delay=0.05, opts=opts, vocab=vocab)

    async def scenario(h):
        old = h.transcriber
        h.service.apply_settings()  # nada mudou
        await asyncio.sleep(0.1)
        assert built == []

        h.settings.update({"model": "medium", "compute_type": "int8_float16", "prompt": "Outro estilo."})
        h.service.apply_settings()
        assert await asyncio.to_thread(started.wait, 2)
        assert built[0][0] == TranscriberOptions(
            model="medium", compute_type="int8_float16", language="pt", prompt="Outro estilo."
        )
        assert built[0][1] is old.vocab  # reaproveita o vocabulário (hot reload dos arquivos)

        # enquanto carrega, o ditado segue com o antigo
        h.press(LC)
        h.press(LW)
        await asyncio.sleep(0.5)
        h.release(LW)
        h.release(LC)
        await asyncio.sleep(0.6)
        assert h.service.transcriber is old and h.texts == ["frase "]

        release.set()
        for _ in range(50):
            if h.service.transcriber is not old:
                break
            await asyncio.sleep(0.05)
        assert h.service.transcriber.text == "novo"
        assert len(built) == 1

        h.press(LC)
        h.press(LW)
        await asyncio.sleep(0.5)
        h.release(LW)
        h.release(LC)
        await asyncio.sleep(0.6)
        assert h.texts == ["frase ", "novo "]

    run(tmp_path, scenario, transcriber=FakeTranscriber(opts=TranscriberOptions(prompt=DEFAULTS["prompt"])),
        transcriber_factory=factory)


def test_transcriber_reload_failure_keeps_old_and_can_retry(tmp_path):
    calls = []

    def factory(opts, vocab):
        calls.append(opts)
        if len(calls) == 1:
            raise RuntimeError("sem VRAM")
        return FakeTranscriber(text="novo", opts=opts, vocab=vocab)

    async def scenario(h):
        old = h.service.transcriber
        h.settings.update({"model": "medium"})
        h.service.apply_settings()
        for _ in range(50):
            if calls and not h.service._reloading:
                break
            await asyncio.sleep(0.05)
        assert h.service.transcriber is old
        h.service.apply_settings()  # tenta de novo
        for _ in range(50):
            if h.service.transcriber is not old:
                break
            await asyncio.sleep(0.05)
        assert h.service.transcriber.text == "novo" and len(calls) == 2

    run(tmp_path, scenario, transcriber=FakeTranscriber(opts=TranscriberOptions(prompt=DEFAULTS["prompt"])),
        transcriber_factory=factory)


def test_recorder_marks_its_pipewire_node_as_ours():
    cmd = dictation.RECORD_CMD
    assert cmd[:8] == ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "--raw"]
    assert cmd[-1] == "-" and "--target" not in cmd  # microfone padrão, como em produção
    props = cmd[cmd.index("-P") + 1]
    assert 'application.name = "whisper-ditado"' in props
    assert 'node.name = "whisper_ditado_dictation"' in props


def test_transcriber_options_carry_production_prompt(tmp_path):
    store = Store(tmp_path / "x.db")
    opts = transcriber_options(Settings(store))
    assert opts == TranscriberOptions(
        model="large-v3-turbo", compute_type="float16", language="pt", prompt=DEFAULTS["prompt"]
    )
    s = Settings(store)
    s.update({"prompt": ""})
    assert transcriber_options(s).prompt is None
    store.close()


# --- varredura de teclados ---------------------------------------------------


def test_scan_filters_devices_and_rechecks_reused_nodes(tmp_path):
    async def scenario(h):
        h.service.devices.clear()
        nodes = {name: tmp_path / name for name in ("kbd", "mouse", "ydotoold", "other")}
        for n in nodes.values():
            n.write_text("")
        devs = {
            str(nodes["kbd"]): FakeDevice("AT keyboard"),
            str(nodes["mouse"]): FakeDevice("Mouse", keys=[e.BTN_LEFT]),
            str(nodes["ydotoold"]): FakeDevice("ydotoold virtual device"),
            str(nodes["other"]): FakeDevice("Sem hotkey", keys=[A]),
        }
        opened = []

        def opener(path):
            opened.append(path)
            return devs[path]

        h.service._list_devices = lambda: list(devs)
        h.service._open_device = opener
        h.service._scan()
        assert list(h.service.devices) == [str(nodes["kbd"])]
        assert set(h.service.skipped) == {str(nodes[n]) for n in ("mouse", "ydotoold", "other")}
        assert all(devs[p].closed for p in h.service.skipped)

        opened.clear()
        h.service._scan()
        assert opened == []  # descartados e já abertos não são reabertos

        # o kernel reaproveita o caminho para outro nó: precisa reavaliar
        mouse = str(nodes["mouse"])
        os.unlink(mouse)
        nodes["mouse"].write_text("novo")
        devs[mouse] = FakeDevice("Teclado novo")
        h.service._scan()
        assert opened == [mouse]
        assert mouse in h.service.devices

        # nós que sumiram saem de `skipped`
        h.service._list_devices = lambda: [str(nodes["kbd"])]
        h.service._scan()
        assert set(h.service.skipped) == set()
        for dev in list(h.service.devices.values()):
            dev.close()
        h.service.devices.clear()

    run(tmp_path, scenario)


def test_changing_ignore_devices_drops_matching_keyboard(tmp_path):
    async def scenario(h):
        h.kb.name = "Teclado do Fulano"
        h.settings.update({"ignore_devices": ["Fulano"]})
        h.service.apply_settings()
        assert h.service.devices == {}
        assert h.kb.closed

    run(tmp_path, scenario)


# --- EventBus ----------------------------------------------------------------


def test_bus_publish_without_subscribers_or_loop_is_noop():
    EventBus().publish({"type": "x"})  # sem loop em execução e sem assinante


def test_bus_delivers_in_order_from_threads():
    async def main():
        bus = EventBus()
        sub = bus.subscribe()
        t = threading.Thread(target=lambda: [bus.publish({"type": "n", "i": i}) for i in range(5)])
        t.start()
        t.join()
        got = [await asyncio.wait_for(sub.__anext__(), 1) for _ in range(5)]
        assert [g["i"] for g in got] == list(range(5))
        # com o consumidor já esperando
        waiter = asyncio.ensure_future(sub.__anext__())
        await asyncio.sleep(0.05)
        await asyncio.to_thread(bus.publish, {"type": "late"})
        assert (await asyncio.wait_for(waiter, 1))["type"] == "late"

    asyncio.run(main())


def test_bus_slow_subscriber_drops_oldest_without_blocking_others():
    async def main():
        bus = EventBus(maxsize=3)
        slow, fast = bus.subscribe(), bus.subscribe()
        got = []
        for i in range(10):
            bus.publish({"type": "n", "i": i})
            got.append(await asyncio.wait_for(fast.__anext__(), 1))
        assert [g["i"] for g in got] == list(range(10))  # o rápido recebe tudo
        assert slow.dropped == 7
        rest = [await asyncio.wait_for(slow.__anext__(), 1) for _ in range(3)]
        assert [g["i"] for g in rest] == [7, 8, 9]  # ficam os mais novos

    asyncio.run(main())


def test_bus_close_stops_iteration_and_unsubscribes():
    async def main():
        bus = EventBus()
        sub = bus.subscribe()
        waiter = asyncio.ensure_future(sub.__anext__())
        await asyncio.sleep(0.05)
        sub.close()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(waiter, 1)
        bus.publish({"type": "x"})
        assert bus._subs == []

    asyncio.run(main())


def test_bus_publish_survives_closed_loop():
    bus = EventBus()
    holder = {}

    async def main():
        holder["sub"] = bus.subscribe()

    asyncio.run(main())  # o loop do assinante já fechou
    bus.publish({"type": "x"})  # não levanta
    assert bus._subs == []


def test_transcriber_reload_finishing_after_stop_is_discarded(tmp_path):
    started, release = threading.Event(), threading.Event()

    def factory(opts, vocab):
        started.set()
        assert release.wait(5)
        return FakeTranscriber(text="novo", opts=opts, vocab=vocab)

    async def scenario(h):
        old = h.service.transcriber
        h.settings.update({"model": "medium"})
        h.service.apply_settings()
        assert await asyncio.to_thread(started.wait, 2)
        h.service.stopping = True  # desligando enquanto o modelo novo carrega
        release.set()
        for _ in range(50):
            if not h.service._reloading:
                break
            await asyncio.sleep(0.05)
        assert not h.service._reloading
        assert h.service.transcriber is old  # não troca depois do desligamento

    run(tmp_path, scenario, transcriber_factory=factory)


def test_dictation_marks_busy_from_recording_until_paste(tmp_path):
    from whisper_ditado.priority import DictationBusy

    busy = DictationBusy()

    async def scenario(h):
        h.press(LC)
        h.press(LW)
        assert busy.busy  # gravando
        await asyncio.sleep(0.5)
        h.release(LW)
        h.release(LC)
        await asyncio.sleep(0.1)
        assert busy.busy and h.pasted == []  # transcrevendo
        await asyncio.sleep(0.6)
        assert h.texts == ["frase "] and not busy.busy

        # gravação curta demais e cancelada também liberam
        h.press(LC)
        h.press(LW)
        h.release(LW)
        h.release(LC)
        assert not busy.busy
        h.press(LC)
        h.press(LW)
        assert busy.busy
        h.press(A)  # atalho com outra tecla: cancela
        assert not busy.busy

    run(tmp_path, scenario, dictation_busy=busy)


def test_dictation_busy_released_when_pending_jobs_are_cancelled(tmp_path):
    from whisper_ditado.priority import DictationBusy

    busy = DictationBusy()

    async def scenario(h):
        for _ in range(2):  # a segunda fica na fila atrás da primeira
            h.press(LC)
            h.press(LW)
            await asyncio.sleep(0.5)
            h.release(LW)
            h.release(LC)
        h.service.worker.shutdown(wait=False, cancel_futures=True)
        assert await asyncio.to_thread(busy.wait_idle, 3)

    run(tmp_path, scenario, transcriber=FakeTranscriber(delay=0.5), dictation_busy=busy)


class BrokenRecorder(FakeRecorder):
    def stop(self):
        raise OSError("pw-record sumiu")


@pytest.mark.parametrize("ending", ["cancel", "finish"])
def test_busy_returns_to_zero_when_stop_raises(tmp_path, ending):
    from whisper_ditado.priority import DictationBusy

    busy = DictationBusy()

    async def scenario(h):
        h.service._recorder_factory = BrokenRecorder
        h.press(LC)
        h.press(LW)
        assert busy.busy
        with pytest.raises(OSError):
            if ending == "cancel":
                h.press(A)  # atalho com outra tecla
            else:
                h.release(LW)
        assert not busy.busy  # contador não vaza: a GPU das reuniões não fica esperando
        assert h.service.recorder is None
        events = await h.events()
        assert {"type": "dictation", "active": False} in events

    run(tmp_path, scenario, dictation_busy=busy)


def test_busy_returns_to_zero_when_submit_raises(tmp_path):
    from whisper_ditado.priority import DictationBusy

    busy = DictationBusy()

    async def scenario(h):
        def broken_submit(*_args, **_kwargs):
            raise ValueError("fila quebrada")

        h.press(LC)
        h.press(LW)
        await asyncio.sleep(0.5)
        h.service.worker.submit = broken_submit
        with pytest.raises(ValueError):
            h.release(LW)
        assert not busy.busy

    run(tmp_path, scenario, dictation_busy=busy)


def test_output_dictation_uses_sink_and_does_not_mark_microphone_span(tmp_path):
    async def scenario(h):
        h.press(LC)
        h.press(SH)
        assert h.service.source == "pc"
        assert len(h.output_recorders) == 1
        assert not h.recorders
        await asyncio.sleep(0.5)
        h.release(SH)
        h.release(LC)
        await asyncio.sleep(0.5)
        assert h.texts == ["frase "]
        assert h.spans == []
        assert not h.service.busy.busy
        assert await h.events() == [
            {"type": "dictation", "active": True, "source": "pc"},
            {"type": "dictation", "active": False, "source": "pc"},
        ]
    run(tmp_path, scenario)


def test_output_shortcut_cancels_with_extra_key_and_requires_left_modifiers(tmp_path):
    async def scenario(h):
        h.press(RC)
        h.press(SH)
        assert h.service.recorder is None
        h.release(RC)
        h.release(SH)
        h.press(SH)
        h.press(LC)
        assert h.service.recorder is not None
        h.press(A)
        assert h.service.recorder is None
        assert h.output_recorders[0].stopped
        h.release(A)
        h.release(SH)
        h.release(LC)
        await asyncio.sleep(0.4)
        assert not h.texts
        assert not h.service.busy.busy
    run(tmp_path, scenario)


def test_output_shortcut_changes_live_and_pauses_with_dictation(tmp_path):
    async def scenario(h):
        h.settings.update({"output_key": "alt+KEY_F8"})
        h.service.apply_settings()
        h.press(LC)
        h.press(SH)
        assert h.service.recorder is None
        h.release(SH)
        h.release(LC)
        h.press(e.KEY_LEFTALT)
        h.press(e.KEY_F8)
        assert h.service.source == "pc"
        assert h.service.recorder is not None
        h.settings.update({"dictation_enabled": False})
        h.service.apply_settings()
        assert h.service.recorder is None
        assert not h.service.busy.busy
        h.release(e.KEY_F8)
        h.release(e.KEY_LEFTALT)
        h.press(e.KEY_LEFTALT)
        h.press(e.KEY_F8)
        assert h.service.recorder is None
    run(tmp_path, scenario)


def test_output_recorder_command_captures_sink(monkeypatch):
    commands = []
    class Proc:
        stdout = SimpleNamespace(read1=lambda n: b"")
    monkeypatch.setattr(dictation.subprocess, "Popen", lambda cmd, **kw: commands.append(cmd) or Proc())
    recorder = dictation.Recorder(capture_sink=True)
    recorder._thread.join(timeout=1)
    props = commands[0][commands[0].index("-P") + 1]
    assert "stream.capture.sink = true" in props
    assert "whisper_ditado_dictation_pc" in props


def test_switching_from_single_modifier_shortcut_still_finishes_output(tmp_path):
    async def scenario(h):
        h.settings.update({"key": "KEY_LEFTCTRL"})
        h.service.apply_settings()
        h.press(LC)
        assert len(h.recorders) == 1
        h.press(SH)
        assert h.recorders[0].stopped
        assert h.service.source == "pc"
        assert h.service.output_hotkey.recording
        await asyncio.sleep(0.5)
        h.release(SH)
        h.release(LC)
        assert h.service.recorder is None
        await asyncio.sleep(0.5)
        assert h.texts == ["frase "]
        assert not h.spans
    run(tmp_path, scenario)


def test_each_dictation_source_plays_its_start_cue(tmp_path, monkeypatch):
    sounds = []
    monkeypatch.setattr(dictation, "play_start_sound", sounds.append)
    async def scenario(h):
        h.press(LC)
        assert sounds == []
        h.press(LW)
        assert sounds == ["mic"]
        h.press(A)
        h.release(A)
        h.release(LW)
        h.press(SH)
        assert sounds == ["mic", "pc"]
        h.release(SH)
        h.release(LC)
        h.settings.update({"dictation_enabled": False})
        h.service.apply_settings()
        h.press(LC)
        h.press(SH)
        assert sounds == ["mic", "pc"]
    run(tmp_path, scenario)


def test_print_hotkey_calls_on_print_once_per_press_even_paused(tmp_path):
    prints = []
    ALT, P = e.KEY_LEFTALT, e.KEY_P

    async def scenario(h):
        h.settings.update({"dictation_enabled": False})
        h.service.apply_settings()
        h.press(LC)
        h.press(ALT)
        assert prints == []
        h.press(P)
        h.service._on_key(P, 2)  # autorepeat
        h.service._on_key(P, 2)
        assert prints == [1]
        h.release(P)
        h.press(P)
        assert prints == [1, 1]
        h.release(P)
        h.release(ALT)
        h.release(LC)
        assert h.service.recorder is None and h.recorders == [] and h.output_recorders == []

        # atalho trocado ao vivo
        h.settings.update({"print_key": "alt+KEY_F7"})
        h.service.apply_settings()
        h.press(LC)
        h.press(ALT)
        h.press(P)
        assert prints == [1, 1]
        h.release(P)
        h.release(ALT)
        h.release(LC)
        h.press(ALT)
        h.press(e.KEY_F7)
        assert prints == [1, 1, 1]
        h.release(e.KEY_F7)
        h.release(ALT)

    run(tmp_path, scenario, on_print=lambda: prints.append(1))


def test_print_hotkey_failure_is_logged_and_does_not_break_keys(tmp_path, caplog):
    ALT, P = e.KEY_LEFTALT, e.KEY_P

    def boom():
        raise RuntimeError("falhou")

    async def scenario(h):
        h.press(LC)
        h.press(ALT)
        h.press(P)
        h.release(P)
        h.release(ALT)
        h.press(LW)  # o ditado segue funcionando
        assert h.service.recorder is not None
        h.release(LW)
        h.release(LC)

    run(tmp_path, scenario, on_print=boom)
    assert "print" in caplog.text
