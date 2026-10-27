"""Captura da tela inteira pelo portal xdg-desktop-portal (Screenshot) do GNOME."""

from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from jeepney import DBusAddress, MatchRule, message_bus, new_method_call
from jeepney.io.blocking import Proxy, open_dbus_connection

SCREENSHOT = DBusAddress(
    "/org/freedesktop/portal/desktop",
    bus_name="org.freedesktop.portal.Desktop",
    interface="org.freedesktop.portal.Screenshot",
)

_MSG_UNAVAILABLE = "Portal de captura indisponível"
_MSG_TIMEOUT = "O GNOME não respondeu ao pedido de print"
_MSG_REFUSED = "O GNOME recusou o print (cancelado ou sem permissão)"
_MSG_FAILED = "O portal de captura falhou"


class PortalError(Exception):
    """Falha ao capturar a tela pelo portal; a mensagem está em pt-BR."""


def request_path(unique_name: str, token: str) -> str:
    """Caminho do objeto Request que o portal usa para responder ao nosso pedido."""
    sender = unique_name.lstrip(":").replace(".", "_")
    return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"


def parse_response(code: int, results: dict) -> Path:
    """Converte o sinal Response do portal no caminho do arquivo gravado."""
    if code == 1:
        raise PortalError(_MSG_REFUSED)
    if code != 0:
        raise PortalError(_MSG_FAILED)
    try:
        uri = results["uri"][1]
        parsed = urlparse(uri)
    except (KeyError, IndexError, TypeError, ValueError):
        raise PortalError(_MSG_FAILED) from None
    if parsed.scheme != "file":
        raise PortalError(_MSG_FAILED)
    return Path(unquote(parsed.path))


def capture_screen(timeout_s: float = 10.0) -> Path:
    """Captura a tela inteira sem diálogo e devolve o arquivo gravado pelo portal."""
    try:
        conn = open_dbus_connection(bus="SESSION")
    except Exception as exc:
        raise PortalError(_MSG_UNAVAILABLE) from exc
    try:
        token = "tars" + uuid.uuid4().hex
        rule = MatchRule(
            type="signal",
            interface="org.freedesktop.portal.Request",
            member="Response",
            path=request_path(conn.unique_name, token),
        )
        # A assinatura do sinal precisa existir antes da chamada, senão a resposta se perde.
        try:
            Proxy(message_bus, conn).AddMatch(rule)
            with conn.filter(rule) as queue:
                conn.send_and_get_reply(
                    new_method_call(
                        SCREENSHOT,
                        "Screenshot",
                        "sa{sv}",
                        ("", {"handle_token": ("s", token), "interactive": ("b", False)}),
                    ),
                    timeout=timeout_s,
                )
                code, results = conn.recv_until_filtered(queue, timeout=timeout_s).body
        except TimeoutError as exc:
            raise PortalError(_MSG_TIMEOUT) from exc
        except PortalError:
            raise
        except Exception as exc:
            raise PortalError(_MSG_UNAVAILABLE) from exc
        return parse_response(code, results)
    finally:
        conn.close()
