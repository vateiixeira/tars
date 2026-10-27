// Funções puras dos componentes de reunião: texto da transcrição, nome do arquivo exportado,
// duração legível e linhas de alerta.
import type { AlertKind, DiarizationAlertKind, MeetingDetail, Segment, Speaker } from "../api/types";
import { mmss, timeOfDay } from "./format";

const pad = (n: number) => String(n).padStart(2, "0");

/** Nome mostrado/copiado para um falante (igual ao backend: sem rótulo vira "Desconhecido"). */
export function speakerName(speaker: Speaker | undefined): string {
  return speaker?.label || "Desconhecido";
}

/** Até três falas com duração suficiente para reconhecer a voz, limitadas a dez segundos. */
export function speakerSamples(segments: Segment[], speakerId: string): Segment[] {
  const candidates = segments
    .filter((s) => s.speaker_id === speakerId && Number.isFinite(s.start_s)
      && Number.isFinite(s.end_s) && s.start_s >= 0 && s.end_s > s.start_s)
    .map((s) => ({ ...s, end_s: Math.min(s.end_s, s.start_s + 10) }))
    .sort((a, b) => (b.end_s - b.start_s) - (a.end_s - a.start_s) || a.start_s - b.start_s);
  const samples: Segment[] = [];
  for (const candidate of candidates) {
    if (samples.some((s) => s.start_s < candidate.end_s && s.end_s > candidate.start_s)) continue;
    samples.push(candidate);
    if (samples.length === 3) break;
  }
  return samples.sort((a, b) => a.start_s - b.start_s);
}

/** Corpo da transcrição no formato de `export.md` (`[mm:ss] Nome: texto`, sem cabeçalho). */
export function transcriptText(segments: Segment[], speakers: Speaker[]): string {
  const byId = new Map(speakers.map((s) => [s.id, s]));
  return segments
    .map((seg) => {
      const name = speakerName(seg.speaker_id ? byId.get(seg.speaker_id) : undefined);
      return `[${mmss(seg.start_s)}] ${name}: ${seg.text}${seg.dictated ? " (ditado)" : ""}`;
    })
    .join("\n");
}

/** `reuniao-AAAAMMDD-HHMM.md`, como o backend sugere no Content-Disposition. */
export function exportFileName(startedAt: string): string {
  const d = new Date(startedAt);
  return `reuniao-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.md`;
}

/** "< 1 min", "42 min", "1 h 05 min". */
export function formatDuration(seconds: number): string {
  const minutes = Math.floor(Math.max(0, seconds) / 60);
  if (minutes < 1) return "< 1 min";
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${pad(minutes % 60)} min`;
}

export function peopleLabel(count: number): string {
  return count === 1 ? "1 pessoa" : `${count} pessoas`;
}

/** Rótulos dos alertas já registrados (a pílula ao vivo usa outros textos, em `ALERT_MESSAGES`). */
const ALERT_LABELS: Record<AlertKind | DiarizationAlertKind, string> = {
  mic_silent: "Microfone sem sinal",
  pc_silent: "Sem som do PC",
  pc_wrong_output: "Áudio da chamada fora da saída gravada",
  mic_dead: "Microfone parou",
  pc_dead: "Captura do PC parou",
  no_diarization: "Falantes não separados: nenhum motor de separação funcionou",
  pyannote_no_token: "pyannote não usado: falta o token do Hugging Face. Falantes separados pelo motor local",
  pyannote_missing_extra:
    "pyannote não usado: o extra não está instalado (uv sync --extra pyannote). Falantes separados pelo motor local",
  pyannote_failed: "pyannote não usado: falhou ao carregar ou rodar. Falantes separados pelo motor local",
};

/** Alertas do processamento valem para a reunião toda: saem só com o rótulo, sem intervalo. */
const DIARIZATION_ALERTS: ReadonlySet<string> = new Set<DiarizationAlertKind>([
  "no_diarization",
  "pyannote_no_token",
  "pyannote_missing_extra",
  "pyannote_failed",
]);

/** "Microfone sem sinal de 12:10 a 12:40": os offsets do alerta somados ao início da reunião. */
export function alertLines(meeting: Pick<MeetingDetail, "started_at" | "alerts">): string[] {
  const start = new Date(meeting.started_at).getTime();
  const clock = (offset: number) => timeOfDay(new Date(start + offset * 1000).toISOString());
  return meeting.alerts.map((a) => {
    const label = ALERT_LABELS[a.kind] ?? a.kind;
    return DIARIZATION_ALERTS.has(a.kind) ? label : `${label} de ${clock(a.start_s)} a ${clock(a.end_s)}`;
  });
}
