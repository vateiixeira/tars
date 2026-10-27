import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { ref } from "vue";
import { ApiError, type ApiClient } from "../api/client";
import type { ConnectionState, EventConnection } from "../api/events";
import type { ClaudeModels, Devices, ServerEvent, Settings, Voice } from "../api/types";
import { DEFAULT_SUMMARY_PROMPT } from "../components/settings/constants";
import { createStatusStore, setStatusStore } from "../composables/useStatus";
import { resetAutoRefresh } from "../components/settings/claudeAutoRefresh";
import SettingsView from "./SettingsView.vue";

const opener = vi.hoisted(() => ({ openUrl: vi.fn(async (_url: string) => {}) }));
vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: (url: string) => opener.openUrl(url) }));
// Evento do Rust ao esconder/mostrar a janela principal (fechar só esconde: o app fica na bandeja).
const tauriEvents = vi.hoisted(() => ({ handlers: new Map<string, (e: { payload: unknown }) => void>() }));
vi.mock("@tauri-apps/api/event", () => ({
  listen: async (name: string, cb: (e: { payload: unknown }) => void) => {
    tauriEvents.handlers.set(name, cb);
    return () => tauriEvents.handlers.delete(name);
  },
}));

function setVisibility(state: "hidden" | "visible"): void {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

const baseSettings: Settings = {
  dictation_enabled: true,
  dictation_evaluation_enabled: false,
  key: "ctrl+super",
  output_key: "KEY_LEFTCTRL+KEY_LEFTSHIFT",
  paste: "shift+insert",
  model: "large-v3-turbo",
  compute_type: "float16",
  prompt: "Olá!",
  trailing_space: true,
  min_sec: 0.4,
  max_sec: 300,
  ignore_devices: ["ydotoold"],
  ydotool: "/usr/bin/ydotool",
  auto_title: true,
  summary_prompt: "Resuma.",
  diarizer: "local",
  voice_match_threshold_local: 0.5,
  voice_match_threshold_pyannote: 0.6,
  mic_device: null,
  pc_device: null,
  follow_call_app: true,
  aec_enabled: true,
  claude_bin: null,
  claude_model: null,
  claude_effort: null,
  tasks_model: null,
  tasks_effort: null,
  auto_summary: true,
  meeting_model_idle_sec: 120,
  issues_enabled: false,
  issues_root: "",
  issues_mcp_url: "",
  issues_mcp_name: "issues",
  tasks_prompt: "",
  hf_token_set: false,
  print_mode: "off",
  print_folder: null,
  print_key: "ctrl+alt+p",
};

const devices: Devices = {
  sources: [
    { name: "mic.usb", description: "Microfone USB" },
    { name: "mic.int", description: "Microfone interno" },
  ],
  sinks: [{ name: "out.hdmi", description: "HDMI" }],
  default_source: "mic.int",
  default_sink: "out.hdmi",
};

const voice = (id: string, name: string): Voice => ({
  id,
  name,
  n_samples: 2,
  created_at: null,
  updated_at: null,
});

const catalog: ClaudeModels = {
  default_model: "opus",
  default_label: "Opus 5.5",
  models: [
    { alias: "opus", model_id: "claude-opus-5-5", label: "Opus 5.5", is_new: true },
    { alias: "sonnet", model_id: "claude-sonnet-5-5", label: "Sonnet 5.5", is_new: false },
  ],
  updated_at: "2026-10-20T14:35:00",
};

function setup(overrides: Partial<Record<keyof ApiClient, unknown>> = {}, saved: Partial<Settings> = {}) {
  let current = { ...baseSettings, ...saved };
  const listeners = new Map<string, ((e: ServerEvent) => void)[]>();
  const events: EventConnection = {
    on: (type, cb) => {
      listeners.set(type, [...(listeners.get(type) ?? []), cb as (e: ServerEvent) => void]);
      return () => {};
    },
    state: ref<ConnectionState>("connecting"),
    error: ref<string | null>(null),
    close: () => {},
  };
  const emit = (e: ServerEvent) => listeners.get(e.type)?.forEach((cb) => cb(e));
  const client = {
    status: vi.fn(async () => {
      throw new Error("sem status");
    }),
    getSettings: vi.fn(async () => current),
    putSettings: vi.fn(async (partial: Record<string, unknown>) => {
      current = { ...current, ...partial } as Settings;
      if ("hf_token" in partial) {
        delete (current as unknown as Record<string, unknown>).hf_token;
        current.hf_token_set = partial.hf_token !== null;
      }
      return current;
    }),
    getText: vi.fn(async () => "deploy\n"),
    putText: vi.fn(async (_kind: string, text: string) => text),
    devices: vi.fn(async () => devices),
    monitorLevels: vi.fn(async (on: boolean) => ({ on })),
    listVoices: vi.fn(async () => [voice("v1", "Ana"), voice("v2", "Beto")]),
    renameVoice: vi.fn(async (id: string, name: string) => voice(id, name)),
    deleteVoice: vi.fn(async () => ({ ok: true })),
    claudeModels: vi.fn(async () => catalog),
    refreshClaudeModels: vi.fn(async () => catalog),
    ...overrides,
  } as unknown as ApiClient;
  setStatusStore(createStatusStore({ client, events }));
  return { client, emit };
}

let wrapper: VueWrapper | null = null;

async function open(section?: string): Promise<VueWrapper> {
  wrapper = mount(SettingsView, { attachTo: document.body });
  await flushPromises();
  if (section) {
    await wrapper.get(`[data-section="${section}"]`).trigger("click");
    await flushPromises();
  }
  return wrapper;
}

beforeEach(() => {
  vi.useRealTimers();
  resetAutoRefresh();
});
afterEach(() => {
  setVisibility("visible");
  wrapper?.unmount();
  wrapper = null;
  setStatusStore(null);
});

describe("SettingsView", () => {
  it("permite ligar e desligar a coleta de áudio para avaliação", async () => {
    const { client } = setup();
    const w = await open();
    const toggle = w.get("#toggle-dictation_evaluation_enabled");
    expect((toggle.element as HTMLInputElement).checked).toBe(false);
    expect(w.text()).toContain("neste computador para avaliação posterior");

    await toggle.setValue(true);
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ dictation_evaluation_enabled: true });

    await toggle.setValue(false);
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ dictation_evaluation_enabled: false });
  });

  it("oferece modelos Whisper em português e salva modelo e precisão", async () => {
    const { client } = setup();
    const w = await open("whisper");
    const select = w.get("#whisper-model");
    expect((select.element as HTMLSelectElement).value).toBe("large-v3-turbo");
    expect(select.findAll("option").map((o) => o.attributes("value"))).toEqual(["large-v3-turbo", "large-v3", "medium", "small"]);
    expect(w.text()).toContain("recomendado");
    await select.setValue("small");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ model: "small" });
    expect(w.text()).toContain("Pode errar mais nomes e termos técnicos");
    await w.get("#whisper-precision").setValue("int8_float16");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ compute_type: "int8_float16" });
  });

  it("preserva modelo e precisão personalizados já salvos", async () => {
    setup({}, { model: "/modelos/whisper-local", compute_type: "float32" });
    const w = await open("whisper");
    expect((w.get("#whisper-model").element as HTMLSelectElement).value).toBe("/modelos/whisper-local");
    expect((w.get("#whisper-precision").element as HTMLSelectElement).value).toBe("float32");
    expect(w.text()).toContain("personalizado");
  });

  it("restaura o modelo salvo e mostra erro se a alteração falhar", async () => {
    setup({ putSettings: vi.fn(async () => { throw new ApiError(400, "Modelo indisponível"); }) });
    const w = await open("whisper");
    await w.get("#whisper-model").setValue("small");
    await flushPromises();
    expect((w.get("#whisper-model").element as HTMLSelectElement).value).toBe("large-v3-turbo");
    expect(w.get('[data-test="field-error"]').text()).toBe("Modelo indisponível");
  });

  it("permite configurar a liberação por inatividade sem desligar o ditado", async () => {
    const { client } = setup();
    const w = await open("meetings");
    const select = w.get("#meet-model-idle");
    expect((select.element as HTMLSelectElement).value).toBe("120");
    await select.setValue("60");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ meeting_model_idle_sec: 60 });
    await select.setValue("0");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ meeting_model_idle_sec: 0 });
    expect(w.text()).toContain("O ditado continua pronto");
  });

  it("edits the computer audio shortcut separately from the microphone", async () => {
    const { client } = setup();
    const w = await open();
    expect((w.get("#dict-output-key").element as HTMLInputElement).value).toBe("KEY_LEFTCTRL+KEY_LEFTSHIFT");
    await w.get("#dict-output-key").setValue("alt+KEY_F8");
    await w.get("#dict-output-key").trigger("change");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ output_key: "alt+KEY_F8" });
    expect((w.get("#dict-key").element as HTMLInputElement).value).toBe("ctrl+super");
  });

  it("shows validation error under field", async () => {
    const msg = "Tecla de ditado inválida: 'xx' (ex.: ctrl+super, KEY_RIGHTCTRL, alt+KEY_SPACE)";
    const { client } = setup({
      putSettings: vi.fn(async () => {
        throw new ApiError(400, msg);
      }),
    });
    const w = await open();
    const input = w.get("#dict-key");
    await input.setValue("xx");
    await input.trigger("change");
    await flushPromises();

    expect(client.putSettings).toHaveBeenCalledWith({ key: "xx" });
    const field = input.element.closest(".field")!;
    expect(field.querySelector('[data-test="field-error"]')?.textContent).toBe(msg);
    // o erro pertence só a este campo
    expect(w.findAll('[data-test="field-error"]')).toHaveLength(1);
    // o valor digitado permanece no campo
    expect((input.element as HTMLInputElement).value).toBe("xx");

    // voltar a digitar limpa o erro
    await input.trigger("input");
    expect(w.findAll('[data-test="field-error"]')).toHaveLength(0);
  });

  it("sends empty duration as null so the backend explains", async () => {
    const { client } = setup();
    const w = await open();
    const input = w.get("#dict-min");
    await input.setValue("");
    await input.trigger("change");
    await flushPromises();
    expect(client.putSettings).toHaveBeenCalledWith({ min_sec: null });
  });

  it("text editor saves vocab", async () => {
    const { client } = setup();
    const w = await open("vocab");
    expect(client.getText).toHaveBeenCalledWith("vocab");
    const area = w.get("textarea");
    expect((area.element as HTMLTextAreaElement).value).toBe("deploy\n");
    const save = w.get("button.primary");
    expect(save.attributes("disabled")).toBeDefined(); // sem alteração

    await area.setValue("deploy\nstaging\n");
    expect(save.attributes("disabled")).toBeUndefined();
    await save.trigger("click");
    await flushPromises();

    expect(client.putText).toHaveBeenCalledWith("vocab", "deploy\nstaging\n");
    expect(w.text()).toContain("Salvo. Vale a partir do próximo ditado.");
    expect(save.attributes("disabled")).toBeDefined();
  });

  it("preserves separate unsaved text drafts when changing sections", async () => {
    setup();
    const w = await open("vocab");
    await w.get("textarea").setValue("termo ainda não salvo");
    await w.get('[data-section="subs"]').trigger("click"); await flushPromises();
    await w.get("textarea").setValue("errado => certo");
    await w.get('[data-section="vocab"]').trigger("click"); await flushPromises();
    expect((w.get("textarea").element as HTMLTextAreaElement).value).toBe("termo ainda não salvo");
    await w.get('[data-section="subs"]').trigger("click"); await flushPromises();
    expect((w.get("textarea").element as HTMLTextAreaElement).value).toBe("errado => certo");
  });

  it("audio section starts and stops level monitor", async () => {
    const { client, emit } = setup();
    const w = await open("audio");
    expect(client.monitorLevels).toHaveBeenCalledTimes(1);
    expect(client.monitorLevels).toHaveBeenLastCalledWith(true);

    emit({ type: "levels", mic_db: -30, pc_db: -90 });
    await flushPromises();
    const bars = w.findAll('[data-test="meter-bar"]');
    expect(bars).toHaveLength(2);
    expect(bars[0].attributes("style")).toContain("width: 50%");
    expect(bars[1].attributes("style")).toContain("width: 0%");

    // trocar de seção desliga o monitor
    await w.get('[data-section="dictation"]').trigger("click");
    await flushPromises();
    expect(client.monitorLevels).toHaveBeenCalledTimes(2);
    expect(client.monitorLevels).toHaveBeenLastCalledWith(false);

    // voltar liga de novo; desmontar a tela desliga
    await w.get('[data-section="audio"]').trigger("click");
    await flushPromises();
    expect(client.monitorLevels).toHaveBeenLastCalledWith(true);
    w.unmount();
    wrapper = null;
    expect(client.monitorLevels).toHaveBeenLastCalledWith(false);
    expect(client.monitorLevels).toHaveBeenCalledTimes(4);
  });

  it("audio section renews the level monitor while open", async () => {
    vi.useFakeTimers();
    const { client } = setup();
    const w = await open("audio");
    expect(client.monitorLevels).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(29_000);
    expect(client.monitorLevels).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(client.monitorLevels).toHaveBeenCalledTimes(2);
    expect(client.monitorLevels).toHaveBeenLastCalledWith(true);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(client.monitorLevels).toHaveBeenCalledTimes(3);

    // ao sair da seção desliga e para de renovar
    await w.get('[data-section="dictation"]').trigger("click");
    await flushPromises();
    expect(client.monitorLevels).toHaveBeenCalledTimes(4);
    expect(client.monitorLevels).toHaveBeenLastCalledWith(false);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(client.monitorLevels).toHaveBeenCalledTimes(4);
  });

  it("audio section pauses the level monitor while the window is hidden", async () => {
    vi.useFakeTimers();
    const { client } = setup();
    await open("audio");
    expect(client.monitorLevels).toHaveBeenCalledTimes(1);

    // a página fica oculta (janela escondida/minimizada): desliga e para de renovar
    setVisibility("hidden");
    await flushPromises();
    expect(client.monitorLevels).toHaveBeenCalledTimes(2);
    expect(client.monitorLevels).toHaveBeenLastCalledWith(false);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(client.monitorLevels).toHaveBeenCalledTimes(2);

    setVisibility("visible");
    await flushPromises();
    expect(client.monitorLevels).toHaveBeenCalledTimes(3);
    expect(client.monitorLevels).toHaveBeenLastCalledWith(true);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(client.monitorLevels).toHaveBeenCalledTimes(4);
  });

  it("audio section pauses the level monitor when the window goes to the tray", async () => {
    vi.useFakeTimers();
    const { client } = setup();
    await open("audio");
    await flushPromises();
    const visibility = tauriEvents.handlers.get("main-visibility");
    expect(visibility).toBeDefined();

    visibility!({ payload: false }); // X da janela: o Rust esconde e avisa
    await flushPromises();
    expect(client.monitorLevels).toHaveBeenLastCalledWith(false);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(client.monitorLevels).toHaveBeenCalledTimes(2);

    visibility!({ payload: true }); // "Abrir" da bandeja
    await flushPromises();
    expect(client.monitorLevels).toHaveBeenCalledTimes(3);
    expect(client.monitorLevels).toHaveBeenLastCalledWith(true);
  });

  it("explains that following the call app records the output where it plays", async () => {
    setup();
    const w = await open("audio");
    expect(w.text()).not.toContain("só o som do aplicativo");
    expect(w.text()).toContain("Grava a saída de áudio onde o app da chamada");
  });

  it("reloads settings when dictation is paused from the tray", async () => {
    const { client, emit } = setup();
    const status = (enabled: boolean): ServerEvent => ({
      type: "status",
      dictation: { enabled, active: false, key: "ctrl+super" },
      meeting: { state: "idle", meeting_id: null, started_at: null, followed_app: null, alerts: [], print_mode: "off" },
    });
    emit(status(true));
    const w = await open();
    expect(client.getSettings).toHaveBeenCalledTimes(1);
    const toggle = () => w.findAll("label").find((l) => l.text().includes("Ditado ligado"))!.get("input");
    expect((toggle().element as HTMLInputElement).checked).toBe(true);

    // "Pausar ditado" na bandeja: o backend muda o valor por fora desta tela
    (client.getSettings as ReturnType<typeof vi.fn>).mockResolvedValue({ ...baseSettings, dictation_enabled: false });
    emit(status(false));
    await flushPromises();
    expect(client.getSettings).toHaveBeenCalledTimes(2);
    expect((toggle().element as HTMLInputElement).checked).toBe(false);

    emit(status(false)); // mesmo valor: não recarrega de novo
    await flushPromises();
    expect(client.getSettings).toHaveBeenCalledTimes(2);
  });

  it("device select sends null for system default", async () => {
    const { client } = setup();
    const w = await open("audio");
    const mic = w.get("#audio-mic");
    expect(mic.findAll("option").map((o) => o.text())).toEqual([
      "Padrão do sistema (Microfone interno)",
      "Microfone USB",
      "Microfone interno",
    ]);

    await mic.setValue("mic.usb");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ mic_device: "mic.usb" });

    await mic.setValue("");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ mic_device: null });

    await w.get("#audio-pc").setValue("out.hdmi");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ pc_device: "out.hdmi" });

    // liga/desliga
    await w.get("#toggle-aec_enabled").setValue(false);
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ aec_enabled: false });
  });

  it("hf token is never displayed back", async () => {
    const secret = "hf_SEGREDO123";
    const { client } = setup({}, { diarizer: "pyannote" });
    const w = await open("meetings");
    const input = w.get("#meet-token");
    expect(w.get('[data-test="token-status"]').text()).toBe("Nenhum token configurado");
    expect(w.find('[data-test="token-remove"]').exists()).toBe(false);
    // vazio não salva nada
    expect(w.get('[data-test="token-save"]').attributes("disabled")).toBeDefined();
    await input.trigger("keydown.enter");
    expect(client.putSettings).not.toHaveBeenCalled();

    await input.setValue(secret);
    await w.get('[data-test="token-save"]').trigger("click");
    await flushPromises();

    expect(client.putSettings).toHaveBeenCalledWith({ hf_token: secret });
    expect((input.element as HTMLInputElement).type).toBe("password");
    expect((input.element as HTMLInputElement).value).toBe("");
    expect(w.html()).not.toContain(secret);
    // html() não serializa o `value` dos inputs: conferir direto em todos
    for (const el of w.element.querySelectorAll("input, textarea, select")) {
      expect((el as HTMLInputElement).value).not.toContain(secret);
    }
    expect(w.get('[data-test="token-status"]').text()).toBe("Token configurado");

    // remover é explícito e envia null
    await w.get('[data-test="token-remove"]').trigger("click");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ hf_token: null });
    expect(w.get('[data-test="token-status"]').text()).toBe("Nenhum token configurado");
  });

  it("opens the Hugging Face model page in the browser", async () => {
    setup({}, { diarizer: "pyannote" });
    const w = await open("meetings");
    const link = w.get('[data-test="hf-model-link"]');
    expect(link.attributes("target")).toBeUndefined();
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.element.dispatchEvent(click);
    await flushPromises();
    // o webview não abre aba nova: vai pelo navegador do sistema
    expect(click.defaultPrevented).toBe(true);
    expect(opener.openUrl).toHaveBeenCalledWith("https://huggingface.co/pyannote/speaker-diarization-community-1");
  });

  it("restores the default summary prompt", async () => {
    const { client } = setup();
    const w = await open("meetings");
    await w.get('[data-test="restore-summary"]').trigger("click");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ summary_prompt: DEFAULT_SUMMARY_PROMPT });
    expect(w.get('[data-test="restore-summary"]').attributes("disabled")).toBeDefined();

  });

  it("saves the threshold of the selected engine", async () => {
    const { client } = setup();
    const w = await open("meetings");
    expect(w.get('[data-test="threshold-value"]').text()).toBe("0,50");
    const range = w.get("#meet-threshold");
    await range.setValue("0.35");
    await range.trigger("change");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ voice_match_threshold_local: 0.35 });
    expect(w.get('[data-test="threshold-value"]').text()).toBe("0,35");

    await w.get('[data-test="diarizer-select"]').setValue("pyannote");
    await flushPromises();
    expect(w.get('[data-test="threshold-value"]').text()).toBe("0,60");
    await w.get("#meet-threshold").setValue("0.75");
    await w.get("#meet-threshold").trigger("change");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ voice_match_threshold_pyannote: 0.75 });
    expect(w.get('[data-test="threshold-value"]').text()).toBe("0,75");
  });

  it("hides the token field while the local engine is selected", async () => {
    setup();
    const w = await open("meetings");
    expect(w.find('[data-test="token-save"]').exists()).toBe(false);
    expect(w.find('[data-test="hf-model-link"]').exists()).toBe(false);
  });

  it("saves the engine choice", async () => {
    const { client } = setup();
    const w = await open("meetings");
    await w.get('[data-test="diarizer-select"]').setValue("pyannote");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ diarizer: "pyannote" });
    expect(w.find('[data-test="token-save"]').exists()).toBe(true);
  });

  it("renames and deletes voices with confirmation", async () => {
    const { client } = setup();
    const w = await open("voices");
    expect(w.findAll('[data-test="voice"]')).toHaveLength(2);

    await w.findAll('[data-test="voice-rename"]')[0].trigger("click");
    const input = w.get("input[type=text]");
    await input.setValue("Ana Souza");
    await w.get('[data-test="voice-save"]').trigger("click");
    await flushPromises();
    expect(client.renameVoice).toHaveBeenCalledWith("v1", "Ana Souza");
    expect(w.text()).toContain("Ana Souza");

    await w.findAll('[data-test="voice-delete"]')[1].trigger("click");
    expect(client.deleteVoice).not.toHaveBeenCalled();
    expect(w.text()).toContain("Apagar a voz “Beto”?");
    await w.get('[data-test="voice-confirm-delete"]').trigger("click");
    await flushPromises();
    expect(client.deleteVoice).toHaveBeenCalledWith("v2");
    expect(w.findAll('[data-test="voice"]')).toHaveLength(1);
  });

  it("shows the backend error when a voice rename fails", async () => {
    setup({
      renameVoice: vi.fn(async () => {
        throw new ApiError(400, "O nome da voz não pode ficar vazio");
      }),
    });
    const w = await open("voices");
    await w.findAll('[data-test="voice-rename"]')[0].trigger("click");
    await w.get("input[type=text]").setValue(" ");
    await w.get('[data-test="voice-save"]').trigger("click");
    await flushPromises();
    expect(w.get('[data-test="voice-error"]').text()).toBe("O nome da voz não pode ficar vazio");
  });

  it("Reuniões tem o toggle 'Resumir automaticamente' que salva auto_summary", async () => {
    const { client } = setup();
    const w = await open("meetings");
    const toggle = w.findAll("label").find((l) => l.text().includes("Resumir automaticamente"))!.get("input");
    expect((toggle.element as HTMLInputElement).checked).toBe(true);
    expect(w.text()).toContain("Ao terminar o processamento, o Claude gera o resumo sem precisar clicar.");
    await toggle.setValue(false);
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ auto_summary: false });
  });

  it("seção Criador de issues fica no grupo Integrações e o toggle 'Criador de issues' salva issues_enabled", async () => {
    const { client } = setup();
    const w = await open();
    const ids = w.findAll("[data-section]").map((b) => b.attributes("data-section"));
    expect(ids).toEqual(["dictation", "audio", "whisper", "vocab", "subs", "meetings", "voices", "claude", "issues"]);
    expect(w.get('[data-section="issues"]').text()).toBe("Criador de issues");

    await w.get('[data-section="issues"]').trigger("click");
    await flushPromises();
    expect(w.text()).toContain("Ao terminar cada reunião, o Claude levanta as tarefas a partir da pasta raiz dos projetos.");
    await w.get("#toggle-issues_enabled").setValue(true);
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ issues_enabled: true });
  });

  it("pasta raiz salva no change e mostra o erro do backend", async () => {
    const msg = "Pasta raiz não encontrada: /nao/existe";
    const putSettings = vi.fn(async (_p: Record<string, unknown>) => {
      throw new ApiError(400, msg);
    });
    const { client } = setup({ putSettings });
    const w = await open("issues");
    const input = w.get("#issues-root");
    (input.element as HTMLInputElement).value = "/nao/existe";
    await input.trigger("input"); // digitar não salva; só o change
    expect(client.putSettings).not.toHaveBeenCalled();
    await input.trigger("change");
    await flushPromises();
    expect(client.putSettings).toHaveBeenCalledWith({ issues_root: "/nao/existe" });
    const field = input.element.closest(".field")!;
    expect(field.querySelector('[data-test="field-error"]')?.textContent).toBe(msg);
    expect((input.element as HTMLInputElement).value).toBe("/nao/existe");
    await input.trigger("input");
    expect(w.findAll('[data-test="field-error"]')).toHaveLength(0);
  });

  it("pasta raiz salva com sucesso e mostra o valor confirmado", async () => {
    const { client } = setup();
    const w = await open("issues");
    const input = w.get("#issues-root");
    await input.setValue("/home/test/projects");
    await input.trigger("change");
    await flushPromises();
    expect(client.putSettings).toHaveBeenCalledWith({ issues_root: "/home/test/projects" });
    expect((input.element as HTMLInputElement).value).toBe("/home/test/projects");
    expect(w.findAll('[data-test="field-error"]')).toHaveLength(0);
  });

  it("saves a custom MCP server name and clears the connection result", async () => {
    const { client } = setup();
    const w = await open("issues");
    await w.get('[data-test="issues-test"]').trigger("click");
    await flushPromises();
    expect(w.find('[data-test="issues-status"]').exists()).toBe(true);
    await w.get("#issues-server").setValue("my-tracker");
    await w.get("#issues-server").trigger("change");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ issues_mcp_name: "my-tracker" });
    expect(w.find('[data-test="issues-status"]').exists()).toBe(false);
  });

  it("endereço do MCP de issues salva issues_mcp_url", async () => {
    const { client } = setup();
    const w = await open("issues");
    const input = w.get("#issues-url");
    await input.setValue("http://localhost:8080/mcp");
    await input.trigger("change");
    await flushPromises();
    expect(client.putSettings).toHaveBeenCalledWith({ issues_mcp_url: "http://localhost:8080/mcp" });
  });

  it("'Testar conexão' mostra 'Conectado como vi'", async () => {
    let finish!: (v: { ok: true; username: string }) => void;
    const issueTest = vi.fn(() => new Promise<{ ok: true; username: string }>((r) => (finish = r)));
    setup({ issueTest });
    const w = await open("issues");
    const btn = w.get('[data-test="issues-test"]');
    expect(btn.text()).toBe("Testar conexão");
    await btn.trigger("click");
    expect(issueTest).toHaveBeenCalledTimes(1);
    expect(btn.text()).toBe("Testando…");
    expect(btn.attributes("disabled")).toBeDefined();
    finish({ ok: true, username: "vi" });
    await flushPromises();
    expect(w.get('[data-test="issues-status"]').text()).toBe("Conectado como vi");
    expect(btn.text()).toBe("Testar conexão");
    expect(btn.attributes("disabled")).toBeUndefined();
  });

  it("salvar outro endereço do MCP de issues limpa o 'Conectado como'", async () => {
    setup({ issueTest: vi.fn(async () => ({ ok: true as const, username: "vi" })) });
    const w = await open("issues");
    await w.get('[data-test="issues-test"]').trigger("click");
    await flushPromises();
    expect(w.get('[data-test="issues-status"]').text()).toBe("Conectado como vi");
    const input = w.get("#issues-url");
    await input.setValue("http://localhost:9090/mcp");
    await input.trigger("change");
    await flushPromises();
    expect(w.find('[data-test="issues-status"]').exists()).toBe(false);
  });

  it("'Testar conexão' com erro mostra a mensagem do backend (com a instrução de login)", async () => {
    const msg = "MCP de issues não autenticado. Rode `claude mcp` e faça login no issues.";
    setup({
      issueTest: vi.fn(async () => {
        throw new ApiError(502, msg);
      }),
    });
    const w = await open("issues");
    await w.get('[data-test="issues-test"]').trigger("click");
    await flushPromises();
    expect(w.get('[data-test="issues-status"]').text()).toBe(msg);
    expect(w.text()).not.toContain("Conectado como");
  });

  it("prompt do levantamento salva no change; 'Restaurar padrão' envia tasks_prompt: null", async () => {
    const { client } = setup();
    const w = await open("issues");
    const area = w.get("#issues-prompt");
    (area.element as HTMLTextAreaElement).value = "Levante as tarefas.";
    await area.trigger("input"); // digitar não salva; só o change
    expect(client.putSettings).not.toHaveBeenCalled();
    await area.trigger("change");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ tasks_prompt: "Levante as tarefas." });
    expect((area.element as HTMLTextAreaElement).value).toBe("Levante as tarefas.");

    // rascunho não salvo é descartado ao restaurar
    (area.element as HTMLTextAreaElement).value = "rascunho";
    await area.trigger("input");
    await w.get('[data-test="restore-tasks-prompt"]').trigger("click");
    await flushPromises();
    expect(client.putSettings).toHaveBeenLastCalledWith({ tasks_prompt: null });
    expect((area.element as HTMLTextAreaElement).value).toBe("");
  });

  describe("seção Claude", () => {
    const optionTexts = (w: VueWrapper, id: string) => w.get(id).findAll("option").map((o) => o.text());

    it("aparece no grupo Integrações antes de Criador de issues", async () => {
      setup();
      const w = await open();
      const ids = w.findAll("[data-section]").map((b) => b.attributes("data-section"));
      expect(ids.indexOf("claude")).toBe(ids.indexOf("issues") - 1);
      expect(w.get('[data-section="claude"]').text()).toBe("Claude");
    });

    it("mostra o título, a nota e os dois grupos", async () => {
      setup();
      const w = await open("claude");
      expect(w.get("h2").text()).toBe("Claude");
      expect(w.text()).toContain(
        "O modelo fica gravado pelo apelido (opus, sonnet…): quando sair uma versão nova, ela passa a ser usada sozinha.",
      );
      const titles = w.findAll(".group h3").map((h) => h.text());
      expect(titles).toEqual(["Resumo, títulos e nomes", "Levantamento de tarefas e criação no MCP de issues"]);
    });

    it("lista os modelos com 'hoje' e o marcador de novo, e o padrão com o rótulo", async () => {
      setup();
      const w = await open("claude");
      const expected = [
        "Padrão do Claude Code (hoje Opus 5.5)",
        "Opus (hoje Opus 5.5) · novo",
        "Sonnet (hoje Sonnet 5.5)",
      ];
      expect(optionTexts(w, "#claude-model")).toEqual(expected);
      expect(optionTexts(w, "#tasks-model")).toEqual(expected);
      expect(optionTexts(w, "#claude-effort")).toEqual([
        "Padrão", "Baixo (low)", "Médio (medium)", "Alto (high)", "Muito alto (xhigh)", "Máximo (max)",
      ]);
    });

    it("o padrão fica sem rótulo quando o backend não informa", async () => {
      setup({ claudeModels: vi.fn(async () => ({ ...catalog, default_label: null })) });
      const w = await open("claude");
      expect(optionTexts(w, "#claude-model")[0]).toBe("Padrão do Claude Code");
    });

    it("valor salvo fora da lista aparece marcado e selecionado", async () => {
      setup({}, { claude_model: "claude-opus-4-1", tasks_model: "sonnet", tasks_effort: "high" });
      const w = await open("claude");
      expect(optionTexts(w, "#claude-model")[3]).toBe("claude-opus-4-1 (não está na lista)");
      expect((w.get("#claude-model").element as HTMLSelectElement).value).toBe("claude-opus-4-1");
      expect(optionTexts(w, "#tasks-model")).toHaveLength(3);
      expect((w.get("#tasks-model").element as HTMLSelectElement).value).toBe("sonnet");
      expect((w.get("#tasks-effort").element as HTMLSelectElement).value).toBe("high");
      expect((w.get("#claude-effort").element as HTMLSelectElement).value).toBe("");
    });

    it("trocar modelo ou effort salva a chave certa; o padrão envia null", async () => {
      const { client } = setup();
      const w = await open("claude");
      const put = client.putSettings as ReturnType<typeof vi.fn>;

      await w.get("#claude-model").setValue("sonnet");
      await flushPromises();
      expect(put).toHaveBeenLastCalledWith({ claude_model: "sonnet" });
      await w.get("#claude-model").setValue("");
      await flushPromises();
      expect(put).toHaveBeenLastCalledWith({ claude_model: null });

      await w.get("#claude-effort").setValue("xhigh");
      await flushPromises();
      expect(put).toHaveBeenLastCalledWith({ claude_effort: "xhigh" });
      await w.get("#claude-effort").setValue("");
      await flushPromises();
      expect(put).toHaveBeenLastCalledWith({ claude_effort: null });

      await w.get("#tasks-model").setValue("opus");
      await flushPromises();
      expect(put).toHaveBeenLastCalledWith({ tasks_model: "opus" });
      await w.get("#tasks-effort").setValue("max");
      await flushPromises();
      expect(put).toHaveBeenLastCalledWith({ tasks_effort: "max" });
    });

    it("erro ao salvar aparece no campo e o select volta ao valor salvo", async () => {
      setup({
        putSettings: vi.fn(async () => {
          throw new ApiError(400, "Modelo inválido");
        }),
      });
      const w = await open("claude");
      await w.get("#claude-model").setValue("sonnet");
      await flushPromises();
      const field = w.get("#claude-model").element.closest(".field")!;
      expect(field.querySelector('[data-test="field-error"]')?.textContent).toBe("Modelo inválido");
      expect((w.get("#claude-model").element as HTMLSelectElement).value).toBe("");
    });

    it("mostra quando a lista foi atualizada, no horário local", async () => {
      setup();
      const w = await open("claude");
      expect(w.get('[data-test="claude-updated"]').text()).toBe("Lista atualizada em 20/10 14:35");
    });

    it("'Atualizar modelos' chama o refresh, trava o botão e atualiza a lista e a data", async () => {
      let finish!: (v: ClaudeModels) => void;
      const refreshClaudeModels = vi.fn(() => new Promise<ClaudeModels>((r) => (finish = r)));
      setup({ refreshClaudeModels });
      const w = await open("claude");
      const btn = w.get('[data-test="claude-refresh"]');
      expect(btn.text()).toBe("Atualizar modelos");

      await btn.trigger("click");
      expect(refreshClaudeModels).toHaveBeenCalledTimes(1);
      expect(btn.text()).toBe("Atualizando…");
      expect(btn.attributes("disabled")).toBeDefined();

      finish({
        ...catalog,
        models: [...catalog.models, { alias: "haiku", model_id: "claude-haiku-5", label: "Haiku 5", is_new: true }],
        updated_at: "2026-10-21T08:05:00",
      });
      await flushPromises();
      expect(btn.text()).toBe("Atualizar modelos");
      expect(btn.attributes("disabled")).toBeUndefined();
      expect(w.get('[data-test="claude-updated"]').text()).toBe("Lista atualizada em 21/10 08:05");
      expect(optionTexts(w, "#claude-model")).toContain("Haiku (hoje Haiku 5) · novo");
      expect(w.find('[data-test="claude-error"]').exists()).toBe(false);
    });

    it("não atualiza sozinho quando já há lista", async () => {
      const { client } = setup();
      await open("claude");
      expect(client.claudeModels).toHaveBeenCalledTimes(1);
      expect(client.refreshClaudeModels).not.toHaveBeenCalled();
    });

    it("não atualiza sozinho se a lista veio vazia mas já foi consultada", async () => {
      const { client } = setup({
        claudeModels: vi.fn(async () => ({ ...catalog, models: [], updated_at: "2026-10-20T14:35:00" })),
      });
      await open("claude");
      expect(client.refreshClaudeModels).not.toHaveBeenCalled();
    });

    it("atualiza uma vez sozinho quando o cache está vazio e nunca foi consultado", async () => {
      const empty: ClaudeModels = { default_model: null, default_label: null, models: [], updated_at: null };
      const { client } = setup({ claudeModels: vi.fn(async () => empty) });
      const w = await open("claude");
      expect(client.refreshClaudeModels).toHaveBeenCalledTimes(1);
      expect(w.get('[data-test="claude-updated"]').text()).toBe("Lista atualizada em 20/10 14:35");
      expect(optionTexts(w, "#claude-model")).toHaveLength(3);
    });

    it("oferece o modelo salvo enquanto a lista carrega", async () => {
      setup({ claudeModels: vi.fn(() => new Promise<ClaudeModels>(() => {})) }, { claude_model: "opus" });
      const w = await open("claude");
      expect(optionTexts(w, "#claude-model")).toEqual(["Padrão do Claude Code", "opus"]);
      expect((w.get("#claude-model").element as HTMLSelectElement).value).toBe("opus");
    });

    it("oferece o modelo salvo quando a lista não carrega", async () => {
      setup(
        {
          claudeModels: vi.fn(async () => {
            throw new ApiError(500, "Falha ao ler a lista");
          }),
        },
        { tasks_model: "sonnet" },
      );
      const w = await open("claude");
      expect(optionTexts(w, "#tasks-model")).toEqual(["Padrão do Claude Code", "sonnet"]);
      expect((w.get("#tasks-model").element as HTMLSelectElement).value).toBe("sonnet");
    });

    it("não repete a atualização automática ao reabrir a seção", async () => {
      const empty: ClaudeModels = { default_model: null, default_label: null, models: [], updated_at: null };
      const refreshClaudeModels = vi.fn(async () => {
        throw new ApiError(502, "claude não encontrado");
      });
      setup({ claudeModels: vi.fn(async () => empty), refreshClaudeModels });
      const w = await open("claude");
      expect(refreshClaudeModels).toHaveBeenCalledTimes(1);

      await w.get('[data-section="audio"]').trigger("click"); // desmonta a seção
      await flushPromises();
      await w.get('[data-section="claude"]').trigger("click"); // monta de novo
      await flushPromises();
      expect(refreshClaudeModels).toHaveBeenCalledTimes(1);

      // o botão manual continua funcionando
      await w.get('[data-test="claude-refresh"]').trigger("click");
      await flushPromises();
      expect(refreshClaudeModels).toHaveBeenCalledTimes(2);
    });

    it("mostra 'Lista ainda não consultada' sem data", async () => {
      const empty: ClaudeModels = { default_model: null, default_label: null, models: [], updated_at: null };
      setup({
        claudeModels: vi.fn(async () => empty),
        refreshClaudeModels: vi.fn(async () => {
          throw new ApiError(502, "Não foi possível consultar os modelos");
        }),
      });
      const w = await open("claude");
      expect(w.get('[data-test="claude-updated"]').text()).toBe("Lista ainda não consultada");
    });

    it("mostra o erro do backend quando a atualização falha e mantém a lista", async () => {
      const msg = "Não foi possível consultar os modelos do Claude";
      setup({
        refreshClaudeModels: vi.fn(async () => {
          throw new ApiError(502, msg);
        }),
      });
      const w = await open("claude");
      await w.get('[data-test="claude-refresh"]').trigger("click");
      await flushPromises();
      expect(w.get('[data-test="claude-error"]').text()).toBe(msg);
      expect(w.get('[data-test="claude-error"]').attributes("role")).toBe("alert");
      expect(optionTexts(w, "#claude-model")).toHaveLength(3);
      expect(w.get('[data-test="claude-refresh"]').attributes("disabled")).toBeUndefined();
    });

    it("mostra o erro quando não consegue carregar a lista", async () => {
      setup({
        claudeModels: vi.fn(async () => {
          throw new ApiError(500, "Falha ao ler a lista");
        }),
      });
      const w = await open("claude");
      expect(w.get('[data-test="claude-error"]').text()).toBe("Falha ao ler a lista");
    });
  });
});
