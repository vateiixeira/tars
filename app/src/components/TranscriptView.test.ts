import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import type { MeetingPrint, Segment, Speaker } from "../api/types";
import TranscriptView from "./TranscriptView.vue";

const speakers: Speaker[] = [
  { id: "s1", label: "Você", voice_id: null, is_me: true, color_index: 0 },
  { id: "s2", label: "João", voice_id: "v", is_me: false, color_index: 1 },
];

const seg = (i: number, extra: Partial<Segment> = {}): Segment => ({
  id: i, speaker_id: i % 2 ? "s1" : "s2", source: "mic", start_s: i * 3, end_s: i * 3 + 2, text: `fala ${i}`,
  dictated: false, ...extra,
});

describe("TranscriptView", () => {
  it("TranscriptView renders only visible rows for 5000 segments", () => {
    const segments = Array.from({ length: 5000 }, (_, i) => seg(i));
    const w = mount(TranscriptView, { props: { segments, speakers, prints: [] } });
    const rows = w.findAll(".row");
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThan(200);
    expect(rows[0].text()).toContain("00:00");
  });

  it("renders time, speaker name, dictated marker and emits play", async () => {
    const segments = [seg(1), seg(2, { dictated: true })];
    const w = mount(TranscriptView, { props: { segments, speakers, prints: [] } });
    const rows = w.findAll(".row");
    expect(rows[0].text()).toContain("00:03");
    expect(rows[0].text()).toContain("Você");
    expect(rows[0].text()).toContain("fala 1");
    expect(rows[1].find("em").text()).toBe("(ditado)");
    await rows[0].get(".play").trigger("click");
    expect(w.emitted("play")?.[0]).toEqual([segments[0]]);
  });

  it("scrolling moves the rendered window", async () => {
    const segments = Array.from({ length: 5000 }, (_, i) => seg(i));
    const w = mount(TranscriptView, { props: { segments, speakers, prints: [] } });
    const el = w.get(".scroller").element as HTMLElement;
    el.scrollTop = 44 * 2500;
    await w.get(".scroller").trigger("scroll");
    const rows = w.findAll(".row");
    expect(rows.length).toBeLessThan(200);
    expect(rows.some((r) => r.text().includes("fala 2500"))).toBe(true);
    expect(rows.some((r) => r.attributes("data-id") === "0")).toBe(false);
  });

  it("mostra o selo 📷 na fala do minuto do print e emite openPrint", async () => {
    // falas em 0, 3, 6, 9...; o print em 7s cai na fala que começa em 6s
    const segments = Array.from({ length: 5 }, (_, i) => seg(i));
    const prints: MeetingPrint[] = [
      { id: "p1", label: "P1", at_s: 7, source: "native", created_at: "2026-10-19T14:00:07", sent_to_claude: true },
    ];
    const w = mount(TranscriptView, { props: { segments, speakers, prints } });
    const rows = w.findAll(".row");
    expect(rows.map((r) => r.find(".print-badge").exists())).toEqual([false, false, true, false, false]);
    expect(rows[2].get(".print-badge").text()).toContain("📷");
    expect(rows[2].get(".print-badge").text()).toContain("P1");
    await rows[2].get(".print-badge").trigger("click");
    expect(w.emitted("openPrint")?.[0]).toEqual(["p1"]);
  });

  it("põe o selo na primeira fala se o print vem antes de todas", () => {
    const segments = [seg(2), seg(3)];
    const prints: MeetingPrint[] = [
      { id: "p1", label: "P1", at_s: 1, source: "native", created_at: "2026-10-19T14:00:01", sent_to_claude: true },
    ];
    const w = mount(TranscriptView, { props: { segments, speakers, prints } });
    expect(w.findAll(".row")[0].find(".print-badge").exists()).toBe(true);
  });
});
