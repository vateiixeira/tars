// Miniaturas dos prints de uma reunião. As imagens vêm da API com o token (não dá para usar `<img src>`
// direto), então viram object URLs: criadas ao carregar, revogadas quando o id sai da lista, quando a
// reunião muda e ao desmontar. Respostas que chegam depois disso são descartadas sem criar URL.
import { onBeforeUnmount, ref, watch } from "vue";
import { useStatus } from "./useStatus";

export function usePrintThumbs(meetingId: () => string, ids: () => string[]) {
  const { client } = useStatus();
  /** id do print -> object URL da miniatura. */
  const thumbs = ref<Record<string, string>>({});
  /** ids cuja miniatura falhou ao carregar (o chamador mostra um fallback). */
  const failed = ref<ReadonlySet<string>>(new Set());
  const pending = new Set<string>();
  let alive = true;
  let epoch = 0; // muda a cada troca de reunião: invalida o que estava em voo

  function drop(id: string): void {
    const url = thumbs.value[id];
    if (url) URL.revokeObjectURL(url);
    const { [id]: _gone, ...rest } = thumbs.value;
    thumbs.value = rest;
  }

  async function load(id: string): Promise<void> {
    pending.add(id);
    const seq = epoch;
    const mid = meetingId();
    const current = () => alive && seq === epoch && ids().includes(id);
    try {
      const blob = await client.printBlob(mid, id, "thumb");
      if (!current()) return;
      thumbs.value = { ...thumbs.value, [id]: URL.createObjectURL(blob) };
    } catch {
      if (current()) failed.value = new Set(failed.value).add(id);
    } finally {
      if (seq === epoch) pending.delete(id);
    }
  }

  function sync(): void {
    const wanted = new Set(ids());
    for (const id of Object.keys(thumbs.value)) if (!wanted.has(id)) drop(id);
    if ([...failed.value].some((id) => !wanted.has(id)))
      failed.value = new Set([...failed.value].filter((id) => wanted.has(id)));
    for (const id of wanted)
      if (!(id in thumbs.value) && !failed.value.has(id) && !pending.has(id)) void load(id);
  }

  let lastMeeting = meetingId();
  watch(
    () => `${meetingId()}\u0001${ids().join("\u0000")}`,
    () => {
      if (meetingId() !== lastMeeting) {
        lastMeeting = meetingId();
        epoch++;
        pending.clear();
        for (const id of Object.keys(thumbs.value)) drop(id);
        failed.value = new Set();
      }
      sync();
    },
    { immediate: true },
  );

  onBeforeUnmount(() => {
    alive = false;
    for (const id of Object.keys(thumbs.value)) drop(id);
  });

  return { thumbs, failed };
}
