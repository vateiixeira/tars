import copy
import io
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.meeting.test_finalize import (
    MIC_AMP,
    PC_AMP,
    FakeDiarizer,
    FakeTranscriber,
    w,
)
from whisper_ditado import paths
from whisper_ditado.audio import pwgraph
from whisper_ditado.meeting.session import MeetingManager, MeetingStateError
from whisper_ditado.prints.portal import PortalError
from whisper_ditado.settings import Settings
from whisper_ditado.store import Store

FIXTURES = Path(__file__).parent.parent / "fixtures"
SR = 16000
ANALOG = "alsa_output.pci-0000_09_00.4.analog-stereo"
HDMI = "alsa_output.pci-0000_07_00.1.hdmi-stereo"
MIC = "alsa_input.pci-0000_09_00.4.analog-stereo"
WEBCAM_MIC = "alsa_input.usb-046d_C270_HD_WEBCAM_200901010001-02.mono-fallback"
AEC_PREFIX = "whisper_ditado_aec_"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buf, "PNG")
    return buf.getvalue()


PNG_BYTES = _png()


# -- fakes -----------------------------------------------------------------------


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeBus:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def publish(self, event: dict) -> None:
        with self._lock:
            self.events.append(dict(event))

    def of(self, kind: str) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["type"] == kind]


class FakeRecorder:
    def __init__(self, name, out_path, target, capture_sink, clock, amp):
        self.name = name
        self.out_path = Path(out_path)
        self.target = target
        self.capture_sink = capture_sink
        self.clock = clock
        self.amp = amp
        self.level = -20.0
        self.alive_flag = True
        self.retargets: list = []
        self.t0 = None
        self.stopped = False

    def start(self, t0):
        self.t0 = t0
        if str(self.out_path) != "/dev/null":
            self.out_path.write_bytes(b"")

    @property
    def level_db(self):
        return self.level

    @property
    def alive(self):
        return self.alive_flag

    def retarget(self, target):
        self.retargets.append(target)
        self.target = target

    def stop(self):
        self.stopped = True
        n = round((self.clock() - self.t0) * SR)
        if str(self.out_path) != "/dev/null":
            value = int(self.amp * 32768)
            self.out_path.write_bytes(np.full(n, value, dtype=np.int16).tobytes())
        return n


class FakeAec:
    def __init__(self, log, mic, sink, name, *, gate=None, mic_rec=None):
        self.log = log
        self.mic, self.sink, self.name = mic, sink, name
        self.stopped = False
        self.gate = gate  # threading.Event: start() só volta quando liberado
        self.mic_target_at_stop = "?"
        self.mic_rec = mic_rec  # callable -> trilha do microfone (para ver o alvo ao parar)
        log.append(self)

    def start(self):
        if self.gate is not None:
            assert self.gate.wait(5)
        return self.name

    def stop(self):
        if self.mic_rec is not None:
            self.mic_target_at_stop = self.mic_rec().target
        self.stopped = True


def load_graph(name: str = "pwdump_idle.json", *, firefox_sink: str | None = None,
               default_source: str | None = None) -> pwgraph.Graph:
    dump = json.loads((FIXTURES / name).read_text())
    dump = copy.deepcopy(dump)
    if firefox_sink:
        sink_id = {o["info"]["props"]["node.name"]: o["id"] for o in dump
                   if o["type"].endswith(":Node")}[firefox_sink]
        for o in dump:
            if o["id"] == 131:
                o["info"]["input-node-id"] = sink_id
    graph = pwgraph.parse(dump)
    if default_source:
        graph.default_source = default_source
    return graph


def wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("condição não atingida")


# -- fixtures --------------------------------------------------------------------


@pytest.fixture
def store(data_home):
    s = Store(paths.db_path())
    yield s
    s.close()


@pytest.fixture
def settings(store):
    return Settings(store)


class Rig:
    """Monta um MeetingManager com fakes; `graph` é o que o snapshot devolve."""

    def __init__(self, store, settings, *, clock=None, tick_s=None, diarizer=None, **manager_kwargs):
        self.store, self.settings = store, settings
        self.clock = clock or FakeClock()
        self.bus = FakeBus()
        self.recorders: list[FakeRecorder] = []
        self.aecs: list[FakeAec] = []
        self.aec_gate: threading.Event | None = None
        self.graph = load_graph()
        self.snapshot_error: Exception | None = None
        self.transcriber = FakeTranscriber({MIC_AMP: [w(0.1, "oi")], PC_AMP: [w(0.2, "olá")]})
        self.factory_calls = 0
        self.manager = MeetingManager(
            store, settings, self.bus, self.factory, diarizer or FakeDiarizer(available=False), None,
            recorder_factory=self.make_recorder,
            aec_factory=lambda mic, sink, name: FakeAec(
                self.aecs, mic, sink, name, gate=self.aec_gate, mic_rec=lambda: self.rec("mic")),
            snapshot=self.snapshot, clock=self.clock, tick_s=tick_s, vad=lambda audio: [],
            **manager_kwargs,
        )

    def factory(self):
        self.factory_calls += 1
        return self.transcriber

    def snapshot(self):
        if self.snapshot_error:
            raise self.snapshot_error
        return self.graph

    def make_recorder(self, name, out_path, target, capture_sink):
        amp = MIC_AMP if name == "mic" else PC_AMP
        rec = FakeRecorder(name, out_path, target, capture_sink, self.clock, amp)
        self.recorders.append(rec)
        return rec

    def meeting_recorders(self) -> list[FakeRecorder]:
        return [r for r in self.recorders if str(r.out_path) != "/dev/null"]

    def rec(self, name) -> FakeRecorder:
        return [r for r in self.meeting_recorders() if r.name == name][-1]

    def start(self) -> str:
        before = len(self.meeting_recorders())
        mid = self.manager.start()
        wait_for(lambda: len([r for r in self.meeting_recorders() if r.t0 is not None])
                 >= before + 2)
        if self.aec_gate is None:  # preparação completa: grafo lido e AEC inicial ligado
            session = self.manager._session
            wait_for(lambda: session.ready.is_set() and session.aec_thread is None)
        return mid

    def advance(self, seconds: float, step: float = 0.5) -> None:
        for _ in range(round(seconds / step)):
            self.clock.t += step
            self.manager.tick()


# -- testes ----------------------------------------------------------------------


def test_start_stop_creates_meeting_and_publishes_events(store, settings):
    rig = Rig(store, settings, clock=time.monotonic, tick_s=0.05)
    mgr = rig.manager
    assert mgr.state == "idle" and mgr.current_id is None

    mid = rig.start()
    assert mgr.state == "recording" and mgr.current_id == mid
    assert mgr.started_at is not None
    assert store.get_meeting(mid).status == "recording"
    with pytest.raises(MeetingStateError):
        mgr.start()

    mic, pc = rig.rec("mic"), rig.rec("pc")
    d = paths.meeting_dir(mid)
    assert mic.out_path == d / "mic.raw" and pc.out_path == d / "pc.raw"
    assert mic.t0 == pc.t0  # trilhas alinhadas ao mesmo início
    assert pc.capture_sink and not mic.capture_sink
    assert pc.target is None  # saída padrão (ninguém em chamada): sem reiniciar a captura
    # AEC ligado por padrão: microfone grava a fonte virtual sem eco
    wait_for(lambda: mic.target is not None)
    assert len(rig.aecs) == 1 and (rig.aecs[0].mic, rig.aecs[0].sink) == (MIC, ANALOG)
    assert mic.target == rig.aecs[0].name and mic.target.startswith(AEC_PREFIX + mid[:8])

    wait_for(lambda: len(rig.bus.of("levels")) >= 3)
    assert rig.bus.of("levels")[0] == {"type": "levels", "mic_db": -20.0, "pc_db": -20.0}
    time.sleep(0.2)

    assert mgr.stop() == mid
    assert mgr.state == "processing" and mgr.current_id == mid
    with pytest.raises(MeetingStateError):
        mgr.stop()
    assert mgr.wait_idle(10)

    assert mgr.state == "idle" and mgr.current_id is None
    assert mic.stopped and pc.stopped and rig.aecs[0].stopped
    m = store.get_meeting(mid)
    assert m.status == "ready", m.error
    assert m.ended_at is not None
    assert [(e["meeting_id"], e["status"]) for e in rig.bus.of("meeting_updated")] == [
        (mid, "recording"), (mid, "processing"), (mid, "ready")]
    assert [e["stage"] for e in rig.bus.of("progress")] == [
        "transcrevendo", "separando vozes", "finalizando"]
    assert all(e["meeting_id"] == mid for e in rig.bus.of("progress"))
    texts = [s.text for s in store.get_segments(mid)]
    assert texts == ["oi", "olá"]
    assert rig.factory_calls >= 1
    assert (d / "mic.opus").exists() and not (d / "mic.raw").exists()


def test_alerts_are_published_and_saved(store, settings):
    rig = Rig(store, settings)
    mid = rig.start()
    rig.rec("mic").level = -math.inf  # microfone mudo

    rig.advance(14.0)
    assert rig.bus.of("alert") == []
    rig.advance(2.0)  # 16 s sem sinal
    assert rig.bus.of("alert") == [
        {"type": "alert", "kind": "mic_silent", "active": True, "message": "Microfone sem sinal"}]
    assert rig.manager.active_alerts == ["mic_silent"]
    levels = rig.bus.of("levels")
    assert levels and levels[-1]["mic_db"] == -100.0  # -inf vira o piso (JSON válido)

    # captura do PC morreu: só alerta depois de 1 s seguido sem processo (evita piscar)
    rig.rec("pc").alive_flag = False
    rig.advance(0.5)
    assert not [e for e in rig.bus.of("alert") if e["kind"] == "pc_dead"]
    rig.advance(1.0)
    assert {"type": "alert", "kind": "pc_dead", "active": True,
            "message": "Captura do áudio do PC parou"} in rig.bus.of("alert")
    rig.rec("pc").alive_flag = True
    rig.advance(0.5)
    assert rig.bus.of("alert")[-1]["kind"] == "pc_dead"
    assert rig.bus.of("alert")[-1]["active"] is False

    rig.manager.stop()
    assert rig.manager.wait_idle(10)

    assert {"type": "alert", "kind": "mic_silent", "active": False,
            "message": "Microfone sem sinal"} in rig.bus.of("alert")
    alerts = {a.kind: a for a in store.get_alerts(mid)}
    # no_diarization vem da finalização (o diarizador falso está indisponível)
    assert set(alerts) == {"mic_silent", "pc_dead", "no_diarization"}
    # início = quando o silêncio começou (o alerta disparou 15 s depois, em 15,5 s)
    assert alerts["mic_silent"].start_s == pytest.approx(0.5)
    assert alerts["mic_silent"].end_s == pytest.approx(18.0)
    assert alerts["pc_dead"].start_s == pytest.approx(17.5)
    assert alerts["pc_dead"].end_s == pytest.approx(18.0)


def test_pc_track_follows_call_app(store, settings):
    rig = Rig(store, settings)
    mid = rig.start()
    pc, mic = rig.rec("pc"), rig.rec("mic")
    assert pc.target is None and rig.manager.followed_app is None

    rig.graph = load_graph("pwdump_call_firefox.json", firefox_sink=HDMI)
    rig.advance(2.0)

    assert pc.retargets == [HDMI]
    assert rig.manager.followed_app == "Firefox"
    assert store.get_meeting(mid).followed_app == "Firefox"
    # AEC reiniciado com a nova saída como referência; o microfone passa pelo dispositivo cru
    # antes de o AEC antigo cair e depois vai para a fonte do AEC novo (outro nome de nó)
    wait_for(lambda: len(rig.aecs) == 2)
    wait_for(lambda: mic.retargets[-1:] == [rig.aecs[1].name])
    first, second = rig.aecs
    assert mic.retargets == [first.name, MIC, second.name]
    assert first.mic_target_at_stop == MIC
    assert first.name != second.name and second.name.startswith(AEC_PREFIX + mid[:8])
    assert first.stopped and (second.mic, second.sink) == (MIC, HDMI)
    assert not [e for e in rig.bus.of("alert") if e["kind"] == "pc_wrong_output"]

    # mesma situação no próximo snapshot: nada muda
    rig.advance(2.0)
    assert pc.retargets == [HDMI] and len(rig.aecs) == 2

    # pw-dump falhando não derruba a gravação
    rig.snapshot_error = FileNotFoundError("pw-dump")
    rig.advance(2.0)
    assert rig.manager.state == "recording"

    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    assert rig.aecs[1].stopped


def test_mic_follows_default_source_without_aec(store, settings):
    settings.update({"aec_enabled": False})
    rig = Rig(store, settings)
    rig.start()
    mic = rig.rec("mic")
    assert mic.target is None and rig.aecs == []  # microfone padrão do sistema

    rig.graph = load_graph(default_source=WEBCAM_MIC)
    rig.advance(2.0)
    assert mic.retargets == [WEBCAM_MIC]

    rig.manager.stop()
    assert rig.manager.wait_idle(10)


def test_fixed_devices_are_not_retargeted_and_mismatch_alerts(store, settings):
    settings.update({"pc_device": ANALOG, "mic_device": WEBCAM_MIC, "aec_enabled": False})
    rig = Rig(store, settings)
    rig.start()
    pc, mic = rig.rec("pc"), rig.rec("mic")
    assert pc.target == ANALOG and mic.target == WEBCAM_MIC

    rig.graph = load_graph("pwdump_call_firefox.json", firefox_sink=HDMI, default_source=MIC)
    rig.advance(2.0)

    assert pc.retargets == [] and mic.retargets == []
    assert {"type": "alert", "kind": "pc_wrong_output", "active": True,
            "message": "Áudio da chamada não está sendo captado"} in rig.bus.of("alert")
    rig.manager.stop()
    assert rig.manager.wait_idle(10)


def test_dictation_span_is_relative_to_start(store, settings):
    rig = Rig(store, settings)
    t0 = rig.clock.t
    mid = rig.start()
    rig.manager.register_dictation_span(t0 + 3.0, t0 + 5.5)
    rig.manager.register_dictation_span(t0 - 2.0, t0 + 1.0)  # começou antes da reunião
    assert store.get_dictation_spans(mid) == [(0.0, 1.0), (3.0, 5.5)]
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    rig.manager.register_dictation_span(t0 + 6.0, t0 + 7.0)  # sem reunião: ignorado
    assert store.get_dictation_spans(mid) == [(0.0, 1.0), (3.0, 5.5)]


def test_monitor_levels_outside_meeting(store, settings):
    rig = Rig(store, settings)
    rig.manager.monitor_levels(True)
    wait_for(lambda: len(rig.bus.of("levels")) >= 2)
    mon = [r for r in rig.recorders if str(r.out_path) == "/dev/null"]
    assert {r.name for r in mon} == {"mic", "pc"}
    assert {r.name: r.target for r in mon} == {"mic": MIC, "pc": ANALOG}
    assert rig.aecs == []  # só mede nível

    rig.clock.t += 61.0  # desliga sozinho após 60 s
    wait_for(lambda: all(r.stopped for r in mon))
    count = len(rig.bus.of("levels"))
    time.sleep(0.25)
    assert len(rig.bus.of("levels")) == count

    # ligado e desligado à mão
    rig.manager.monitor_levels(True)
    wait_for(lambda: len([r for r in rig.recorders if str(r.out_path) == "/dev/null"]) == 4)
    rig.manager.monitor_levels(False)
    wait_for(lambda: all(r.stopped for r in rig.recorders))

    # durante a gravação, ignorado (os níveis já são publicados)
    rig.start()
    before = len(rig.recorders)
    rig.manager.monitor_levels(True)
    time.sleep(0.2)
    assert len(rig.recorders) == before
    rig.manager.stop()
    assert rig.manager.wait_idle(10)


def test_start_stops_level_monitor(store, settings):
    rig = Rig(store, settings)
    rig.manager.monitor_levels(True)
    wait_for(lambda: len(rig.recorders) == 2)
    rig.start()
    wait_for(lambda: all(r.stopped for r in rig.recorders if str(r.out_path) == "/dev/null"))
    rig.manager.stop()
    assert rig.manager.wait_idle(10)


def test_manager_reprocess_and_recover_publish_status(store, settings):
    rig = Rig(store, settings)
    mid = rig.start()
    rig.advance(1.0)
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    rig.bus.events.clear()

    rig.manager.reprocess(mid)
    assert rig.manager.state == "processing"
    with pytest.raises(MeetingStateError):
        rig.manager.reprocess(mid)
    assert rig.manager.wait_idle(10)
    assert [e["status"] for e in rig.bus.of("meeting_updated")] == ["processing", "ready"]
    assert store.get_meeting(mid).status == "ready"
    with pytest.raises(KeyError):
        rig.manager.reprocess("nao-existe")

    orphan = store.create_meeting(store.get_meeting(mid).started_at).id
    (paths.meeting_dir(orphan) / "mic.raw").write_bytes(
        np.full(SR * 2, int(MIC_AMP * 32768), dtype=np.int16).tobytes())
    rig.bus.events.clear()
    rig.manager.recover()
    assert rig.manager.wait_idle(10)
    assert store.get_meeting(orphan).status == "ready"
    assert [(e["meeting_id"], e["status"]) for e in rig.bus.of("meeting_updated")] == [
        (orphan, "processing"), (orphan, "ready")]
    assert rig.manager.state == "idle"


def test_tracks_record_while_aec_loads(store, settings):
    rig = Rig(store, settings)
    rig.aec_gate = threading.Event()  # o AEC "demora" para subir
    mid = rig.start()
    # as duas trilhas já gravam antes do AEC: PC primeiro, microfone no dispositivo cru
    assert [r.name for r in rig.meeting_recorders()] == ["pc", "mic"]
    mic = rig.rec("mic")
    assert mic.target is None and mic.retargets == []
    wait_for(lambda: len(rig.aecs) == 1)
    assert rig.aecs[0].name.startswith(AEC_PREFIX + mid[:8])
    rig.aec_gate.set()
    wait_for(lambda: mic.retargets == [rig.aecs[0].name])
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    assert rig.aecs[0].stopped


def test_aec_node_names_are_unique_per_meeting(store, settings):
    rig = Rig(store, settings)
    first = rig.start()
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    second = rig.start()
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    a, b = rig.aecs
    assert a.name != b.name
    assert a.name.startswith(AEC_PREFIX + first[:8]) and b.name.startswith(AEC_PREFIX + second[:8])


def test_track_that_fails_to_start_raises_dead_alert(store, settings):
    rig = Rig(store, settings)
    make = rig.make_recorder

    def flaky(name, out_path, target, capture_sink):
        if name == "pc":
            raise OSError("disco cheio")
        return make(name, out_path, target, capture_sink)

    rig.manager._recorder_factory = flaky
    mid = rig.manager.start()
    session = rig.manager._session
    wait_for(session.ready.is_set)
    assert session.pc is None and session.mic is not None  # o microfone segue gravando
    rig.advance(1.5)
    assert rig.manager.state == "recording"
    assert rig.manager.active_alerts == ["pc_dead"]
    assert {"type": "alert", "kind": "pc_dead", "active": True,
            "message": "Captura do áudio do PC parou"} in rig.bus.of("alert")
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    assert "pc_dead" in {a.kind for a in store.get_alerts(mid)}
    assert store.get_meeting(mid).status == "ready"


def test_setup_failure_without_tracks_still_alerts(store, settings):
    rig = Rig(store, settings)

    def broken(*args):
        raise RuntimeError("pw-record sumiu")

    rig.manager._recorder_factory = broken
    rig.manager.start()
    wait_for(rig.manager._session.ready.is_set)
    rig.advance(1.5)
    assert set(rig.manager.active_alerts) == {"mic_dead", "pc_dead"}
    rig.manager.stop()
    assert rig.manager.wait_idle(10)


def test_meeting_gpu_work_waits_for_dictation(store, settings):
    from whisper_ditado.priority import DictationBusy

    busy = DictationBusy()
    gates = []

    def gate():
        gates.append(busy.busy)
        busy.wait_idle(5)

    rig = Rig(store, settings, gpu_gate=gate)
    busy.enter()  # ditando quando a reunião começa
    mid = rig.start()
    time.sleep(0.3)
    assert rig.factory_calls == 0  # a carga do modelo espera o ditado acabar
    busy.leave()
    wait_for(lambda: rig.factory_calls == 1)
    assert rig.transcriber.pause is gate  # o Whisper das reuniões cede a GPU entre os trechos

    rig.advance(1.0)
    busy.enter()
    rig.manager.stop()
    time.sleep(0.3)
    assert store.get_meeting(mid).status == "processing"  # finalização parada no portão
    busy.leave()
    assert rig.manager.wait_idle(10)
    assert store.get_meeting(mid).status == "ready"
    assert True in gates


def test_wait_idle_returns_promptly_while_dictation_holds_the_gate(store, settings):
    from whisper_ditado.priority import DictationBusy

    busy = DictationBusy()
    rig = Rig(store, settings, gpu_gate=lambda: busy.wait_idle(5))
    busy.enter()  # ditando (ou contador preso) sem trabalho de reunião na fila
    t0 = time.monotonic()
    assert rig.manager.wait_idle(1.0)  # o sentinela do `wait_idle` não passa pelo portão
    assert time.monotonic() - t0 < 1.0
    busy.leave()


def test_diarization_waits_for_dictation_first(store, settings):
    log = []

    class LoggingDiarizer(FakeDiarizer):
        def run(self, audio):
            log.append("diarize")
            return super().run(audio)

    def gate():
        log.append("gate")

    rig = Rig(store, settings, diarizer=LoggingDiarizer(), gpu_gate=gate)
    rig.start()
    rig.advance(1.0)
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    assert "diarize" in log
    assert log[log.index("diarize") - 1] == "gate"


def test_meeting_model_failure_is_not_retried_every_feed(store, settings, caplog):
    rig = Rig(store, settings)

    def broken():
        rig.factory_calls += 1
        raise RuntimeError("sem VRAM")

    rig.manager._transcriber_factory = broken
    mid = rig.start()
    for _ in range(4):  # 40 s: quatro rodadas incrementais
        rig.advance(10.0, step=10.0)
        assert rig.manager.wait_idle(5)
    assert rig.factory_calls == 1
    assert caplog.text.count("Modelo das reuniões indisponível") == 1
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    assert rig.factory_calls == 2  # uma nova tentativa na finalização
    assert store.get_meeting(mid).status == "failed"


def test_idle_models_release_after_deadline_and_reload_on_demand(store, settings, monkeypatch):
    releases, unloads = [], []
    monkeypatch.setattr("whisper_ditado.meeting.session.release_unused_memory", lambda: releases.append(True))
    diarizer = FakeDiarizer(available=False)
    diarizer.unload = lambda: unloads.append("diarizer")
    rig = Rig(store, settings, diarizer=diarizer, gpu_gate=lambda: None)
    rig.transcriber.unload = lambda: unloads.append("whisper")
    manager = rig.manager
    manager._gpu.submit(manager._get_transcriber).result(2)
    rig.clock.t += 119
    assert manager.release_idle_models().result(2) is False
    assert unloads == []
    rig.clock.t += 1
    assert manager.release_idle_models().result(2) is True
    assert manager._transcriber is None
    assert unloads == ["whisper", "diarizer"]
    assert releases == [True]
    assert manager.release_idle_models().result(2) is False
    manager._gpu.submit(manager._get_transcriber).result(2)
    assert rig.factory_calls == 2


def test_idle_release_never_loads_a_model(store, settings, monkeypatch):
    monkeypatch.setattr("whisper_ditado.meeting.session.release_unused_memory", lambda: pytest.fail("nada a liberar"))
    rig = Rig(store, settings)
    rig.clock.t += 1000
    assert rig.manager.release_idle_models().result(2) is False
    assert rig.factory_calls == 0


def test_idle_cleanup_does_not_wait_for_dictation_gate(store, settings, monkeypatch):
    monkeypatch.setattr("whisper_ditado.meeting.session.release_unused_memory", lambda: None)

    def gate():
        raise AssertionError("liberar modelos ociosos não deve esperar o ditado")

    rig = Rig(store, settings, gpu_gate=gate)
    rig.manager._gpu.submit(rig.manager._get_transcriber, gated=False).result(2)
    rig.clock.t += 120
    assert rig.manager.release_idle_models().result(2) is True


def test_idle_release_can_be_disabled_or_changed_without_restart(store, settings, monkeypatch):
    monkeypatch.setattr("whisper_ditado.meeting.session.release_unused_memory", lambda: None)
    rig = Rig(store, settings)
    rig.manager._gpu.submit(rig.manager._get_transcriber).result(2)
    settings.update({"meeting_model_idle_sec": 0})
    rig.clock.t += 1000
    assert rig.manager.release_idle_models().result(2) is False
    settings.update({"meeting_model_idle_sec": 60})
    assert rig.manager.release_idle_models().result(2) is True


def test_idle_release_does_not_touch_recording_or_queued_processing(store, settings, monkeypatch):
    monkeypatch.setattr("whisper_ditado.meeting.session.release_unused_memory", lambda: None)
    rig = Rig(store, settings)
    mid = rig.start()
    assert rig.manager.wait_idle(2)
    rig.clock.t += 300
    assert rig.manager.release_idle_models().result(2) is False
    # Também protege trabalhos ainda na fila, antes de começarem a usar o modelo.
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    rig.clock.t += 300
    with rig.manager._lock:
        rig.manager._processing.append(mid)
    try:
        assert rig.manager.release_idle_models().result(2) is False
    finally:
        with rig.manager._lock:
            rig.manager._processing.remove(mid)
    assert rig.manager.release_idle_models().result(2) is True


def test_model_setting_change_waits_for_meeting_to_end(store, settings, monkeypatch):
    monkeypatch.setattr("whisper_ditado.meeting.session.release_unused_memory", lambda: None)
    rig = Rig(store, settings)
    rig.start()
    assert rig.manager.wait_idle(2)
    rig.manager.apply_settings()
    assert rig.manager.wait_idle(2)
    assert rig.manager._transcriber is not None
    rig.clock.t += 1
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    # Não precisa aguardar os 2 minutos quando existe uma troca explícita de modelo.
    assert rig.manager.release_idle_models().result(2) is True
    assert rig.manager._transcriber is None


# -- on_ready (trabalhos pós-reunião) ---------------------------------------------


def _finish_meeting(rig) -> str:
    mid = rig.start()
    rig.advance(1.0)
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    return mid


def test_on_ready_called_after_finalize(store, settings):
    seen = []

    def on_ready(mid):
        seen.append((mid, store.get_meeting(mid).status, rig.manager.state))

    rig = Rig(store, settings, on_ready=on_ready)
    mid = _finish_meeting(rig)
    assert seen == [(mid, "ready", "idle")]  # uma vez, já com a reunião pronta e fora de "processing"


def test_on_ready_called_after_recovery(store, settings):
    seen = []
    rig = Rig(store, settings, on_ready=seen.append)
    orphan = store.create_meeting(datetime.now()).id
    (paths.meeting_dir(orphan) / "mic.raw").write_bytes(
        np.full(SR * 2, int(MIC_AMP * 32768), dtype=np.int16).tobytes())
    rig.manager.recover()
    assert rig.manager.wait_idle(10)
    assert store.get_meeting(orphan).status == "ready"
    assert seen == [orphan]


def test_on_ready_not_called_on_reprocess(store, settings):
    seen = []
    rig = Rig(store, settings, on_ready=seen.append)
    mid = _finish_meeting(rig)
    assert seen == [mid]
    rig.manager.reprocess(mid)
    assert rig.manager.wait_idle(10)
    assert store.get_meeting(mid).status == "ready"
    assert seen == [mid]


def test_on_ready_not_called_when_failed(store, settings):
    seen = []
    rig = Rig(store, settings, on_ready=seen.append)

    def broken():
        raise RuntimeError("sem VRAM")

    rig.manager._transcriber_factory = broken
    mid = _finish_meeting(rig)
    assert store.get_meeting(mid).status == "failed"
    assert seen == []


def test_on_ready_exception_does_not_break_processing(store, settings, caplog):
    def boom(mid):
        raise RuntimeError("falhou")

    rig = Rig(store, settings, on_ready=boom)
    mid = _finish_meeting(rig)
    assert store.get_meeting(mid).status == "ready"
    assert rig.manager.state == "idle" and rig.manager._processing == []
    assert "on_ready" in caplog.text
    # a fila da GPU segue funcionando
    rig.manager.reprocess(mid)
    assert rig.manager.wait_idle(10)
    assert store.get_meeting(mid).status == "ready"


def test_reprocess_keeps_task_suggestions(store, settings):
    rig = Rig(store, settings)
    mid = _finish_meeting(rig)
    kept = store.add_task(mid, {"title": "Criada"}, origin="claude")
    store.set_job(mid, "tasks", "done")
    store.claim_tasks_for_create([kept.id])
    store.finish_create(kept.id, "created", number=7, url="https://k/7")
    free = store.add_task(mid, {"title": "Solta"}, origin="manual")
    rig.manager.reprocess(mid)
    assert rig.manager.wait_idle(10)
    assert [(t.id, t.create_state) for t in store.list_tasks(mid)] == [
        (kept.id, "created"), (free.id, "none")]
    assert store.get_job(mid, "tasks").state == "done"


# -- prints de tela ----------------------------------------------------------------


def _never_capture():
    raise AssertionError("não deveria capturar")


def test_capture_print_native_stores_moves_and_beeps(store, settings, tmp_path):
    settings.update({"print_mode": "native"})
    shot = tmp_path / "Screenshot.png"
    shot.write_bytes(PNG_BYTES)
    sounds = []
    rig = Rig(store, settings, capture=lambda: shot, print_sound=sounds.append,
              notify_fn=lambda msg: pytest.fail(msg))
    mid = rig.start()
    rig.advance(3.0)
    assert rig.manager.capture_print() is True
    wait_for(lambda: sounds == [True] and rig.bus.of("print_captured"))
    [p] = store.list_prints(mid)
    assert p.source == "native" and p.at_s == pytest.approx(3.0) and not shot.exists()
    assert {"type": "print_captured", "meeting_id": mid, "print_id": p.id} in rig.bus.events
    wait_for(lambda: not rig.manager._print_lock.locked())


def test_capture_print_failure_beeps_error_alerts_and_notifies(store, settings):
    settings.update({"print_mode": "native"})
    sounds, notes = [], []

    def refuse():
        raise PortalError("O GNOME recusou o print (cancelado ou sem permissão)")

    rig = Rig(store, settings, capture=refuse, print_sound=sounds.append, notify_fn=notes.append)
    mid = rig.start()
    rig.advance(2.0)
    assert rig.manager.capture_print() is True
    wait_for(lambda: [a.kind for a in store.get_alerts(mid)] == ["print_failed"])
    wait_for(lambda: not rig.manager._print_lock.locked())
    assert sounds == [False]
    assert notes == [("Não foi possível tirar o print: "
                      "O GNOME recusou o print (cancelado ou sem permissão)")]
    [alert] = store.get_alerts(mid)
    assert alert.start_s == alert.end_s == pytest.approx(2.0)
    assert store.list_prints(mid) == []
    assert not rig.bus.of("print_captured")
    # a falha libera o atalho para tentar de novo
    assert rig.manager.capture_print() is True
    wait_for(lambda: sounds == [False, False])


def test_capture_print_does_nothing_outside_native_recording(store, settings):
    sounds = []
    rig = Rig(store, settings, capture=_never_capture, print_sound=sounds.append)
    settings.update({"print_mode": "native"})
    assert rig.manager.capture_print() is False  # sem reunião
    settings.update({"print_mode": "flameshot"})
    rig.start()
    assert rig.manager.capture_print() is False
    settings.update({"print_mode": "off"})
    assert rig.manager.capture_print() is False
    time.sleep(0.05)
    assert sounds == []


def test_capture_print_ignores_concurrent_press(store, settings, tmp_path):
    settings.update({"print_mode": "native"})
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow_capture():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        shot = tmp_path / f"shot{len(calls)}.png"
        shot.write_bytes(PNG_BYTES)
        return shot

    sounds = []
    rig = Rig(store, settings, capture=slow_capture, print_sound=sounds.append)
    mid = rig.start()
    assert rig.manager.capture_print() is True
    assert entered.wait(2)
    assert rig.manager.capture_print() is False
    release.set()
    wait_for(lambda: sounds == [True])
    wait_for(lambda: not rig.manager._print_lock.locked())
    assert len(calls) == 1 and len(store.list_prints(mid)) == 1


def test_finalize_imports_flameshot_prints(store, settings, tmp_path):
    folder = tmp_path / "Imagens"
    folder.mkdir()
    settings.update({"print_mode": "flameshot", "print_folder": str(folder)})
    seen = []
    rig = Rig(store, settings, capture=_never_capture,
              on_ready=lambda mid: seen.append(len(store.list_prints(mid))))
    mid = rig.start()
    started = rig.manager.started_at
    shot = folder / "flameshot.png"
    shot.write_bytes(PNG_BYTES)
    ts = (started + timedelta(seconds=0.5)).timestamp()
    os.utime(shot, (ts, ts))
    rig.advance(1.0)
    rig.manager.stop()
    assert rig.manager.wait_idle(10)
    [p] = store.list_prints(mid)
    assert p.source == "flameshot" and p.origin_name == "flameshot.png"
    assert p.at_s == pytest.approx(0.5, abs=0.01)
    assert shot.exists()  # o original do Flameshot fica intacto
    assert seen == [1]  # importado antes de avisar on_ready
    assert store.get_meeting(mid).status == "ready"


def test_finalize_ignores_missing_print_folder(store, settings, tmp_path):
    folder = tmp_path / "Imagens"
    folder.mkdir()
    settings.update({"print_mode": "flameshot", "print_folder": str(folder)})
    folder.rmdir()
    rig = Rig(store, settings, capture=_never_capture)
    mid = _finish_meeting(rig)
    assert store.get_meeting(mid).status == "ready"
    assert store.list_prints(mid) == []


def test_tick_records_clock_marks(store, settings):
    clock = FakeClock()
    t0_wall = datetime(2026, 10, 27, 9, 0, 0)
    rig = Rig(store, settings, clock=clock,
              now=lambda: t0_wall + timedelta(seconds=clock.t - 1000.0))
    rig.start()
    rig.advance(25.0)
    marks = rig.manager._session.clock._marks
    assert [rel for rel, _ in marks] == [0.0, 10.0, 20.0]
    assert [wall for _, wall in marks] == [
        t0_wall, t0_wall + timedelta(seconds=10), t0_wall + timedelta(seconds=20)]


def test_tick_marks_clock_right_after_resume(store, settings):
    clock, suspended = FakeClock(), [0.0]
    t0_wall = datetime(2026, 10, 27, 9, 0, 0)
    rig = Rig(store, settings, clock=clock,
              now=lambda: t0_wall + timedelta(seconds=clock.t - 1000.0 + suspended[0]))
    rig.start()
    rig.advance(25.0)  # marcas em 10 e 20 s
    suspended[0] = 3600.0  # 1 h suspenso: o relógio monotônico parou, o de parede não
    rig.advance(0.5)  # primeira rodada após retomar
    marks = rig.manager._session.clock
    # print do Flameshot 2 s após retomar (rel. 27 s), antes da próxima marca regular
    assert marks.to_rel(t0_wall + timedelta(seconds=25.0 + 3600.0 + 2.0)) == 27.0
