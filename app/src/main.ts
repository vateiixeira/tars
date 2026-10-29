import { createApp } from "vue";
import App from "./App.vue";
import "@fontsource-variable/quicksand";
import "@fontsource/jetbrains-mono/500.css";
import "./styles/base.css";
import { initAppearance } from "./composables/useAppearance";

initAppearance();

createApp(App).mount("#app");
