// Textos fixos das configurações que o backend não devolve em `GET /settings`.

/** Idêntico a `DEFAULT_SUMMARY_PROMPT` de settings.py: "Restaurar padrão" grava este texto. */
export const DEFAULT_SUMMARY_PROMPT =
  "Resuma a reunião abaixo em português do Brasil, em Markdown, com as seções: " +
  "**Pontos principais**, **Decisões**, **Tarefas** (quem faz o quê; use o nome de quem falou) " +
  "e **Pendências**. Seja objetivo e não invente nada que não esteja na transcrição.";

/** Modelo de diarização que o usuário precisa aceitar no Hugging Face para o token funcionar. */
export const HF_MODEL_URL = "https://huggingface.co/pyannote/speaker-diarization-community-1";

/** Opções de `key_mode` aceitas pelo backend (KEY_MODES). */
export const KEY_MODE_OPTIONS = [
  { value: "hold", label: "Segurar para falar (padrão)", hint: "Grava enquanto a combinação estiver segurada e transcreve ao soltar." },
  { value: "toggle", label: "Pressionar para ligar e desligar", hint: "Pressione uma vez para começar a gravar e de novo para transcrever e colar. Outra tecla junto com a pressionada que liga cancela a gravação." },
] as const;

/** Opções de `paste` aceitas pelo backend (PASTE_KEYS). */
export const PASTE_OPTIONS = ["shift+insert", "ctrl+v", "ctrl+shift+v"] as const;

// Modelos multilíngues, adequados para português. Não oferecer variantes .en ou distil inglesas.
export const WHISPER_MODELS = [
  { value: "large-v3-turbo", label: "Large v3 Turbo (recomendado)", hint: "Bom equilíbrio entre qualidade e velocidade. É o padrão do app." },
  { value: "large-v3", label: "Large v3 (modelo completo)", hint: "Modelo completo, com maior uso de memória e processamento mais lento." },
  { value: "medium", label: "Medium (intermediário)", hint: "Menor que o Large v3, mas pode ser mais lento que o Turbo." },
  { value: "small", label: "Small (mais leve)", hint: "Usa menos memória. Pode errar mais nomes e termos técnicos." },
] as const;

export const WHISPER_PRECISIONS = [
  { value: "float16", label: "Padrão (float16)", hint: "Precisão padrão para a GPU, priorizando a qualidade." },
  { value: "int8_float16", label: "Menor uso de memória (int8_float16)", hint: "Reduz a memória dos pesos do modelo. A qualidade e a velocidade podem variar." },
] as const;

export const MEETING_MODEL_IDLE_OPTIONS = [
  { value: 60, label: "Após 1 minuto sem reuniões" },
  { value: 120, label: "Após 2 minutos sem reuniões (recomendado)" },
  { value: 300, label: "Após 5 minutos sem reuniões" },
  { value: 600, label: "Após 10 minutos sem reuniões" },
  { value: 0, label: "Manter carregados" },
];
