import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import type { Speaker } from "../api/types";
import SpeakerChips from "./SpeakerChips.vue";

const speakers: Speaker[] = [
  { id: "s1", label: "Você", voice_id: null, is_me: true, color_index: 0 },
  { id: "s2", label: "João", voice_id: "v1", is_me: false, color_index: 1 },
  { id: "s3", label: "Pessoa 3", voice_id: null, is_me: false, color_index: 2 },
];

describe("SpeakerChips", () => {
  it("colors by color_index and dashes unidentified speakers", () => {
    const w = mount(SpeakerChips, { props: { speakers } });
    const chips = w.findAll(".chip");
    expect(chips).toHaveLength(3);
    expect(chips[0].get("i").attributes("style")).toContain("var(--speaker-0)");
    expect(chips[1].get("i").attributes("style")).toContain("var(--speaker-1)");
    expect(chips[0].classes()).not.toContain("unknown");
    expect(chips[1].classes()).not.toContain("unknown");
    expect(chips[2].classes()).toContain("unknown");
    expect(chips[2].text()).toContain("Pessoa 3");
    expect(chips[2].text()).toContain("identificar");
  });

  it("emits select with the speaker and the click event", async () => {
    const w = mount(SpeakerChips, { props: { speakers } });
    await w.findAll(".chip")[2].trigger("click");
    const [speaker, event] = w.emitted("select")![0] as [Speaker, MouseEvent];
    expect(speaker.id).toBe("s3");
    expect(event).toBeInstanceOf(MouseEvent);
  });

  it("Você fica clicável com meClickable (reunião importada de vídeo ou áudio)", async () => {
    const w = mount(SpeakerChips, { props: { speakers, meClickable: true } });
    const me = w.get('[data-speaker="s1"]');
    expect(me.element.tagName).toBe("BUTTON");
    expect(me.classes()).not.toContain("unknown");
    await me.trigger("click");
    expect((w.emitted("select")![0] as [Speaker])[0].id).toBe("s1");
  });

  it("does not make Você clickable", async () => {
    const w = mount(SpeakerChips, { props: { speakers } });
    const me = w.get('[data-speaker="s1"]');
    expect(me.element.tagName).toBe("SPAN");
    await me.trigger("click");
    expect(w.emitted("select")).toBeUndefined();
  });
});
