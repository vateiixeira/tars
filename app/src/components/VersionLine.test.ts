import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import VersionLine from "./VersionLine.vue";
import { IDLE, installStore } from "../test/meetingFakes";
import { setStatusStore } from "../composables/useStatus";
import type { Status, VersionInfo } from "../api/types";

const openUrl = vi.fn(async (_url: string) => {});
const writeText = vi.fn(async (_text: string) => {});
vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: (u: string) => openUrl(u) }));
vi.mock("@tauri-apps/plugin-clipboard-manager", () => ({ writeText: (t: string) => writeText(t) }));
vi.mock("../lib/appVersion", () => ({ appVersion: "0.2.0" }));

const URL_V3 = "https://github.com/example/tars/releases/tag/v0.3.0";
const COMMAND = "python3 scripts/update.py";
const FORCE_COMMAND = "python3 scripts/update.py --force";

function version(over: Partial<VersionInfo> = {}): VersionInfo {
  return {
    current: "0.2.0",
    latest: "0.2.0",
    url: null,
    update_available: false,
    install: "git",
    ...over,
  };
}

let wrapper: ReturnType<typeof mount> | undefined;
function open(v: VersionInfo | null) {
  const status: Status = { ...IDLE, ...(v ? { version: v } : {}) };
  installStore({}, status);
  wrapper = mount(VersionLine, { attachTo: document.body });
  return flushPromises();
}
const dialog = () => document.querySelector<HTMLElement>("[role=dialog]");
const button = (label: string) =>
  [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.includes(label));

beforeEach(() => {
  openUrl.mockClear();
  writeText.mockClear();
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  setStatusStore(null);
  document.body.innerHTML = "";
});

describe("VersionLine", () => {
  it("mostra só a versão quando não há atualização", async () => {
    await open(version());
    expect(wrapper!.text()).toBe("TARS 0.2.0");
    expect(wrapper!.find("button").exists()).toBe(false);
  });

  it("mostra o link e abre o diálogo com o comando numa instalação git", async () => {
    await open(version({ latest: "0.3.0", url: URL_V3, update_available: true }));
    expect(wrapper!.text()).toBe("TARS 0.2.0 · atualizar para 0.3.0");
    expect(dialog()).toBeNull();

    await wrapper!.get("button").trigger("click");
    expect(dialog()?.getAttribute("aria-label")).toBe("TARS 0.3.0 disponível");
    expect(dialog()?.querySelector("code")?.textContent).toBe(COMMAND);

    button("Copiar")!.click();
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith(COMMAND);

    button("Ver novidades")!.click();
    expect(openUrl).toHaveBeenCalledWith(URL_V3);
  });

  it("instalação por pacote mostra download em vez do comando", async () => {
    await open(version({ latest: "0.3.0", url: URL_V3, update_available: true, install: "package" }));
    await wrapper!.get("button").trigger("click");
    expect(dialog()).not.toBeNull();
    expect(dialog()!.textContent).not.toContain("scripts/update.py");
    expect(dialog()!.querySelector("code")).toBeNull();

    button("Baixar a versão 0.3.0")!.click();
    expect(openUrl).toHaveBeenCalledWith(URL_V3);
  });

  it("app mais antigo que o serviço: pede recompilar e mostra o comando com --force", async () => {
    await open(version({ current: "0.3.0", latest: "0.3.0" }));
    expect(wrapper!.text()).toBe("app 0.2.0 · serviço 0.3.0");

    await wrapper!.get("button").trigger("click");
    expect(dialog()!.querySelector("code")?.textContent).toBe(FORCE_COMMAND);
    expect(dialog()!.textContent).toContain("não foi recompilado");
    expect(dialog()!.textContent).toContain("feche e reabra o app");

    button("Copiar")!.click();
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith(FORCE_COMMAND);
  });

  it("app mais novo que o serviço: pede refazer o serviço, sem mandar reabrir o app", async () => {
    await open(version({ current: "0.1.9", latest: "0.1.9" }));
    expect(wrapper!.text()).toBe("app 0.2.0 · serviço 0.1.9");

    await wrapper!.get("button").trigger("click");
    expect(dialog()!.querySelector("code")?.textContent).toBe(FORCE_COMMAND);
    expect(dialog()!.textContent).toContain("serviço ficou para trás");
    expect(dialog()!.textContent).not.toContain("não foi recompilado");

    button("Copiar")!.click();
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith(FORCE_COMMAND);
  });

  it("compara as versões como números (0.10.0 é maior que 0.9.0)", async () => {
    await open(version({ current: "0.10.0", latest: "0.10.0" }));
    await wrapper!.get("button").trigger("click");
    expect(dialog()!.textContent).toContain("não foi recompilado");
  });

  it("divergência numa instalação por pacote mostra o download em vez do comando", async () => {
    await open(version({ current: "0.3.0", latest: "0.3.0", url: URL_V3, install: "package" }));
    await wrapper!.get("button").trigger("click");
    expect(dialog()!.textContent).not.toContain("scripts/update.py");
    expect(dialog()!.querySelector("code")).toBeNull();
    expect(button("Copiar")).toBeUndefined();

    button("Baixar a versão 0.3.0")!.click();
    expect(openUrl).toHaveBeenCalledWith(URL_V3);
  });

  it("divergência tem precedência sobre o aviso de atualização", async () => {
    await open(version({ current: "0.3.0", latest: "0.4.0", url: URL_V3, update_available: true }));
    expect(wrapper!.text()).toBe("app 0.2.0 · serviço 0.3.0");
  });

  it("erro ao copiar vai para o console", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    writeText.mockRejectedValueOnce(new Error("sem área de transferência"));
    await open(version({ latest: "0.3.0", url: URL_V3, update_available: true }));
    await wrapper!.get("button").trigger("click");
    button("Copiar")!.click();
    await flushPromises();
    expect(error).toHaveBeenCalled();
    error.mockRestore();
  });

  it("sem backend mostra só a versão do app", async () => {
    await open(null);
    expect(wrapper!.text()).toBe("TARS 0.2.0");
  });

  it("desconectado ignora o último status, mesmo com atualização ou divergência", async () => {
    const status: Status = { ...IDLE, version: version({ latest: "0.3.0", url: URL_V3, update_available: true }) };
    const { events } = installStore({}, status);
    wrapper = mount(VersionLine, { attachTo: document.body });
    await flushPromises();
    expect(wrapper.text()).toBe("TARS 0.2.0 · atualizar para 0.3.0");

    events.state.value = "closed";
    await flushPromises();
    expect(wrapper.text()).toBe("TARS 0.2.0");
    expect(wrapper.find("button").exists()).toBe(false);
  });
});
