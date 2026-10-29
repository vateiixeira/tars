"""Trabalhos depois que a reunião fica pronta: resumo, levantamento de tarefas e criação no MCP de issues.

Só o resumo é automático (com os prints da reunião como imagens); o levantamento roda apenas quando
pedido (`rescan`) e também envia os prints.

Rodam em duas threads daemon próprias, fora da thread da GPU e fora do loop do asyncio:

- `issues-claude`: resumo automático e levantamento manual (cada um pode levar minutos);
- `issues-create`: criação das issues (uma por vez, em série).

Cada passo grava o estado em `meeting_jobs` e publica `meeting_job` (`running` e o estado final);
quando as sugestões mudam, publica `tasks_changed`. Reunião excluída no meio do caminho: o trabalho
termina em silêncio (o banco devolve `False`/`[]`). Nenhum erro escapa das threads, e nenhuma
sugestão fica em `creating`: toda saída da criação grava `created`, `failed` ou `uncertain`.
Depois de uma criação `created`, os prints da sugestão são anexados à issue (melhor esforço):
o resultado vai só para `attach_state` e nunca muda `create_state`.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path

from whisper_ditado.issues import autofill, runner
from whisper_ditado.issues.client import IssueClient, IssueError, OptionsCache, issue_fields_from
from whisper_ditado.issues.scan import run_scan
from whisper_ditado.prints.files import print_path
from whisper_ditado.prints.images import MAX_EDGE, ClaudeImages, jpeg_bytes, load_for_claude, print_labels
from whisper_ditado.claude_cli import ClaudeCli, ClaudeError, sync_summary_cli, sync_tasks_cli, transcript_text
from whisper_ditado.settings import Settings
from whisper_ditado.procs import Cancellation, ProcessCancelled, cancellation_scope
from whisper_ditado.store import Store
from whisper_ditado.worker import SerialWorker

log = logging.getLogger("ditado")

STAGE_NAME = "To Do"
NO_SUMMARY_TEXT = "O Claude devolveu um resumo vazio"
SUMMARY_UNEXPECTED = "Erro inesperado ao gerar o resumo"
SCAN_UNEXPECTED = "Erro inesperado no levantamento"
CREATE_UNEXPECTED = "Erro inesperado na criação — confira no MCP de issues"
NO_TRANSCRIPT_SCAN = "A reunião não tem transcrição para levantar tarefas"
SHUTDOWN_NOT_SENT = "Interrompido: o serviço está encerrando — nada foi criado"
ATTACH_UNEXPECTED = "Erro inesperado ao anexar os prints"


class JobBusy(Exception):
    """O trabalho pedido já está em andamento para essa reunião."""


class PostMeetingJobs:
    def __init__(self, store: Store, settings: Settings, bus, claude: ClaudeCli,
                 issues_client: IssueClient, options: OptionsCache, tasks_claude: ClaudeCli) -> None:
        self._store = store
        self._settings = settings
        self._bus = bus
        self._claude = claude  # resumo: `claude_*`
        self._tasks_claude = tasks_claude  # levantamento, opções e criação: `tasks_*` (o mesmo cli do `issues_client`)
        self._issues_client = issues_client
        self._options = options
        self._claim_lock = threading.Lock()  # tira a corrida entre `rescan` e `on_ready` pelo job `tasks`
        self._scans: dict[str, Cancellation] = {}
        self._stopping = threading.Event()
        self._claude_worker = SerialWorker("issues-claude")
        self._create_worker = SerialWorker("issues-create")

    # -- API ----------------------------------------------------------------------

    def on_ready(self, meeting_id: str) -> None:
        """Chamado na thread da GPU quando a reunião fica pronta: só enfileira e volta."""
        self._claude_worker.submit(self._after_ready, meeting_id, gated=False)

    def rescan(self, meeting_id: str, *, context: str = "") -> None:
        """Pede um novo levantamento. `ValueError` (pt-BR) se não dá para rodar; `JobBusy` se já roda."""
        if self._store.get_meeting(meeting_id) is None:
            raise ValueError("Reunião não encontrada")
        if not self._settings.get("issues_enabled"):
            raise ValueError("O criador de issues está desligado")
        root = self._root()
        if not root.is_dir():
            raise ValueError(f"Pasta raiz não encontrada: {root}")
        if not self._store.get_segments(meeting_id):
            raise ValueError(NO_TRANSCRIPT_SCAN)
        cancellation = self._claim_scan(meeting_id)
        if cancellation is None:
            raise JobBusy("O levantamento já está em andamento")
        self._claude_worker.submit(self._scan, meeting_id, context, cancellation, gated=False)

    def cancel_scan(self, meeting_id: str) -> None:
        """Cancela só este levantamento, inclusive se ainda estiver na fila. Idempotente."""
        with self._claim_lock:
            cancellation = self._scans.get(meeting_id)
            if cancellation is None:
                return
            cancellation.cancel()
            self._finish(meeting_id, "tasks", "cancelled")
            del self._scans[meeting_id]

    def create(self, task_ids: list[str]) -> None:
        """Enfileira a criação dos ids (já reservados como `creating` pelo chamador)."""
        self._create_worker.submit(self._create_all, list(task_ids), gated=False)

    def shutdown(self) -> None:
        """Mata os `claude` em execução (`procs.kill_all`: resumo, levantamento, opções e criação) e
        descarta o que está na fila. Não espera as threads (daemon)."""
        self._stopping.set()
        runner.kill_all()

    # -- depois da reunião pronta -----------------------------------------------------

    def _after_ready(self, meeting_id: str) -> None:
        try:
            segments = self._store.get_segments(meeting_id)
            if not segments or self._stopping.is_set():
                return
            if self._settings.get("auto_summary") and not self._store.get_summary(meeting_id):
                self._summarize(meeting_id)
        except Exception:
            log.exception("Falha nos trabalhos pós-reunião de %s", meeting_id)

    # -- resumo -------------------------------------------------------------------------

    def _summarize(self, meeting_id: str) -> None:
        if self._stopping.is_set():  # o serviço está encerrando: não inicia outro `claude`
            return
        if not self._begin(meeting_id, "summary"):
            return
        try:
            segments = self._store.get_segments(meeting_id)
            transcript = transcript_text(segments, self._store.get_speakers(meeting_id))
            sync_summary_cli(self._claude, self._settings)
            ci = self._load_images(meeting_id)
            text = self._claude.summarize(transcript, self._settings.get("summary_prompt"), images=ci.parts)
            if not text or not text.strip():
                raise ClaudeError(NO_SUMMARY_TEXT)
            self._store.set_summary(meeting_id, text)
        except ClaudeError as exc:
            self._finish(meeting_id, "summary", "error", str(exc))
        except Exception:
            self._finish(meeting_id, "summary", "error", SUMMARY_UNEXPECTED)  # primeiro: o job nunca fica `running`
            self._log_unexpected(meeting_id, "Falha ao gerar o resumo de %s")
        else:
            self._finish(meeting_id, "summary", "done")

    # -- levantamento -------------------------------------------------------------------

    def _load_images(self, meeting_id: str) -> ClaudeImages:
        """Prints da reunião prontos para o Claude; qualquer falha vira log e lista vazia."""
        try:
            return load_for_claude(self._store, meeting_id)
        except Exception:
            log.exception("Não foi possível carregar os prints de %s; seguindo sem imagens", meeting_id)
            return ClaudeImages()

    def _claim_scan(self, meeting_id: str) -> Cancellation | None:
        """Reserva uma execução própria; None se o job já roda ou a reunião sumiu."""
        with self._claim_lock:
            job = self._store.get_job(meeting_id, "tasks")
            if job is not None and job.state == "running":
                return None
            if not self._store.set_job(meeting_id, "tasks", "running"):
                return None
            cancellation = Cancellation()
            self._scans[meeting_id] = cancellation
            self._publish_job(meeting_id, "tasks", "running")
            return cancellation

    def _scan(self, meeting_id: str, context: str = "", cancellation: Cancellation | None = None) -> None:
        """Roda o levantamento (o job `tasks` já está `running`, ver `_claim_scan`)."""
        if self._stopping.is_set() or cancellation is None:
            return
        try:
            with cancellation_scope(cancellation):
                root = self._root()
                if not root.is_dir():
                    raise ClaudeError(f"Pasta raiz não encontrada: {root}")
                segments = self._store.get_segments(meeting_id)
                if not segments:
                    raise ClaudeError(NO_TRANSCRIPT_SCAN)
                transcript = transcript_text(segments, self._store.get_speakers(meeting_id))
                summary = self._store.get_summary(meeting_id) or None
                options = None
                sync_tasks_cli(self._tasks_claude, self._settings)
                try:
                    options = self._options.get()
                except IssueError as exc:
                    log.warning("Levantamento sem as opções do MCP de issues: %s", exc)
                cancellation.check()
                existing = self._store.list_tasks(meeting_id)
                ci = self._load_images(meeting_id)
                items = run_scan(self._tasks_claude, root, self._settings.get("tasks_prompt"), transcript,
                                 summary, options, existing, segments, context=context, images=ci.parts)
                stage = options.stage_named(STAGE_NAME) if options is not None else None
                stage_id = stage.id if stage is not None else None
                # Cancelar e gravar o resultado disputam o mesmo lock. Uma execução antiga
                # nunca publica resultados nem estado por cima de um novo levantamento.
                with self._claim_lock:
                    cancellation.check()
                    self._store.replace_scan_tasks(meeting_id, [i.as_store_item(stage_id, ci.labels) for i in items])
                    self._fill_after_scan(meeting_id, options)
                    self._finish_scan(meeting_id, cancellation, "done")
        except ProcessCancelled:
            pass  # cancel_scan já publicou o estado; mantém as sugestões anteriores
        except ClaudeError as exc:
            with self._claim_lock:
                self._finish_scan(meeting_id, cancellation, "error", str(exc))
        except Exception:
            with self._claim_lock:
                self._finish_scan(meeting_id, cancellation, "error", SCAN_UNEXPECTED)
            self._log_unexpected(meeting_id, "Falha no levantamento de %s")

    def _fill_after_scan(self, meeting_id: str, options) -> None:
        """Completa pelo último usado os cartões novos; uma falha aqui não derruba o levantamento."""
        try:
            autofill.fill_from_sources(self._store, options, meeting_id)
        except Exception:
            log.exception("Falha ao preencher pelo último usado as sugestões de %s", meeting_id)

    def _finish_scan(self, meeting_id: str, cancellation: Cancellation, state: str,
                     error: str | None = None) -> None:
        """Chamado com _claim_lock adquirido; só a execução atual pode terminar o job."""
        if self._scans.get(meeting_id) is not cancellation:
            return
        if self._finish(meeting_id, "tasks", state, error) and state == "done":
            self._bus.publish({"type": "tasks_changed", "meeting_id": meeting_id})
        del self._scans[meeting_id]

    # -- criação no MCP de issues ---------------------------------------------------------------

    def _create_all(self, task_ids: list[str]) -> None:
        for task_id in task_ids:
            try:
                meeting_id = self._create_one(task_id)
            except Exception:  # defesa final: o item não pode ficar em `creating`
                log.exception("Falha ao criar a sugestão %s", task_id)
                meeting_id = self._finish_create(task_id, "uncertain", error=CREATE_UNEXPECTED)
            if meeting_id is not None:
                self._bus.publish({"type": "tasks_changed", "meeting_id": meeting_id})

    def _create_one(self, task_id: str) -> str | None:
        """Cria uma issue; devolve o `meeting_id` da sugestão (ou None se ela sumiu)."""
        task = self._store.get_task(task_id)
        if task is None:
            return None
        if self._stopping.is_set():  # nada foi enviado ainda: libera o item em vez de deixá-lo preso
            self._finish_create(task_id, "failed", error=SHUTDOWN_NOT_SENT)
            return task.meeting_id
        try:
            fields = issue_fields_from(task)
        except ValueError as exc:
            self._store.finish_create(task_id, "failed", error=str(exc))
            return task.meeting_id
        sync_tasks_cli(self._tasks_claude, self._settings)
        try:
            outcome = self._issues_client.create_issue(fields)
            self._store.finish_create(task_id, outcome.state, number=outcome.number,
                                      url=outcome.url, error=outcome.error)
        except Exception:
            # `create_issue` não levanta; se levantou (ou o estado era inválido), a issue pode existir.
            log.exception("Falha ao criar a sugestão %s", task_id)
            self._store.finish_create(task_id, "uncertain", error=CREATE_UNEXPECTED)
            return task.meeting_id
        if outcome.state == "created":
            self._propagate_created(task_id)
        if outcome.state == "created" and outcome.number is not None:
            try:
                self._attach(task_id, outcome.number, outcome.url)
            except Exception:
                # Nada do anexo pode chegar ao `_create_all`, que regravaria `create_state`.
                log.exception("Falha inesperada no anexo dos prints da sugestão %s", task_id)
                self._fail_sending_attach(task_id)
        return task.meeting_id

    def _propagate_created(self, task_id: str) -> None:
        """Leva responsável e equipe da issue criada aos cartões irmãos (nunca afeta a criação)."""
        try:
            task = self._store.get_task(task_id)
            if task is not None:
                autofill.propagate(self._store, self._options.peek(), task)
        except Exception:
            log.exception("Falha ao propagar o responsável e a equipe da sugestão %s", task_id)

    def _attach(self, task_id: str, number: int, url: str | None) -> None:
        """Anexa à issue criada os prints da sugestão que ainda existem em disco (melhor esforço)."""
        try:
            task = self._store.get_task(task_id)
            if task is None or not task.print_ids or self._stopping.is_set():
                return
            label_of = {pid: label for label, pid in print_labels(self._store.list_prints(task.meeting_id)).items()}
            found = []
            for print_id in task.print_ids:
                p = self._store.get_print(print_id)
                if p is None or print_id not in label_of:
                    continue
                path = print_path(p)
                if path.is_file():
                    found.append((label_of[print_id], path))
            if not found:
                return
        except Exception:
            log.exception("Não foi possível preparar os prints da sugestão %s", task_id)
            self._set_attach(task_id, "failed", ATTACH_UNEXPECTED)
            return
        try:
            with tempfile.TemporaryDirectory(prefix="whisper-ditado-prints-", ignore_cleanup_errors=True) as tmp:
                files = []
                for label, path in found:
                    try:
                        data = jpeg_bytes(path, MAX_EDGE)
                    except Exception as exc:  # um arquivo ruim não impede o anexo dos outros
                        log.warning("print %s ilegível, fica fora do anexo da sugestão %s: %s", label, task_id, exc)
                        continue
                    jpg = Path(tmp) / f"print-{label}.jpg"
                    jpg.write_bytes(data)
                    files.append((label, jpg))
                if not files:
                    return  # nenhum legível: como se a sugestão não tivesse prints
                self._set_attach(task_id, "sending")
                try:
                    self._bus.publish({"type": "tasks_changed", "meeting_id": task.meeting_id})  # a issue já aparece criada
                except Exception:
                    log.exception("Não foi possível avisar a mudança da sugestão %s", task_id)
                result = self._issues_client.attach_prints(number, url, files)
            self._store.set_attach_state(task_id, result.state, result.error)  # estado inválido cai no except
        except Exception:
            log.exception("Falha ao anexar os prints da sugestão %s", task_id)
            self._set_attach(task_id, "failed", ATTACH_UNEXPECTED)

    def _fail_sending_attach(self, task_id: str) -> None:
        """Garante um estado final: um anexo que ficou em `sending` vira `failed`."""
        try:
            task = self._store.get_task(task_id)
            if task is not None and task.attach_state == "sending":
                self._store.set_attach_state(task_id, "failed", ATTACH_UNEXPECTED)
        except Exception:
            log.exception("Não foi possível gravar o estado do anexo de %s", task_id)

    def _set_attach(self, task_id: str, state: str, error: str | None = None) -> None:
        try:
            self._store.set_attach_state(task_id, state, error)
        except Exception:
            log.exception("Não foi possível gravar o estado do anexo de %s", task_id)

    def _finish_create(self, task_id: str, state: str, *, error: str) -> str | None:
        try:
            task = self._store.get_task(task_id)
            self._store.finish_create(task_id, state, error=error)
            return task.meeting_id if task is not None else None
        except Exception:
            log.exception("Não foi possível gravar o fim da criação de %s", task_id)
            return None

    # -- auxiliares ---------------------------------------------------------------------

    def _root(self) -> Path:
        return Path(str(self._settings.get("issues_root"))).expanduser()

    def _begin(self, meeting_id: str, kind: str) -> bool:
        if not self._store.set_job(meeting_id, kind, "running"):
            return False
        self._publish_job(meeting_id, kind, "running")
        return True

    def _finish(self, meeting_id: str, kind: str, state: str, error: str | None = None) -> bool:
        """Grava o estado final; False (e nada publicado) se a reunião foi excluída ou o serviço parou."""
        if state == "error" and self._stopping.is_set():
            return False  # erro causado pelo encerramento: fica `running` e o próximo início grava "Interrompido"
        try:
            if not self._store.set_job(meeting_id, kind, state, error):
                return False
        except Exception:
            log.exception("Não foi possível gravar o estado do trabalho %s de %s", kind, meeting_id)
            return False
        self._publish_job(meeting_id, kind, state)
        return True

    def _log_unexpected(self, meeting_id: str, message: str) -> None:
        """Registra a falha inesperada (com traceback), exceto se a reunião foi excluída no meio."""
        try:
            if self._store.get_meeting(meeting_id) is None:
                return
        except Exception:
            log.exception("Não foi possível consultar a reunião %s", meeting_id)
        log.exception(message, meeting_id)

    def _publish_job(self, meeting_id: str, kind: str, state: str) -> None:
        self._bus.publish({"type": "meeting_job", "meeting_id": meeting_id, "kind": kind, "state": state})
