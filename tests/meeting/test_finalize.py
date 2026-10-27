import dataclasses
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from whisper_ditado import paths
from whisper_ditado.meeting import finalize as fin
from whisper_ditado.meeting.diarize import DiarizationResult, Diarizer
from whisper_ditado.meeting.timeline import Turn
from whisper_ditado.settings import Settings
from whisper_ditado.store import Store, Voice, VoiceEmbedding
from whisper_ditado.transcriber import Word

SR = 16000
MIC_AMP = 0.1
PC_AMP = 0.2
STARTED = datetime(2026, 10, 19, 14, 5)


# -- fakes compartilhados (também usados em test_session) ----------------------


class FakeVocab:
    def __init__(self, subs: dict[str, str] | None = None) -> None:
        self.subs = subs or {}

    def apply(self, text: str) -> str:
        for src, dst in self.subs.items():
            text = text.replace(src, dst)
        return text


class FakeTranscriber:
    """Devolve as palavras da trilha identificada pela amplitude do áudio (MIC_AMP/PC_AMP)."""

    def __init__(self, by_amp: dict[float, list[Word]] | None = None, default=None, subs=None,
                 sequence: list[list[Word]] | None = None):
        self.by_amp = by_amp or {}
        self.default = default or []
        self.sequence = sequence  # uma resposta por chamada, em ordem (ignora o áudio)
        self.vocab = FakeVocab(subs)
        self.calls: list[tuple[float, float]] = []

    def transcribe_words(self, audio, offset_s=0.0):
        self.calls.append((offset_s, len(audio) / SR))
        if self.sequence is not None:
            return self.sequence[len(self.calls) - 1]
        if len(audio) == 0:
            return []
        amp = round(float(np.abs(audio).max()), 2)
        found = self.by_amp.get(amp, self.default)
        end = offset_s + len(audio) / SR
        return [w for w in found if offset_s <= w.start_s < end]


class FakeDiarizer:
    """Devolve `result`, carimbando `model`/`engine` quando ele vier sem esses campos."""

    def __init__(self, result: DiarizationResult | None = None, available: bool = True,
                 model: str = "fake-model", engine: str = "local"):
        self.result = result or DiarizationResult()
        self._available = available
        self.model = model
        self.engine = engine
        self.calls = 0

    @property
    def available(self) -> bool:
        return self._available

    def run(self, audio):
        self.calls += 1
        return dataclasses.replace(self.result, model=self.result.model or self.model,
                                   engine=self.result.engine or self.engine)


def w(start: float, text: str, dur: float = 0.4) -> Word:
    return Word(start, start + dur, text, 0.9)


def write_raw(path: Path, seconds: float, amp: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = int(amp * 32768)
    path.write_bytes(np.full(int(seconds * SR), value, dtype=np.int16).tobytes())
    return path


# -- fixtures --------------------------------------------------------------------


@pytest.fixture
def store(data_home):
    s = Store(paths.db_path())
    yield s
    s.close()


@pytest.fixture
def settings(store):
    return Settings(store)


def new_meeting(store, *, mic_s=20.0, pc_s=20.0, pc_amp=PC_AMP, status="recording"):
    m = store.create_meeting(STARTED)
    d = paths.meeting_dir(m.id)
    if mic_s is not None:
        write_raw(d / "mic.raw", mic_s, MIC_AMP)
    if pc_s is not None:
        write_raw(d / "pc.raw", pc_s, pc_amp)
    if status != "recording":
        store.update_meeting(m.id, status=status)
    return m.id, d


def labels(store, mid):
    return {sp.id: sp.label for sp in store.get_speakers(mid)}


# -- testes ----------------------------------------------------------------------


def test_finalize_builds_transcript_with_me_and_people(store, settings):
    mid, d = new_meeting(store, status="processing")
    e1, e2, e3 = (np.eye(4, dtype=np.float32)[i] for i in range(3))
    joao = store.upsert_voice(Voice(id="v-joao", name="João",
                                    embeddings={"fake-model": VoiceEmbedding(e3 * 0.9 + 0.01)}))
    store.upsert_voice(Voice(id="v-ana", name="Ana", embeddings={
        "fake-model": VoiceEmbedding(np.array([0, 0, 0, 1], np.float32))}))
    words = {
        "mic": [w(0.5, "bom"), w(1.0, "dia")],
        "pc": [w(3.0, "oi"), w(3.5, "gente"), w(6.0, "tudo"), w(6.5, "certo"),
               w(9.0, "beleza"), w(12.0, "valeu")],
    }
    turns = [Turn(2.8, 4.2, "SPEAKER_01"), Turn(5.8, 7.2, "SPEAKER_00"),
             Turn(8.8, 9.6, "SPEAKER_02"), Turn(11.9, 12.5, "SPEAKER_01")]
    diarizer = FakeDiarizer(DiarizationResult(
        turns, {"SPEAKER_00": e2, "SPEAKER_01": e1, "SPEAKER_02": e3}))
    transcriber = FakeTranscriber(subs={"oi": "olá"})
    stages = []

    fin.finalize(mid, store, settings, transcriber, diarizer, words, None, on_progress=stages.append)

    m = store.get_meeting(mid)
    assert m.status == "ready" and m.error is None
    assert m.title == "Reunião 14:05" and m.title_auto is True
    assert stages == ["transcrevendo", "separando vozes", "finalizando"]
    assert diarizer.calls == 1

    speakers = {sp.label: sp for sp in store.get_speakers(mid)}
    assert set(speakers) == {"Você", "Pessoa 1", "Pessoa 2", "João"}
    me = speakers["Você"]
    assert me.is_me and me.color_index == 0 and me.embedding is None
    # "Pessoa N" e cores na ordem de primeira fala do PC
    assert speakers["Pessoa 1"].color_index == 1 and speakers["Pessoa 2"].color_index == 2
    assert speakers["João"].color_index == 3 and speakers["João"].voice_id == joao.id
    np.testing.assert_allclose(speakers["Pessoa 1"].embedding, e1)
    np.testing.assert_allclose(speakers["Pessoa 2"].embedding, e2)
    assert all(not sp.is_me and sp.voice_id is None for k, sp in speakers.items()
               if k.startswith("Pessoa"))

    names = labels(store, mid)
    got = [(names[s.speaker_id], s.source, s.text) for s in store.get_segments(mid)]
    assert got == [
        ("Você", "mic", "bom dia"),
        ("Pessoa 1", "pc", "olá gente"),  # substituição do vocabulário aplicada
        ("Pessoa 2", "pc", "tudo certo"),
        ("João", "pc", "beleza"),
        ("Pessoa 1", "pc", "valeu"),
    ]
    # áudio compactado e .raw apagado só depois da conversão
    for name in ("mic", "pc"):
        assert (d / f"{name}.opus").stat().st_size > 0
        assert not (d / f"{name}.raw").exists()
    assert not list(d.glob("*.part"))


def _two_speaker_case(store, pc_s=20.0):
    mid, _ = new_meeting(store, pc_s=pc_s)
    e1, e2 = np.eye(3, dtype=np.float32)[0], np.eye(3, dtype=np.float32)[1]
    store.upsert_voice(Voice(id="v-ana", name="Ana", embeddings={"fake-model": VoiceEmbedding(e1)}))
    words = {"mic": [w(0.5, "oi")], "pc": [w(3.0, "bom"), w(6.0, "dia")]}
    result = DiarizationResult([Turn(2.8, 3.6, "A"), Turn(5.8, 6.6, "B")], {"A": e1, "B": e2})
    return mid, words, result


def test_finalize_tags_speaker_embeddings_with_model(store, settings):
    mid, words, result = _two_speaker_case(store)

    fin.finalize(mid, store, settings, FakeTranscriber(), FakeDiarizer(result), words, None)

    speakers = {sp.label: sp for sp in store.get_speakers(mid)}
    assert set(speakers) == {"Você", "Ana", "Pessoa 1"}
    assert speakers["Ana"].voice_id == "v-ana"
    for label in ("Ana", "Pessoa 1"):
        assert speakers[label].embedding is not None
        assert speakers[label].embedding_model == "fake-model"
    assert speakers["Você"].embedding is None and speakers["Você"].embedding_model is None


def test_finalize_skips_match_and_embedding_when_model_unknown(store, settings):
    mid, words, result = _two_speaker_case(store)

    fin.finalize(mid, store, settings, FakeTranscriber(), FakeDiarizer(result, model=""), words, None)

    assert store.get_meeting(mid).status == "ready"
    speakers = store.get_speakers(mid)
    assert sorted(sp.label for sp in speakers) == ["Pessoa 1", "Pessoa 2", "Você"]
    assert all(sp.voice_id is None for sp in speakers)
    assert all(sp.embedding is None and sp.embedding_model is None for sp in speakers)


def test_finalize_unknown_engine_keeps_embedding_but_skips_match(store, settings, caplog):
    mid, words, result = _two_speaker_case(store)

    with caplog.at_level(logging.WARNING, logger="ditado"):
        fin.finalize(mid, store, settings, FakeTranscriber(), FakeDiarizer(result, engine="gpu"),
                     words, None)

    assert store.get_meeting(mid).status == "ready"
    speakers = [sp for sp in store.get_speakers(mid) if not sp.is_me]
    assert all(sp.voice_id is None for sp in speakers)
    assert all(sp.embedding_model == "fake-model" for sp in speakers)
    assert any("gpu" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("engine, local, pyannote, matched", [
    ("local", 0.6, 0.9, True),
    ("local", 0.8, 0.5, False),
    ("pyannote", 0.8, 0.5, True),
    ("pyannote", 0.5, 0.8, False),
])
def test_finalize_uses_threshold_of_the_engine(store, settings, engine, local, pyannote, matched):
    mid, words, result = _two_speaker_case(store)
    # A fica com cosseno ~0.707 em relação à Ana; B é ortogonal
    result = dataclasses.replace(result, embeddings={"A": np.array([1, 0, 1], np.float32),
                                                    "B": np.eye(3, dtype=np.float32)[1]})
    settings.update({"voice_match_threshold_local": local, "voice_match_threshold_pyannote": pyannote})

    fin.finalize(mid, store, settings, FakeTranscriber(), FakeDiarizer(result, engine=engine),
                 words, None)

    voice_ids = {sp.voice_id for sp in store.get_speakers(mid)} - {None}
    assert voice_ids == ({"v-ana"} if matched else set())


def test_fallback_alert_replaces_previous_diarization_alerts(store, settings):
    mid, words, result = _two_speaker_case(store, pc_s=30.0)
    store.add_alert(mid, "no_diarization", 0.0, 30.0)
    store.add_alert(mid, "mic_silent", 5.0, 9.0)
    fallback = dataclasses.replace(result, fallback="pyannote_no_token")

    fin.reprocess(mid, store, settings, FakeTranscriber(default=words["pc"]),
                  FakeDiarizer(fallback), None)
    alerts = {(a.kind, a.start_s, a.end_s) for a in store.get_alerts(mid)}
    assert alerts == {("pyannote_no_token", 0.0, 30.0), ("mic_silent", 5.0, 9.0)}

    # troca do alerta de fallback por outro, sem acumular
    other = dataclasses.replace(result, fallback="pyannote_failed")
    fin.reprocess(mid, store, settings, FakeTranscriber(default=words["pc"]), FakeDiarizer(other), None)
    assert sorted(a.kind for a in store.get_alerts(mid)) == ["mic_silent", "pyannote_failed"]

    # reprocessar sem fallback não deixa alerta de diarização
    fin.reprocess(mid, store, settings, FakeTranscriber(default=words["pc"]), FakeDiarizer(result), None)
    assert [a.kind for a in store.get_alerts(mid)] == ["mic_silent"]


def test_failed_diarization_replaces_fallback_alert_with_no_diarization(store, settings):
    mid, words, _ = _two_speaker_case(store)
    store.add_alert(mid, "pyannote_no_token", 0.0, 20.0)

    fin.finalize(mid, store, settings, FakeTranscriber(), FakeDiarizer(available=False), words, None)

    assert [a.kind for a in store.get_alerts(mid)] == ["no_diarization"]


def test_fallback_uses_local_threshold_and_model(store, settings):
    mid, words, result = _two_speaker_case(store)
    # A fica com cosseno ~0.707 em relação à Ana
    result = dataclasses.replace(
        result, embeddings={"A": np.array([1, 0, 1], np.float32), "B": np.eye(3, dtype=np.float32)[1]},
        model="sherpa-modelo", engine="local", fallback="pyannote_failed")
    store.upsert_voice(Voice(id="v-ana2", name="Ana2", embeddings={
        "sherpa-modelo": VoiceEmbedding(np.eye(3, dtype=np.float32)[0])}))
    settings.update({"voice_match_threshold_local": 0.9, "voice_match_threshold_pyannote": 0.1})

    fin.finalize(mid, store, settings, FakeTranscriber(), FakeDiarizer(result), words, None)

    speakers = [sp for sp in store.get_speakers(mid) if not sp.is_me]
    assert all(sp.voice_id is None for sp in speakers)
    assert all(sp.embedding_model == "sherpa-modelo" for sp in speakers)


def test_finalize_words_without_turn_go_to_nearest_speaker(store, settings):
    mid, _ = new_meeting(store)
    words = {"mic": [], "pc": [w(1.0, "oi"), w(9.0, "solto")]}
    turns = [Turn(0.8, 1.6, "A"), Turn(4.0, 5.0, "B")]
    fin.finalize(mid, store, settings, FakeTranscriber(), FakeDiarizer(DiarizationResult(turns, {})),
                 words, None)
    names = labels(store, mid)
    got = [(names[s.speaker_id], s.text) for s in store.get_segments(mid)]
    assert got == [("Pessoa 1", "oi"), ("Pessoa 2", "solto")]
    assert "desconhecido" not in names.values()


def test_finalize_with_silent_pc_track(store, settings):
    mid, d = new_meeting(store, pc_amp=0.0)
    diarizer = FakeDiarizer(available=True)
    words = {"mic": [w(1.0, "sozinho"), w(1.5, "aqui")], "pc": []}

    fin.finalize(mid, store, settings, FakeTranscriber(), diarizer, words, None)

    m = store.get_meeting(mid)
    assert m.status == "ready"
    assert diarizer.calls == 0  # nada a separar: nem chama o pyannote
    speakers = store.get_speakers(mid)
    assert [sp.label for sp in speakers] == ["Você"]
    assert [s.text for s in store.get_segments(mid)] == ["sozinho aqui"]
    assert store.get_alerts(mid) == []
    assert (d / "pc.opus").exists() and not (d / "pc.raw").exists()


def test_finalize_without_hf_token_uses_single_speaker(store, settings):
    mid, _ = new_meeting(store, pc_s=30.0)
    words = {"mic": [w(1.0, "pergunta")], "pc": [w(3.0, "resposta"), w(8.0, "outra"), w(8.5, "fala")]}

    fin.finalize(mid, store, settings, FakeTranscriber(), Diarizer(None), words, None)

    m = store.get_meeting(mid)
    assert m.status == "ready"
    names = labels(store, mid)
    assert sorted(names.values()) == ["Participantes", "Você"]
    pc_segments = [s for s in store.get_segments(mid) if s.source == "pc"]
    assert {names[s.speaker_id] for s in pc_segments} == {"Participantes"}
    assert [s.text for s in pc_segments] == ["resposta", "outra fala"]
    alerts = store.get_alerts(mid)
    assert [(a.kind, a.start_s, a.end_s) for a in alerts] == [("no_diarization", 0.0, 30.0)]

    # reprocessar sem token não duplica o aviso
    fin.reprocess(mid, store, settings, FakeTranscriber(default=[w(3.0, "resposta")]),
                  Diarizer(None), None)
    assert [a.kind for a in store.get_alerts(mid)] == ["no_diarization"]


    # token configurado depois: o reprocessamento separa as vozes e o aviso velho sai
    turns = DiarizationResult([Turn(2.5, 4.0, "SPEAKER_00")], {})
    store.add_alert(mid, "mic_silent", 5.0, 9.0)
    fin.reprocess(mid, store, settings, FakeTranscriber(default=[w(3.0, "resposta")]),
                  FakeDiarizer(turns), None)
    assert store.get_meeting(mid).status == "ready"
    assert [a.kind for a in store.get_alerts(mid)] == ["mic_silent"]


def test_finalize_marks_dictated_segments(store, settings):
    mid, _ = new_meeting(store)
    store.add_dictation_span(mid, 0.9, 2.6)
    words = {"mic": [w(1.0, "texto"), w(1.6, "ditado"), w(10.0, "fala"), w(10.5, "normal")],
             "pc": []}
    seen = []

    def title_fn(transcript):
        seen.append(transcript)
        return "  Revisão do ditado  "

    fin.finalize(mid, store, settings, FakeTranscriber(), FakeDiarizer(), words, title_fn)

    segs = store.get_segments(mid)
    assert [(s.text, s.dictated) for s in segs] == [("texto ditado", True), ("fala normal", False)]
    m = store.get_meeting(mid)
    assert m.title == "Revisão do ditado" and m.title_auto is True
    assert seen == ["[00:01] Você: texto ditado (ditado)\n[00:10] Você: fala normal"]


def test_finalize_title_errors_and_auto_title_off_fall_back(store, settings):
    def boom(_):
        raise RuntimeError("Claude Code não está logado")

    mid, _ = new_meeting(store)
    fin.finalize(mid, store, settings, FakeTranscriber(), FakeDiarizer(),
                 {"mic": [w(1.0, "oi")], "pc": []}, boom)
    assert store.get_meeting(mid).title == "Reunião 14:05"
    assert store.get_meeting(mid).status == "ready"

    settings.update({"auto_title": False})
    called = []
    mid2, _ = new_meeting(store)
    fin.finalize(mid2, store, settings, FakeTranscriber(), FakeDiarizer(),
                 {"mic": [w(1.0, "oi")], "pc": []}, lambda t: called.append(t) or "X")
    assert store.get_meeting(mid2).title == "Reunião 14:05" and called == []


def test_finalize_failure_sets_failed_and_keeps_audio(store, settings):
    class Broken(FakeTranscriber):
        def transcribe_words(self, audio, offset_s=0.0):
            raise RuntimeError("CUDA out of memory")

    mid, d = new_meeting(store, status="processing")

    fin.finalize(mid, store, settings, Broken(), FakeDiarizer(), None, None)

    m = store.get_meeting(mid)
    assert m.status == "failed"
    assert m.error == "Falha ao processar a reunião: CUDA out of memory"
    assert (d / "mic.raw").exists() and (d / "pc.raw").exists()
    assert not (d / "mic.opus").exists() and not (d / "pc.opus").exists()


def test_startup_recovers_orphan_recording(store, settings):
    mid, d = new_meeting(store, mic_s=12.0, pc_s=12.0)  # ficou em 'recording' (backend caiu)
    orphan_processing, _ = new_meeting(store, status="processing")
    no_audio = store.create_meeting(STARTED).id
    ready = store.create_meeting(STARTED).id
    store.update_meeting(ready, status="ready")
    transcriber = FakeTranscriber({MIC_AMP: [w(1.0, "oi"), w(1.4, "pessoal")],
                                   PC_AMP: [w(4.0, "bom"), w(4.4, "dia")]})
    diarizer = FakeDiarizer(DiarizationResult([Turn(3.9, 5.0, "SPEAKER_00")], {}))
    loads, statuses = [], []

    def factory():
        loads.append(1)
        return transcriber

    recovered = fin.recover(store, settings, factory, diarizer, None,
                            on_status=lambda m, s: statuses.append((m, s)))

    assert sorted(recovered) == sorted([mid, orphan_processing])
    for meeting_id in (mid, orphan_processing):
        m = store.get_meeting(meeting_id)
        assert m.status == "ready"
        segs = store.get_segments(meeting_id)
        assert [s.text for s in segs] == ["oi pessoal", "bom dia"]
    m = store.get_meeting(mid)
    assert m.ended_at is not None and (m.ended_at - m.started_at).total_seconds() == 12.0
    assert (d / "mic.opus").exists() and not (d / "mic.raw").exists()
    # transcreveu cada trilha inteira de uma vez (words=None)
    assert sorted(transcriber.calls) == [(0.0, 12.0), (0.0, 12.0), (0.0, 20.0), (0.0, 20.0)]
    failed = store.get_meeting(no_audio)
    assert failed.status == "failed" and "áudio" in failed.error
    assert store.get_meeting(ready).status == "ready"
    assert (mid, "processing") in statuses and (mid, "ready") in statuses
    assert (no_audio, "failed") in statuses


def test_recover_without_orphans_does_not_load_model(store, settings):
    def factory():
        raise AssertionError("não deveria carregar o modelo")

    assert fin.recover(store, settings, factory, FakeDiarizer(), None) == []


def test_reprocess_decodes_opus_and_preserves_title_and_summary(store, settings):
    mid, d = new_meeting(store)
    words = {"mic": [w(1.0, "primeira")], "pc": [w(3.0, "versão")]}
    fin.finalize(mid, store, settings, FakeTranscriber(), Diarizer(None), words, None)
    assert not (d / "pc.raw").exists()
    old_ids = {sp.id for sp in store.get_speakers(mid)}
    store.update_meeting(mid, title="Planejamento", title_auto=False)
    store.set_summary(mid, "resumo editado")
    # ordem das trilhas: mic, depois pc
    transcriber = FakeTranscriber(sequence=[[w(1.0, "mic"), w(1.4, "refeito")],
                                            [w(6.0, "pc"), w(6.4, "refeito")]])
    diarizer = FakeDiarizer(DiarizationResult([Turn(5.5, 7.0, "S1")], {}))

    fin.reprocess(mid, store, settings, transcriber, diarizer, lambda t: "não usar")

    m = store.get_meeting(mid)
    assert m.status == "ready"
    assert m.title == "Planejamento" and m.title_auto is False
    assert store.get_summary(mid) == "resumo editado"
    assert diarizer.calls == 1
    # decodificou as duas trilhas (Opus -> PCM 16 kHz em memória), ~20 s cada
    assert len(transcriber.calls) == 2
    assert all(abs(dur - 20.0) < 0.1 for _, dur in transcriber.calls)
    speakers = store.get_speakers(mid)
    assert {sp.label for sp in speakers} == {"Você", "Pessoa 1"}
    assert not ({sp.id for sp in speakers} & old_ids)
    assert [s.text for s in store.get_segments(mid)] == ["mic refeito", "pc refeito"]


def test_title_edited_while_automatic_title_is_generated_survives(store, settings):
    mid, _ = new_meeting(store)

    def concurrent_rename(transcript):
        store.update_meeting(mid, title="Título escolhido por mim", title_auto=False)
        return "Título automático atrasado"

    fin.finalize(mid, store, settings, FakeTranscriber(), None,
                 {"mic": [w(1.0, "Olá")], "pc": []}, concurrent_rename)

    meeting = store.get_meeting(mid)
    assert meeting.status == "ready"
    assert meeting.title == "Título escolhido por mim"
    assert meeting.title_auto is False


# -- GPU ---------------------------------------------------------------------------


@pytest.mark.gpu
def test_end_to_end_with_synthetic_tracks(store, settings, tmp_path):
    from tests.helpers.tts import synth
    from whisper_ditado.transcriber import Transcriber, TranscriberOptions
    from whisper_ditado.vocab import Vocabulary

    def place(track: np.ndarray, at_s: float, clip: np.ndarray) -> None:
        start = int(at_s * SR)
        track[start:start + len(clip)] += clip

    total = 40.0
    mic = np.zeros(int(total * SR), np.float32)
    pc = np.zeros(int(total * SR), np.float32)
    place(mic, 1.0, synth("Bom dia, hoje eu fiz o deploy no servidor de produção."))
    place(pc, 9.0, synth("Ótimo, o deploy funcionou sem nenhum erro?", "pt-br"))
    place(pc, 16.0, synth("Sim, mas precisamos revisar o banco de dados amanhã.", "pt-br+f3"))
    place(pc, 24.0, synth("Então eu marco uma reunião para quinta-feira.", "pt-br"))

    m = store.create_meeting(STARTED)
    d = paths.meeting_dir(m.id)
    for name, audio in (("mic", mic), ("pc", pc)):
        (d / f"{name}.raw").write_bytes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    store.update_meeting(m.id, status="processing")

    vp, sp = tmp_path / "vocabulario.txt", tmp_path / "substituicoes.txt"
    vp.write_text("deploy\n")
    sp.write_text("")
    transcriber = Transcriber(TranscriberOptions(), Vocabulary(vp, sp))

    # Sem token do Hugging Face nesta máquina: caminho de falante único ("Participantes").
    fin.finalize(m.id, store, settings, transcriber, Diarizer(None), None, None)

    meeting = store.get_meeting(m.id)
    assert meeting.status == "ready", meeting.error
    names = labels(store, m.id)
    segs = store.get_segments(m.id)
    mine = [s for s in segs if names[s.speaker_id] == "Você"]
    others = [s for s in segs if names[s.speaker_id] != "Você"]
    assert mine and all(s.source == "mic" for s in mine)
    assert others and all(s.source == "pc" for s in others)
    text = " ".join(s.text for s in segs).lower()
    assert "deploy" in text
    assert (d / "mic.opus").exists() and (d / "pc.opus").exists()
    assert not (d / "mic.raw").exists() and not (d / "pc.raw").exists()
