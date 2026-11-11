from datetime import date

import pytest

from whisper_ditado.meeting.readai import ImportFormatError, parse_readai

SAMPLE = (
    "Awake e Angular - mudança de mecanismo de créditos\n"
    "qua., 11 de nov. de 2026\n\n"
    "0:03 - Felipe Iaropoli\nOpa, fala ali.\n\n"
    "0:12 - Alessandro Reichert\nPrimeiro parágrafo.\n\n Segundo parágrafo.\n\n"
    "1:02:03 - Felipe Iaropoli\nValeu, até já.\n"
)


def test_parses_title_date_and_turns():
    t = parse_readai(SAMPLE)
    assert t.title == "Awake e Angular - mudança de mecanismo de créditos"
    assert t.date == date(2026, 11, 11)
    assert [(x.name, x.start_s) for x in t.turns] == [
        ("Felipe Iaropoli", 3.0), ("Alessandro Reichert", 12.0), ("Felipe Iaropoli", 3723.0)]
    assert t.turns[1].text == "Primeiro parágrafo.\n\nSegundo parágrafo."
    assert t.turns[0].end_s == 12.0
    assert t.turns[2].end_s > 3723.0
    assert t.duration_s == t.turns[-1].end_s
    assert t.people == ["Felipe Iaropoli", "Alessandro Reichert"]


def test_unknown_date_is_none():
    assert parse_readai(SAMPLE.replace("qua., 11 de nov. de 2026", "ontem")).date is None


@pytest.mark.parametrize("text", ["", "uma linha qualquer\nsem falas\n"])
def test_rejects_other_formats(text):
    with pytest.raises(ImportFormatError, match="só a do Read AI"):
        parse_readai(text)
