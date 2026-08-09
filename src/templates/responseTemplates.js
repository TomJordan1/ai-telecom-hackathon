/**
 * responseTemplates.js
 * ------------------------------------------------------------------
 * Fábricas de "blocks" reutilizables para armar respuestas ricas.
 * Hoy soportamos quick_replies y card (sin imagen todavía). Cuando
 * agreguen las plantillas con imágenes, este es el archivo donde se
 * suman nuevos "createXxxBlock" (ej: createImageBlock, createCarousel).
 *
 * Nada de esto llama al modelo: son helpers para construir bloques a
 * mano cuando el flujo es determinístico (ej: un menú fijo), en vez
 * de depender de que el modelo los genere siempre.
 * ------------------------------------------------------------------
 */

function createQuickReplies(options = []) {
  return { type: 'quick_replies', options };
}

function createCard({ title, subtitle = '', imageUrl = null, actionLabel = null }) {
  return {
    type: 'card',
    title,
    subtitle,
    image_url: imageUrl,
    action_label: actionLabel,
  };
}

// --- Placeholder para lo que viene más adelante ---
// function createImageBlock({ imageUrl, caption }) {
//   return { type: 'image', image_url: imageUrl, caption };
// }
//
// function createCarouselBlock(cards = []) {
//   return { type: 'carousel', cards };
// }

const MENU_PRINCIPAL = {
  reply_text: 'Hola, soy Lucía 👋 ¿En qué te ayudo hoy?',
  blocks: [
    createQuickReplies([
      'Ver mi plan',
      'Pagar mi recibo',
      'Soporte técnico',
      'Hablar con un asesor',
    ]),
  ],
  intent: 'general_info',
  escalate_to_human: false,
  confidence: 1,
};

module.exports = {
  createQuickReplies,
  createCard,
  MENU_PRINCIPAL,
};
