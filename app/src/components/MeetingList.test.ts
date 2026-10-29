import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setStatusStore } from "../composables/useStatus";
import type { MeetingListItem } from "../api/types";
import { ApiError } from "../api/client";
import { installStore, reconnect } from "../test/meetingFakes";
import MeetingList from "./MeetingList.vue";

afterEach(() => {
  vi.useRealTimers();
  setStatusStore(null);
});

const item = (id: string, title: string | null, started_at: string, extra: Partial<MeetingListItem> = {}): MeetingListItem => ({
  id, title, started_at, duration_s: 42 * 60, speakers_count: 4, status: "ready", ...extra,
});

describe("MeetingList", () => {
  it("MeetingList groups by day and filters with search", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(new Date("2026-10-19T15:00:00"));
    const listMeetings = vi.fn(async (q?: string) =>
      q
        ? [item("a", "Alinhamento NFSe", "2026-10-19T14:00:00")]
        : [
            item("a", "Alinhamento NFSe", "2026-10-19T14:00:00"),
            item("b", null, "2026-10-18T11:02:00", { duration_s: 8 * 60, speakers_count: 2 }),
          ],
    );
    installStore({ listMeetings });
    const w = mount(MeetingList, { props: { selectedId: "a" } });
    await flushPromises();

    expect(w.findAll(".day").map((d) => d.text())).toEqual(["Hoje", "Ontem"]);
    expect(w.findAll(".item")).toHaveLength(2);
    expect(w.findAll(".item")[0].text()).toContain("14:00 · 42 min · 4 pessoas");
    expect(w.findAll(".item")[0].classes()).toContain("on");
    expect(w.findAll(".item")[1].text()).toContain("Reunião sem título");

    await w.get("input").setValue("nfse");
    await vi.advanceTimersByTimeAsync(299);
    expect(listMeetings).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await flushPromises();
    expect(listMeetings).toHaveBeenLastCalledWith("nfse", undefined);
    expect(w.findAll(".item")).toHaveLength(1);
  });

  it("emits select and reloads on meeting_updated", async () => {
    const listMeetings = vi.fn(async () => [item("a", "X", "2026-10-19T14:00:00")]);
    const { emit } = installStore({ listMeetings });
    const w = mount(MeetingList, { props: { selectedId: null } });
    await flushPromises();
    await w.get(".item").trigger("click");
    expect(w.emitted("select")?.[0]).toEqual(["a"]);
    emit({ type: "meeting_updated", meeting_id: "a", status: "ready" });
    await flushPromises();
    expect(listMeetings).toHaveBeenCalledTimes(2);
  });

  it("reloads when the connection opens after a failed first load", async () => {
    const listMeetings = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(0, "Serviço do ditado não está rodando"))
      .mockResolvedValue([item("a", "Alinhamento", "2026-10-19T14:00:00")]);
    const { events, emit } = installStore({ listMeetings });
    const w = mount(MeetingList, { props: { selectedId: null } });
    await flushPromises();
    expect(w.text()).toContain("Serviço do ditado não está rodando");

    await reconnect(events, emit);
    await flushPromises();
    expect(w.text()).not.toContain("não está rodando");
    expect(w.findAll(".item")).toHaveLength(1);
  });

  it("hides the archived link when there is nothing archived", async () => {
    installStore({ listMeetings: vi.fn(async () => [item("a", "X", "2026-10-19T14:00:00")]) });
    const w = mount(MeetingList, { props: { selectedId: null } });
    await flushPromises();
    expect(w.find(".archive-toggle").exists()).toBe(false);
  });

  it("switches to the archived list and back", async () => {
    const listMeetings = vi.fn(async (_q?: string, archived?: boolean) =>
      archived
        ? [item("z", "Antiga", "2026-09-01T10:00:00")]
        : [item("a", "Atual", "2026-10-19T14:00:00")],
    );
    const archivedCount = vi.fn(async () => ({ count: 1 }));
    installStore({ listMeetings, archivedCount });
    const w = mount(MeetingList, { props: { selectedId: null } });
    await flushPromises();
    expect(w.get(".archive-toggle").text()).toBe("Arquivadas · 1");
    expect(w.get(".archive-toggle").attributes("aria-pressed")).toBe("false");

    await w.get(".archive-toggle").trigger("click");
    await flushPromises();
    expect(listMeetings).toHaveBeenLastCalledWith(undefined, true);
    expect(w.findAll(".item").map((i) => i.find("b").text())).toEqual(["Antiga"]);
    expect(w.get(".archive-toggle").attributes("aria-pressed")).toBe("true");

    await w.get("input").setValue("antiga");
    await w.get("input").trigger("input");
    await new Promise((r) => setTimeout(r, 310));
    await flushPromises();
    expect(listMeetings).toHaveBeenLastCalledWith("antiga", true);

    await w.findAll(".filter-chip")[0].trigger("click"); // Todas
    await flushPromises();
    expect(listMeetings).toHaveBeenLastCalledWith("antiga", undefined);
    expect(w.get(".archive-toggle").attributes("aria-pressed")).toBe("false");
  });

  it("filters this week's meetings without asking the backend again", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-21T15:00:00")); // quarta-feira
    const listMeetings = vi.fn(async () => [
      item("a", "Quarta", "2026-10-21T09:00:00"),
      item("b", "Segunda", "2026-10-19T08:00:00"),
      item("c", "Domingo passado", "2026-10-18T20:00:00"),
    ]);
    installStore({ listMeetings });
    const w = mount(MeetingList, { props: { selectedId: null } });
    await flushPromises();
    const titles = () => w.findAll(".item").map((i) => i.find("b").text());
    expect(titles()).toHaveLength(3);
    expect(w.findAll(".filter-chip").map((c) => c.text())).toEqual(["Todas", "Esta semana"]);

    await w.findAll(".filter-chip")[1].trigger("click");
    await flushPromises();
    expect(titles()).toEqual(["Quarta", "Segunda"]);
    expect(listMeetings).toHaveBeenCalledTimes(1);
  });

  it("shows live, processing and failed states on the cards", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    vi.setSystemTime(new Date("2026-10-19T14:12:47"));
    installStore({
      listMeetings: vi.fn(async () => [
        item("r", null, "2026-10-19T14:00:00", { status: "recording" }),
        item("p", "P", "2026-10-19T13:00:00", { status: "processing" }),
        item("f", "F", "2026-10-19T12:00:00", { status: "failed" }),
      ]),
    });
    const w = mount(MeetingList, { props: { selectedId: null } });
    await flushPromises();
    const [rec, proc, failed] = w.findAll(".item");
    expect(rec.classes()).toContain("live");
    expect(rec.find(".rec-dot").exists()).toBe(true);
    expect(rec.text()).toContain("gravando · 12:47");
    await vi.advanceTimersByTimeAsync(2000);
    expect(rec.text()).toContain("gravando · 12:49");
    expect(proc.find(".loader").exists()).toBe(true);
    expect(proc.text()).toContain("13:00 · processando…");
    expect(failed.classes()).toContain("failed");
    expect(failed.text()).toContain("12:00 · falhou");
  });

  it("focuses the search field on request", async () => {
    installStore();
    const w = mount(MeetingList, { props: { selectedId: null }, attachTo: document.body });
    await flushPromises();
    (w.vm as unknown as { focusSearch(): void }).focusSearch();
    expect(document.activeElement).toBe(w.get("input.search").element);
    w.unmount();
  });
});
