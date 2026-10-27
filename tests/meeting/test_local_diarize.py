import gc
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time

import numpy as np
import pytest

from whisper_ditado.meeting.local_diarize import (
    DEFAULT_CLUSTER_THRESHOLD,
    DEFAULT_EMBEDDING,
    MIN_SPEAKER_S,
    RETRY_AFTER_S,
    LocalDiarizer,
    _ChildProcess,
    _Sherpa,
    default_threads,
    terminate_children,
)

SR = 16000


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeBackend:
    """Trechos fixos; `embed_chunk` devolve o vetor pela amplitude do pedaço."""

    def __init__(self, segments, vectors_by_amp: dict[float, list[float]] | None = None):
        self._segments = segments
        self.vectors_by_amp = vectors_by_amp or {}
        self.embedded: list[float] = []

    def segments(self, audio):
        return list(self._segments)

    def embed_chunk(self, chunk):
        amp = round(float(np.max(np.abs(chunk))), 3)
        self.embedded.append(amp)
        return np.asarray(self.vectors_by_amp.get(amp, [0.0, 0.0]), dtype=np.float32)


def _audio(segments, amp_by_cluster: dict[int, float]) -> np.ndarray:
    """Áudio com cada trecho na amplitude do seu cluster (silêncio no resto)."""
    total = max(end for _, end, _ in segments)
    audio = np.zeros(round(total * SR), dtype=np.float32)
    for start, end, cluster in segments:
        audio[round(start * SR):round(end * SR)] = amp_by_cluster[cluster]
    return audio


def _diarizer(backend, **kwargs) -> tuple[LocalDiarizer, list[int]]:
    calls: list[int] = []

    def loader():
        calls.append(1)
        return backend

    return LocalDiarizer(loader=loader, **kwargs), calls


def test_defaults():
    assert DEFAULT_EMBEDDING == "nemo_en_titanet_small"
    assert DEFAULT_CLUSTER_THRESHOLD == 1.0
    assert MIN_SPEAKER_S == 12.0
    assert RETRY_AFTER_S == 300.0
    assert LocalDiarizer.engine == "local"
    assert 1 <= default_threads() <= 8


def test_run_labels_by_first_appearance_and_tags_model():
    segs = [(0.0, 10.0, 1), (10.0, 20.0, 0), (20.0, 30.0, 1), (30.0, 40.0, 0)]
    backend = FakeBackend(segs, {0.2: [1.0, 0.0], 0.4: [0.0, 1.0]})
    diarizer, _ = _diarizer(backend)
    result = diarizer.run(_audio(segs, {1: 0.2, 0: 0.4}))

    assert [(t.start_s, t.end_s, t.speaker) for t in result.turns] == [
        (0.0, 10.0, "SPEAKER_00"),
        (10.0, 20.0, "SPEAKER_01"),
        (20.0, 30.0, "SPEAKER_00"),
        (30.0, 40.0, "SPEAKER_01"),
    ]
    assert result.model == "sherpa-nemo_en_titanet_small"
    assert diarizer.model_id == result.model
    assert result.engine == "local"
    assert result.embed is not None
    np.testing.assert_allclose(result.embeddings["SPEAKER_00"], [1.0, 0.0])
    np.testing.assert_allclose(result.embeddings["SPEAKER_01"], [0.0, 1.0])
    assert result.embeddings["SPEAKER_00"].dtype == np.float32


def test_run_merges_small_clusters_into_most_similar():
    # A (30 s), B (30 s) e C (2 s, parecido com B)
    segs = [(0.0, 30.0, 0), (30.0, 60.0, 1), (60.0, 62.0, 2)]
    backend = FakeBackend(segs, {0.2: [1.0, 0.0], 0.4: [0.0, 1.0], 0.6: [0.1, 0.9]})
    diarizer, _ = _diarizer(backend)
    result = diarizer.run(_audio(segs, {0: 0.2, 1: 0.4, 2: 0.6}))

    assert [t.speaker for t in result.turns] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_01"]
    assert len({t.speaker for t in result.turns}) == 2
    assert set(result.embeddings) == {"SPEAKER_00", "SPEAKER_01"}
    # o embedding final de B inclui o trecho de C
    b = result.embeddings["SPEAKER_01"]
    assert b[0] > 0 and b[1] > b[0]
    np.testing.assert_allclose(np.linalg.norm(b), 1.0, rtol=1e-5)


def test_run_single_voice_split_into_tiny_clusters_becomes_one_speaker():
    segs = [(0.0, 3.0, 0), (3.0, 5.0, 1), (5.0, 6.0, 2)]
    backend = FakeBackend(segs, {0.2: [1.0, 0.0], 0.4: [0.9, 0.1], 0.6: [0.0, 1.0]})
    diarizer, _ = _diarizer(backend)
    result = diarizer.run(_audio(segs, {0: 0.2, 1: 0.4, 2: 0.6}))

    assert [(t.start_s, t.end_s, t.speaker) for t in result.turns] == [
        (0.0, 3.0, "SPEAKER_00"),
        (3.0, 5.0, "SPEAKER_00"),
        (5.0, 6.0, "SPEAKER_00"),
    ]
    assert set(result.embeddings) == {"SPEAKER_00"}


def test_run_without_embeddings_merges_into_longest():
    segs = [(0.0, 14.0, 0), (14.0, 30.0, 1), (30.0, 32.0, 2)]
    backend = FakeBackend(segs, {})  # nenhum vetor utilizável
    diarizer, _ = _diarizer(backend, min_speaker_s=12.0)
    result = diarizer.run(_audio(segs, {0: 0.2, 1: 0.4, 2: 0.6}))

    assert [t.speaker for t in result.turns] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_01"]
    assert result.embeddings == {}


def test_run_empty_audio_and_unavailable():
    diarizer, _ = _diarizer(FakeBackend([]))
    result = diarizer.run(np.zeros(0, dtype=np.float32))
    assert result.turns == []
    assert result.model == "sherpa-nemo_en_titanet_small"
    assert result.engine == "local"
    assert result.embed is not None

    def broken():
        raise OSError("sem rede para baixar o modelo")

    diarizer = LocalDiarizer(loader=broken)
    assert diarizer.last_error is None
    assert diarizer.available is False
    assert "sem rede para baixar o modelo" in diarizer.last_error
    with pytest.raises(RuntimeError, match="separação local indisponível"):
        diarizer.run(np.ones(SR, dtype=np.float32))


def test_load_failure_retries_after_delay():
    clock = FakeClock()
    calls: list[int] = []

    def loader():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("falha temporária")
        return FakeBackend([])

    diarizer = LocalDiarizer(loader=loader, clock=clock)
    assert diarizer.available is False
    clock.t += 10
    assert diarizer.available is False
    assert len(calls) == 1  # dentro do intervalo: não tenta de novo
    clock.t += 291  # +301 s desde a falha
    assert diarizer.available is True
    assert diarizer.last_error is None
    assert len(calls) == 2


def test_embed_uses_speech_chunks_and_unload_releases():
    backend = FakeBackend([], {0.5: [0.0, 3.0]})
    diarizer, calls = _diarizer(backend)
    audio = np.full(SR * 4, 0.5, dtype=np.float32)

    assert diarizer.embed(audio, [(0.0, 0.3)]) is None  # curto demais para virar voz
    vec = diarizer.embed(audio, [(0.0, 2.0)])
    np.testing.assert_allclose(vec, [0.0, 1.0])
    assert len(calls) == 1

    diarizer.unload()
    assert diarizer.available is True
    assert len(calls) == 2


def test_available_does_not_wait_for_a_running_run():
    started, release = threading.Event(), threading.Event()

    class BlockingBackend(FakeBackend):
        def segments(self, audio):
            started.set()
            assert release.wait(5)
            return []

    diarizer, _ = _diarizer(BlockingBackend([]))
    assert diarizer.available is True  # carregado antes da separação
    worker = threading.Thread(target=diarizer.run, args=(np.ones(SR, dtype=np.float32),))
    worker.start()
    try:
        assert started.wait(5)
        answer: list[bool] = []
        probe = threading.Thread(target=lambda: answer.append(diarizer.available))
        probe.start()
        probe.join(1)
        assert answer == [True], "available ficou esperando o run terminar"
    finally:
        release.set()
        worker.join(5)


def _gone(pid: int) -> bool:
    """O processo não existe mais ou já é zumbi (morreu e só falta o pai recolher)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read()
    except (FileNotFoundError, ProcessLookupError):
        return True
    return stat.rsplit(")", 1)[1].split()[0] == "Z"  # o nome do processo pode ter espaços


def _wait_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while not _gone(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def test_child_process_is_spawned_persistent_and_recreated_after_dying():
    child = _ChildProcess()
    try:
        pid = child.run(os.getpid)
        assert pid != os.getpid()
        assert child._pool._mp_context.get_start_method() == "spawn"  # nunca fork
        assert child.run(os.getpid) == pid  # o mesmo filho atende as chamadas seguintes

        with pytest.raises(ValueError):  # exceção vinda do filho sobe como veio
            child.run(int, "não é número")
        assert child.run(os.getpid) == pid

        with pytest.raises(RuntimeError, match="terminou inesperadamente"):
            child.run(os._exit, 1)  # o filho morre: BrokenProcessPool
        assert _wait_gone(pid)
        assert child.run(os.getpid) not in (pid, os.getpid())  # recriado, não fica quebrado
    finally:
        child.close()


def test_unload_stops_the_child_process():
    child = _ChildProcess()
    diarizer = LocalDiarizer(loader=lambda: _Sherpa(child, extractor=None))
    assert diarizer.available
    pid = child.run(os.getpid)

    diarizer.unload()
    assert _wait_gone(pid), "o processo filho continuou vivo depois do unload"
    assert child._pool is None


def test_sigint_does_not_kill_the_child_process():
    # Ctrl+C no terminal manda SIGINT ao grupo todo; o desligamento é decidido pelo pai
    child = _ChildProcess()
    try:
        pid = child.run(os.getpid)  # depois do initializer
        os.kill(pid, signal.SIGINT)
        time.sleep(0.3)
        assert child.run(os.getpid) == pid
    finally:
        child.close()


def test_dropping_an_unclosed_backend_ends_its_child():
    child = _ChildProcess()
    backend = _Sherpa(child, extractor=None)
    pid = child.run(os.getpid)

    del backend, child
    gc.collect()
    assert _wait_gone(pid), "o filho de um backend largado sem close() continuou vivo"


def test_terminate_children_kills_a_busy_child_at_once():
    child = _ChildProcess()
    pid = child.run(os.getpid)
    errors: list[BaseException] = []

    def busy():
        try:
            child.run(time.sleep, 60)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=busy)
    worker.start()
    time.sleep(0.3)
    terminate_children()
    worker.join(10)
    assert not worker.is_alive()
    assert _wait_gone(pid)
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    assert child.run(os.getpid) != pid  # e o próximo uso cria outro filho
    child.close()


_EXIT_SCRIPT = """
import os, sys, threading, time
from whisper_ditado.meeting.local_diarize import _ChildProcess, terminate_children

child = _ChildProcess()
with open(sys.argv[2], "w") as f:
    f.write(str(child.run(os.getpid)))
if sys.argv[1] == "busy":  # um trabalho longo em andamento no filho na hora da saída
    threading.Thread(target=child.run, args=(time.sleep, 60), daemon=True).start()
    time.sleep(0.5)
elif sys.argv[1] == "hard":  # o backend sai por os._exit, sem finalizar o pool
    os._exit(0)
elif sys.argv[1] == "hard-busy":  # como o `main._hard_exit`, com o filho preso no GIL dele
    threading.Thread(target=child.run, args=(pow, 7, 10**8), daemon=True).start()
    time.sleep(0.5)
    terminate_children()
    os._exit(0)
"""


@pytest.mark.parametrize("mode", ["idle", "busy", "hard", "hard-busy"])
def test_child_process_ends_with_the_parent(tmp_path, mode):
    pid_file, err_file = tmp_path / "child.pid", tmp_path / "stderr"
    # saídas em arquivo, não em pipe: um filho órfão herdaria o pipe e seguraria a leitura
    with err_file.open("wb") as err:
        proc = subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(_EXIT_SCRIPT), mode, str(pid_file)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
        )
    child_pid = None
    try:
        proc.wait(timeout=30)
        assert proc.returncode == 0, err_file.read_text()
        child_pid = int(pid_file.read_text())
        assert _wait_gone(child_pid), f"processo filho órfão ({mode})"
    finally:
        if proc.poll() is None:
            proc.kill()
        if child_pid is not None and not _wait_gone(child_pid, 0):
            os.kill(child_pid, signal.SIGKILL)


@pytest.fixture(scope="module")
def models_home(tmp_path_factory):
    """XDG_DATA_HOME temporário compartilhado pelos testes do módulo: baixa os modelos uma vez."""
    return tmp_path_factory.mktemp("xdg-data")


def _two_voices(utterances: int) -> tuple[np.ndarray, list[tuple[float, float, int]]]:
    """Falas alternadas de duas vozes do espeak, com 0,8 s de silêncio entre elas."""
    from tests.helpers.tts import synth

    gap = np.zeros(int(0.8 * SR), dtype=np.float32)
    texts = [
        "Bom dia pessoal, vamos começar a reunião de hoje.",
        "Obrigada, eu preparei o relatório das vendas do mês.",
    ]
    voices = [synth(texts[0], voice="pt-br"), synth(texts[1], voice="pt-br+f3")]
    chunks, expected = [], []
    t = 0.0
    for i in range(utterances):
        audio = voices[i % 2]
        chunks += [audio, gap]
        expected.append((t, t + len(audio) / SR, i % 2))
        t += len(audio) / SR + 0.8
    return np.concatenate(chunks), expected


@pytest.mark.models
def test_local_diarizer_separates_two_synthetic_voices(models_home, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(models_home))
    # As vozes do espeak são sintéticas e bem mais próximas que vozes reais (cosseno ~0.65): o teste
    # fixa o próprio threshold de distância em vez de depender do default, calibrado em reuniões reais.
    diarizer = LocalDiarizer(cluster_threshold=0.2)
    assert diarizer.available, diarizer.last_error

    audio, expected = _two_voices(8)
    try:
        result = diarizer.run(audio)
    finally:
        diarizer.unload()

    assert result.engine == "local"
    assert len({turn.speaker for turn in result.turns}) >= 2
    assert set(result.embeddings) <= {turn.speaker for turn in result.turns}
    # no meio de cada fala, o falante deve ser o mesmo para as falas de mesma voz
    by_voice: dict[int, set[str]] = {0: set(), 1: set()}
    for start, end, voice in expected:
        mid = (start + end) / 2
        hit = [turn.speaker for turn in result.turns if turn.start_s <= mid <= turn.end_s]
        assert hit, f"sem turno em {mid:.1f}s"
        by_voice[voice].update(hit)
    assert by_voice[0] and by_voice[1]
    assert by_voice[0].isdisjoint(by_voice[1])


@pytest.mark.models
def test_run_does_not_hold_the_gil(models_home, monkeypatch):
    """A separação não pode parar as outras threads do backend (API, ditado) enquanto roda."""
    monkeypatch.setenv("XDG_DATA_HOME", str(models_home))
    diarizer = LocalDiarizer(cluster_threshold=0.2)
    assert diarizer.available, diarizer.last_error
    audio, _ = _two_voices(24)  # ~95 s
    assert 60 * SR <= len(audio) <= 120 * SR

    gaps: list[float] = []
    stop = threading.Event()

    def tick():
        last = time.perf_counter()
        while not stop.is_set():
            time.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    ticker = threading.Thread(target=tick)
    ticker.start()
    try:
        result = diarizer.run(audio)
    finally:
        stop.set()
        ticker.join(5)
        diarizer.unload()

    assert result.turns
    assert max(gaps) < 0.5, f"outra thread ficou {max(gaps):.2f}s parada durante a separação"
