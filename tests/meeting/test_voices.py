import numpy as np

from whisper_ditado.meeting.voices import cosine, enroll, inherit_links, match_speakers
from whisper_ditado.store import Voice, VoiceEmbedding

M = "modelo-x"


def _voice(vid, emb, model=M):
    return Voice(id=vid, name=vid,
                 embeddings={model: VoiceEmbedding(np.asarray(emb, dtype=np.float32))})


def test_cosine_basics():
    assert cosine(np.array([1.0, 0.0]), np.array([1.0, 0.0])) == 1.0
    assert abs(cosine(np.array([1.0, 0.0]), np.array([0.0, 1.0]))) < 1e-9
    assert cosine(np.zeros(2), np.array([1.0, 0.0])) == 0.0


def test_match_assigns_each_voice_once():
    voices = [_voice("v1", [1, 0]), _voice("v2", [0, 1])]
    speakers = {
        "A": np.array([1.0, 0.1], dtype=np.float32),
        "B": np.array([1.0, 0.2], dtype=np.float32),
    }
    result = match_speakers(speakers, voices, threshold=0.0, model=M)
    # A é mais próximo de v1, então B fica com v2, mesmo sendo mais próximo de v1
    assert result == {"A": "v1", "B": "v2"}


def test_match_respects_threshold():
    voices = [_voice("v1", [1, 0]), _voice("v2", [0, 1]), Voice(id="v3", name="v3")]
    speakers = {"A": np.array([1.0, 0.0], dtype=np.float32),
                "B": np.array([1.0, 1.0], dtype=np.float32)}
    result = match_speakers(speakers, voices, threshold=0.9, model=M)
    assert result == {"A": "v1"}


def test_match_ignores_voices_without_that_model():
    voices = [_voice("v1", [1, 0], model="m1"), _voice("v2", [0, 1], model="m2")]
    speakers = {"A": np.array([1.0, 0.0], dtype=np.float32)}
    # v1 é idêntica, mas é de outro modelo: nunca compara embeddings de modelos diferentes
    assert match_speakers(speakers, voices, threshold=0.0, model="m2") == {"A": "v2"}
    assert match_speakers(speakers, voices, threshold=0.5, model="m2") == {}
    assert match_speakers(speakers, voices, threshold=0.0, model="m3") == {}


def test_enroll_new_voice():
    emb = np.array([3.0, 4.0], dtype=np.float32)
    v = enroll(None, "Ana", emb, M, "mt1", "SPEAKER_00")
    assert v.name == "Ana"
    assert set(v.embeddings) == {M}
    item = v.embeddings[M]
    assert item.n_samples == 1
    assert v.source_meeting_id == "mt1" and v.source_speaker_id == "SPEAKER_00"
    assert item.vector.dtype == np.float32
    assert np.allclose(item.vector, [0.6, 0.8])
    assert len(v.id) == 32
    assert v.created_at is not None and v.created_at == v.updated_at
    assert item.updated_at == v.updated_at


def test_enroll_updates_running_mean():
    first = enroll(None, "Ana", np.array([1.0, 0.0], dtype=np.float32), M, "mt1", "S0")
    old_updated = first.updated_at
    second = enroll(first, "Ana", np.array([0.0, 1.0], dtype=np.float32), M, "mt2", "S1")
    assert second.id == first.id
    item = second.embeddings[M]
    assert item.n_samples == 2
    assert np.allclose(item.vector, [2 ** -0.5, 2 ** -0.5], atol=1e-6)
    assert abs(np.linalg.norm(item.vector) - 1.0) < 1e-6
    assert item.vector.dtype == np.float32
    assert second.created_at == first.created_at
    assert second.updated_at >= old_updated


def test_enroll_running_mean_is_not_dominated_by_a_large_raw_embedding():
    # centróides do pyannote não vêm normalizados: a escala não pode pesar na média
    first = enroll(None, "Ana", np.array([1.0, 0.0], dtype=np.float32), M, "mt1", "S0")
    second = enroll(first, "Ana", np.array([0.0, 50.0], dtype=np.float32), M, "mt2", "S1")
    assert np.allclose(second.embeddings[M].vector, [2 ** -0.5, 2 ** -0.5], atol=1e-6)
    third = enroll(second, "Ana", np.array([0.0, 0.01], dtype=np.float32), M, "mt3", "S2")
    # peso 2 para a voz salva (unitária) e 1 para a amostra nova (normalizada)
    expected = np.array([2 * 2 ** -0.5, 2 * 2 ** -0.5 + 1.0])
    expected /= np.linalg.norm(expected)
    assert np.allclose(third.embeddings[M].vector, expected, atol=1e-5)


def test_enroll_updates_only_the_given_model():
    other = VoiceEmbedding(np.array([0.0, 0.0, 1.0], dtype=np.float32), 5)
    voice = Voice(id="v1", name="Ana", embeddings={
        "m1": VoiceEmbedding(np.array([1.0, 0.0], dtype=np.float32), 3),
        "m2": other,
    })
    updated = enroll(voice, "Ana", np.array([0.0, 1.0], dtype=np.float32), "m1", "mt9", "S9")
    assert updated.id == "v1" and set(updated.embeddings) == {"m1", "m2"}
    assert updated.embeddings["m1"].n_samples == 4
    expected = np.array([3.0, 1.0]) / np.linalg.norm([3.0, 1.0])
    assert np.allclose(updated.embeddings["m1"].vector, expected, atol=1e-6)
    assert updated.embeddings["m2"] is other  # outros modelos intactos
    assert voice.embeddings["m1"].n_samples == 3  # a voz de entrada não é alterada

    # primeiro embedding de um modelo novo numa voz que já existe
    added = enroll(updated, "Ana", np.array([2.0, 0.0, 0.0], dtype=np.float32), "m3", "mt9", "S9")
    assert set(added.embeddings) == {"m1", "m2", "m3"}
    assert added.embeddings["m3"].n_samples == 1
    assert np.allclose(added.embeddings["m3"].vector, [1.0, 0.0, 0.0])


def test_inherit_links_picks_largest_overlap():
    new = {"A": [(0.0, 10.0)], "B": [(10.0, 20.0)]}
    old = {"v1": [(8.0, 20.0)]}
    assert inherit_links(new, old) == {"B": "v1"}


def test_inherit_links_is_one_to_one():
    # o cluster antigo v1 se dividiu em A e B: só o de maior sobreposição herda
    new = {"A": [(0.0, 12.0)], "B": [(12.0, 20.0)]}
    old = {"v1": [(0.0, 20.0)]}
    assert inherit_links(new, old) == {"A": "v1"}
    # e uma chave nova também só fica com uma voz
    assert inherit_links({"A": [(0.0, 20.0)]}, {"v1": [(0.0, 12.0)], "v2": [(12.0, 20.0)]}) == {"A": "v1"}


def test_inherit_links_ignores_no_overlap():
    assert inherit_links({"A": [(0.0, 5.0)]}, {"v1": [(5.0, 9.0)]}) == {}
    assert inherit_links({}, {"v1": [(0.0, 9.0)]}) == {}
    assert inherit_links({"A": [(0.0, 5.0)]}, {}) == {}


def test_inherit_links_ignores_edge_overlap():
    # os trechos só se encostam na borda (0,1 s): não é a mesma pessoa
    assert inherit_links({"A": [(0.0, 10.0)]}, {"v1": [(9.9, 20.0)]}) == {}


def test_inherit_links_requires_fraction_of_shorter_total():
    # 2 s de 20 s em comum ficam abaixo de 30% do menor total (6 s)
    assert inherit_links({"A": [(0.0, 20.0)]}, {"v1": [(18.0, 38.0)]}) == {}
    # 6 s chegam ao mínimo
    assert inherit_links({"A": [(0.0, 20.0)]}, {"v1": [(14.0, 34.0)]}) == {"A": "v1"}


def test_inherit_links_sums_overlap_across_spans():
    new = {"A": [(0.0, 3.0), (10.0, 13.0)], "B": [(4.0, 8.0)]}
    old = {"v1": [(0.0, 3.0), (10.0, 13.0)], "v2": [(0.0, 20.0)]}
    # A: v1=6, v2=6 (empate, vale a ordem); B: v1=0, v2=4 e sobra a v2
    assert inherit_links(new, old) == {"A": "v1", "B": "v2"}
