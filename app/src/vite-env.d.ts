/// <reference types="vite/client" />

/** Versão do app, injetada pelo `define` do vite.config.ts. */
declare const __APP_VERSION__: string;

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<{}, {}, any>;
  export default component;
}
