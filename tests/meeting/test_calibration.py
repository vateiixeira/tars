import numpy as np
import pytest

from whisper_ditado.meeting.calibration import (
    agreement,
    auc,
    from_model,
    leave_one_meeting_out_matches,
    leave_one_out_scores,
    meetings_with_other_model,
    pick_threshold,
)


def test_agreement_maps_labels_one_to_one():
    ref = [(0.0, 10.0, "a"), (10.0, 20.0, "b")]
    # um rótulo só da hipótese casa com um rótulo só da referência
    assert agreement(ref, [(0.0, 10.0, "x"), (10.0, 20.0, "x")]) == pytest.approx(0.5)
    # rótulos com nomes trocados, mas correspondentes
    assert agreement(ref, [(0.0, 10.0, "y"), (10.0, 20.0, "x")]) == pytest.approx(1.0)


def test_agreement_partial_overlap_and_empty_ref():
    ref = [(0.0, 10.0, "a"), (10.0, 20.0, "b")]
    hyp = [(0.0, 5.0, "x"), (5.0, 20.0, "y")]
    # guloso pela maior sobreposição: y->b (10 s) primeiro; y já usado, sobra x->a (5 s)
    assert agreement(ref, hyp) == pytest.approx(15.0 / 20.0)
    assert agreement([], hyp) == 0.0
    assert agreement(ref, []) == 0.0


def test_pick_threshold_separable_midpoint():
    assert pick_threshold([0.8, 0.9], [0.2, 0.4]) == 0.6


def test_pick_threshold_overlap_maximizes_youden():
    # 0.4: TPR 1, FPR 0.25 (0.75) supera 0.7 (0.5), 0.75 (0.25) e 0.8 (0.5)
    assert pick_threshold([0.4, 0.7, 0.8, 0.9], [0.1, 0.2, 0.3, 0.75]) == 0.4
    # 0.5, 0.7 e 0.9 empatam em 1/3: fica o maior
    assert pick_threshold([0.5, 0.7, 0.9], [0.3, 0.6, 0.8]) == 0.9


def test_leave_one_out_scores_skips_single_sample_voices():
    a1, a2 = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    b1 = np.array([2.0, 0.0])  # não normalizado de propósito
    positives, negatives = leave_one_out_scores({"a": [a1, a2], "b": [b1]})

    # só a voz "a" tem outras amostras: um positivo por amostra dela
    assert sorted(positives) == pytest.approx([0.0, 0.0])
    # a1 e a2 contra a média de "b" ([1, 0]); b1 contra a média normalizada de "a"
    s = 1 / np.sqrt(2)
    assert sorted(negatives) == pytest.approx(sorted([1.0, 0.0, s]))


def test_leave_one_out_scores_positive_uses_mean_of_others():
    v = {"a": [np.array([1.0, 0.0]), np.array([1.0, 0.0]), np.array([0.0, 1.0])]}
    positives, negatives = leave_one_out_scores(v)
    s = 1 / np.sqrt(2)
    assert sorted(positives) == pytest.approx(sorted([s, s, 0.0]))
    assert negatives == []


def test_auc_counts_ordered_pairs_and_ties():
    assert auc([0.9, 0.8], [0.1, 0.2]) == 1.0
    # pares (0.5,0.5) empatados valem meio; (0.5,0.7) perde; (0.9,*) ganha os dois
    assert auc([0.5, 0.9], [0.5, 0.7]) == pytest.approx((0.5 + 0 + 1 + 1) / 4)
    assert auc([], [0.1]) == 0.0


def test_leave_one_meeting_out_matches_counts_outcomes():
    samples = [
        ("m1", "a", np.array([1.0, 0.0])),
        ("m2", "a", np.array([0.95, 0.05])),
        ("m2", "c", np.array([0.0, 1.0])),  # "c" só existe em m2 e não parece ninguém: sem voz
        ("m3", "d", np.array([0.9, 0.1])),  # "d" só existe em m3 e parece "a": casa errado
    ]
    # (certos, errados, sem voz), comparando cada reunião com as vozes das outras
    assert leave_one_meeting_out_matches(samples, threshold=0.8) == (2, 1, 1)
    assert leave_one_meeting_out_matches(samples, threshold=0.9999) == (0, 0, 4)


def test_from_model_accepts_unset_and_same_model_only():
    assert from_model(None, "pyannote-community-1")
    assert from_model("pyannote-community-1", "pyannote-community-1")
    assert not from_model("sherpa-nemo_en_titanet_small", "pyannote-community-1")


def test_meetings_with_other_model_flags_reprocessed_meetings():
    rows = [
        ("m1", "pyannote-community-1"),
        ("m1", None),  # falante sem embedding
        ("m2", "pyannote-community-1"),
        ("m2", "sherpa-nemo_en_titanet_small"),  # reprocessada com o motor local
        ("m3", None),
    ]
    assert meetings_with_other_model(rows, "pyannote-community-1") == {"m2"}
    assert meetings_with_other_model([], "pyannote-community-1") == set()
