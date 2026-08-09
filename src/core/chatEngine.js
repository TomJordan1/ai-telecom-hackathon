
const { callDeepSeek } = require('./deepseekClient');
const { LUCIA_SYSTEM_PROMPT, CHANNEL_TONE_NOTES } = require('../config/persona');
const {
  RESPONSE_JSON_SCHEMA_DESCRIPTION,
  validateLuciaResponse,
} = require('./responseSchema');

const conversationStore = new Map();

const MAX_HISTORY_MESSAGES = 12; // ventana de contexto que mandamos al modelo

function getHistory(sessionId) {
  if (!conversationStore.has(sessionId)) {
    conversationStore.set(sessionId, []);
  }
  return conversationStore.get(sessionId);
}

function pushToHistory(sessionId, role, content) {
  const history = getHistory(sessionId);
  history.push({ role, content });
  // recorta la ventana para no mandar contexto infinito a la API
  while (history.length > MAX_HISTORY_MESSAGES) {
    history.shift();
  }
}

/**
 * Construye el system prompt final combinando la personalidad base,
 * el matiz de tono según el canal, y el contrato de formato JSON.
 */
function buildSystemPrompt(channel, extraContext) {
  const toneNote = CHANNEL_TONE_NOTES[channel] || '';
  const contextBlock = extraContext
    ? `\n\n## Contexto adicional del cliente (usar solo si es relevante, no inventar sobre esto)\n${extraContext}`
    : '';

  return [
    LUCIA_SYSTEM_PROMPT,
    toneNote ? `\n## Nota de tono para este canal (${channel})\n${toneNote}` : '',
    contextBlock,
    `\n## Formato de respuesta obligatorio\n${RESPONSE_JSON_SCHEMA_DESCRIPTION}`,
  ]
    .filter(Boolean)
    .join('\n');
}

/**
 * Función principal: procesa un mensaje entrante y devuelve la
 * respuesta estructurada de Lucía.
 *
 * @param {object} params
 * @param {string} params.sessionId - identificador único de la conversación
 *   (ej: número de WhatsApp, userId de la app, sessionId del website)
 * @param {string} params.channel - 'whatsapp' | 'mobileApp' | 'website'
 * @param {string} params.userMessage - texto del usuario
 * @param {string} [params.extraContext] - datos del cliente ya autenticado
 *   (ej: "Nombre: Sebastián. Plan actual: Movistar Full 40GB.") que el
 *   backend de Movistar le pase a Lucía. Nunca datos sensibles completos.
 * @returns {Promise<object>} respuesta validada según responseSchema.js
 */
async function handleIncomingMessage({ sessionId, channel, userMessage, extraContext }) {
  if (!sessionId || !channel || !userMessage) {
    throw new Error('handleIncomingMessage requiere sessionId, channel y userMessage');
  }

  const systemPrompt = buildSystemPrompt(channel, extraContext);
  const history = getHistory(sessionId);

  const messages = [
    { role: 'system', content: systemPrompt },
    ...history,
    { role: 'user', content: userMessage },
  ];

  let raw;
  try {
    raw = await callDeepSeek(messages);
  } catch (err) {
    // Fallback de seguridad: si DeepSeek falla, Lucía no se cae,
    // responde algo honesto y ofrece escalar a un humano.
    return validateLuciaResponse({
      reply_text:
        'Ahora mismo tengo un problema técnico para responderte. Puedo conectarte con un asesor si prefieres.',
      blocks: [{ type: 'quick_replies', options: ['Hablar con un asesor', 'Intentar de nuevo'] }],
      intent: 'other',
      escalate_to_human: true,
      confidence: 0,
      _error: err.message,
    });
  }

  const response = validateLuciaResponse(raw);

  // guardamos el turno en el historial para mantener contexto
  pushToHistory(sessionId, 'user', userMessage);
  pushToHistory(sessionId, 'assistant', response.reply_text);

  return response;
}

function resetConversation(sessionId) {
  conversationStore.delete(sessionId);
}

module.exports = {
  handleIncomingMessage,
  resetConversation,
};
