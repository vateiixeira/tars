import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setStatusStore } from "../composables/useStatus";
import type { MeetingPrint, TaskSuggestion } from "../api/types";
import { installStore } from "../test/meetingFakes";

vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: vi.fn(async () => {}) }));

import TaskCard from "./TaskCard.vue";

const print = (n: number): MeetingPrint => ({
  id: `p${n}`, label: `P${n}`, at_s: 100 * n, source: "flameshot", created_at: "2026-10-19T14:12:05", sent_to_claude: true,
});
const PRINTS = [print(1), print(2), print(3)];

const task = (extra: Partial<TaskSuggestion> = {}): TaskSuggestion => ({
  id: "t1",
  meeting_id: "m1",
  origin: "claude",
  position: 0,
  kind: "feature",
  title: "Corrigir tela",
  description: "",
  project: null,
  product_id: null,
  assignee_id: null,
  team_id: null,
  stage_id: null,
  priority: "medium",
  suggested: null,
  evidence: [],
  edited: false,
  discarded: false,
  create_state: "none",
  create_error: null,
  issue_number: null,
  issue_url: null,
  repeats_number: null,
  print_ids: ["p1", "p2"],
  attach_state: "none",
  attach_error: null,
  ...extra,
});

const createObjectURL = vi.fn((b: Blob) => `blob:${(b as Blob & { tag?: string }).tag ?? "x"}`);
const revokeObjectURL = vi.fn();
const blobOf = (tag: string) => Object.assign(new Blob([tag]), { tag });

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(URL, { createObjectURL, revokeObjectURL });
});
afterEach(() => setStatusStore(null));

function mountCard(t: TaskSuggestion, overrides: Record<string, unknown> = {}) {
  const printBlob = vi.fn(async (_m: string, id: string, size: string) => blobOf(`${id}-${size}`));
  installStore({ printBlob, ...overrides });
  const w = mount(TaskCard, {
    props: { task: t, options: null, missing: [], prints: PRINTS },
    attachTo: document.body,
  });
  return { w, printBlob };
}

describe("TaskCard prints", () => {
  it("lista os prints da sugestão e remove um com o x", async () => {
    const { w, printBlob } = mountCard(task());
    await flushPromises();
    const items = w.findAll(".task-prints li");
    expect(items).toHaveLength(2);
    expect(items[0].text()).toContain("P1");
    expect(items[1].text()).toContain("P2");
    expect(printBlob).toHaveBeenCalledWith("m1", "p1", "thumb");
    expect(items[0].get("img").attributes("src")).toBe("blob:p1-thumb");
    await w.get('[aria-label="Tirar P2 desta tarefa"]').trigger("click");
    expect(w.emitted("update")).toEqual([["t1", { print_ids: ["p1"] }]]);
    w.unmount();
  });

  it("desabilita o x quando a sugestão está travada", async () => {
    const { w } = mountCard(task({ create_state: "created", issue_number: 7 }));
    await flushPromises();
    expect(w.get('[aria-label="Tirar P1 desta tarefa"]').attributes("disabled")).toBeDefined();
    w.unmount();
  });

  it("revoga as miniaturas ao tirar um print e ao desmontar", async () => {
    const { w } = mountCard(task());
    await flushPromises();
    await w.setProps({ task: task({ print_ids: ["p1"] }) });
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:p2-thumb");
    w.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:p1-thumb");
  });

  it("mostra o rótulo quando a miniatura falha", async () => {
    const { w } = mountCard(task(), { printBlob: vi.fn(async () => { throw new Error("x"); }) });
    await flushPromises();
    expect(w.findAll(".task-prints img")).toHaveLength(0);
    const fallbacks = w.findAll(".task-prints .thumb-fallback");
    expect(fallbacks).toHaveLength(2);
    expect(fallbacks[0].text()).toBe("P1");
    w.unmount();
  });

  it("mostra a nota quando o MCP não aceitou imagens", async () => {
    const { w } = mountCard(task({ attach_state: "unsupported" }));
    await flushPromises();
    expect(w.text()).toContain("Prints não enviados: o MCP não aceita imagens");
    w.unmount();
  });

  it("mostra falha do anexo com o erro", async () => {
    const { w } = mountCard(task({ attach_state: "failed", attach_error: "timeout no upload" }));
    await flushPromises();
    expect(w.text()).toContain("Prints não enviados: timeout no upload");
    w.unmount();
  });
});
