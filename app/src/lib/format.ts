// Formatação de tempos, datas e cores da interface.

/** Cores dos falantes por `color_index` (índice 0 = "Você"). */
export const SPEAKER_COLORS = [
  "var(--speaker-0)", "var(--speaker-1)", "var(--speaker-2)", "var(--speaker-3)", "var(--speaker-4)", "var(--speaker-5)", "var(--speaker-6)", "var(--speaker-7)",
] as const;

const pad = (n: number) => String(n).padStart(2, "0");

/** 65 -> "01:05"; a partir de 1 h, "H:MM:SS" (3725 -> "1:02:05"). Frações são truncadas. */
export function mmss(seconds: number): string {
  const total = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/** Hora local "HH:MM" de uma data ISO. */
export function timeOfDay(iso: string): string {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function dayLabel(d: Date, now: Date): string {
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (dayKey(d) === dayKey(now)) return "Hoje";
  if (dayKey(d) === dayKey(yesterday)) return "Ontem";
  const label = d.toLocaleDateString("pt-BR", {
    weekday: "long",
    day: "numeric",
    month: "long",
    ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
  });
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export interface DayGroup<T> {
  label: string;
  items: T[];
}

/** Agrupa por dia local, na ordem em que os itens vêm ("Hoje", "Ontem" ou data longa pt-BR). */
export function groupByDay<T extends { started_at: string }>(
  items: T[],
  now: Date = new Date(),
): DayGroup<T>[] {
  const groups: DayGroup<T>[] = [];
  const byKey = new Map<string, DayGroup<T>>();
  for (const item of items) {
    const d = new Date(item.started_at);
    const key = dayKey(d);
    let group = byKey.get(key);
    if (!group) {
      group = { label: dayLabel(d, now), items: [] };
      byKey.set(key, group);
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups;
}

/** Começo (00:00 local) da semana de `now`, contada a partir de segunda-feira. */
export function startOfWeek(now: Date = new Date()): Date {
  const daysSinceMonday = (now.getDay() + 6) % 7;
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysSinceMonday);
}

export function speakerColor(index: number): string {
  const n = SPEAKER_COLORS.length;
  return SPEAKER_COLORS[((Math.trunc(index) % n) + n) % n];
}
