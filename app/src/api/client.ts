// Cliente REST da API local: token Bearer, erros do backend (`detail` em pt-BR) como ApiError e
// releitura de `api.json` quando o backend reinicia (token novo -> 401).
import type {
  ApiInfo,
  ClaudeModels,
  CreateResult,
  Devices,
  IssueOptions,
  JobState,
  MeetingDetail,
  MeetingListItem,
  NameSuggestion,
  Settings,
  SettingsUpdate,
  Status,
  TaskFields,
  TaskSuggestion,
  TasksPayload,
  TextKind,
  TrackSource,
  Voice,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** `status` 0 = não chegou a falar com o backend (sem api.json, conexão recusada...). */
export const UNREACHABLE = 0;
const UNREACHABLE_MESSAGE = "Não foi possível falar com o serviço do ditado";

export function apiBase(info: ApiInfo): string {
  return `http://127.0.0.1:${info.port}/api`;
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string" && body.detail) return body.detail;
  } catch {
    // corpo não é JSON: cai na mensagem genérica
  }
  return `Erro ${res.status} no serviço do ditado`;
}

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

const enc = encodeURIComponent;

export function createClient(getInfo: () => Promise<ApiInfo>, fetchFn: typeof fetch = fetch) {
  let cached: Promise<ApiInfo> | null = null;

  function info(fresh = false): Promise<ApiInfo> {
    if (fresh || !cached) {
      const p = getInfo();
      cached = p;
      // falhou (ex.: serviço parado): não guardar, a próxima chamada tenta de novo
      p.catch(() => {
        if (cached === p) cached = null;
      });
    }
    return cached;
  }

  async function request(method: Method, path: string, body?: unknown): Promise<Response> {
    for (let attempt = 0; ; attempt++) {
      let current: ApiInfo;
      try {
        current = await info(attempt > 0);
      } catch (e) {
        throw new ApiError(UNREACHABLE, e instanceof Error ? e.message : String(e || UNREACHABLE_MESSAGE));
      }
      const headers: Record<string, string> = { Authorization: `Bearer ${current.token}` };
      if (body !== undefined) headers["Content-Type"] = "application/json";
      let res: Response;
      try {
        res = await fetchFn(apiBase(current) + path, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
        });
      } catch {
        cached = null; // backend pode ter voltado em outra porta: reler na próxima chamada
        throw new ApiError(UNREACHABLE, UNREACHABLE_MESSAGE);
      }
      if (res.status === 401 && attempt === 0) continue; // token velho: relê api.json uma vez
      if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
      return res;
    }
  }

  async function json<T>(method: Method, path: string, body?: unknown): Promise<T> {
    const res = await request(method, path, body);
    return (await res.json()) as T;
  }

  return {
    // status e gravação
    status: () => json<Status>("GET", "/status"),
    startMeeting: () => json<{ meeting_id: string }>("POST", "/meetings/start"),
    stopMeeting: () => json<{ meeting_id: string }>("POST", "/meetings/stop"),
    monitorLevels: (on: boolean) => json<{ on: boolean }>("POST", "/levels/monitor", { on }),

    // reuniões
    /** Lista principal ou, com `archived`, só as reuniões arquivadas. */
    listMeetings: (q?: string, archived?: boolean) =>
      json<MeetingListItem[]>("GET", `/meetings${query({ q, archived: archived || undefined })}`),
    archivedCount: () => json<{ count: number }>("GET", "/meetings/archived/count"),
    archiveMeeting: (id: string) =>
      json<{ id: string; archived_at: string }>("POST", `/meetings/${enc(id)}/archive`),
    unarchiveMeeting: (id: string) =>
      json<{ id: string; archived_at: null }>("DELETE", `/meetings/${enc(id)}/archive`),
    getMeeting: (id: string) => json<MeetingDetail>("GET", `/meetings/${enc(id)}`),
    renameMeeting: (id: string, title: string) =>
      json<{ id: string; title: string }>("PATCH", `/meetings/${enc(id)}`, { title }),
    deleteMeeting: (id: string) => json<{ ok: true }>("DELETE", `/meetings/${enc(id)}`),
    reprocess: (id: string) => json<{ meeting_id: string }>("POST", `/meetings/${enc(id)}/reprocess`),
    /** Áudio ogg/opus: mixado por padrão, ou somente a trilha solicitada. */
    audioBlob: async (id: string, start?: number, end?: number, source?: TrackSource): Promise<Blob> =>
      (await request("GET", `/meetings/${enc(id)}/audio${query({ start, end, source })}`)).blob(),
    /** Markdown da reunião (o nome do arquivo do backend não é exposto via CORS). */
    exportMarkdown: async (id: string): Promise<string> =>
      (await request("GET", `/meetings/${enc(id)}/export.md`)).text(),
    /** Zip da reunião: markdown + imagens dos prints. */
    exportZip: async (id: string): Promise<Uint8Array> =>
      new Uint8Array(await (await request("GET", `/meetings/${enc(id)}/export.zip`)).arrayBuffer()),
    /** Imagem do print: miniatura (jpeg até 480px) ou o arquivo original. */
    printBlob: async (meetingId: string, printId: string, size: "thumb" | "full"): Promise<Blob> =>
      (await request("GET", `/meetings/${enc(meetingId)}/prints/${enc(printId)}${query({ size })}`)).blob(),
    deletePrint: (meetingId: string, printId: string) =>
      json<{ ok: true }>("DELETE", `/meetings/${enc(meetingId)}/prints/${enc(printId)}`),
    /** Tira um print agora (só com a gravação em andamento; 409 caso contrário). */
    takePrint: () => json<{ ok: true }>("POST", "/meetings/print"),
    summarize: (id: string) => json<{ summary: string }>("POST", `/meetings/${enc(id)}/summary`),
    nameSuggestions: (id: string) =>
      json<NameSuggestion[]>("POST", `/meetings/${enc(id)}/name-suggestions`),

    // tarefas (criador de issues)
    listTasks: (meetingId: string) => json<TasksPayload>("GET", `/meetings/${enc(meetingId)}/tasks`),
    scanTasks: (meetingId: string, context?: string) =>
      json<{ state: "running" }>("POST", `/meetings/${enc(meetingId)}/tasks/scan`,
        context?.trim() ? { context: context.trim() } : undefined),
    cancelTasksScan: (meetingId: string) =>
      json<{ scan: JobState | null }>("POST", `/meetings/${enc(meetingId)}/tasks/scan/cancel`),
    addTask: (meetingId: string, fields: Partial<TaskFields>) =>
      json<TaskSuggestion>("POST", `/meetings/${enc(meetingId)}/tasks`, fields),
    updateTask: (taskId: string, changes: Partial<TaskFields> & { discarded?: boolean; print_ids?: string[] }) =>
      json<TaskSuggestion>("PATCH", `/tasks/${enc(taskId)}`, changes),
    createTasks: (meetingId: string, items: (TaskFields & { id: string })[]) =>
      json<CreateResult>("POST", `/meetings/${enc(meetingId)}/tasks/create`, { items }),
    resolveTask: (taskId: string, body: { action: "retry" } | { action: "number"; number: number }) =>
      json<TaskSuggestion>("POST", `/tasks/${enc(taskId)}/resolve`, body),
    issueOptions: (refresh = false) =>
      json<IssueOptions>("GET", `/issues/options${query({ refresh: String(refresh) })}`),
    issueTest: () => json<{ ok: true; username: string }>("POST", "/issues/test"),

    // falantes e vozes
    renameSpeaker: (id: string, label: string) =>
      json<{ id: string; label: string }>("PATCH", `/speakers/${enc(id)}`, { label }),
    /** Mescla o falante `id` em `into` (some o `id`); devolve o falante que fica. */
    mergeSpeaker: (id: string, into: string) =>
      json<{ id: string }>("POST", `/speakers/${enc(id)}/merge`, { into_speaker_id: into }),
    enrollSpeaker: (id: string, target: { name: string } | { voice_id: string }) =>
      json<Voice>("POST", `/speakers/${enc(id)}/enroll`, target),
    listVoices: () => json<Voice[]>("GET", "/voices"),
    renameVoice: (id: string, name: string) => json<Voice>("PATCH", `/voices/${enc(id)}`, { name }),
    deleteVoice: (id: string) => json<{ ok: true }>("DELETE", `/voices/${enc(id)}`),

    // modelos do Claude (lista guardada pelo backend; o refresh consulta o CLI)
    claudeModels: () => json<ClaudeModels>("GET", "/claude/models"),
    refreshClaudeModels: () => json<ClaudeModels>("POST", "/claude/models/refresh"),

    // configurações, vocabulário, dispositivos
    getSettings: () => json<Settings>("GET", "/settings"),
    putSettings: (partial: SettingsUpdate) => json<Settings>("PUT", "/settings", partial),
    getText: async (kind: TextKind): Promise<string> =>
      (await json<{ text: string }>("GET", `/${kind}`)).text,
    putText: async (kind: TextKind, text: string): Promise<string> =>
      (await json<{ text: string }>("PUT", `/${kind}`, { text })).text,
    devices: () => json<Devices>("GET", "/devices"),
  };
}

export type ApiClient = ReturnType<typeof createClient>;
