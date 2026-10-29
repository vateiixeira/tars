<script setup lang="ts">
// Área principal das reuniões: a reunião selecionada na lista (que fica na barra lateral) ou um
// estado vazio. Sem nenhuma reunião, "Iniciar reunião" pede ao App a mesma ação da cápsula.
import MeetingDetail from "../components/MeetingDetail.vue";
import AppButton from "../components/ui/AppButton.vue";
import AppIcon from "../components/ui/AppIcon.vue";
import BrandOrb from "../components/ui/BrandOrb.vue";
import { useStatus } from "../composables/useStatus";

defineProps<{
  meetingId: string | null;
  /** `false` quando a biblioteca carregou e não há nenhuma reunião. */
  hasMeetings: boolean;
}>();
const emit = defineEmits<{
  deleted: [meetingId: string];
  tasksView: [active: boolean];
  start: [];
}>();

const { connected } = useStatus();
</script>

<template>
  <div class="meetings-view">
    <MeetingDetail
      v-if="meetingId"
      :meeting-id="meetingId"
      @deleted="emit('deleted', $event)"
      @tasks-view="emit('tasksView', $event)"
    />
    <div v-else class="empty">
      <BrandOrb :size="96" />
      <template v-if="hasMeetings">
        <h1>Nenhuma reunião selecionada</h1>
        <p>Escolha uma reunião na biblioteca para ver o resumo, a transcrição e as tarefas.</p>
      </template>
      <template v-else>
        <h1>Nenhuma reunião ainda</h1>
        <p>
          Clique em Iniciar reunião para gravar o microfone e o áudio do PC. A transcrição e o
          resumo aparecem aqui.
        </p>
        <AppButton variant="primary" class="start" :disabled="!connected" @click="emit('start')">
          <AppIcon name="mic" :size="18" />Iniciar reunião
        </AppButton>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* Painel de vidro sem backdrop-filter: o detalhe tem diálogos e menus fixed (Ruling R2). */
.meetings-view {
  flex: 1;
  overflow: hidden;
  height: 100%;
  min-height: 0;
  border-radius: var(--radius-xl);
  background: var(--glass);
  border: 1px solid var(--glass-border);
}
/* Estado vazio (am-empty). */
.empty {
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-12) var(--space-6);
  text-align: center;
}
h1 {
  margin: var(--space-3) 0 0;
  font-size: 20px;
  line-height: 26px;
  font-weight: 700;
}
p {
  margin: 0;
  max-width: 44ch;
  color: var(--text-muted);
}
.start {
  margin-top: var(--space-2);
}
</style>
