/**
 * Máscara do horário: só dígitos, com os dois pontos depois da hora ("1630" -> "16:30").
 * Ao apagar, não recoloca os dois pontos, senão o backspace ficaria preso neles.
 */
export function maskTime(raw: string, deleting = false): string {
  const digits = raw.replace(/\D/g, "").slice(0, 4);
  if (digits.length > 2) return `${digits.slice(0, 2)}:${digits.slice(2)}`;
  return digits.length === 2 && !deleting ? `${digits}:` : digits;
}
