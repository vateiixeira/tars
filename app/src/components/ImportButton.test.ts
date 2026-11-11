import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { setStatusStore } from "../composables/useStatus";
import { installStore } from "../test/meetingFakes";

type DropHandler = (e: { payload: { type: string; paths?: string[] } }) => void;
const tauri = vi.hoisted(() => ({
  open: vi.fn(async (_o?: unknown): Promise<string | null> => null),
  drop: null as DropHandler | null,
  unlisten: vi.fn(),
}));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: (o?: unknown) => tauri.open(o) }));
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({
    onDragDropEvent: async (cb: DropHandler) => {
      tauri.drop = cb;
      return tauri.unlisten;
    },
  }),
}));

import ImportButton from "./ImportButton.vue";

const preview = {
  kind: "media", title: "reuniao", date: "2026-11-11", time: "16:30", duration_s: 60,
  people: null, my_name_match: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  tauri.drop = null;
});
let mounted: VueWrapper | undefined;
afterEach(() => {
  mounted?.unmount();
  mounted = undefined;
  setStatusStore(null);
});

async function mountButton() {
  const ctx = installStore({
    inspectImport: vi.fn(async () => preview),
    importFile: vi.fn(async () => ({ meeting_id: "m9" })),
  });
  const w = mount(ImportButton, { attachTo: document.body });
  mounted = w;
  await flushPromises();
  return { w, ...ctx };
}

describe("ImportButton", () => {
  it("abre a escolha de arquivo com os filtros e leva à confirmação", async () => {
    tauri.open.mockResolvedValueOnce("/v/reuniao.mp4");
    const { w, client } = await mountButton();
    await w.get("button").trigger("click");
    await flushPromises();
    expect(tauri.open).toHaveBeenCalledWith({
      multiple: false,
      directory: false,
      filters: [
        { name: "Transcrição do Read AI", extensions: ["txt"] },
        {
          name: "Vídeo ou áudio",
          extensions: ["mp4", "mkv", "mov", "webm", "mp3", "m4a", "wav", "ogg", "opus", "flac", "aac"],
        },
      ],
    });
    expect(client.inspectImport).toHaveBeenCalledWith("/v/reuniao.mp4");
    await w.findAll("button").find((b) => b.text() === "Importar e transcrever")!.trigger("click");
    await flushPromises();
    expect(w.emitted("imported")).toEqual([["m9"]]);
    expect(w.find('[role="dialog"]').exists()).toBe(false);
  });

  it("cancelar a escolha de arquivo não abre nada", async () => {
    const { w, client } = await mountButton();
    await w.get("button").trigger("click");
    await flushPromises();
    expect(client.inspectImport).not.toHaveBeenCalled();
    expect(w.find('[role="dialog"]').exists()).toBe(false);
  });

  it("soltar arquivos na janela usa só o primeiro caminho do drop", async () => {
    const { w, client } = await mountButton();
    tauri.drop!({ payload: { type: "enter", paths: ["/x/a.txt"] } });
    tauri.drop!({ payload: { type: "leave" } });
    await flushPromises();
    expect(client.inspectImport).not.toHaveBeenCalled();
    tauri.drop!({ payload: { type: "drop", paths: ["/x/a.mp4", "/x/b.mp4"] } });
    await flushPromises();
    expect(client.inspectImport).toHaveBeenCalledTimes(1);
    expect(client.inspectImport).toHaveBeenCalledWith("/x/a.mp4");
    expect(w.find('[role="dialog"]').exists()).toBe(true);
  });

  it("para de ouvir o arrastar e soltar ao desmontar", async () => {
    const { w } = await mountButton();
    w.unmount();
    mounted = undefined;
    expect(tauri.unlisten).toHaveBeenCalledTimes(1);
  });
});
