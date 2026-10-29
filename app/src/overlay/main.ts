// Entrada do overlay (overlay.html): a pílula sempre por cima que mostra gravação e ditado.
import { createApp } from "vue";
import Overlay from "./Overlay.vue";
import "@fontsource-variable/quicksand";
import "../styles/tokens/colors.css";
import "../styles/tokens/metrics.css";
import { initAppearance } from "../composables/useAppearance";
initAppearance();

createApp(Overlay).mount("#overlay");
