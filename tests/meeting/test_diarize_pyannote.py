import os

import pytest

pytest.importorskip("pyannote.audio")

import numpy as np  # noqa: E402

from whisper_ditado.meeting.diarize import PIPELINE_ID, DiarizationResult, Diarizer  # noqa: E402


def _saved_token() -> str | None:
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    try:
        from whisper_ditado import paths
        from whisper_ditado.settings import Settings
        from whisper_ditado.store import Store

        if paths.db_path().exists():
            return Settings(Store(paths.db_path())).get("hf_token")
    except Exception:
        pass
    return None


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakePipeline:
    def __init__(self, output=None):
        self.output = output
        self.device = None
        self.calls = []

    def to(self, device):
        self.device = device
        return self

    def __call__(self, file):
        self.calls.append(file)
        return self.output


@pytest.fixture
def hub(monkeypatch):
    """Cache local e download falsos (nada de rede nem de GPU)."""
    import huggingface_hub
    from huggingface_hub.errors import LocalEntryNotFoundError
    from pyannote.audio import Pipeline

    state = {"cached": None, "fail": None, "loads": []}

    def snapshot_download(repo_id, local_files_only=False, **kwargs):
        assert local_files_only, "o cache local não pode ir para a rede"
        state["loads"].append(("snapshot", repo_id))
        if state["cached"] is None:
            raise LocalEntryNotFoundError("fora do cache")
        return state["cached"]

    def from_pretrained(checkpoint, token=None, **kwargs):
        state["loads"].append(("pipeline", str(checkpoint), token))
        if state["fail"] is not None:
            raise state["fail"]
        return FakePipeline()

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)
    monkeypatch.setattr(Pipeline, "from_pretrained", from_pretrained)
    return state


@pytest.mark.filterwarnings(r"ignore:(?s).*torchcodec:UserWarning")
def test_diarizer_loads_from_local_cache_first(hub):
    hub["cached"] = "/cache/community-1"
    diarizer = Diarizer("tok")
    assert diarizer.available is True
    assert hub["loads"] == [("snapshot", PIPELINE_ID), ("pipeline", "/cache/community-1", None)]


@pytest.mark.filterwarnings(r"ignore:(?s).*torchcodec:UserWarning")
def test_diarizer_downloads_with_token_when_not_cached(hub):
    diarizer = Diarizer("tok")
    assert diarizer.available is True
    assert hub["loads"] == [("snapshot", PIPELINE_ID), ("pipeline", PIPELINE_ID, "tok")]


@pytest.mark.filterwarnings(r"ignore:(?s).*torchcodec:UserWarning")
def test_diarizer_unavailable_when_loading_fails_then_retries(hub):
    hub["fail"] = OSError("termos do modelo não aceitos")
    clock = FakeClock()
    diarizer = Diarizer("tok", clock=clock)

    assert diarizer.available is False
    attempts = len(hub["loads"])
    assert diarizer.available is False  # logo em seguida: não tenta de novo
    assert len(hub["loads"]) == attempts
    with pytest.raises(RuntimeError):
        diarizer.run(np.zeros(16000, dtype=np.float32))

    # termos aceitos depois: com o mesmo token, tenta de novo passado o intervalo
    hub["fail"] = None
    clock.t += Diarizer.RETRY_AFTER_S - 1
    assert diarizer.available is False
    clock.t += 1
    assert diarizer.available is True


@pytest.mark.filterwarnings(r"ignore:(?s).*torchcodec:UserWarning")
def test_diarizer_retries_immediately_with_a_new_token(hub):
    hub["fail"] = OSError("token inválido")
    diarizer = Diarizer("velho", clock=FakeClock())
    assert diarizer.available is False
    hub["fail"] = None
    diarizer.hf_token = "novo"
    assert diarizer.available is True
    assert hub["loads"][-1] == ("pipeline", PIPELINE_ID, "novo")


def test_settings_diarizer_recovers_after_token_change(hub, tmp_path, monkeypatch):
    from whisper_ditado import main
    from whisper_ditado.settings import Settings
    from whisper_ditado.store import Store

    monkeypatch.delenv("HF_TOKEN", raising=False)
    store = Store(tmp_path / "x.db")
    settings = Settings(store)
    settings.update({"hf_token": "sem-termos"})
    diarizer = main._SettingsDiarizer(settings)
    hub["fail"] = OSError("termos não aceitos")
    assert diarizer.available is False
    hub["fail"] = None
    settings.update({"hf_token": "outro"})  # vale na próxima reunião, sem reiniciar
    assert diarizer.available is True
    store.close()


def _output(spans, exclusive, embeddings):
    from pyannote.audio.pipelines.speaker_diarization import DiarizeOutput
    from pyannote.core import Annotation, Segment

    full, excl = Annotation(), Annotation()
    for start, end, label in spans:
        full[Segment(start, end)] = label
    for start, end, label in exclusive:
        excl[Segment(start, end)] = label
    return DiarizeOutput(full, excl, embeddings)


def _with_pipeline(output) -> tuple[Diarizer, FakePipeline]:
    diarizer = Diarizer("tok")
    diarizer._pipeline = FakePipeline(output)
    return diarizer, diarizer._pipeline


def test_run_maps_exclusive_turns_and_embeddings_by_label_order():
    # rótulos em ordem de labels() (alfabética), não na ordem de aparição
    emb = np.array([[1, 0, 0], [0, 2, 0], [0, 0, 3]], dtype=np.float64)
    output = _output(
        spans=[(0, 5, "SPEAKER_01"), (4, 9, "SPEAKER_00"), (9, 10, "SPEAKER_02")],
        exclusive=[(0, 4.5, "SPEAKER_01"), (4.5, 9, "SPEAKER_00")],  # SPEAKER_02 só sobreposto
        embeddings=emb,
    )
    diarizer, pipeline = _with_pipeline(output)
    result = diarizer.run(np.ones(16000 * 10, dtype=np.float32) * 0.1)

    assert [(t.start_s, t.end_s, t.speaker) for t in result.turns] == [
        (0.0, 4.5, "SPEAKER_01"), (4.5, 9.0, "SPEAKER_00")]
    assert set(result.embeddings) == {"SPEAKER_00", "SPEAKER_01"}  # SPEAKER_02 fora do exclusivo
    np.testing.assert_array_equal(result.embeddings["SPEAKER_00"], [1, 0, 0])
    np.testing.assert_array_equal(result.embeddings["SPEAKER_01"], [0, 2, 0])
    assert result.embeddings["SPEAKER_00"].dtype == np.float32
    (call,) = pipeline.calls
    assert call["sample_rate"] == 16000 and tuple(call["waveform"].shape) == (1, 160000)


def test_run_drops_zero_padded_and_non_finite_embeddings():
    emb = np.array([[0.5, 0.5], [0.0, 0.0], [np.nan, 1.0]])
    output = _output(
        spans=[(0, 2, "A"), (2, 4, "B"), (4, 6, "C")],
        exclusive=[(0, 2, "A"), (2, 4, "B"), (4, 6, "C")],
        embeddings=emb,
    )
    diarizer, _ = _with_pipeline(output)
    result = diarizer.run(np.ones(16000 * 6, dtype=np.float32))
    assert [t.speaker for t in result.turns] == ["A", "B", "C"]
    assert set(result.embeddings) == {"A"}  # B (centróide zerado) e C (NaN) ficam sem voz


def test_run_on_silence_returns_empty_result():
    output = _output(spans=[], exclusive=[], embeddings=np.zeros((0, 256)))
    diarizer, _ = _with_pipeline(output)
    assert diarizer.run(np.zeros(16000 * 3, dtype=np.float32)) == DiarizationResult([], {})
    diarizer, _ = _with_pipeline(_output([], [], None))
    assert diarizer.run(np.zeros(16000, dtype=np.float32)) == DiarizationResult([], {})


@pytest.mark.gpu
def test_diarizer_separates_two_synthetic_voices():
    token = _saved_token()
    if not token:
        pytest.skip("sem token do Hugging Face (HF_TOKEN ou hf_token salvo)")
    from tests.helpers.tts import synth

    diarizer = Diarizer(token)
    if not diarizer.available:
        pytest.skip("pipeline pyannote indisponível (termos do modelo não aceitos?)")

    sr = 16000
    gap = np.zeros(int(0.8 * sr), dtype=np.float32)
    texts = [
        "Bom dia pessoal, vamos começar a reunião de hoje.",
        "Obrigada, eu preparei o relatório das vendas do mês.",
    ]
    chunks, expected = [], []
    t = 0.0
    for i in range(8):
        voice = "pt-br" if i % 2 == 0 else "pt-br+f3"
        audio = synth(texts[i % 2], voice=voice)
        chunks += [audio, gap]
        expected.append((t, t + len(audio) / sr, i % 2))
        t += len(audio) / sr + 0.8
    result = diarizer.run(np.concatenate(chunks))

    assert len({turn.speaker for turn in result.turns}) >= 2
    assert set(result.embeddings) <= {turn.speaker for turn in result.turns}
    # no meio de cada fala, o falante deve ser o mesmo para as falas de mesma voz
    by_voice: dict[int, set[str]] = {0: set(), 1: set()}
    for start, end, voice in expected:
        mid = (start + end) / 2
        hit = [turn.speaker for turn in result.turns if turn.start_s - 1 <= mid <= turn.end_s + 1]
        assert hit, f"sem turno perto de {mid:.1f}s"
        by_voice[voice].add(hit[0])
    assert by_voice[0] != by_voice[1]
