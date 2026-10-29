<script setup lang="ts">
// Área principal das reuniões: a reunião selecionada na lista (que fica na barra lateral).
import MeetingDetail from "../components/MeetingDetail.vue";

defineProps<{ meetingId: string | null }>();
const emit = defineEmits<{ deleted: [meetingId: string]; tasksView: [active: boolean] }>();
</script>

<template>
  <div class="meetings-view">
    <MeetingDetail v-if="meetingId" :meeting-id="meetingId" @deleted="emit('deleted', $event)" @tasks-view="emit('tasksView', $event)" />
    <div v-else class="empty">
      <p class="empty-kicker">Reuniões de equipe</p><h1 class="empty-title">Registro e acompanhamento de reuniões</h1>
      <p>Inicie uma gravação ou selecione uma reunião para consultar a transcrição, revisar decisões e acompanhar tarefas.</p>
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
.empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  padding: 32px;
  text-align: center;
}
.empty p {
  margin: 0;
}
.empty-title {
  font-size: 1.8rem;
  color:var(--text);
  max-width:24ch;
  line-height:1.3;
  margin-bottom: 4px !important;
}
.empty > p { max-width:48ch; line-height:1.7; }
.empty-kicker { font-size:var(--font-meta); margin-bottom:16px!important; }
</style>
