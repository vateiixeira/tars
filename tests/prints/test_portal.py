from pathlib import Path

import pytest

from whisper_ditado.prints.portal import PortalError, parse_response, request_path


def test_request_path_follows_portal_convention():
    assert request_path(":1.64", "tars1") == "/org/freedesktop/portal/desktop/request/1_64/tars1"


def test_parse_response_ok_and_errors():
    assert parse_response(0, {"uri": ("s", "file:///home/u/Imagens/Screenshot%201.png")}) == \
        Path("/home/u/Imagens/Screenshot 1.png")
    with pytest.raises(PortalError, match="recusou"):
        parse_response(1, {})
    with pytest.raises(PortalError, match="falhou"):
        parse_response(2, {})
    with pytest.raises(PortalError, match="falhou"):
        parse_response(0, {"uri": ("s", "https://x/y.png")})
