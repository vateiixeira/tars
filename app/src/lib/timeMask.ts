/**
 * Máscara do horário: só dígitos, com os dois pontos depois da hora ("1630" -> "16:30").
 * Um separador digitado depois da hora vale como os dois pontos ("9:30", "9h30" -> "9:30"),
 * para não juntar a hora aos minutos ("93:0").
 * Ao apagar, não recoloca os dois pontos, senão o backspace ficaria preso neles.
 */
export function maskTime(raw: string, deleting = false): string {
  const typed = /^(\d{1,2})\D+(\d*)/.exec(raw);
  if (typed) {
    const [, hour, rest] = typed;
    const minutes = rest.slice(0, 2);
    return deleting && !minutes ? hour : `${hour}:${minutes}`;
  }
  const digits = raw.replace(/\D/g, "").slice(0, 4);
  if (digits.length > 2) return `${digits.slice(0, 2)}:${digits.slice(2)}`;
  return digits.length === 2 && !deleting ? `${digits}:` : digits;
}
