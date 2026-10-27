#!/usr/bin/env python3
"""Avalia o motor local de separação de falantes com as reuniões já gravadas.

Lê o banco **só em leitura** e os `pc.opus` das reuniões; nunca escreve em nenhum dos dois.
O gabarito é o que já está gravado: os falantes do pyannote nos segmentos `pc` e as ligações
falante→voz feitas pelo usuário. A saída não traz nomes nem texto: vozes viram "voz N" e reuniões
"reunião N".

    uv run --frozen python scripts/eval_diarization.py voices
    uv run --frozen python scripts/eval_diarization.py clusters --embedding <nome> \\
        --thresholds 0.6,0.7,0.8,0.9,1.0,1.1,1.2 --min-speaker 0,4,8,12 --max-minutes 20
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from whisper_ditado import paths
from whisper_ditado.meeting.calibration import (
    agreement,
    auc,
    from_model,
    leave_one_meeting_out_matches,
    leave_one_out_scores,
    meetings_with_other_model,
    pick_threshold,
)
from whisper_ditado.meeting.diarize import PYANNOTE_MODEL
from whisper_ditado.meeting.finalize import decode_opus
from whisper_ditado.meeting.local_diarize import LocalDiarizer, load_backend
from whisper_ditado.meeting.sherpa_models import EMBEDDINGS, SEGMENTATION, ensure_model
from whisper_ditado.meeting.speech import SAMPLE_RATE, usable


@dataclass
class Meeting:
    id: str
    name: str  # "reunião N", na ordem de início


@dataclass
class LinkedSpeaker:
    id: str
    meeting_id: str
    voice: str  # "voz N"
    spans: list[tuple[float, float]]
    pyannote: np.ndarray | None


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def connect(db: Path) -> sqlite3.Connection:
    if not db.exists():
        raise SystemExit(f"banco não encontrado: {db}")
    return sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)


def has_embedding_model(conn: sqlite3.Connection) -> bool:
    """A coluna `speakers.embedding_model` existe a partir da migração V6."""
    return any(r[1] == "embedding_model" for r in conn.execute("PRAGMA table_info(speakers)"))


def reran_meetings(conn: sqlite3.Connection) -> set[str]:
    """Reuniões cujos falantes já não são (só) do pyannote, por reprocessamento com o motor local."""
    if not has_embedding_model(conn):
        return set()
    rows = conn.execute("SELECT meeting_id, embedding_model FROM speakers").fetchall()
    return meetings_with_other_model(rows, PYANNOTE_MODEL)


def meetings(conn: sqlite3.Connection, root: Path) -> list[Meeting]:
    rows = conn.execute("SELECT id FROM meetings ORDER BY started_at, id").fetchall()
    found = [r[0] for r in rows if (root / r[0] / "pc.opus").exists()]
    return [Meeting(mid, f"reunião {i}") for i, mid in enumerate(found, 1)]


def linked_speakers(conn: sqlite3.Connection) -> list[LinkedSpeaker]:
    voice_order = [
        r[0] for r in conn.execute("SELECT id FROM voices ORDER BY created_at, id").fetchall()
    ]
    voice_name = {vid: f"voz {i}" for i, vid in enumerate(voice_order, 1)}
    out: list[LinkedSpeaker] = []
    model_col = "embedding_model" if has_embedding_model(conn) else "NULL"
    rows = conn.execute(
        f"SELECT id, meeting_id, voice_id, embedding, {model_col} FROM speakers"
        " WHERE voice_id IS NOT NULL ORDER BY meeting_id, id"
    ).fetchall()
    for sid, mid, vid, blob, emb_model in rows:
        spans = [
            (float(s), float(e))
            for s, e in conn.execute(
                "SELECT start_s, end_s FROM segments"
                " WHERE speaker_id = ? AND source = 'pc' ORDER BY start_s",
                (sid,),
            )
        ]
        # só embeddings do pyannote: depois de reprocessar com o motor local há vetores de outro
        # modelo (e de outra dimensão) na mesma coluna
        emb = None
        if blob and from_model(emb_model, PYANNOTE_MODEL):
            emb = usable(np.frombuffer(blob, dtype=np.float32))
        out.append(LinkedSpeaker(sid, mid, voice_name.get(vid, "voz ?"), spans, emb))
    return out


def reference(
    conn: sqlite3.Connection, meeting_id: str, max_s: float | None
) -> list[tuple[float, float, str]]:
    """Segmentos `pc` com falante do pyannote, cortados aos primeiros `max_s` segundos."""
    ref = []
    for s, e, spk in conn.execute(
        "SELECT start_s, end_s, speaker_id FROM segments"
        " WHERE meeting_id = ? AND source = 'pc' AND speaker_id IS NOT NULL ORDER BY start_s",
        (meeting_id,),
    ):
        s, e = float(s), float(e)
        if max_s is not None:
            e = min(e, max_s)
        if e > s:
            ref.append((s, e, str(spk)))
    return ref


Sample = tuple[str, str, np.ndarray]  # (reunião, voz, embedding)


def _separation_row(name: str, samples: list[Sample]) -> dict:
    by_voice: dict[str, list[np.ndarray]] = {}
    for _, voice, emb in samples:
        by_voice.setdefault(voice, []).append(emb)
    pos, neg = leave_one_out_scores(by_voice)
    row = {"modelo": name, "pos": len(pos), "neg": len(neg)}
    if not pos or not neg:
        return row
    t = pick_threshold(pos, neg)
    right, wrong, none = leave_one_meeting_out_matches(samples, t)
    row.update(
        sep=min(pos) - max(neg),
        min_pos=min(pos),
        max_neg=max(neg),
        auc=auc(pos, neg),
        limiar=t,
        tpr=float(np.mean(np.asarray(pos) >= t)),
        fpr=float(np.mean(np.asarray(neg) >= t)),
        casamento=f"{right}/{wrong}/{none}",
    )
    return row


def _fmt(v, spec: str = ".3f") -> str:
    return "—" if v is None else format(v, spec)


def cmd_voices(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    root = args.meetings_dir
    order = meetings(conn, root)
    speakers = linked_speakers(conn)
    names = list(EMBEDDINGS) if not args.embeddings else args.embeddings
    diarizers = {n: LocalDiarizer(embedding=n, num_threads=args.threads) for n in names}
    for n, d in diarizers.items():
        log(f"carregando {n}…")
        if not d.available:
            raise SystemExit(f"modelo indisponível: {n}: {d.last_error}")

    samples: dict[str, list[Sample]] = {n: [] for n in names}
    pyannote: list[Sample] = []
    seconds = {n: 0.0 for n in names}
    missing = {n: 0 for n in names}
    for m in order:
        here = [s for s in speakers if s.meeting_id == m.id]
        if not here:
            continue
        log(f"{m.name}: {len(here)} falante(s) ligado(s)")
        pyannote.extend((m.name, s.voice, s.pyannote) for s in here if s.pyannote is not None)
        audio = decode_opus(root / m.id / "pc.opus")
        for spk in here:
            for n, d in diarizers.items():
                t0 = time.perf_counter()
                emb = d.embed(audio, spk.spans)
                seconds[n] += time.perf_counter() - t0
                if emb is None:
                    missing[n] += 1
                else:
                    samples[n].append((m.name, spk.voice, emb))
        del audio

    voices = sorted({s.voice for s in speakers}, key=lambda v: int(v.split()[-1]))
    print(f"\n{len(speakers)} falantes ligados a {len(voices)} vozes em {len(order)} reuniões")
    for v in voices:
        k = sum(s.voice == v for s in speakers)
        print(f"  {v}: {k} falante(s)")
    print(
        "\n| modelo | positivos | negativos | separação | min pos | max neg | AUC | limiar"
        " | TPR | FPR | casamento certos/errados/sem voz | sem embedding | tempo (s) |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    rows = [(_separation_row(n, samples[n]), missing[n], seconds[n]) for n in names]
    rows.append((_separation_row("pyannote (gravado)", pyannote), None, None))
    for row, miss, secs in rows:
        print(
            f"| {row['modelo']} | {row['pos']} | {row['neg']} | {_fmt(row.get('sep'))}"
            f" | {_fmt(row.get('min_pos'))} | {_fmt(row.get('max_neg'))}"
            f" | {_fmt(row.get('auc'))} | {_fmt(row.get('limiar'), '.2f')}"
            f" | {_fmt(row.get('tpr'), '.2f')} | {_fmt(row.get('fpr'), '.2f')}"
            f" | {row.get('casamento', '—')} | {_fmt(miss, 'd')} | {_fmt(secs, '.1f')} |"
        )


class CachedBackend:
    """Repete o resultado do sherpa para o mesmo áudio (só o pós-processamento muda)."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._key: int | None = None
        self._segments: list[tuple[float, float, int]] = []
        self._chunks: dict[tuple[int, int], np.ndarray] = {}

    def segments(self, audio: np.ndarray) -> list[tuple[float, float, int]]:
        key = id(audio)
        if key != self._key:
            self._key = key
            self._chunks.clear()
            self._segments = self._inner.segments(audio)
        return self._segments

    def embed_chunk(self, chunk: np.ndarray) -> np.ndarray:
        key = (chunk.__array_interface__["data"][0], len(chunk))
        vec = self._chunks.get(key)
        if vec is None:
            vec = self._chunks[key] = self._inner.embed_chunk(chunk)
        return vec


def _floats(text: str) -> list[float]:
    return [float(x) for x in text.split(",") if x.strip()]


def cmd_clusters(args: argparse.Namespace) -> None:
    if args.embedding not in EMBEDDINGS:
        raise SystemExit(f"modelo de embedding desconhecido: {args.embedding}")
    conn = connect(args.db)
    root = args.meetings_dir
    max_s = args.max_minutes * 60.0 if args.max_minutes > 0 else None
    thresholds, mins = _floats(args.thresholds), _floats(args.min_speaker)
    cache = paths.models_dir()
    seg_path = ensure_model(SEGMENTATION, cache)
    emb_path = ensure_model(EMBEDDINGS[args.embedding], cache)

    # (limiar, mínimo) -> lista de (agreement, |erro nº falantes|, duração da referência)
    results: dict[tuple[float, float], list[tuple[float, int, float]]] = {}
    print(f"\nmodelo {args.embedding}, primeiros {args.max_minutes:g} min de cada reunião")
    print("\n| reunião | falantes ref | limiar | mínimo (s) | falantes | clusters sherpa | agreement |")
    print("|---|---|---|---|---|---|---|")
    reran = reran_meetings(conn)
    skipped = 0
    for m in meetings(conn, root):
        if m.id in reran:
            log(f"{m.name}: reprocessada com outro motor; a referência não é do pyannote; pulada")
            skipped += 1
            continue
        ref = reference(conn, m.id, max_s)
        if not ref:
            log(f"{m.name}: sem segmentos pc com falante no trecho; pulada")
            continue
        n_ref = len({label for _, _, label in ref})
        ref_s = sum(e - s for s, e, _ in ref)
        audio = decode_opus(root / m.id / "pc.opus")
        if max_s is not None:
            audio = np.ascontiguousarray(audio[: round(max_s * SAMPLE_RATE)])
        log(f"{m.name}: {len(audio) / SAMPLE_RATE / 60:.1f} min, {n_ref} falante(s) na referência")
        for t in thresholds:
            t0 = time.perf_counter()
            backend = CachedBackend(load_backend(seg_path, emb_path, t, args.threads))
            raw = None
            for ms in mins:
                d = LocalDiarizer(
                    embedding=args.embedding,
                    cluster_threshold=t,
                    min_speaker_s=ms,
                    num_threads=args.threads,
                    loader=lambda b=backend: b,
                )
                result = d.run(audio)
                if raw is None:
                    raw = len({c for _, _, c in backend.segments(audio)})
                hyp = [(x.start_s, x.end_s, x.speaker) for x in result.turns]
                n_hyp = len({label for _, _, label in hyp})
                score = agreement(ref, hyp)
                results.setdefault((t, ms), []).append((score, abs(n_hyp - n_ref), ref_s))
                print(f"| {m.name} | {n_ref} | {t:g} | {ms:g} | {n_hyp} | {raw} | {score:.3f} |")
            log(f"  limiar {t:g}: {time.perf_counter() - t0:.0f} s")
        del audio

    print(f"\nreuniões puladas por não terem mais a referência do pyannote: {skipped}")
    print("\n| limiar | mínimo (s) | agreement médio | agreement ponderado | erro médio nº falantes |")
    print("|---|---|---|---|---|")
    ranked = []
    for (t, ms), rows in results.items():
        mean = float(np.mean([r[0] for r in rows]))
        weighted = sum(r[0] * r[2] for r in rows) / sum(r[2] for r in rows)
        err = float(np.mean([r[1] for r in rows]))
        ranked.append((t, ms, mean, weighted, err))
        print(f"| {t:g} | {ms:g} | {mean:.3f} | {weighted:.3f} | {err:.2f} |")
    if ranked:
        best = max(ranked, key=lambda r: (round(r[2], 3), -r[4]))
        print(
            f"\nmelhor: limiar {best[0]:g}, mínimo {best[1]:g} s"
            f" (agreement {best[2]:.3f}, erro {best[4]:.2f})"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=None, help="banco (padrão: o do TARS)")
    parser.add_argument("--meetings-dir", type=Path, default=None, help="pasta das reuniões")
    parser.add_argument("--threads", type=int, default=6)
    sub = parser.add_subparsers(dest="mode", required=True)
    voices = sub.add_parser("voices", help="separação entre vozes por modelo de embedding")
    voices.add_argument("--embeddings", type=lambda s: s.split(","), default=None)
    clusters = sub.add_parser("clusters", help="concordância com o pyannote por parâmetro")
    clusters.add_argument("--embedding", required=True)
    clusters.add_argument("--thresholds", default="0.6,0.7,0.8,0.9,1.0,1.1,1.2")
    clusters.add_argument("--min-speaker", default="0,4,8,12")
    clusters.add_argument("--max-minutes", type=float, default=20.0)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.db = args.db or paths.db_path()
    args.meetings_dir = args.meetings_dir or args.db.parent / "meetings"
    if args.mode == "voices":
        cmd_voices(args)
    else:
        cmd_clusters(args)


if __name__ == "__main__":
    main()
