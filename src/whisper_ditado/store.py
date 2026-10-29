"""Persistência SQLite das reuniões, falantes, vozes e configurações.

Convenções:
- ids de reunião são uuid4 hex, gerados por `create_meeting`; ids de falante e de
  voz vêm do chamador (se vierem vazios, são gerados como uuid4 hex);
- datas são gravadas em ISO 8601 (TEXT) e lidas como `datetime`;
- embeddings são gravados como `float32.tobytes()`;
- nada é apagado automaticamente: só `delete_*` (e a troca de transcrição em
  `replace_transcript`) removem linhas.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from whisper_ditado.issues.prompts import KIND_LABELS, PRIORITIES
from whisper_ditado.issues.legacy import migration_sql

_SCHEMA_V1 = """
CREATE TABLE meetings (
    id TEXT PRIMARY KEY,
    title TEXT,
    title_auto INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'recording'
        CHECK (status IN ('recording','processing','ready','failed')),
    error TEXT,
    followed_app TEXT
);
CREATE TABLE voices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    embedding BLOB,
    n_samples INTEGER NOT NULL DEFAULT 1,
    source_meeting_id TEXT,
    source_speaker_id TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE speakers (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    label TEXT,
    voice_id TEXT REFERENCES voices(id) ON DELETE SET NULL,
    is_me INTEGER NOT NULL DEFAULT 0,
    color_index INTEGER NOT NULL DEFAULT 0,
    embedding BLOB
);
CREATE TABLE segments (
    id INTEGER PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    speaker_id TEXT REFERENCES speakers(id) ON DELETE CASCADE,
    source TEXT NOT NULL CHECK (source IN ('mic','pc')),
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    text TEXT NOT NULL,
    dictated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE summaries (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL
);
CREATE TABLE dictation_spans (
    id INTEGER PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL
);
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX idx_speakers_meeting ON speakers(meeting_id);
CREATE INDEX idx_segments_meeting ON segments(meeting_id, start_s);
CREATE INDEX idx_segments_speaker ON segments(speaker_id);
CREATE INDEX idx_alerts_meeting ON alerts(meeting_id);
CREATE INDEX idx_spans_meeting ON dictation_spans(meeting_id);
CREATE INDEX idx_meetings_started ON meetings(started_at);
"""

_SCHEMA_V2 = """
CREATE TABLE meeting_jobs (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,              -- summary | tasks
    state TEXT NOT NULL,             -- running | done | error | cancelled
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (meeting_id, kind)
);
CREATE TABLE task_suggestions (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    origin TEXT NOT NULL,            -- claude | manual
    kind TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    project TEXT, product_id INTEGER, assignee_id INTEGER, team_id INTEGER, stage_id INTEGER,
    priority TEXT NOT NULL DEFAULT 'medium',
    suggested_json TEXT, evidence_json TEXT NOT NULL DEFAULT '[]', repeats_number INTEGER,
    edited INTEGER NOT NULL DEFAULT 0, discarded INTEGER NOT NULL DEFAULT 0,
    create_state TEXT NOT NULL DEFAULT 'none',
    create_error TEXT, kbgen_number INTEGER, kbgen_url TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX task_suggestions_meeting ON task_suggestions(meeting_id, position);
"""

_SCHEMA_V4 = """
CREATE TABLE dictation_samples (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    duration_s REAL NOT NULL,
    transcript TEXT NOT NULL,
    pasted INTEGER NOT NULL,
    error TEXT,
    config_json TEXT NOT NULL,
    evaluation_json TEXT,
    evaluated_at TEXT
);
CREATE INDEX dictation_samples_created ON dictation_samples(created_at);
"""

_SCHEMA_V5 = """
ALTER TABLE dictation_samples ADD COLUMN raw_transcript TEXT;
ALTER TABLE dictation_samples ADD COLUMN review_json TEXT;
"""
_SCHEMA_V6 = """
CREATE TABLE meeting_prints (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    at_s REAL NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('flameshot','native')),
    file_name TEXT NOT NULL,
    origin_name TEXT,
    origin_mtime_ns INTEGER,
    created_at TEXT NOT NULL,
    seq INTEGER NOT NULL,
    UNIQUE (meeting_id, origin_name, origin_mtime_ns),
    UNIQUE (meeting_id, seq)
);
CREATE INDEX idx_prints_meeting ON meeting_prints(meeting_id, at_s);
ALTER TABLE task_suggestions ADD COLUMN print_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE task_suggestions ADD COLUMN attach_state TEXT NOT NULL DEFAULT 'none';
ALTER TABLE task_suggestions ADD COLUMN attach_error TEXT;
"""

# Embeddings de modelos diferentes nunca são comparados: cada voz guarda um por modelo.
# Os existentes são do pyannote; as colunas `voices.embedding`/`voices.n_samples` ficam
# como legado e não são mais lidas nem escritas. Quem já tinha token segue no pyannote.
_SCHEMA_V7 = """
CREATE TABLE voice_embeddings (
    voice_id TEXT NOT NULL REFERENCES voices(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    embedding BLOB NOT NULL,
    n_samples INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT,
    PRIMARY KEY (voice_id, model)
);
INSERT INTO voice_embeddings(voice_id, model, embedding, n_samples, updated_at)
    SELECT id, 'pyannote-community-1', embedding, n_samples, updated_at FROM voices WHERE embedding IS NOT NULL;
ALTER TABLE speakers ADD COLUMN embedding_model TEXT;
UPDATE speakers SET embedding_model = 'pyannote-community-1' WHERE embedding IS NOT NULL;
INSERT OR IGNORE INTO settings(key, value) SELECT 'diarizer', '"pyannote"'
    WHERE EXISTS (SELECT 1 FROM settings WHERE key = 'hf_token' AND value NOT IN ('null', '""'));
INSERT OR IGNORE INTO settings(key, value)
    SELECT 'voice_match_threshold_pyannote', value FROM settings WHERE key = 'voice_match_threshold';
DELETE FROM settings WHERE key = 'voice_match_threshold';
"""

# Reunião arquivada sai da lista principal, mas continua inteira e editável.
_SCHEMA_V8 = """
ALTER TABLE meetings ADD COLUMN archived_at TEXT;
CREATE INDEX idx_meetings_archived ON meetings(archived_at);
"""

# Índice = versão destino (`PRAGMA user_version` após aplicar).
_MIGRATIONS = [
    _SCHEMA_V1, _SCHEMA_V2, migration_sql, _SCHEMA_V4, _SCHEMA_V5, _SCHEMA_V6, _SCHEMA_V7,
    _SCHEMA_V8,
]


@dataclass
class Meeting:
    id: str
    title: str | None
    title_auto: bool
    started_at: datetime
    ended_at: datetime | None
    status: str
    error: str | None = None
    followed_app: str | None = None
    archived_at: datetime | None = None


@dataclass
class MeetingSummary:
    id: str
    title: str | None
    started_at: datetime
    duration_s: float
    speakers_count: int
    status: str


@dataclass
class Speaker:
    id: str
    meeting_id: str
    label: str | None = None
    voice_id: str | None = None
    is_me: bool = False
    color_index: int = 0
    embedding: np.ndarray | None = None
    embedding_model: str | None = None


@dataclass
class Segment:
    id: int | None
    meeting_id: str
    speaker_id: str | None
    source: str
    start_s: float
    end_s: float
    text: str
    dictated: bool = False


@dataclass
class VoiceEmbedding:
    vector: np.ndarray
    n_samples: int = 1
    updated_at: datetime | None = None


@dataclass
class Voice:
    id: str
    name: str
    embeddings: dict[str, VoiceEmbedding] = field(default_factory=dict)  # modelo -> embedding
    source_meeting_id: str | None = None
    source_speaker_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Alert:
    id: int | None
    meeting_id: str
    kind: str
    start_s: float
    end_s: float


@dataclass
class Job:
    meeting_id: str
    kind: str
    state: str
    error: str | None
    updated_at: str


@dataclass
class MeetingPrint:
    id: str
    meeting_id: str
    at_s: float
    source: str
    file_name: str
    origin_name: str | None
    origin_mtime_ns: int | None
    created_at: str
    seq: int  # número do print na reunião (P1, P2...); não muda quando outro print é apagado

    @property
    def label(self) -> str:
        return f"P{self.seq}"


@dataclass
class TaskSuggestion:
    id: str
    meeting_id: str
    origin: str
    position: int
    kind: str
    title: str
    description: str
    project: str | None
    product_id: int | None
    assignee_id: int | None
    team_id: int | None
    stage_id: int | None
    priority: str
    suggested: dict | None
    evidence: list[dict]
    edited: bool
    discarded: bool
    create_state: str
    create_error: str | None
    issue_number: int | None
    issue_url: str | None
    repeats_number: int | None
    created_at: str
    updated_at: str
    print_ids: list[str] = field(default_factory=list)
    attach_state: str = "none"  # none | sending | sent | unsupported | failed
    attach_error: str | None = None


class TaskLocked(Exception):
    """A sugestão já foi (ou está sendo) criada no MCP de issues e não aceita mais mudanças."""


# Campos de conteúdo de uma sugestão: os que o operador edita e que vão para o MCP de issues.
CONTENT_FIELDS = ("kind", "title", "description", "project", "product_id", "assignee_id",
                  "team_id", "stage_id", "priority")
_LOCKED_STATES = ("creating", "created", "uncertain")
# Campos que o preenchimento pelo último usado completa; a origem de cada um fica em `suggested["fill"]`.
FILL_FIELDS = ("assignee_id", "team_id")
_FINISH_STATES = ("created", "failed", "uncertain")
_ATTACH_STATES = ("none", "sending", "sent", "unsupported", "failed")
_RESTART_TASK_ERROR = "O serviço reiniciou durante a criação — confira no MCP de issues"
_RESTART_JOB_ERROR = "Interrompido: o serviço reiniciou"


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


def _blob(emb: np.ndarray | None) -> bytes | None:
    if emb is None:
        return None
    return np.asarray(emb, dtype=np.float32).tobytes()


def _emb(blob: bytes | None) -> np.ndarray | None:
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32).copy()


def _like_pattern(query: str) -> str:
    escaped = query.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# Colunas atualizáveis por `update_*` e o conversor de cada uma para o banco.
_MEETING_FIELDS = {
    "title": None,
    "title_auto": int,
    "started_at": _iso,
    "ended_at": _iso,
    "status": None,
    "error": None,
    "followed_app": None,
    "archived_at": _iso,
}
_SPEAKER_FIELDS = {
    "label": None,
    "voice_id": None,
    "is_me": int,
    "color_index": None,
    "embedding": _blob,
    "embedding_model": None,
}


class MeetingBusy(Exception):
    """A reunião ou suas criações externas ainda precisam ser concluídas."""


class Store:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        if not self._path.exists():
            # cria já com 0600, sem janela em que o arquivo fique legível por outros
            os.close(os.open(self._path, os.O_CREAT | os.O_WRONLY, 0o600))
        os.chmod(self._path, 0o600)
        # isolation_level=None: transações explícitas (BEGIN/COMMIT) em `_tx`.
        self._conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        # LIKE do SQLite só ignora maiúsculas em ASCII; casefold cobre acentos.
        self._conn.create_function("casefold", 1, lambda s: s.casefold() if s else s,
                                   deterministic=True)
        self._migrate()

    # -- infraestrutura -------------------------------------------------

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for target, script in enumerate(_MIGRATIONS, start=1):
                if version >= target:
                    continue
                script = script() if callable(script) else script
                self._conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={target};\nCOMMIT;")

    @contextmanager
    def _tx(self):
        """Transação única (commit ao sair, rollback em exceção), sob o lock."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- reuniões -------------------------------------------------------

    @staticmethod
    def _meeting(row: sqlite3.Row) -> Meeting:
        return Meeting(
            id=row["id"], title=row["title"], title_auto=bool(row["title_auto"]),
            started_at=_dt(row["started_at"]), ended_at=_dt(row["ended_at"]),
            status=row["status"], error=row["error"], followed_app=row["followed_app"],
            archived_at=_dt(row["archived_at"]),
        )

    def create_meeting(self, started_at: datetime) -> Meeting:
        mid = uuid.uuid4().hex
        with self._tx() as c:
            c.execute(
                "INSERT INTO meetings(id, title_auto, started_at, status) VALUES (?,0,?,'recording')",
                (mid, started_at.isoformat()),
            )
        return Meeting(id=mid, title=None, title_auto=False, started_at=started_at,
                       ended_at=None, status="recording")

    def get_meeting(self, meeting_id: str) -> Meeting | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        return self._meeting(row) if row else None

    def list_meetings(
        self, query: str | None = None, *, archived: bool | None = False
    ) -> list[MeetingSummary]:
        """Reuniões mais recentes primeiro; `archived=None` traz arquivadas e não arquivadas."""
        sql = """
            SELECT m.id, m.title, m.started_at, m.status, m.ended_at,
                   (SELECT COUNT(*) FROM speakers sp WHERE sp.meeting_id = m.id) AS speakers_count,
                   (SELECT COALESCE(MAX(sg.end_s), 0) FROM segments sg WHERE sg.meeting_id = m.id)
                       AS last_end
            FROM meetings m
        """
        params: list = []
        where: list[str] = []
        if archived is not None:
            where.append("m.archived_at IS NOT NULL" if archived else "m.archived_at IS NULL")
        if query:
            where.append("""(casefold(m.title) LIKE ? ESCAPE '\\'
                   OR EXISTS (SELECT 1 FROM segments sg
                              WHERE sg.meeting_id = m.id AND casefold(sg.text) LIKE ? ESCAPE '\\'))""")
            pattern = _like_pattern(query)
            params = [pattern, pattern]
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY m.started_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            started, ended = _dt(r["started_at"]), _dt(r["ended_at"])
            # reunião encerrada: tempo de parede; em curso: fim do último segmento
            duration = (ended - started).total_seconds() if ended else float(r["last_end"])
            out.append(MeetingSummary(
                id=r["id"], title=r["title"], started_at=started, duration_s=duration,
                speakers_count=r["speakers_count"], status=r["status"],
            ))
        return out

    def count_archived(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM meetings WHERE archived_at IS NOT NULL"
            ).fetchone()[0]

    def set_archived(self, meeting_id: str, archived: bool) -> None:
        """Arquivar recusa (`MeetingBusy`) reunião gravando/processando; desarquivar sempre pode."""
        with self._tx() as c:
            if not archived:
                c.execute("UPDATE meetings SET archived_at=NULL WHERE id=?", (meeting_id,))
                return
            cur = c.execute(
                "UPDATE meetings SET archived_at=? WHERE id=?"
                " AND status NOT IN ('recording','processing')",
                (datetime.now().isoformat(), meeting_id),
            )
            if cur.rowcount == 0:
                raise MeetingBusy("A reunião ainda está sendo gravada ou processada")

    def update_meeting(self, meeting_id: str, **fields) -> None:
        self._update("meetings", meeting_id, _MEETING_FIELDS, fields)

    def update_auto_title(self, meeting_id: str, title: str) -> None:
        """Só substitui um título que ainda é automático (ou não foi preenchido)."""
        with self._tx() as c:
            c.execute(
                "UPDATE meetings SET title=?, title_auto=1 WHERE id=?"
                " AND (title IS NULL OR title='' OR title_auto=1)",
                (title, meeting_id),
            )

    def delete_meeting(self, meeting_id: str, *, only_finished: bool = False) -> None:
        with self._tx() as c:
            if only_finished:
                meeting = c.execute("SELECT status FROM meetings WHERE id=?", (meeting_id,)).fetchone()
                if meeting and meeting["status"] in ("recording", "processing"):
                    raise MeetingBusy("A reunião ainda está sendo gravada ou processada")
                pending = c.execute(
                    "SELECT 1 FROM task_suggestions WHERE meeting_id=?"
                    " AND create_state IN ('creating','uncertain') LIMIT 1", (meeting_id,),
                ).fetchone()
                if pending:
                    raise MeetingBusy(
                        "Há tarefas sendo criadas ou aguardando conferência no MCP de issues. "
                        "Conclua a criação ou confira o resultado antes de excluir a reunião."
                    )
            c.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))

    def meetings_with_status(self, status: str) -> list[Meeting]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM meetings WHERE status=? ORDER BY started_at", (status,)
            ).fetchall()
        return [self._meeting(r) for r in rows]

    def _update(self, table: str, row_id: str, allowed: dict, fields: dict) -> None:
        unknown = set(fields) - set(allowed)
        if unknown:
            raise ValueError(f"campos inválidos para {table}: {sorted(unknown)}")
        if not fields:
            return
        values = []
        for name, value in fields.items():
            conv = allowed[name]
            values.append(conv(value) if conv and value is not None else value)
        assignments = ", ".join(f"{name}=?" for name in fields)
        with self._tx() as c:
            c.execute(f"UPDATE {table} SET {assignments} WHERE id=?", [*values, row_id])

    # -- transcrição ----------------------------------------------------

    @staticmethod
    def _speaker(row: sqlite3.Row) -> Speaker:
        return Speaker(
            id=row["id"], meeting_id=row["meeting_id"], label=row["label"],
            voice_id=row["voice_id"], is_me=bool(row["is_me"]),
            color_index=row["color_index"], embedding=_emb(row["embedding"]),
            embedding_model=row["embedding_model"],
        )

    @staticmethod
    def _segment(row: sqlite3.Row) -> Segment:
        return Segment(
            id=row["id"], meeting_id=row["meeting_id"], speaker_id=row["speaker_id"],
            source=row["source"], start_s=row["start_s"], end_s=row["end_s"],
            text=row["text"], dictated=bool(row["dictated"]),
        )

    def replace_transcript(
        self, meeting_id: str, speakers: list[Speaker], segments: list[Segment]
    ) -> None:
        """Troca falantes e segmentos da reunião numa única transação.

        Ids de falante vazios são gerados; `meeting_id` das linhas é forçado ao
        da reunião; ids de segmento são sempre atribuídos pelo banco.
        """
        with self._tx() as c:
            c.execute("DELETE FROM segments WHERE meeting_id=?", (meeting_id,))
            c.execute("DELETE FROM speakers WHERE meeting_id=?", (meeting_id,))
            for sp in speakers:
                c.execute(
                    "INSERT INTO speakers(id, meeting_id, label, voice_id, is_me, color_index,"
                    " embedding, embedding_model) VALUES (?,?,?,?,?,?,?,?)",
                    (sp.id or uuid.uuid4().hex, meeting_id, sp.label, sp.voice_id,
                     int(sp.is_me), sp.color_index, _blob(sp.embedding), sp.embedding_model),
                )
            for sg in segments:
                c.execute(
                    "INSERT INTO segments(meeting_id, speaker_id, source, start_s, end_s, text, dictated)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (meeting_id, sg.speaker_id, sg.source, sg.start_s, sg.end_s,
                     sg.text, int(sg.dictated)),
                )

    def get_speakers(self, meeting_id: str) -> list[Speaker]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM speakers WHERE meeting_id=? ORDER BY rowid", (meeting_id,)
            ).fetchall()
        return [self._speaker(r) for r in rows]

    def get_segments(self, meeting_id: str) -> list[Segment]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM segments WHERE meeting_id=? ORDER BY start_s, id", (meeting_id,)
            ).fetchall()
        return [self._segment(r) for r in rows]

    def update_speaker(self, speaker_id: str, **fields) -> None:
        self._update("speakers", speaker_id, _SPEAKER_FIELDS, fields)

    def merge_speakers(self, src_id: str, dst_id: str) -> None:
        """Move os segmentos de `src` para `dst` e apaga `src` (mesma reunião)."""
        if src_id == dst_id:
            raise ValueError("src e dst são o mesmo falante")
        with self._tx() as c:
            rows = c.execute(
                "SELECT id, meeting_id FROM speakers WHERE id IN (?,?)", (src_id, dst_id)
            ).fetchall()
            meetings = {r["id"]: r["meeting_id"] for r in rows}
            if len(meetings) != 2 or meetings[src_id] != meetings[dst_id]:
                raise ValueError("falantes inexistentes ou de reuniões diferentes")
            c.execute("UPDATE segments SET speaker_id=? WHERE speaker_id=?", (dst_id, src_id))
            c.execute("DELETE FROM speakers WHERE id=?", (src_id,))

    # -- resumo, alertas, ditado ---------------------------------------

    def set_summary(self, meeting_id: str, text: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO summaries(meeting_id, text, created_at) VALUES (?,?,?)"
                " ON CONFLICT(meeting_id) DO UPDATE SET text=excluded.text,"
                " created_at=excluded.created_at",
                (meeting_id, text, datetime.now().isoformat()),
            )

    def get_summary(self, meeting_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT text FROM summaries WHERE meeting_id=?", (meeting_id,)
            ).fetchone()
        return row["text"] if row else None

    def add_alert(self, meeting_id: str, kind: str, start_s: float, end_s: float) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO alerts(meeting_id, kind, start_s, end_s) VALUES (?,?,?,?)",
                (meeting_id, kind, start_s, end_s),
            )

    def delete_alerts(self, meeting_id: str, kind: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM alerts WHERE meeting_id=? AND kind=?", (meeting_id, kind))

    def get_alerts(self, meeting_id: str) -> list[Alert]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alerts WHERE meeting_id=? ORDER BY start_s, id", (meeting_id,)
            ).fetchall()
        return [Alert(r["id"], r["meeting_id"], r["kind"], r["start_s"], r["end_s"]) for r in rows]

    def add_dictation_span(self, meeting_id: str, start_s: float, end_s: float) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO dictation_spans(meeting_id, start_s, end_s) VALUES (?,?,?)",
                (meeting_id, start_s, end_s),
            )

    def get_dictation_spans(self, meeting_id: str) -> list[tuple[float, float]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT start_s, end_s FROM dictation_spans WHERE meeting_id=? ORDER BY start_s, id",
                (meeting_id,),
            ).fetchall()
        return [(r["start_s"], r["end_s"]) for r in rows]

    # -- vozes ----------------------------------------------------------

    @staticmethod
    def _voice(row: sqlite3.Row, embeddings: dict[str, VoiceEmbedding]) -> Voice:
        return Voice(
            id=row["id"], name=row["name"], embeddings=embeddings,
            source_meeting_id=row["source_meeting_id"],
            source_speaker_id=row["source_speaker_id"],
            created_at=_dt(row["created_at"]), updated_at=_dt(row["updated_at"]),
        )

    def _voice_embeddings(self, voice_id: str | None = None) -> dict[str, dict[str, VoiceEmbedding]]:
        """voice_id -> modelo -> embedding (de uma voz, ou de todas). Chamar sob o lock."""
        sql = "SELECT * FROM voice_embeddings"
        params: tuple = ()
        if voice_id is not None:
            sql += " WHERE voice_id=?"
            params = (voice_id,)
        out: dict[str, dict[str, VoiceEmbedding]] = {}
        for r in self._conn.execute(sql + " ORDER BY model", params).fetchall():
            out.setdefault(r["voice_id"], {})[r["model"]] = VoiceEmbedding(
                _emb(r["embedding"]), r["n_samples"], _dt(r["updated_at"]))
        return out

    @staticmethod
    def _put_voice_embedding(c: sqlite3.Connection, voice_id: str, model: str,
                             emb: VoiceEmbedding) -> None:
        c.execute(
            "INSERT INTO voice_embeddings(voice_id, model, embedding, n_samples, updated_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(voice_id, model) DO UPDATE SET"
            " embedding=excluded.embedding, n_samples=excluded.n_samples,"
            " updated_at=excluded.updated_at",
            (voice_id, model, _blob(emb.vector), emb.n_samples, _iso(emb.updated_at)),
        )

    def upsert_voice(self, voice: Voice) -> Voice:
        """Insere ou atualiza a voz (id vazio gera um uuid4 hex). Retorna a voz gravada.

        Grava cada item de `voice.embeddings`; modelos ausentes do dicionário não são apagados.
        """
        vid = voice.id or uuid.uuid4().hex
        now = datetime.now()
        created = voice.created_at or now
        updated = voice.updated_at or now
        with self._tx() as c:
            c.execute(
                "INSERT INTO voices(id, name, source_meeting_id, source_speaker_id, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
                " source_meeting_id=excluded.source_meeting_id,"
                " source_speaker_id=excluded.source_speaker_id, updated_at=excluded.updated_at",
                (vid, voice.name, voice.source_meeting_id, voice.source_speaker_id,
                 created.isoformat(), updated.isoformat()),
            )
            for model, emb in voice.embeddings.items():
                self._put_voice_embedding(c, vid, model, emb)
        return self.get_voice(vid)

    def set_voice_embedding(self, voice_id: str, model: str, emb: VoiceEmbedding) -> None:
        """Grava (ou substitui) o embedding de um modelo da voz."""
        with self._tx() as c:
            self._put_voice_embedding(c, voice_id, model, emb)

    def list_voices(self) -> list[Voice]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM voices ORDER BY name, id").fetchall()
            embeddings = self._voice_embeddings()
        return [self._voice(r, embeddings.get(r["id"], {})) for r in rows]

    def get_voice(self, voice_id: str) -> Voice | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM voices WHERE id=?", (voice_id,)).fetchone()
            embeddings = self._voice_embeddings(voice_id) if row else {}
        return self._voice(row, embeddings.get(voice_id, {})) if row else None

    def linked_speakers(self, voice_id: str) -> list[Speaker]:
        """Falantes (não "Você") ligados à voz, pela ordem das reuniões."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT sp.* FROM speakers sp JOIN meetings m ON m.id = sp.meeting_id"
                " WHERE sp.voice_id=? AND sp.is_me=0 ORDER BY m.started_at, sp.rowid",
                (voice_id,),
            ).fetchall()
        return [self._speaker(r) for r in rows]

    def delete_voice(self, voice_id: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM voices WHERE id=?", (voice_id,))

    # -- trabalhos pós-reunião e sugestões de tarefas -------------------

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(meeting_id=row["meeting_id"], kind=row["kind"], state=row["state"],
                   error=row["error"], updated_at=row["updated_at"])

    @staticmethod
    def _task(row: sqlite3.Row) -> TaskSuggestion:
        return TaskSuggestion(
            id=row["id"], meeting_id=row["meeting_id"], origin=row["origin"],
            position=row["position"], kind=row["kind"], title=row["title"],
            description=row["description"], project=row["project"],
            product_id=row["product_id"], assignee_id=row["assignee_id"],
            team_id=row["team_id"], stage_id=row["stage_id"], priority=row["priority"],
            suggested=json.loads(row["suggested_json"]) if row["suggested_json"] else None,
            evidence=json.loads(row["evidence_json"]),
            edited=bool(row["edited"]), discarded=bool(row["discarded"]),
            create_state=row["create_state"], create_error=row["create_error"],
            issue_number=row["issue_number"], issue_url=row["issue_url"],
            repeats_number=row["repeats_number"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            print_ids=json.loads(row["print_ids_json"]),
            attach_state=row["attach_state"], attach_error=row["attach_error"],
        )

    @staticmethod
    def _is_missing_meeting(exc: sqlite3.IntegrityError) -> bool:
        return "FOREIGN KEY" in str(exc).upper()

    def set_job(self, meeting_id: str, kind: str, state: str, error: str | None = None) -> bool:
        """Grava o estado do trabalho; False se a reunião não existe (foi excluída)."""
        try:
            with self._tx() as c:
                c.execute(
                    "INSERT INTO meeting_jobs(meeting_id, kind, state, error, updated_at)"
                    " VALUES (?,?,?,?,?) ON CONFLICT(meeting_id, kind) DO UPDATE SET"
                    " state=excluded.state, error=excluded.error, updated_at=excluded.updated_at",
                    (meeting_id, kind, state, error, datetime.now().isoformat()),
                )
        except sqlite3.IntegrityError as exc:
            if not self._is_missing_meeting(exc):
                raise
            return False
        return True

    def get_job(self, meeting_id: str, kind: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM meeting_jobs WHERE meeting_id=? AND kind=?", (meeting_id, kind)
            ).fetchone()
        return self._job(row) if row else None

    def list_tasks(self, meeting_id: str) -> list[TaskSuggestion]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM task_suggestions WHERE meeting_id=? ORDER BY position, rowid",
                (meeting_id,),
            ).fetchall()
        return [self._task(r) for r in rows]

    def get_task(self, task_id: str) -> TaskSuggestion | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task_suggestions WHERE id=?", (task_id,)
            ).fetchone()
        return self._task(row) if row else None

    @staticmethod
    def _check_content(fields: dict) -> None:
        unknown = set(fields) - set(CONTENT_FIELDS)
        if unknown:
            raise ValueError(f"campos inválidos para a sugestão: {sorted(unknown)}")
        if "kind" in fields and fields["kind"] not in KIND_LABELS:
            raise ValueError(f"tipo inválido: {fields['kind']!r}")
        if "priority" in fields and fields["priority"] not in PRIORITIES:
            raise ValueError(f"prioridade inválida: {fields['priority']!r}")

    def _insert_task(
        self, c: sqlite3.Connection, meeting_id: str, fields: dict, origin: str,
        suggested: dict | None, evidence: list | None, repeats_number: int | None,
        print_ids: list[str] | None = None,
    ) -> str:
        """Insere ao fim da lista da reunião (dentro de uma transação aberta)."""
        self._check_content(fields)
        row = {"kind": "tarefa", "title": "", "description": "", "priority": "medium",
               "project": None, "product_id": None, "assignee_id": None,
               "team_id": None, "stage_id": None, **fields}
        position = c.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM task_suggestions WHERE meeting_id=?",
            (meeting_id,),
        ).fetchone()[0]
        tid = uuid.uuid4().hex
        now = datetime.now().isoformat()
        c.execute(
            "INSERT INTO task_suggestions(id, meeting_id, position, origin, kind, title,"
            " description, project, product_id, assignee_id, team_id, stage_id, priority,"
            " suggested_json, evidence_json, repeats_number, print_ids_json,"
            " created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, meeting_id, position, origin, *(row[name] for name in CONTENT_FIELDS),
             json.dumps(suggested) if suggested is not None else None,
             json.dumps(evidence or []), repeats_number, json.dumps(print_ids or []), now, now),
        )
        return tid

    def add_task(
        self, meeting_id: str, fields: dict, *, origin: str, suggested: dict | None = None,
        evidence: list | None = None, repeats_number: int | None = None,
        print_ids: list[str] | None = None,
    ) -> TaskSuggestion | None:
        """Acrescenta uma sugestão (campos ausentes ficam no padrão); None se a reunião sumiu."""
        try:
            with self._tx() as c:
                tid = self._insert_task(c, meeting_id, fields, origin, suggested, evidence,
                                        repeats_number, print_ids)
        except sqlite3.IntegrityError as exc:
            if not self._is_missing_meeting(exc):
                raise
            return None
        return self.get_task(tid)

    def replace_scan_tasks(self, meeting_id: str, items: list[dict]) -> list[TaskSuggestion]:
        """Troca as sugestões do Claude ainda intocadas pelas de um novo levantamento.

        Ficam: as editadas, as descartadas, as já enviadas (qualquer `create_state` diferente
        de `none`) e as manuais. Os itens novos entram depois delas. Devolve a lista completa
        da reunião; `[]` se a reunião não existe mais.
        """
        try:
            with self._tx() as c:
                c.execute(
                    "DELETE FROM task_suggestions WHERE meeting_id=? AND origin='claude'"
                    " AND edited=0 AND discarded=0 AND create_state='none'",
                    (meeting_id,),
                )
                for item in items:
                    self._insert_task(
                        c, meeting_id, item["fields"], "claude", item.get("suggested"),
                        item.get("evidence"), item.get("repeats_number"), item.get("print_ids"),
                    )
        except sqlite3.IntegrityError as exc:
            if not self._is_missing_meeting(exc):
                raise
            return []
        return self.list_tasks(meeting_id)

    @staticmethod
    def _with_fill_marks(suggested_json: str | None, fields, mark: str) -> str:
        """`suggested_json` com `suggested["fill"][campo] = mark` para cada campo."""
        suggested = json.loads(suggested_json) if suggested_json else {}
        suggested["fill"] = {**suggested.get("fill", {}), **dict.fromkeys(fields, mark)}
        return json.dumps(suggested)

    def update_task(self, task_id: str, changes: dict) -> TaskSuggestion:
        """Aplica mudanças de conteúdo e/ou `discarded`; só mudança de conteúdo marca `edited`.

        Responsável e equipe enviados (mesmo vazios ou com o mesmo valor) ficam marcados como
        escolha do operador (`suggested["fill"][campo] = "manual"`); só a marca não é edição.
        Levanta `TaskLocked` se a sugestão já está sendo criada/foi criada/está incerta e
        `KeyError` se não existe.
        """
        content = {k: v for k, v in changes.items() if k != "discarded"}
        self._check_content(content)
        with self._tx() as c:
            row = c.execute("SELECT * FROM task_suggestions WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["create_state"] in _LOCKED_STATES:
                raise TaskLocked(task_id)
            sets: dict = {name: value for name, value in content.items() if row[name] != value}
            if sets:
                sets["edited"] = 1
            if "discarded" in changes:
                sets["discarded"] = int(bool(changes["discarded"]))
            chosen = [name for name in FILL_FIELDS if name in content]
            if chosen:
                marked = self._with_fill_marks(row["suggested_json"], chosen, "manual")
                if json.loads(marked) != json.loads(row["suggested_json"] or "{}"):
                    sets["suggested_json"] = marked
            if sets:
                sets["updated_at"] = datetime.now().isoformat()
                assignments = ", ".join(f"{name}=?" for name in sets)
                c.execute(f"UPDATE task_suggestions SET {assignments} WHERE id=?",
                          [*sets.values(), task_id])
        return self.get_task(task_id)

    def tasks_for_product(self, product_id: int, limit: int = 500) -> list[TaskSuggestion]:
        """Sugestões do produto em todas as reuniões, da mudança mais recente para a mais antiga."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM task_suggestions WHERE product_id=?"
                " ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (product_id, limit),
            ).fetchall()
        return [self._task(r) for r in rows]

    def fill_task(self, task_id: str, values: dict[str, int], expected_updated_at: str) -> bool:
        """Preenchimento automático: grava `values` marcados como "last", sem marcar `edited`.

        Só grava se a sugestão continua pendente e intocada desde a leitura (`updated_at` igual
        a `expected_updated_at`): uma edição do operador no meio vence. Devolve se gravou.
        """
        unknown = set(values) - set(FILL_FIELDS)
        if unknown:
            raise ValueError(f"campos inválidos para o preenchimento: {sorted(unknown)}")
        if not values:
            return False
        with self._tx() as c:
            row = c.execute(
                "SELECT suggested_json FROM task_suggestions WHERE id=? AND updated_at=?"
                " AND discarded=0 AND create_state IN ('none','failed')",
                (task_id, expected_updated_at),
            ).fetchone()
            if row is None:
                return False
            sets = {**values, "suggested_json": self._with_fill_marks(row["suggested_json"], values, "last"),
                    "updated_at": datetime.now().isoformat()}
            assignments = ", ".join(f"{name}=?" for name in sets)
            c.execute(f"UPDATE task_suggestions SET {assignments} WHERE id=?", [*sets.values(), task_id])
        return True

    def claim_tasks_for_create(self, task_ids: list[str]) -> list[str]:
        """Reserva (`creating`) as sugestões que podem ser criadas; devolve só as reservadas.

        Atômico: duas chamadas com o mesmo id nunca reservam o mesmo item.
        """
        ids = list(dict.fromkeys(task_ids))
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        with self._tx() as c:
            claimed = [r["id"] for r in c.execute(
                f"SELECT id FROM task_suggestions WHERE id IN ({marks}) AND discarded=0"
                " AND create_state IN ('none','failed')", ids,
            ).fetchall()]
            if claimed:
                inner = ",".join("?" for _ in claimed)
                c.execute(
                    "UPDATE task_suggestions SET create_state='creating', create_error=NULL,"
                    f" updated_at=? WHERE id IN ({inner})",
                    [datetime.now().isoformat(), *claimed],
                )
        order = {tid: i for i, tid in enumerate(ids)}
        return sorted(claimed, key=order.__getitem__)

    def update_and_claim(self, items: list[tuple[str, dict]]) -> list[str]:
        """Grava o conteúdo e reserva (`creating`) as sugestões elegíveis, numa só transação.

        Elegível: `create_state` em `none`/`failed` e não descartada. As outras (e ids que não
        existem) ficam intactas e não voltam. Conteúdo inválido levanta `ValueError` antes de
        gravar qualquer coisa. Id repetido: vale o primeiro. Devolve os ids reservados, na ordem.
        """
        for _, content in items:
            self._check_content(content)
        claimed: list[str] = []
        with self._tx() as c:
            for task_id, content in items:
                row = c.execute(
                    "SELECT * FROM task_suggestions WHERE id=? AND discarded=0"
                    " AND create_state IN ('none','failed')", (task_id,),
                ).fetchone()
                if row is None:
                    continue
                now = datetime.now().isoformat()
                sets: dict = {name: value for name, value in content.items() if row[name] != value}
                if sets:
                    sets["edited"] = 1
                sets.update(create_state="creating", create_error=None, updated_at=now)
                assignments = ", ".join(f"{name}=?" for name in sets)
                c.execute(f"UPDATE task_suggestions SET {assignments} WHERE id=?",
                          [*sets.values(), task_id])
                claimed.append(task_id)
        return claimed

    def finish_create(
        self, task_id: str, state: str, *, number: int | None = None, url: str | None = None,
        error: str | None = None,
    ) -> None:
        """Encerra uma criação: `created`, `failed` ou `uncertain` (sem efeito se o item sumiu)."""
        if state not in _FINISH_STATES:
            raise ValueError(f"estado de criação inválido: {state!r}")
        with self._tx() as c:
            c.execute(
                "UPDATE task_suggestions SET create_state=?, issue_number=?, issue_url=?,"
                " create_error=?, updated_at=? WHERE id=?",
                (state, number, url, error, datetime.now().isoformat(), task_id),
            )

    def resolve_uncertain(self, task_id: str, number: int | None) -> TaskSuggestion:
        """Resolve um item incerto: sem número volta a `none` (criar de novo); com número, `created`.

        Levanta `TaskLocked` se o item não está incerto e `KeyError` se não existe.
        """
        with self._tx() as c:
            row = c.execute(
                "SELECT create_state FROM task_suggestions WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["create_state"] != "uncertain":
                raise TaskLocked(task_id)
            if number is None:
                state, num = "none", None
            else:
                state, num = "created", number
            c.execute(
                "UPDATE task_suggestions SET create_state=?, issue_number=?, issue_url=NULL,"
                " create_error=NULL, updated_at=? WHERE id=?",
                (state, num, datetime.now().isoformat(), task_id),
            )
        return self.get_task(task_id)

    def set_task_print_ids(self, task_id: str, print_ids: list[str]) -> TaskSuggestion:
        """Troca os prints anexados à sugestão (todos devem ser prints da reunião dela).

        Mudar a lista marca `edited` (um novo levantamento preserva a escolha do operador);
        a mesma lista não grava nada. Levanta `TaskLocked` se a sugestão já está sendo
        criada/foi criada/está incerta, `KeyError` se não existe e `ValueError` se algum id
        não é print da reunião.
        """
        ids = list(dict.fromkeys(print_ids))
        with self._tx() as c:
            row = c.execute(
                "SELECT meeting_id, create_state, print_ids_json FROM task_suggestions WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["create_state"] in _LOCKED_STATES:
                raise TaskLocked(task_id)
            if ids:
                marks = ",".join("?" for _ in ids)
                found = c.execute(
                    f"SELECT COUNT(*) FROM meeting_prints WHERE meeting_id=? AND id IN ({marks})",
                    [row["meeting_id"], *ids],
                ).fetchone()[0]
                if found != len(ids):
                    raise ValueError("print inexistente nesta reunião")
            if json.loads(row["print_ids_json"]) != ids:
                c.execute(
                    "UPDATE task_suggestions SET print_ids_json=?, edited=1, updated_at=? WHERE id=?",
                    (json.dumps(ids), datetime.now().isoformat(), task_id),
                )
        return self.get_task(task_id)

    def set_attach_state(self, task_id: str, state: str, error: str | None = None) -> None:
        """Grava o estado do anexo dos prints (sem efeito se o item sumiu)."""
        if state not in _ATTACH_STATES:
            raise ValueError(f"estado de anexo inválido: {state!r}")
        with self._tx() as c:
            c.execute(
                "UPDATE task_suggestions SET attach_state=?, attach_error=?, updated_at=?"
                " WHERE id=?",
                (state, error, datetime.now().isoformat(), task_id),
            )

    def recover_interrupted(self) -> None:
        """Na subida: criações, anexos e trabalhos que ficaram no meio viram incerto/erro."""
        now = datetime.now().isoformat()
        with self._tx() as c:
            c.execute(
                "UPDATE task_suggestions SET create_state='uncertain', create_error=?,"
                " updated_at=? WHERE create_state='creating'",
                (_RESTART_TASK_ERROR, now),
            )
            c.execute(
                "UPDATE task_suggestions SET attach_state='failed', attach_error=?,"
                " updated_at=? WHERE attach_state='sending'",
                (_RESTART_JOB_ERROR, now),
            )
            c.execute(
                "UPDATE meeting_jobs SET state='error', error=?, updated_at=?"
                " WHERE state='running'",
                (_RESTART_JOB_ERROR, now),
            )

    # -- prints de tela das reuniões --------------------------------------

    @staticmethod
    def _print(row: sqlite3.Row) -> MeetingPrint:
        return MeetingPrint(
            id=row["id"], meeting_id=row["meeting_id"], at_s=row["at_s"], source=row["source"],
            file_name=row["file_name"], origin_name=row["origin_name"],
            origin_mtime_ns=row["origin_mtime_ns"], created_at=row["created_at"], seq=row["seq"],
        )

    def add_print(
        self, meeting_id: str, *, print_id: str, at_s: float, source: str, file_name: str,
        origin_name: str | None = None, origin_mtime_ns: int | None = None,
    ) -> MeetingPrint | None:
        """Registra um print; None se já existe (mesma origem) ou se a reunião sumiu.

        O número (`seq`) é o maior da reunião + 1, na ordem de inserção: quem insere vários
        de uma vez (importação do Flameshot) o faz na ordem de `at_s`.
        """
        try:
            with self._tx() as c:
                c.execute(
                    "INSERT INTO meeting_prints(id, meeting_id, at_s, source, file_name,"
                    " origin_name, origin_mtime_ns, created_at, seq) VALUES (?,?,?,?,?,?,?,?,"
                    " (SELECT COALESCE(MAX(seq), 0) + 1 FROM meeting_prints WHERE meeting_id=?))",
                    (print_id, meeting_id, at_s, source, file_name, origin_name,
                     origin_mtime_ns, datetime.now().isoformat(), meeting_id),
                )
        except sqlite3.IntegrityError as exc:
            if not (self._is_missing_meeting(exc) or "UNIQUE" in str(exc).upper()):
                raise
            return None
        return self.get_print(print_id)

    def list_prints(self, meeting_id: str) -> list[MeetingPrint]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM meeting_prints WHERE meeting_id=? ORDER BY at_s, created_at",
                (meeting_id,),
            ).fetchall()
        return [self._print(r) for r in rows]

    def get_print(self, print_id: str) -> MeetingPrint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM meeting_prints WHERE id=?", (print_id,)
            ).fetchone()
        return self._print(row) if row else None

    def delete_print(self, print_id: str) -> MeetingPrint | None:
        """Apaga o print e o tira das sugestões da reunião, na mesma transação."""
        with self._tx() as c:
            row = c.execute("SELECT * FROM meeting_prints WHERE id=?", (print_id,)).fetchone()
            if row is None:
                return None
            c.execute("DELETE FROM meeting_prints WHERE id=?", (print_id,))
            for task in c.execute(
                "SELECT id, print_ids_json FROM task_suggestions WHERE meeting_id=?",
                (row["meeting_id"],),
            ).fetchall():
                ids = json.loads(task["print_ids_json"])
                if print_id in ids:
                    c.execute(
                        "UPDATE task_suggestions SET print_ids_json=? WHERE id=?",
                        (json.dumps([i for i in ids if i != print_id]), task["id"]),
                    )
        return self._print(row)

    # -- exemplos de ditado para avaliação -------------------------------

    def add_dictation_sample(self, sample_id: str, *, created_at: str, duration_s: float,
                             transcript: str, pasted: bool, error: str | None, config: dict,
                             raw_transcript: str | None = None) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO dictation_samples"
                "(id,created_at,duration_s,transcript,pasted,error,config_json,raw_transcript)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (sample_id, created_at, duration_s, transcript, int(pasted), error,
                 json.dumps(config, ensure_ascii=False), raw_transcript),
            )

    def list_dictation_samples(self, *, pending_only: bool = False,
                              limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM dictation_samples"
        if pending_only:
            sql += " WHERE evaluation_json IS NULL AND review_json IS NULL"
        sql += " ORDER BY created_at, id"
        params = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._dictation_sample(row) for row in rows]

    @staticmethod
    def _dictation_sample(row) -> dict:
        item = dict(row)
        item["pasted"] = bool(item["pasted"])
        item["config"] = json.loads(item.pop("config_json"))
        for field in ("evaluation", "review"):
            value = item.pop(f"{field}_json")
            item[field] = json.loads(value) if value else None
        return item

    def get_dictation_sample(self, sample_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM dictation_samples WHERE id=?",
                                     (sample_id,)).fetchone()
        return self._dictation_sample(row) if row is not None else None

    def set_dictation_review(self, sample_id: str, review: dict) -> None:
        """Preserva a avaliação da IA e os dados originais da captura."""
        with self._tx() as c:
            changed = c.execute("UPDATE dictation_samples SET review_json=? WHERE id=?",
                                (json.dumps(review, ensure_ascii=False), sample_id))
            if not changed.rowcount:
                raise KeyError(sample_id)

    def set_dictation_evaluation(self, sample_id: str, evaluation: dict) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE dictation_samples SET evaluation_json=?, evaluated_at=? WHERE id=?",
                (json.dumps(evaluation, ensure_ascii=False), datetime.now().isoformat(), sample_id),
            )

    # -- configurações --------------------------------------------------

    def get_setting(self, key: str, default=None):
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value) -> None:
        self.set_settings({key: value})

    def set_settings(self, values: dict) -> None:
        """Grava um conjunto de configurações numa única transação."""
        with self._tx() as c:
            c.executemany(
                "INSERT INTO settings(key, value) VALUES (?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(key, json.dumps(value)) for key, value in values.items()],
            )

    def all_settings(self) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}
