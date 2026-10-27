"""Testes rápidos (sem GPU, sem carregar modelo) das peças do entrypoint."""

import json
import os
import threading
import time
from pathlib import Path

from whisper_ditado import main, paths
from whisper_ditado.settings import Settings
from whisper_ditado.store import Store


def test_remove_api_info_only_for_own_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    path = paths.api_info_path()

    path.write_text(json.dumps({"port": 1, "token": "t", "pid": os.getpid() + 1}))
    main._remove_api_info()
    assert path.exists()  # arquivo de outro processo permanece

    path.write_text(json.dumps({"port": 1, "token": "t", "pid": os.getpid()}))
    main._remove_api_info()
    assert not path.exists()

    main._remove_api_info()  # ausente: não levanta
    path.write_text("lixo")
    main._remove_api_info()  # ilegível: não levanta
    assert path.exists()


def test_settings_diarizer_reads_token_lazily(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    settings = Settings(Store(tmp_path / "x.db"))
    diarizer = main._SettingsDiarizer(settings)
    assert diarizer.hf_token is None
    assert diarizer.available is False  # sem token: não tenta carregar nada

    monkeypatch.setenv("HF_TOKEN", "do-ambiente")
    assert diarizer._token() == "do-ambiente"
    settings.update({"hf_token": "das-configuracoes"})
    assert diarizer._token() == "das-configuracoes"  # a configuração tem prioridade


def test_build_diarizer_follows_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    settings = Settings(Store(tmp_path / "x.db"))
    router = main.build_diarizer(settings)

    assert router.chosen() == "local"
    assert router.chosen_model().startswith("sherpa-")
    settings.update({"diarizer": "pyannote"})
    assert router.chosen() == "pyannote"
    assert router.chosen_model() == "pyannote-community-1"


def test_run_server_converts_system_exit(monkeypatch):
    import asyncio

    class Fake:
        async def serve(self):
            raise SystemExit(1)

    try:
        asyncio.run(main._run_server(Fake()))
    except RuntimeError as exc:
        assert "porta" in str(exc)
    else:
        raise AssertionError("esperava RuntimeError")


def test_hard_exit_flushes_and_skips_interpreter_finalization(monkeypatch):
    calls = []
    monkeypatch.setattr(main.logging, "shutdown", lambda: calls.append("logging"))
    monkeypatch.setattr(main, "terminate_children", lambda: calls.append("children"))
    monkeypatch.setattr(main.os, "_exit", lambda rc: calls.append(("exit", rc)))
    main._hard_exit(3)
    assert calls == ["logging", "children", ("exit", 3)]


def test_entrypoint_hard_exits_with_main_code_or_error(monkeypatch):
    exits = []
    monkeypatch.setattr(main, "_hard_exit", exits.append)
    monkeypatch.setattr(main, "main", lambda: 0)
    main._entrypoint()

    def boom():
        raise RuntimeError("falhou")

    monkeypatch.setattr(main, "main", boom)
    main._entrypoint()
    assert exits == [0, 1]
    assert Path(main.__file__).read_text().rstrip().endswith("    _entrypoint()")


def test_backend_unit_signals_only_the_main_process():
    unit = (Path(main.__file__).parents[2] / "whisper-ditado-backend.service").read_text()
    assert "KillMode=mixed" in unit.splitlines()


def test_dictation_gate_waits_at_most_one_recording(tmp_path):
    calls = []

    class Busy:
        def wait_idle(self, timeout):
            calls.append(timeout)
            return False

    settings = Settings(Store(tmp_path / "x.db"))
    settings.update({"max_sec": 60.0})
    main._dictation_gate(Busy(), settings)()
    assert calls == [60.0 + main.DICTATION_GRACE_S]


def test_shutdown_opens_the_dictation_gate(tmp_path):
    from whisper_ditado.priority import DictationBusy

    busy = DictationBusy()
    busy.enter()  # Ctrl+Win segurado no SIGTERM (ou contador preso)
    settings = Settings(Store(tmp_path / "x.db"))
    gate = main._dictation_gate(busy, settings)
    t = threading.Thread(target=gate, daemon=True)
    t.start()
    time.sleep(0.1)
    assert t.is_alive()  # ainda cede a GPU ao ditado
    busy.open()  # primeiro passo do desligamento (`serve`)
    t.join(1.0)
    assert not t.is_alive()  # o desligamento não espera o portão (a finalização tem 30 s)
    t0 = time.monotonic()
    gate()  # e as próximas tarefas passam direto
    assert time.monotonic() - t0 < 0.1


def test_meeting_transcriber_is_always_portuguese(tmp_path, monkeypatch):
    from whisper_ditado import dictation

    monkeypatch.setenv("DITADO_LANG", "en")
    settings = Settings(Store(tmp_path / "x.db"))
    opts = main._meeting_options(settings)
    assert opts.language == "pt"
    assert dictation.transcriber_options(settings).language == "en"  # o ditado segue o ambiente
    assert (opts.model, opts.prompt) == (settings.get("model"), settings.get("prompt"))


def test_memory_maintenance_runs_periodically_and_stops_on_shutdown(monkeypatch):
    import asyncio
    from concurrent.futures import Future

    monkeypatch.setattr(main, "MODEL_IDLE_CHECK_S", 0.001)

    async def run():
        shutdown = asyncio.Event()
        calls = []

        class Meetings:
            def release_idle_models(self):
                calls.append(True)
                if len(calls) == 2:
                    shutdown.set()
                result = Future()
                result.set_result(False)
                return result

        await asyncio.wait_for(main._maintain_meeting_models(Meetings(), shutdown), 1)
        assert calls == [True, True]
        # Já desligado: não enfileira trabalho depois que o banco é fechado.
        await main._maintain_meeting_models(Meetings(), shutdown)
        assert calls == [True, True]

    asyncio.run(run())
