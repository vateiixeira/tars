// Mini medidores de nível (cápsula de gravação): converte o último `levels` do backend em alturas
// de barras. Mesma escala da pílula do overlay.
import type { Levels } from "../composables/useStatus";

/** Abaixo disso o medidor fica vazio (sem sinal). */
export const FLOOR_DB = -60;
/** Daqui para cima, cheio. */
export const FULL_DB = -10;
/** Sem `levels` há mais que isso: medidor vazio, não congelado. */
export const LEVELS_STALE_MS = 3000;
/** As três barras com alturas diferentes, como na prévia. */
export const BAR_SHAPE = [0.6, 1, 0.8] as const;

/** Alturas em px das barras de uma fonte, ou null sem sinal recente. */
export function levelBars(
  levels: Levels | null,
  source: "mic" | "pc",
  now: number,
  maxPx: number,
): number[] | null {
  if (!levels || now - levels.at > LEVELS_STALE_MS) return null;
  const db = source === "mic" ? levels.mic_db : levels.pc_db;
  if (db <= FLOOR_DB) return null;
  const frac = Math.min(1, (db - FLOOR_DB) / (FULL_DB - FLOOR_DB));
  return BAR_SHAPE.map((k) => Math.max(2, Math.round(maxPx * frac * k)));
}
