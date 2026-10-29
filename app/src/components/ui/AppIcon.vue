<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 });
// Ícones de traço do Ametista (docs/design-redesign/ametista/icones.json): viewBox 24, traço 1.9.
const paths: Record<string, string> = {
  mic: "M12 15a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v7a3 3 0 0 0 3 3ZM5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
  settings: "M4 7h16M4 17h16M8 4v6M16 14v6",
  arrow: "M19 12H5m6-6-6 6 6 6",
  chevron: "m9 5 7 7-7 7",
  down: "m6 9 6 6 6-6",
  plus: "M12 5v14M5 12h14",
  check: "m5 12 4 4L19 6",
  copy: "M9 9h11v12H9zM15 9V3H3v12h6",
  export: "M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6",
  play: "m8 5 11 7-11 7V5Z",
  pause: "M8 5v14M16 5v14",
  volume: "m11 4-6 5H2v6h3l6 5V4ZM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14",
  people: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M18 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
  clock: "M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  edit: "m16 3 5 5L8 21H3v-5L16 3Zm-2 2 5 5",
  sun: "M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1 1M18 18l1 1M5 19l1-1M18 6l1-1M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
  moon: "M21 13A9 9 0 0 1 11 3 9 9 0 1 0 21 13Z",
  compare: "M3 4h18v16H3zM12 4v16",
  warning: "m12 3 10 18H2L12 3ZM12 9v5M12 17h.01",
  close: "m6 6 12 12M6 18 18 6",
  refresh: "M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 13 3M5 15a8 8 0 0 0 13 3",
  file: "M14 2H4v20h16V8l-6-6ZM14 2v6h6M8 13h8M8 17h6",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7",
  headphones: "M3 14v-3a9 9 0 0 1 18 0v3M3 13h4v8H3zM17 13h4v8h-4z",
  spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  task: "M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9",
  stop: "M7 7h10v10H7z",
  archive: "M3 4h18v5H3zM5 9v11h14V9M10 13h4",
  link: "M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
  "user-plus": "M15 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M8.5 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM20 8v6M23 11h-6",
  keyboard: "M2 6h20v12H2zM6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10",
  wave: "M3 12h2M7 8v8M11 5v14M15 9v6M19 11v2",
};
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.9"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] ?? paths.file" />
  </svg>
</template>
