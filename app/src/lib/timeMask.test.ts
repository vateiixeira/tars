import { describe, expect, it } from "vitest";
import { maskTime } from "./timeMask";

describe("maskTime", () => {
  it("põe os dois pontos depois da hora enquanto a pessoa digita", () => {
    expect(maskTime("1")).toBe("1");
    expect(maskTime("16")).toBe("16:");
    expect(maskTime("16:3")).toBe("16:3");
    expect(maskTime("1630")).toBe("16:30");
  });

  it("ignora letras e outros separadores e limita a quatro dígitos", () => {
    expect(maskTime("16h30")).toBe("16:30");
    expect(maskTime("16.30")).toBe("16:30");
    expect(maskTime("163045")).toBe("16:30");
    expect(maskTime("ab")).toBe("");
  });

  it("aceita a hora de um dígito com os dois pontos digitados ou colados", () => {
    expect(maskTime("9:")).toBe("9:");
    expect(maskTime("9:3")).toBe("9:3");
    expect(maskTime("9:30")).toBe("9:30");
    expect(maskTime("9h30")).toBe("9:30");
    expect(maskTime("16::")).toBe("16:");
    expect(maskTime("9:305")).toBe("9:30");
  });

  it("editar a hora no meio não junta hora e minutos", () => {
    expect(maskTime("1:30", true)).toBe("1:30");
    expect(maskTime("6:30")).toBe("6:30");
  });

  it("ao apagar não recoloca os dois pontos", () => {
    expect(maskTime("16", true)).toBe("16");
    expect(maskTime("16:", true)).toBe("16");
    expect(maskTime("9:", true)).toBe("9");
    expect(maskTime("1", true)).toBe("1");
  });
});
