// Estado global compartilhado (um por janela): cliente da API, conexão de eventos e o último
// status/alertas/níveis do backend. Os eventos mantêm o estado atualizado; a cada (re)conexão
// também se busca `GET /status`.
import { computed, readonly, ref, watch, type ComputedRef, type Ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { createClient, type ApiClient } from "../api/client";
import { connectEvents, type ConnectionState, type EventConnection } from "../api/events";
import type { AlertKind, ApiInfo, MeetingState, Status } from "../api/types";

/** Mesmos textos de `ALERT_MESSAGES` do backend (o status só traz o `kind`). */
export const ALERT_MESSAGES: Record<AlertKind, string> = {
  mic_silent: "Microfone sem sinal",
  pc_silent: "Nenhum som do PC há um tempo",
  pc_wrong_output: "Áudio da chamada não está sendo captado",
  mic_dead: "Microfone desconectado",
  pc_dead: "Captura do áudio do PC parou",
  print_failed: "Não foi possível tirar um print",
};

/** Pílula e botão de gravar quando o backend volta parado de uma queda no meio da gravação. */
export const INTERRUPTED_MESSAGE = "Gravação interrompida: o serviço reiniciou";
/** Por quanto tempo o aviso de gravação interrompida fica de pé (o botão mantém até a próxima ação). */
export const INTERRUPTED_SHOW_MS = 60_000;

export interface Interrupted {
  meetingId: string;
  /** `Date.now()` de quando se descobriu. */
  at: number;
}

export interface ActiveAlert {
  kind: AlertKind;
  message: string;
}

export interface Levels {
  mic_db: number;
  pc_db: number;
  /** `Date.now()` de quando chegou. */
  at: number;
}

export interface StatusStore {
  client: ApiClient;
  events: EventConnection;
  /** Estado do WebSocket. */
  connection: Readonly<Ref<ConnectionState>>;
  /** Motivo da última falha de conexão (null quando conectado). */
  connectionError: Readonly<Ref<string | null>>;
  /** WebSocket aberto e status já recebido. */
  connected: ComputedRef<boolean>;
  /** Último status do backend (null até o primeiro). */
  status: Readonly<Ref<Status | null>>;
  /** Alertas ativos da gravação, na ordem em que apareceram. */
  alerts: Readonly<Ref<readonly ActiveAlert[]>>;
  /** Últimos níveis recebidos (gravação ou medição das configurações). */
  levels: Readonly<Ref<Levels | null>>;
  /** Ditado gravando agora. */
  dictationActive: ComputedRef<boolean>;
  /** Estado da reunião ("idle" enquanto não há status). */
  meetingState: ComputedRef<MeetingState>;
  /**
   * A conexão caiu gravando e o backend voltou sem essa gravação (reiniciou: ele finaliza a
   * reunião, não retoma). Some ao iniciar outra gravação ou após `INTERRUPTED_SHOW_MS`.
   */
  interrupted: Readonly<Ref<Interrupted | null>>;
  /** Busca `GET /status` e aplica (ignora a resposta se chegou evento mais novo no meio). */
  refresh(): Promise<void>;
}

export interface StatusDeps {
  client: ApiClient;
  events: EventConnection;
}

export function createStatusStore({ client, events }: StatusDeps): StatusStore {
  const status = ref<Status | null>(null);
  const alerts = ref<ActiveAlert[]>([]);
  const levels = ref<Levels | null>(null);
  const messages = new Map<AlertKind, string>(); // último texto vindo do backend por tipo
  let seq = 0; // conta atualizações vindas de eventos (descarta `refresh` atrasado)
  const interrupted = ref<Interrupted | null>(null);
  let lostWhileRecording: string | null = null; // reunião que gravava quando a conexão caiu
  let interruptedTimer: ReturnType<typeof setTimeout> | undefined;

  function setInterrupted(next: Interrupted | null): void {
    clearTimeout(interruptedTimer);
    interrupted.value = next;
    if (next) interruptedTimer = setTimeout(() => (interrupted.value = null), INTERRUPTED_SHOW_MS);
  }

  function setStatus(next: Status): void {
    // primeiro status depois de voltar: a gravação de antes da queda continua?
    if (lostWhileRecording !== null && events.state.value === "open") {
      const lost = lostWhileRecording;
      lostWhileRecording = null;
      if (next.meeting.state !== "recording" || next.meeting.meeting_id !== lost) {
        setInterrupted({ meetingId: lost, at: Date.now() });
      }
    }
    if (next.meeting.state === "recording" && interrupted.value) setInterrupted(null);
    status.value = next;
    alerts.value = next.meeting.alerts.map((kind) => ({
      kind,
      message: messages.get(kind) ?? ALERT_MESSAGES[kind] ?? kind,
    }));
  }

  events.on("status", ({ type: _type, ...next }) => {
    seq++;
    setStatus(next);
  });

  events.on("dictation", ({ active }) => {
    seq++;
    if (status.value) status.value = { ...status.value, dictation: { ...status.value.dictation, active } };
  });

  events.on("alert", ({ kind, active, message }) => {
    seq++;
    messages.set(kind, message);
    const rest = alerts.value.filter((a) => a.kind !== kind);
    alerts.value = active ? [...rest, { kind, message }] : rest;
    if (status.value) {
      const kinds = alerts.value.map((a) => a.kind);
      status.value = { ...status.value, meeting: { ...status.value.meeting, alerts: kinds } };
    }
  });

  events.on("levels", ({ mic_db, pc_db }) => {
    levels.value = { mic_db, pc_db, at: Date.now() };
  });

  async function refresh(): Promise<void> {
    const before = seq;
    const next = await client.status();
    if (seq === before) setStatus(next);
  }

  watch(
    events.state,
    (state) => {
      if (state === "open") {
        refresh().catch(() => {}); // o evento `status` da conexão também chega
      } else if (lostWhileRecording === null && status.value?.meeting.state === "recording") {
        lostWhileRecording = status.value.meeting.meeting_id;
      }
    },
    { immediate: true },
  );

  return {
    client,
    events,
    connection: readonly(events.state),
    connectionError: readonly(events.error),
    connected: computed(() => events.state.value === "open" && status.value !== null),
    status: readonly(status) as Readonly<Ref<Status | null>>,
    alerts: readonly(alerts) as Readonly<Ref<readonly ActiveAlert[]>>,
    levels: readonly(levels),
    dictationActive: computed(() => status.value?.dictation.active ?? false),
    meetingState: computed(() => status.value?.meeting.state ?? "idle"),
    interrupted: readonly(interrupted) as Readonly<Ref<Interrupted | null>>,
    refresh,
  };
}

let store: StatusStore | null = null;

export function getApiInfo(): Promise<ApiInfo> {
  return invoke<ApiInfo>("api_info");
}

/** Singleton da janela; conecta na primeira chamada. */
export function useStatus(): StatusStore {
  if (!store) {
    store = createStatusStore({
      client: createClient(getApiInfo),
      events: connectEvents(getApiInfo),
    });
  }
  return store;
}

/** Testes: troca o singleton (ex.: por `createStatusStore` com cliente e eventos falsos). */
export function setStatusStore(next: StatusStore | null): void {
  if (store && store !== next) store.events.close();
  store = next;
}
