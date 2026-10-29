import { describe, expect, it } from "vitest";
import { groupByDay, mmss, speakerColor, startOfWeek } from "./format";

describe("format", () => {
  it("mmss formats 3725 as 1:02:05 and 65 as 01:05", () => {
    expect(mmss(3725)).toBe("1:02:05");
    expect(mmss(65)).toBe("01:05");
    expect(mmss(0)).toBe("00:00");
    expect(mmss(59.9)).toBe("00:59");
  });

  it("groupByDay labels Hoje and Ontem", () => {
    const now = new Date(2026, 9, 19, 15, 0); // 19/10/2026, segunda-feira
    const items = [
      { id: "a", started_at: "2026-10-19T09:30:00" },
      { id: "b", started_at: "2026-10-19T08:00:00" },
      { id: "c", started_at: "2026-10-18T23:59:00" },
      { id: "d", started_at: "2026-10-13T10:00:00" },
    ];

    const groups = groupByDay(items, now);

    expect(groups.map((g) => g.label)).toEqual(["Hoje", "Ontem", "Terça-feira, 13 de outubro"]);
    expect(groups[0].items.map((i) => i.id)).toEqual(["a", "b"]);
    expect(groups[1].items.map((i) => i.id)).toEqual(["c"]);
    expect(groupByDay([{ started_at: "2025-12-31T10:00:00" }], now)[0].label)
      .toBe("Quarta-feira, 31 de dezembro de 2025");
  });

  it("speakerColor cycles through the palette", () => {
    expect(speakerColor(0)).toBe("var(--speaker-0)");
    expect(speakerColor(7)).toBe("var(--speaker-7)");
    expect(speakerColor(8)).toBe("var(--speaker-0)");
  });

  it("startOfWeek starts on Monday at local midnight", () => {
    const monday = new Date(2026, 9, 19, 0, 0);
    expect(startOfWeek(new Date(2026, 9, 19, 15, 0))).toEqual(monday); // segunda
    expect(startOfWeek(new Date(2026, 9, 21, 9, 30))).toEqual(monday); // quarta
    expect(startOfWeek(new Date(2026, 9, 25, 23, 59))).toEqual(monday); // domingo
    expect(startOfWeek(new Date(2026, 10, 1, 10, 0))).toEqual(new Date(2026, 9, 26)); // virada do mês
  });
});
