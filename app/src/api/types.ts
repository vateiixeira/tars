// Tipos do contrato da API local do backend (src/whisper_ditado/api.py, settings.py e os eventos
// publicados no EventBus). Datas chegam como ISO 8601 sem fuso (horário local do backend).

/** Conteúdo de `api.json` devolvido pelo comando Rust `api_info`. */
export interface ApiInfo {
  port: number;
  token: string;
}

export type MeetingState = "idle" | "recording" | "processing";
export type MeetingStatus = "recording" | "processing" | "ready" | "failed";
export type AlertKind = "mic_silent" | "pc_silent" | "pc_wrong_output" | "mic_dead" | "pc_dead";
/** Motor de separação de falantes (`diarizer` em `/settings`). */
export type Diarizer = "local" | "pyannote";
/** Alertas gravados pelo processamento da reunião (meeting/finalize.py e meeting/engines.py). */
export type DiarizationAlertKind =
  | "no_diarization"
  | "pyannote_no_token"
  | "pyannote_missing_extra"
  | "pyannote_failed";
export type TrackSource = "mic" | "pc";
/** Etapas do processamento (meeting/finalize.py). */
export type ProgressStage = "transcrevendo" | "separando vozes" | "finalizando";

/** `GET /status` e o evento `status`. */
export interface Status {
  dictation: {
    enabled: boolean;
    active: boolean;
    key: string;
    output_key?: string;
    source?: TrackSource;
  };
  meeting: {
    state: MeetingState;
    meeting_id: string | null;
    started_at: string | null;
    followed_app: string | null;
    alerts: AlertKind[];
  };
}

/** Item de `GET /meetings`. */
export interface MeetingListItem {
  id: string;
  title: string | null;
  started_at: string;
  duration_s: number;
  speakers_count: number;
  status: MeetingStatus;
}

export interface Speaker {
  id: string;
  label: string | null;
  voice_id: string | null;
  is_me: boolean;
  color_index: number;
}

export interface Segment {
  id: number;
  speaker_id: string | null;
  source: TrackSource;
  start_s: number;
  end_s: number;
  text: string;
  dictated: boolean;
}

/** Trecho de alerta gravado na reunião (`alerts` do detalhe). */
export interface Alert {
  kind: AlertKind | DiarizationAlertKind;
  start_s: number;
  end_s: number;
}

/** `GET /meetings/{id}`. */
export interface MeetingDetail {
  id: string;
  title: string | null;
  started_at: string;
  ended_at: string | null;
  status: MeetingStatus;
  error: string | null;
  followed_app: string | null;
  speakers: Speaker[];
  segments: Segment[];
  summary: string | null;
  alerts: Alert[];
  summary_job: JobState | null;
}

export interface Voice {
  id: string;
  name: string;
  n_samples: number;
  created_at: string | null;
  updated_at: string | null;
}

/** Esforço do Claude Code; `null` = o padrão dele. */
export type Effort = "low" | "medium" | "high" | "xhigh" | "max";

/** `GET /claude/models` e `POST /claude/models/refresh`. */
export interface ClaudeModels {
  default_model: string | null;
  default_label: string | null;
  models: { alias: string; model_id: string; label: string; is_new: boolean }[];
  updated_at: string | null;
}

/** `GET /settings`: todas as chaves de `DEFAULTS`, sem `hf_token` (só `hf_token_set`). */
export interface Settings {
  dictation_enabled: boolean;
  dictation_evaluation_enabled: boolean;
  key: string;
  output_key: string;
  paste: string;
  model: string;
  compute_type: string;
  prompt: string | null;
  trailing_space: boolean;
  min_sec: number;
  max_sec: number;
  ignore_devices: string[];
  ydotool: string;
  auto_title: boolean;
  summary_prompt: string;
  diarizer: Diarizer;
  voice_match_threshold_local: number;
  voice_match_threshold_pyannote: number;
  mic_device: string | null;
  pc_device: string | null;
  follow_call_app: boolean;
  aec_enabled: boolean;
  claude_bin: string | null;
  claude_model: string | null;
  claude_effort: Effort | null;
  tasks_model: string | null;
  tasks_effort: Effort | null;
  auto_summary: boolean;
  meeting_model_idle_sec: number;
  issues_enabled: boolean;
  issues_root: string;
  issues_mcp_url: string | null;
  issues_mcp_name: string;
  tasks_prompt: string;
  hf_token_set: boolean;
}

/** Corpo de `PUT /settings`: qualquer subconjunto; `hf_token` só é aceito na escrita. */
export type SettingsUpdate = Partial<Omit<Settings, "hf_token_set" | "tasks_prompt">> & {
  hf_token?: string | null;
  /** `null` restaura o prompt padrão. */
  tasks_prompt?: string | null;
};

export interface AudioDevice {
  name: string;
  description: string;
}

/** `GET /devices`. */
export interface Devices {
  sources: AudioDevice[];
  sinks: AudioDevice[];
  default_source: string | null;
  default_sink: string | null;
}

/** Item de `POST /meetings/{id}/name-suggestions`. */
export interface NameSuggestion {
  speaker_id: string;
  label: string;
  name: string;
  evidence: string;
}

export type TextKind = "vocab" | "subs";

// -- tarefas (criador de issues / MCP de issues) ---------------------------------------------------------

export type TaskKind = "feature" | "tarefa" | "bugfix";
export type TaskPriority = "low" | "medium" | "high" | "urgent";
export type CreateState = "none" | "creating" | "created" | "uncertain" | "failed";

/** Tarefa em segundo plano do backend (resumo ou levantamento de tarefas). */
export interface JobState {
  state: "running" | "done" | "error" | "cancelled";
  error: string | null;
  updated_at: string;
}

/** Trecho da transcrição que justifica a tarefa. */
export interface Evidence {
  start_s: number;
  end_s: number;
  speaker: string;
  text: string;
}

/** Campos editáveis de uma tarefa (corpo de criação/edição e de `tasks/create`). */
export interface TaskFields {
  kind: TaskKind;
  title: string;
  description: string;
  project: string | null;
  product_id: number | null;
  assignee_id: number | null;
  team_id: number | null;
  stage_id: number | null;
  priority: TaskPriority;
}

export interface TaskSuggestion extends TaskFields {
  id: string;
  meeting_id: string;
  origin: "claude" | "manual";
  position: number;
  suggested: {
    product_id: number | null;
    assignee_id: number | null;
    team_id: number | null;
    assignee_hint: string | null;
    team_hint: string | null;
  } | null;
  evidence: Evidence[];
  edited: boolean;
  discarded: boolean;
  create_state: CreateState;
  create_error: string | null;
  issue_number: number | null;
  issue_url: string | null;
  repeats_number: number | null;
}

/** `GET /meetings/{id}/tasks`. */
export interface TasksPayload {
  enabled: boolean;
  scan: JobState | null;
  items: TaskSuggestion[];
}

export interface IssueOption {
  id: number;
  name: string;
}

/** `GET /issues/options`. */
export interface IssueOptions {
  username: string;
  people: IssueOption[];
  teams: IssueOption[];
  products: IssueOption[];
  stages: IssueOption[];
  fetched_at: string;
}

/** Resposta de `POST /meetings/{id}/tasks/create`. */
export interface CreateResult {
  accepted: string[];
  rejected: { id: string; reason: string }[];
}

// -- eventos do WebSocket `/api/events` ------------------------------------------------------

export type StatusEvent = { type: "status" } & Status;
export interface DictationEvent {
  type: "dictation";
  active: boolean;
}
/** Níveis em dBFS (piso do backend: -90). */
export interface LevelsEvent {
  type: "levels";
  mic_db: number;
  pc_db: number;
}
export interface AlertEvent {
  type: "alert";
  kind: AlertKind;
  active: boolean;
  message: string;
}
export interface MeetingUpdatedEvent {
  type: "meeting_updated";
  meeting_id: string;
  status: MeetingStatus;
}
export interface ProgressEvent {
  type: "progress";
  meeting_id: string;
  stage: ProgressStage;
}
/** Resumo ou levantamento de tarefas mudou de estado. */
export interface MeetingJobEvent {
  type: "meeting_job";
  meeting_id: string;
  kind: "summary" | "tasks";
  state: JobState["state"];
}
/** Os itens de tarefa da reunião mudaram (criação, levantamento concluído, edição). */
export interface TasksChangedEvent {
  type: "tasks_changed";
  meeting_id: string;
}

export type ServerEvent =
  | StatusEvent
  | DictationEvent
  | LevelsEvent
  | AlertEvent
  | MeetingUpdatedEvent
  | ProgressEvent
  | MeetingJobEvent
  | TasksChangedEvent;

export type ServerEventType = ServerEvent["type"];
export type EventOf<T extends ServerEventType> = Extract<ServerEvent, { type: T }>;
