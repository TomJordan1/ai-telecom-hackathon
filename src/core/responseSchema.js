/**
 * responseSchema.js
 * ------------------------------------------------------------------
 * Define el CONTRATO de salida de Lucía. El modelo nunca responde en
 * texto plano: siempre devuelve un objeto JSON con esta forma. Esto
 * permite que cada canal (WhatsApp, App, Website) renderice la misma
 * respuesta de forma distinta (texto simple, botones, tarjetas, etc.)
 * sin tener que "adivinar" el formato parseando texto libre.
 *
 * Esta es la BASE. Más adelante, cuando agreguen plantillas con
 * imágenes, solo se necesita extender "blocks" con un nuevo tipo
 * ("image", "carousel") sin romper lo ya construido.
 * ------------------------------------------------------------------
 *
 * Forma del objeto que DeepSeek debe devolver (vía JSON mode):
 *
 * {
 *   "reply_text": string,          // Texto principal, ya con el tono de Lucía
 *   "blocks": [                    // Bloques de contenido enriquecido (opcional)
 *     {
 *       "type": "quick_replies",
 *       "options": ["Ver planes", "Hablar con un asesor"]
 *     },
 *     {
 *       "type": "card",            // Para futuras respuestas con imagen/producto
 *       "title": string,
 *       "subtitle": string,
 *       "image_url": string | null,
 *       "action_label": string | null
 *     }
 *   ],
 *   "intent": string,              // Clasificación interna: "billing", "plans",
 *                                  // "support", "portability", "other", etc.
 *   "escalate_to_human": boolean,  // true si se debe derivar a un asesor humano
 *   "confidence": number           // 0-1, qué tan segura está Lucía de la respuesta
 * }
 */

const RESPONSE_JSON_SCHEMA_DESCRIPTION = `
Responde ÚNICAMENTE con un objeto JSON válido (sin markdown, sin \`\`\`json,
sin texto antes o después) con exactamente esta forma:

{
  "reply_text": "string - el mensaje principal para el usuario, en el tono de Lucía",
  "blocks": [
    // opcional, arreglo vacío si no aplica.
    // tipos soportados hoy: "quick_replies", "card"
    { "type": "quick_replies", "options": ["opción 1", "opción 2"] }
  ],
  "intent": "string - una de: billing, plans, technical_support, portability, recharge, general_info, complaint, other",
  "escalate_to_human": false,
  "confidence": 0.0
}

No agregues campos extra. No expliques el JSON. No uses texto plano fuera del JSON.
`.trim();

/**
 * Valida (de forma básica) que la respuesta del modelo cumpla el contrato.
 * Si algo falla, devolvemos un fallback seguro en vez de romper el canal.
 */
function validateLuciaResponse(raw) {
  const fallback = {
    reply_text:
      'Tuve un problema para procesar eso. ¿Puedes contarme de nuevo qué necesitas?',
    blocks: [],
    intent: 'other',
    escalate_to_human: false,
    confidence: 0,
  };

  if (!raw || typeof raw !== 'object') return fallback;
  if (typeof raw.reply_text !== 'string' || raw.reply_text.trim() === '') {
    return fallback;
  }

  return {
    reply_text: raw.reply_text.trim(),
    blocks: Array.isArray(raw.blocks) ? raw.blocks : [],
    intent: typeof raw.intent === 'string' ? raw.intent : 'other',
    escalate_to_human: Boolean(raw.escalate_to_human),
    confidence:
      typeof raw.confidence === 'number'
        ? Math.min(Math.max(raw.confidence, 0), 1)
        : 0.5,
  };
}

module.exports = {
  RESPONSE_JSON_SCHEMA_DESCRIPTION,
  validateLuciaResponse,
};
