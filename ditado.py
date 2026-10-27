#!/usr/bin/env python3
"""Ditado por voz global: segure uma tecla, fale, solte — o texto é colado na janela ativa.

- Teclado lido direto via evdev (o GNOME Wayland não deixa apps registrarem atalhos globais).
- Áudio capturado com pw-record (PCM s16 mono 16 kHz).
- Transcrição com faster-whisper na GPU; o modelo fica carregado e aquecido.
- Colagem via clipboard (wl-copy no Wayland, xclip/xsel no X11) + atalho de colar simulado pelo ydotool, o que
  preserva acentos (o `ydotool type` não digita caracteres fora do layout US).

Configuração por variáveis de ambiente (veja README.md). Vocabulário e substituições
ficam em arquivos editáveis, recarregados a cada ditado.
"""

import asyncio
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import evdev
import numpy as np
from evdev import ecodes
from faster_whisper import WhisperModel

from whisper_ditado.paste import PASTE_KEYS, log_clipboard_session
from whisper_ditado.paste import paste as paste_text

log = logging.getLogger("ditado")

SAMPLE_RATE = 16000
SCAN_INTERVAL_SEC = 3

# Apelidos aceitos em DITADO_KEY; cada um vale tanto para o lado esquerdo quanto o direito.
_CTRL = (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL)
_SUPER = (ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA)
KEY_ALIASES = {
    "ctrl": _CTRL,
    "super": _SUPER,
    "win": _SUPER,
    "meta": _SUPER,
    "alt": (ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT),
    "shift": (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT),
}

# Textos que o Whisper "alucina" quando não há fala (vêm de legendas do treino).
HALLUCINATIONS = {
    "obrigado por assistir.",
    "obrigado por assistir!",
    "thanks for watching!",
    "thank you for watching.",
}


@dataclass(frozen=True)
class Config:
    model: str
    compute_type: str
    language: str | None
    key_name: str
    # Combinação: cada grupo precisa de ao menos uma tecla segurada (ex.: ctrl+super).
    hotkey: tuple[frozenset[int], ...]
    hotkey_codes: frozenset[int]
    paste: str
    min_sec: float
    max_sec: float
    prompt: str | None
    trailing_space: bool
    ignore_devices: tuple[str, ...]
    ydotool: str
    vocab_path: str
    subs_path: str

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get

        key_name = env("DITADO_KEY", "ctrl+super")
        hotkey = parse_hotkey(key_name)

        paste = env("DITADO_PASTE", "shift+insert")
        if paste not in PASTE_KEYS:
            raise SystemExit(f"DITADO_PASTE inválido: {paste} (opções: {', '.join(PASTE_KEYS)})")

        ignore = env("DITADO_IGNORE_DEVICES", "ydotoold")
        conf_dir = os.path.join(env("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "whisper-ditado")
        return cls(
            model=env("DITADO_MODEL", "large-v3-turbo"),
            compute_type=env("DITADO_COMPUTE_TYPE", "float16"),
            language=env("DITADO_LANG", "pt") or None,
            key_name=key_name,
            hotkey=hotkey,
            hotkey_codes=frozenset().union(*hotkey),
            paste=paste,
            min_sec=float(env("DITADO_MIN_SEC", "0.4")),
            max_sec=float(env("DITADO_MAX_SEC", "300")),
            # Mostra ao modelo o estilo esperado: português com termos técnicos escritos em inglês.
            prompt=env(
                "DITADO_PROMPT",
                "Olá! Hoje fiz o deploy no staging, abri um pull request e ajustei o endpoint no backend.",
            )
            or None,
            trailing_space=env("DITADO_TRAILING_SPACE", "1") == "1",
            ignore_devices=tuple(s for s in ignore.split(",") if s.strip()),
            ydotool=env("DITADO_YDOTOOL", "/usr/bin/ydotool"),
            vocab_path=env("DITADO_VOCAB", os.path.join(conf_dir, "vocabulario.txt")),
            subs_path=env("DITADO_SUBS", os.path.join(conf_dir, "substituicoes.txt")),
        )


def parse_hotkey(spec: str) -> tuple[frozenset[int], ...]:
    """'ctrl+super' -> ({LEFTCTRL, RIGHTCTRL}, {LEFTMETA, RIGHTMETA}); aceita nomes do evdev (KEY_F9)."""
    groups = []
    for token in spec.split("+"):
        name = token.strip()
        if name.lower() in KEY_ALIASES:
            groups.append(frozenset(KEY_ALIASES[name.lower()]))
            continue
        code = ecodes.ecodes.get(name.upper())
        if not isinstance(code, int):
            raise SystemExit(
                f"DITADO_KEY inválida: {spec!r} (ex.: ctrl+super, KEY_RIGHTCTRL, alt+KEY_SPACE)"
            )
        groups.append(frozenset({code}))
    return tuple(groups)


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return []
    try:
        content = raw.decode("utf-8-sig")  # -sig descarta o BOM que alguns editores gravam
    except UnicodeDecodeError:
        log.warning("%s não está em UTF-8; lendo como latin-1", path)
        content = raw.decode("latin-1")
    lines = (line.strip() for line in content.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


class Vocabulary:
    """Termos (hotwords) e substituições lidos de arquivos; recarrega quando o arquivo muda.

    vocabulario.txt: um termo por linha, em ordem de prioridade (o fim é cortado se passar do limite).
    substituicoes.txt: `errado => certo` por linha; troca expressão inteira, sem diferenciar maiúsculas.
    """

    # O faster-whisper corta os hotwords em max_length // 2 - 1 tokens (448 // 2 - 1).
    MAX_TOKENS = 223

    def __init__(self, cfg: Config, tokenizer) -> None:
        self.cfg = cfg
        self.tokenizer = tokenizer
        self.hotwords: str | None = None
        self.terms: frozenset[str] = frozenset()
        self.subs: dict[str, str] = {}
        self.subs_pattern: re.Pattern | None = None
        self._keys: tuple = (None, None)
        self.refresh()

    @staticmethod
    def _key(path: str) -> tuple[int, int, int] | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        # mtime sozinho pode não mudar num save rápido; tamanho e inode cobrem save parcial e por rename.
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def refresh(self) -> None:
        keys = (self._key(self.cfg.vocab_path), self._key(self.cfg.subs_path))
        if keys == self._keys:
            return
        # Grava antes de ler: se o arquivo estiver com erro, avisa uma vez e mantém o vocabulário anterior.
        self._keys = keys
        try:
            terms = _read_lines(self.cfg.vocab_path)
            subs: dict[str, str] = {}
            for line in _read_lines(self.cfg.subs_path):
                src, sep, dst = line.partition("=>")
                if not sep or not src.strip():
                    log.warning("Linha ignorada em %s: %r (use `errado => certo`)", self.cfg.subs_path, line)
                    continue
                subs[src.strip().lower()] = dst.strip()
            hotwords = ", ".join(terms) or None
            tokens = len(self.tokenizer.encode(" " + hotwords, add_special_tokens=False).ids) if hotwords else 0
        except Exception:
            log.exception("Falha ao carregar vocabulário/substituições; mantendo os anteriores")
            return

        self.hotwords = hotwords
        self.terms = frozenset(t.lower() for t in terms)
        self.subs = subs
        # Uma passada só, da expressão mais longa para a mais curta: as trocas não se encadeiam.
        alternatives = "|".join(re.escape(src) for src in sorted(subs, key=len, reverse=True))
        self.subs_pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE) if subs else None
        log.info("Vocabulário: %d termos (%d tokens), %d substituições", len(terms), tokens, len(subs))
        if tokens > self.MAX_TOKENS:
            log.warning(
                "Vocabulário passa de %d tokens; os termos do fim de %s serão ignorados",
                self.MAX_TOKENS,
                self.cfg.vocab_path,
            )

    def apply(self, text: str) -> str:
        if self.subs_pattern is None:
            return text

        def replace(match: re.Match) -> str:
            dst = self.subs[match.group(0).lower()]
            if match.group(0)[:1].isupper() and dst[:1].islower():
                dst = dst[0].upper() + dst[1:]  # mantém a maiúscula de início de frase
            return dst

        text = self.subs_pattern.sub(replace, text)
        return re.sub(r" {2,}", " ", text).strip()  # destino vazio (`né =>`) remove a palavra

    def is_echo(self, text: str) -> bool:
        """True se o texto parece só a lista de vocabulário repetida (alucinação em áudio sem fala)."""
        items = [item.strip(" .!?;:").lower() for item in text.split(",")]
        items = [item for item in items if item]
        # Exige uma lista longa: ditar "Docker, Redis e Celery" de propósito não pode ser descartado.
        return len(items) >= 5 and sum(item in self.terms for item in items) >= 0.8 * len(items)


def has_loop(text: str) -> bool:
    """Detecta o loop de repetição do Whisper ("revisado, revisado, revisado, ...").

    Repetições curtas são comuns na fala ("não, não, não", "muito, muito, muito"), então
    1 ou 2 palavras precisam se repetir 5 vezes seguidas; trechos de 3 a 6 palavras, 3 vezes.
    """
    words = re.findall(r"[^\W\d_]+", text.lower())  # ignora números: "versão 1.1.1" não é loop
    for n in range(1, 7):
        repeats = 5 if n <= 2 else 3
        for start in range(len(words) - n * repeats + 1):
            gram = words[start : start + n]
            if all(words[start + i * n : start + (i + 1) * n] == gram for i in range(1, repeats)):
                return True
    return False


def notify(message: str) -> None:
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "Ditado", "Ditado", message])


class Recorder:
    """Grava o microfone via pw-record até stop()."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._proc = subprocess.Popen(
            ["pw-record", "--rate", str(SAMPLE_RATE), "--channels", "1", "--format", "s16", "--raw", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        while chunk := self._proc.stdout.read1(8192):
            self._buf.extend(chunk)

    def stop(self) -> np.ndarray:
        if self._proc.poll() is None:
            self._proc.send_signal(signal.SIGINT)
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._thread.join(timeout=2)
        data = bytes(self._buf[: len(self._buf) // 2 * 2])
        return np.frombuffer(data, np.int16).astype(np.float32) / 32768.0


class Transcriber:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        log.info("Carregando %s (%s) na GPU...", cfg.model, cfg.compute_type)
        try:
            # Usa o cache local primeiro: sobe sem rede e sem consultar o Hugging Face.
            self.model = self._load(local_files_only=True)
        except Exception as exc:
            log.warning("Falha ao carregar do cache (%s); tentando baixar...", exc, exc_info=True)
            self.model = self._load(local_files_only=False)
        self.vocab = Vocabulary(cfg, self.model.hf_tokenizer)
        self._warmup()

    def _load(self, local_files_only: bool) -> WhisperModel:
        return WhisperModel(
            self.cfg.model,
            device="cuda",
            compute_type=self.cfg.compute_type,
            local_files_only=local_files_only,
        )

    def _warmup(self) -> None:
        # A primeira inferência na GPU é lenta (init do cuBLAS/cuDNN); paga isso no boot.
        started = time.perf_counter()
        noise = (np.random.default_rng(0).standard_normal(SAMPLE_RATE) * 0.01).astype(np.float32)
        for vad in (False, True):
            segments, _ = self.model.transcribe(noise, language=self.cfg.language, vad_filter=vad)
            list(segments)
        log.info("Modelo pronto (aquecimento em %.1fs)", time.perf_counter() - started)

    def __call__(self, audio: np.ndarray) -> str:
        try:
            self.vocab.refresh()
        except Exception:  # problema no vocabulário nunca deve custar o ditado
            log.exception("Falha ao recarregar o vocabulário")
        text = self._transcribe(audio)
        if has_loop(text):
            # Com hotwords o Whisper às vezes entra em loop ("revisado, revisado, ..."). Bloquear
            # n-gramas repetidos resolve, mas distorce repetições naturais, então é só o plano B.
            log.info("Loop de repetição detectado; transcrevendo de novo")
            text = self._transcribe(audio, no_repeat_ngram_size=3) or text
        return self.vocab.apply(text)

    def _transcribe(self, audio: np.ndarray, **options) -> str:
        hotwords = self.vocab.hotwords
        segments, _ = self.model.transcribe(
            audio,
            language=self.cfg.language,
            initial_prompt=self.cfg.prompt,
            hotwords=hotwords,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            without_timestamps=True,
            **options,
        )
        # Em áudio quase sem fala o Whisper às vezes "repete" o próprio prompt ou o vocabulário.
        prompt = (self.cfg.prompt or "").strip(" .!?").lower()
        parts = []
        for seg in segments:
            text = seg.text.strip()
            lower = text.lower()
            if not text or lower in HALLUCINATIONS or "amara.org" in lower:
                continue
            if (prompt and lower.strip(" .!?") == prompt) or self.vocab.is_echo(text):
                continue
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
                continue
            parts.append(text)
        return " ".join(parts)


def paste(text: str, cfg: Config) -> None:
    paste_text(text, cfg.paste, cfg.ydotool)


class Ditado:
    def __init__(self, cfg: Config, transcriber: Transcriber) -> None:
        self.cfg = cfg
        self.transcriber = transcriber
        self.devices: dict[str, evdev.InputDevice] = {}
        # Nós ignorados -> identidade do nó; o kernel reaproveita eventN, então o caminho não basta.
        self.skipped: dict[str, tuple[int, int]] = {}
        self.pressed: set[int] = set()
        # Sinaliza ao worker que nenhuma tecla física está segurada: o GNOME soma os
        # modificadores de todos os teclados, então colar com o Ctrl seguro viraria Ctrl+Shift+Insert.
        self.keys_released = threading.Event()
        self.keys_released.set()
        self.stopping = False
        self.recorder: Recorder | None = None
        self.max_timer: asyncio.TimerHandle | None = None
        self.worker = ThreadPoolExecutor(max_workers=1)  # serializa: os textos colam na ordem falada
        self.loop: asyncio.AbstractEventLoop | None = None

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.loop.add_signal_handler(sig, stop.set)

        scanner = asyncio.create_task(self._scan_forever())
        log.info("Pronto. Segure %s para ditar.", self.cfg.key_name)
        log_clipboard_session(log)
        await stop.wait()

        self.stopping = True
        self.keys_released.set()  # libera o worker se estiver esperando para colar
        scanner.cancel()
        self._cancel_recording("encerrando")
        for path in list(self.devices):
            self._drop_device(path)
        self.worker.shutdown(wait=False, cancel_futures=True)

    # --- teclados ---------------------------------------------------------

    async def _scan_forever(self) -> None:
        while True:
            try:
                self._scan()
            except Exception:
                log.exception("Falha ao procurar teclados")
            await asyncio.sleep(SCAN_INTERVAL_SEC)

    def _scan(self) -> None:
        paths = evdev.list_devices()
        for path in set(self.skipped) - set(paths):
            del self.skipped[path]
        for path in paths:
            if path in self.devices:
                continue
            try:
                st = os.stat(path)
                node = (st.st_ino, st.st_ctime_ns)
                if self.skipped.get(path) != node:
                    self._open(path, node)
            except OSError:
                continue  # dispositivo sumiu no meio da varredura

    def _open(self, path: str, node: tuple[int, int]) -> None:
        dev = evdev.InputDevice(path)
        try:
            keys = set(dev.capabilities().get(ecodes.EV_KEY, []))
            ignored = any(name in dev.name for name in self.cfg.ignore_devices)
            has_hotkey = all(group & keys for group in self.cfg.hotkey)
            if not has_hotkey or ecodes.KEY_A not in keys or ignored:
                self.skipped[path] = node
                dev.close()
                return
            self.loop.add_reader(dev.fd, self._on_readable, path)
        except OSError:
            dev.close()
            raise
        self.devices[path] = dev
        log.info("Teclado: %s (%s)", dev.name, path)

    def _drop_device(self, path: str) -> None:
        dev = self.devices.pop(path, None)
        if dev is None:
            return
        self.loop.remove_reader(dev.fd)
        try:
            dev.close()
        except OSError:
            pass

    def _on_readable(self, path: str) -> None:
        dev = self.devices.get(path)
        if dev is None:
            return
        try:
            events = list(dev.read())
        except BlockingIOError:
            return
        except OSError:
            log.info("Teclado removido: %s", path)
            self._drop_device(path)
            self._cancel_recording("teclado removido")
            self._resync_pressed()
            return
        for ev in events:
            if ev.type == ecodes.EV_KEY:
                self._on_key(ev.code, ev.value)
            elif ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_DROPPED:
                self._resync_pressed()

    def _resync_pressed(self) -> None:
        """Relê do kernel quais teclas estão seguradas (corrige eventos de soltar perdidos)."""
        pressed: set[int] = set()
        for dev in list(self.devices.values()):
            try:
                pressed.update(dev.active_keys())
            except OSError:
                pass
        self.pressed = pressed
        self._update_keys_released()

    def _update_keys_released(self) -> None:
        if self.pressed:
            self.keys_released.clear()
        else:
            self.keys_released.set()

    def _combo_held(self) -> bool:
        return all(group & self.pressed for group in self.cfg.hotkey)

    def _on_key(self, code: int, value: int) -> None:
        if value == 2:  # autorepeat
            return
        hot = self.cfg.hotkey_codes
        if value == 1:
            self.pressed.add(code)
            if code in hot:
                # Grava quando a combinação fica completa, e só se nenhuma outra tecla estiver segurada.
                if self.recorder is None and self._combo_held():
                    self._resync_pressed()
                    if self._combo_held() and not (self.pressed - hot):
                        self._start()
            elif self.recorder is not None:
                # A combinação virou parte de um atalho (ex.: Ctrl+Super+Seta), não é ditado.
                self._cancel_recording("atalho com outra tecla")
        else:
            self.pressed.discard(code)
            if code in hot and not self._combo_held():
                self._finish()
        self._update_keys_released()

    # --- gravação ---------------------------------------------------------

    def _start(self) -> None:
        try:
            self.recorder = Recorder()
        except OSError as exc:
            log.error("Não foi possível iniciar o pw-record: %s", exc)
            notify(f"Falha ao abrir o microfone: {exc}")
            return
        self.max_timer = self.loop.call_later(self.cfg.max_sec, self._finish)
        log.info("Gravando...")

    def _take_audio(self) -> np.ndarray:
        rec, self.recorder = self.recorder, None
        if self.max_timer is not None:
            self.max_timer.cancel()
            self.max_timer = None
        return rec.stop()

    def _cancel_recording(self, reason: str) -> None:
        if self.recorder is None:
            return
        self._take_audio()
        log.info("Gravação descartada (%s)", reason)

    def _finish(self) -> None:
        if self.recorder is None:
            return
        audio = self._take_audio()
        secs = len(audio) / SAMPLE_RATE
        if secs < self.cfg.min_sec:
            log.info("Gravação curta demais (%.2fs), ignorada", secs)
            return
        self.worker.submit(self._transcribe_and_paste, audio)

    def _wait_keys_released(self) -> None:
        """Espera o usuário soltar todas as teclas (ex.: já segurando o Ctrl para a próxima frase)."""
        deadline = time.monotonic() + self.cfg.max_sec
        while not self.keys_released.wait(timeout=1.0):
            if self.stopping or time.monotonic() > deadline:
                return
            self.loop.call_soon_threadsafe(self._resync_pressed)

    def _transcribe_and_paste(self, audio: np.ndarray) -> None:
        started = time.perf_counter()
        try:
            text = self.transcriber(audio)
            if not text:
                log.info("Nenhuma fala reconhecida")
                return
            self._wait_keys_released()
            if self.stopping:
                return
            paste(text + " " if self.cfg.trailing_space else text, self.cfg)
            log.info(
                "%.1fs de áudio -> %d caracteres em %.2fs",
                len(audio) / SAMPLE_RATE,
                len(text),
                time.perf_counter() - started,
            )
            log.debug("Texto: %s", text)
        except Exception:
            log.exception("Falha ao transcrever/colar")
            notify("Falha no ditado. Veja: journalctl --user -u whisper-ditado")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    log.setLevel(logging.DEBUG if os.environ.get("DITADO_DEBUG") == "1" else logging.INFO)
    cfg = Config.from_env()
    transcriber = Transcriber(cfg)
    asyncio.run(Ditado(cfg, transcriber).run())


if __name__ == "__main__":
    main()
