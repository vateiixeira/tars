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

  it("ao apagar não recoloca os dois pontos", () => {
    expect(maskTime("16", true)).toBe("16");
    expect(maskTime("16:", true)).toBe("16");
    expect(maskTime("1", true)).toBe("1");
  });
});
