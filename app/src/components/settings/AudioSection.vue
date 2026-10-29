<script setup lang="ts">
// Seção Áudio: dispositivos, opções de captura e medidores ao vivo. O monitor de níveis do
// backend (microfone aberto) fica ligado só enquanto esta seção está montada e a janela à vista:
// fechar a janela só a esconde, e a seção continuaria montada com o app na bandeja.
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { AudioDevice, Devices } from "../../api/types";
import { useStatus } from "../../composables/useStatus";
import { onWindowVisibility, pageVisible } from "../../composables/windowVisibility";
import LevelMeter from "./LevelMeter.vue";
import SettingField from "./SettingField.vue";
import ToggleField from "./ToggleField.vue";
import { messageOf, type SettingsForm } from "./useSettingsForm";

const props = defineProps<{ form: SettingsForm }>();
const { levels } = useStatus();

const devices = ref<Devices | null>(null);
const devicesError = ref<string | null>(null);
const monitorError = ref<string | null>(null);

/** Níveis mais velhos que isto (monitor parado, conexão caída) voltam ao piso. */
const STALE_MS = 2000;
const FLOOR = -90;
const now = ref(Date.now());
let tick: ReturnType<typeof setInterval> | undefined;
/** O backend desliga o monitor após 60 s (MONITOR_MAX_S); reenviar `true` renova o prazo. */
const RENEW_MS = 30_000;
let renew: ReturnType<typeof setInterval> | undefined;

const fresh = computed(() => (levels.value && now.value - levels.value.at < STALE_MS ? levels.value : null));
const micDb = computed(() => fresh.value?.mic_db ?? FLOOR);
const pcDb = computed(() => fresh.value?.pc_db ?? FLOOR);

async function loadDevices(): Promise<void> {
  devicesError.value = null;
  try {
    devices.value = await props.form.client.devices();
  } catch (e) {
    devicesError.value = messageOf(e);
  }
}

async function setMonitor(on: boolean): Promise<void> {
  try {
    await props.form.client.monitorLevels(on);
    if (on) monitorError.value = null;
  } catch (e) {
    if (on) monitorError.value = messageOf(e);
  }
}

let monitoring = false;
let offVisibility: (() => void) | undefined;

function startMonitor(): void {
  if (monitoring) return;
  monitoring = true;
  void setMonitor(true);
  renew = setInterval(() => void setMonitor(true), RENEW_MS);
}

function stopMonitor(): void {
  if (!monitoring) return;
  monitoring = false;
  clearInterval(renew);
  void setMonitor(false);
}

onMounted(() => {
  tick = setInterval(() => (now.value = Date.now()), 500);
  if (pageVisible()) startMonitor();
  offVisibility = onWindowVisibility((visible) => (visible ? startMonitor() : stopMonitor()));
  void loadDevices();
});

onUnmounted(() => {
  clearInterval(tick);
  offVisibility?.();
  stopMonitor();
});

type DeviceKey = "mic_device" | "pc_device";

/** Dispositivos da lista; o salvo que sumiu (desconectado) continua selecionável, marcado. */
function options(list: AudioDevice[] | undefined, saved: string | null): AudioDevice[] {
  const out = [...(list ?? [])];
  if (saved && !out.some((d) => d.name === saved)) {
    out.unshift({ name: saved, description: `${saved} (indisponível)` });
  }
  return out;
}

const micOptions = computed(() => options(devices.value?.sources, props.form.settings.value?.mic_device ?? null));
const pcOptions = computed(() => options(devices.value?.sinks, props.form.settings.value?.pc_device ?? null));

function systemDefaultLabel(name: string | null | undefined, list: AudioDevice[] | undefined): string {
  const desc = list?.find((d) => d.name === name)?.description;
  return desc ? `Padrão do sistema (${desc})` : "Padrão do sistema";
}

/** "Padrão do sistema" (valor vazio) vai como `null`. */
async function saveDevice(key: DeviceKey, e: Event): Promise<void> {
  const select = e.target as HTMLSelectElement;
  const ok = await props.form.save(key, select.value === "" ? null : select.value);
  if (!ok) select.value = props.form.settings.value?.[key] ?? "";
}
</script>

<template>
  <section v-if="form.settings.value" class="s-section">
    <p v-if="devicesError" class="s-error" role="alert">
      {{ devicesError }} <button class="s-btn" @click="loadDevices">Tentar de novo</button>
    </p>

    <div class="s-grid">
      <SettingField label="Microfone" for="audio-mic" :error="form.errors.mic_device">
        <select
          id="audio-mic"
          class="s-select"
          :value="form.settings.value.mic_device ?? ''"
          @change="saveDevice('mic_device', $event)"
        >
          <option value="">{{ systemDefaultLabel(devices?.default_source, devices?.sources) }}</option>
          <option v-for="d in micOptions" :key="d.name" :value="d.name">{{ d.description }}</option>
        </select>
      </SettingField>

      <SettingField label="Saída do PC (áudio da chamada)" for="audio-pc" :error="form.errors.pc_device">
        <select
          id="audio-pc"
          class="s-select"
          :value="form.settings.value.pc_device ?? ''"
          @change="saveDevice('pc_device', $event)"
        >
          <option value="">{{ systemDefaultLabel(devices?.default_sink, devices?.sinks) }}</option>
          <option v-for="d in pcOptions" :key="d.name" :value="d.name">{{ d.description }}</option>
        </select>
      </SettingField>
    </div>

    <ToggleField
      :form="form"
      field="follow_call_app"
      label="Seguir o app da chamada"
      hint="Grava a saída de áudio onde o app da chamada (o que está usando o microfone) está tocando e avisa se for outra."
    />
    <ToggleField
      :form="form"
      field="aec_enabled"
      label="Cancelamento de eco"
      hint="Remove do microfone o som das caixas, para não repetir a fala dos outros."
    />

    <div class="s-card meters">
      <h2>Teste os níveis</h2>
      <p class="s-note">Fale e toque um som no PC para conferir antes da reunião.</p>
      <LevelMeter icon="mic" label="Nível do microfone" :db="micDb" />
      <LevelMeter icon="volume" label="Nível do som do PC" :db="pcDb" />
      <p v-if="monitorError" class="s-error" role="alert" data-test="monitor-error">{{ monitorError }}</p>
    </div>
  </section>
</template>

<style scoped>
.meters {
  gap: var(--space-3);
}
</style>
