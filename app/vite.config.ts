/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import process from "node:process";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const host = process.env.TAURI_DEV_HOST;
const page = (name: string) => fileURLToPath(new URL(name, import.meta.url));
// Versão do app (package.json), embutida no código como `__APP_VERSION__`.
const { version } = JSON.parse(readFileSync(page("package.json"), "utf8")) as { version: string };

// https://vite.dev/config/
export default defineConfig(() => ({
  plugins: [vue()],
  define: { __APP_VERSION__: JSON.stringify(version) },

  // Opções do Tauri (só valem em `tauri dev`/`tauri build`):
  // 1. não esconder os erros do Rust
  clearScreen: false,
  // 2. o Tauri espera uma porta fixa; falhar se ela estiver ocupada
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. não observar `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
  },

  // duas páginas: a janela principal e o overlay
  build: {
    rollupOptions: {
      input: {
        main: page("index.html"),
        overlay: page("overlay.html"),
      },
    },
  },

  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
}));
