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
    expect(rows[1].find("em.dictated").text()).toBe("(ditado)");
    await rows[0].get(".play").trigger("click");
    expect(w.emitted("play")?.[0]).toEqual([segments[0]]);
  });

  it("scrolling moves the rendered window", async () => {
    const segments = Array.from({ length: 5000 }, (_, i) => seg(i));
    const w = mount(TranscriptView, { props: { segments, speakers, prints: [] } });
    const el = w.get(".scroller").element as HTMLElement;
    el.scrollTop = 84 * 2500;
    await w.get(".scroller").trigger("scroll");
    const rows = w.findAll(".row");
    expect(rows.length).toBeLessThan(200);
    expect(rows.some((r) => r.text().includes("fala 2500"))).toBe(true);
    expect(rows.some((r) => r.attributes("data-id") === "0")).toBe(false);
  });

  it("mostra o chip do print na fala do minuto do print e emite openPrint", async () => {
    // falas em 0, 3, 6, 9...; o print em 7s cai na fala que começa em 6s
    const segments = Array.from({ length: 5 }, (_, i) => seg(i));
    const prints: MeetingPrint[] = [
      { id: "p1", label: "P1", at_s: 7, source: "native", created_at: "2026-10-19T14:00:07", sent_to_claude: true },
    ];
    const w = mount(TranscriptView, { props: { segments, speakers, prints } });
    const rows = w.findAll(".row");
    expect(rows.map((r) => r.find(".print-badge").exists())).toEqual([false, false, true, false, false]);
    expect(rows[2].findAll(".print-badge svg")).toHaveLength(1);
    expect(rows[2].get(".print-badge").text()).not.toContain("📷");
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

  it("põe 'Você' (is_me) à direita com o gradiente e os outros à esquerda", () => {
    const w = mount(TranscriptView, { props: { segments: [seg(1), seg(2)], speakers, prints: [] } });
    const [mine, other] = w.findAll(".row");
    expect(mine.classes()).toContain("me");
    expect(mine.get(".am-bubble").classes()).toContain("am-bubble--me");
    expect(other.classes()).not.toContain("me");
    expect(other.get(".am-bubble").classes()).not.toContain("am-bubble--me");
    expect(mine.get(".am-bubble__who").attributes("style")).toContain("--speaker-0");
    expect(other.get(".am-bubble__who").attributes("style")).toContain("--speaker-1");
  });

  it("omite o cabeçalho em falas seguidas do mesmo falante, mantendo o botão de ouvir", async () => {
    const segments = [seg(1), seg(3), seg(2), seg(5, { speaker_id: "s2" })];
    const w = mount(TranscriptView, { props: { segments, speakers, prints: [] } });
    const rows = w.findAll(".row");
    // s1, s1 (sem cabeçalho), s2 (com), s2 (sem)
    expect(rows.map((r) => r.find(".am-bubble__meta").exists())).toEqual([true, false, true, false]);
    expect(rows[1].get(".play").attributes("aria-label")).toBe("Ouvir Você a partir de 00:09");
    await rows[1].get(".play").trigger("click");
    expect(w.emitted("play")?.[0]).toEqual([segments[1]]);
  });

  it("marca a fala que está tocando e só ela", async () => {
    const segments = Array.from({ length: 6 }, (_, i) => seg(i)); // inícios 0,3,6,9,12,15; fim = início + 2
    const w = mount(TranscriptView, { props: { segments, speakers, prints: [], playingAt: null } });
    const playing = () => w.findAll(".row").map((r) => r.get(".am-bubble").classes().includes("is-playing"));
    expect(playing()).toEqual([false, false, false, false, false, false]);
    await w.setProps({ playingAt: 7 });
    expect(playing()).toEqual([false, false, true, false, false, false]);
    await w.setProps({ playingAt: 8.5 }); // no intervalo entre falas, fica na última iniciada
    expect(playing()).toEqual([false, false, true, false, false, false]);
    await w.setProps({ playingAt: 12 });
    expect(playing()).toEqual([false, false, false, false, true, false]);
    await w.setProps({ playingAt: 40 }); // depois do fim da última fala
    expect(playing()).toEqual([false, false, false, false, false, false]);
    await w.setProps({ playingAt: null });
    expect(playing()).toEqual([false, false, false, false, false, false]);
  });

  it("não marca nada se a fala tocando está fora da janela virtualizada", async () => {
    const segments = Array.from({ length: 5000 }, (_, i) => seg(i));
    const w = mount(TranscriptView, { props: { segments, speakers, prints: [], playingAt: 3 * 4000 } });
    expect(w.findAll(".am-bubble.is-playing")).toHaveLength(0);
    expect(w.findAll(".row").length).toBeLessThan(200);
  });
});
