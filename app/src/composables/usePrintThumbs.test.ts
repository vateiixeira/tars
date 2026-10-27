import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, ref } from "vue";
import { setStatusStore } from "./useStatus";
import { installStore } from "../test/meetingFakes";
import { usePrintThumbs } from "./usePrintThumbs";

const createObjectURL = vi.fn((b: Blob) => `blob:${(b as Blob & { tag?: string }).tag ?? "x"}`);
const revokeObjectURL = vi.fn();
const blobOf = (tag: string) => Object.assign(new Blob([tag]), { tag });

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(URL, { createObjectURL, revokeObjectURL });
});
afterEach(() => setStatusStore(null));

function setup(initial: string[], printBlob: (m: string, id: string, size: string) => Promise<Blob>) {
  installStore({ printBlob: vi.fn(printBlob) });
  const meeting = ref("m1");
  const ids = ref(initial);
  let api!: ReturnType<typeof usePrintThumbs>;
  const w = mount(
    defineComponent({
      setup() {
        api = usePrintThumbs(() => meeting.value, () => ids.value);
        return () => h("div");
      },
    }),
  );
  return { w, meeting, ids, api: () => api };
}

describe("usePrintThumbs", () => {
  it("cria uma URL por print e revoga a de quem saiu da lista", async () => {
    const { ids, api } = setup(["p1", "p2"], async (_m, id, size) => blobOf(`${id}-${size}`));
    await flushPromises();
    expect(api().thumbs.value).toEqual({ p1: "blob:p1-thumb", p2: "blob:p2-thumb" });
    ids.value = ["p1"];
    await flushPromises();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:p2-thumb");
    expect(api().thumbs.value).toEqual({ p1: "blob:p1-thumb" });
  });

  it("revoga tudo ao desmontar", async () => {
    const { w, api } = setup(["p1", "p2"], async (_m, id) => blobOf(id));
    await flushPromises();
    w.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:p1");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:p2");
    expect(api().thumbs.value).toEqual({});
  });

  it("resposta que chega depois da remoção não cria URL", async () => {
    let finish!: (b: Blob) => void;
    const { ids, api } = setup(["p1"], () => new Promise<Blob>((resolve) => { finish = resolve; }));
    await flushPromises();
    ids.value = [];
    await flushPromises();
    finish(blobOf("p1"));
    await flushPromises();
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(api().thumbs.value).toEqual({});
  });

  it("resposta que chega depois de desmontar não cria URL", async () => {
    let finish!: (b: Blob) => void;
    const { w } = setup(["p1"], () => new Promise<Blob>((resolve) => { finish = resolve; }));
    await flushPromises();
    w.unmount();
    finish(blobOf("p1"));
    await flushPromises();
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("falha entra em failed e não é tentada de novo", async () => {
    const printBlob = vi.fn(async (): Promise<Blob> => { throw new Error("x"); });
    const { ids, api } = setup(["p1"], printBlob);
    await flushPromises();
    expect(api().failed.value.has("p1")).toBe(true);
    expect(api().thumbs.value).toEqual({});
    ids.value = ["p1", "p2"];
    await flushPromises();
    expect(printBlob).toHaveBeenCalledTimes(2); // p1 uma vez, p2 uma vez
  });

  it("ao trocar de reunião revoga e recarrega, descartando o que estava em voo", async () => {
    const { meeting, api } = setup(["p1"], async (m, id) => blobOf(`${m}-${id}`));
    await flushPromises();
    expect(api().thumbs.value).toEqual({ p1: "blob:m1-p1" });
    meeting.value = "m2";
    await flushPromises();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:m1-p1");
    expect(api().thumbs.value).toEqual({ p1: "blob:m2-p1" });
  });
});
