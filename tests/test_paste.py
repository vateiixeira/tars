import logging
import socket

import pytest

from whisper_ditado import paste as paste_module
from whisper_ditado.paste import (
    ClipboardUnavailable,
    clipboard_commands,
    log_clipboard_session,
)


def which_of(*installed):
    return lambda name: f"/usr/bin/{name}" if name in installed else None


def no_wayland(_path):
    return False


ALL_TOOLS = which_of("wl-copy", "xclip", "xsel")


def test_wayland_uses_wl_copy():
    cmds = clipboard_commands({"WAYLAND_DISPLAY": "wayland-0"}, ALL_TOOLS, no_wayland)
    assert cmds == ("wayland", ["wl-copy"], ["wl-copy", "--primary"])


def test_wayland_wins_over_xwayland_display():
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    assert clipboard_commands(env, ALL_TOOLS, no_wayland).session == "wayland"


def test_wayland_without_wl_copy_explains_package():
    with pytest.raises(ClipboardUnavailable, match="wl-clipboard"):
        clipboard_commands({"WAYLAND_DISPLAY": "wayland-0"}, which_of("xclip"), no_wayland)


def test_default_wayland_socket_covers_service_started_too_early():
    env = {"XDG_RUNTIME_DIR": "/run/user/1000", "DISPLAY": ":0"}
    sockets = {"/run/user/1000/wayland-0"}
    cmds = clipboard_commands(env, ALL_TOOLS, sockets.__contains__)
    assert cmds.session == "wayland"


def test_x11_uses_xclip():
    cmds = clipboard_commands({"DISPLAY": ":0"}, ALL_TOOLS, no_wayland)
    assert cmds == (
        "x11",
        ["xclip", "-selection", "clipboard"],
        ["xclip", "-selection", "primary"],
    )


def test_x11_falls_back_to_xsel():
    cmds = clipboard_commands({"DISPLAY": ":0"}, which_of("xsel"), no_wayland)
    assert cmds == (
        "x11",
        ["xsel", "--clipboard", "--input"],
        ["xsel", "--primary", "--input"],
    )


def test_x11_without_tools_explains_package():
    with pytest.raises(ClipboardUnavailable, match="instale o pacote xclip"):
        clipboard_commands({"DISPLAY": ":0"}, which_of("wl-copy"), no_wayland)


def test_no_graphical_session_explains_import_environment():
    with pytest.raises(ClipboardUnavailable, match="import-environment"):
        clipboard_commands({"XDG_RUNTIME_DIR": "/run/user/1000"}, ALL_TOOLS, no_wayland)


def test_log_clipboard_session(monkeypatch, caplog):
    log = logging.getLogger("test-paste")
    monkeypatch.setattr(paste_module, "clipboard_commands",
                        lambda: paste_module.ClipboardCommands("x11", ["xclip"], ["xclip"]))
    with caplog.at_level(logging.INFO, logger="test-paste"):
        log_clipboard_session(log)

    def unavailable():
        raise ClipboardUnavailable("Sessão X11 sem xclip nem xsel")

    monkeypatch.setattr(paste_module, "clipboard_commands", unavailable)
    with caplog.at_level(logging.INFO, logger="test-paste"):
        log_clipboard_session(log)
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.INFO, "Colagem pelo clipboard da sessão x11"),
        (logging.WARNING,
         "Sessão X11 sem xclip nem xsel; o ditado vai transcrever mas não vai conseguir colar"),
    ]


@pytest.fixture
def calls(monkeypatch):
    recorded = []
    monkeypatch.setattr(paste_module.subprocess, "run",
                        lambda cmd, **kwargs: recorded.append((cmd, kwargs)))
    monkeypatch.setattr(paste_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(paste_module, "clipboard_commands",
                        lambda: paste_module.ClipboardCommands("wayland", ["copy"], ["copy", "--primary"]))
    return recorded


def test_paste_shift_insert_fills_clipboard_and_primary(calls):
    paste_module.paste("olá", "shift+insert", "ydotool")
    data = "olá".encode()
    assert calls == [
        (["copy"], {"input": data, "check": True, "timeout": 5}),
        (["copy", "--primary"], {"input": data, "check": True, "timeout": 5}),
        (["ydotool", "key", "42:1", "110:1", "110:0", "42:0"], {"check": True, "timeout": 5}),
    ]


@pytest.mark.parametrize("mode", ["ctrl+v", "ctrl+shift+v"])
def test_paste_without_shift_insert_skips_primary(calls, mode):
    paste_module.paste("oi", mode, "ydotool")
    assert [cmd for cmd, _ in calls] == [["copy"], ["ydotool", "key", *paste_module.PASTE_KEYS[mode]]]
    assert all(kwargs["check"] is True and kwargs["timeout"] == 5 for _, kwargs in calls)


def test_paste_without_clipboard_does_not_send_keys(monkeypatch, calls):
    def unavailable():
        raise ClipboardUnavailable("sem sessão")

    monkeypatch.setattr(paste_module, "clipboard_commands", unavailable)
    with pytest.raises(ClipboardUnavailable):
        paste_module.paste("oi", "ctrl+v", "ydotool")
    assert calls == []


def test_paste_x11_shift_insert_end_to_end(monkeypatch):
    recorded = []
    monkeypatch.setattr(paste_module.subprocess, "run", lambda cmd, **kwargs: recorded.append(cmd))
    monkeypatch.setattr(paste_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(paste_module, "clipboard_commands",
                        lambda: clipboard_commands({"DISPLAY": ":0"}, which_of("xclip"), no_wayland))
    paste_module.paste("olá", "shift+insert", "ydotool")
    assert recorded == [
        ["xclip", "-selection", "clipboard"],
        ["xclip", "-selection", "primary"],
        ["ydotool", "key", *paste_module.PASTE_KEYS["shift+insert"]],
    ]


def test_socket_alive_rejects_stale_socket(tmp_path):
    path = str(tmp_path / "wayland-0")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen()
    assert paste_module._socket_alive(path)
    server.close()  # o arquivo do socket continua lá, mas ninguém escuta
    assert not paste_module._socket_alive(path)
    assert not paste_module._socket_alive(str(tmp_path / "inexistente"))
