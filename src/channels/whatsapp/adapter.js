/**
 * channels/whatsapp/adapter.js
 * ------------------------------------------------------------------
 * Traduce la respuesta estructurada de Lucía (reply_text + blocks) al
 * formato que espera la API de WhatsApp Business (Meta Cloud API).
 *
 * WhatsApp permite máximo 3 "quick reply buttons" por mensaje, así
 * que si Lucía sugiere más de 3 opciones, mandamos una lista de texto
 * como fallback simple (base para mejorar luego con "list messages").
 * ------------------------------------------------------------------
 */

const axios = require('axios');
const logger = require('../../utils/logger');

function luciaResponseToWhatsappPayload(to, luciaResponse) {
  const quickReplyBlock = (luciaResponse.blocks || []).find(
    (b) => b.type === 'quick_replies'
  );

  // Sin botones: mensaje de texto simple
  if (!quickReplyBlock || !quickReplyBlock.options?.length) {
    return {
      messaging_product: 'whatsapp',
      to,
      type: 'text',
      text: { body: luciaResponse.reply_text },
    };
  }

  const options = quickReplyBlock.options.slice(0, 3); // límite de WhatsApp

  return {
    messaging_product: 'whatsapp',
    to,
    type: 'interactive',
    interactive: {
      type: 'button',
      body: { text: luciaResponse.reply_text },
      action: {
        buttons: options.map((label, i) => ({
          type: 'reply',
          reply: { id: `opt_${i}`, title: label.slice(0, 20) }, // WhatsApp limita a 20 chars
        })),
      },
    },
  };
}

async function sendWhatsappMessage(payload) {
  const phoneNumberId = process.env.WHATSAPP_PHONE_NUMBER_ID;
  const accessToken = process.env.WHATSAPP_ACCESS_TOKEN;

  if (!phoneNumberId || !accessToken) {
    logger.warn(
      'WHATSAPP_PHONE_NUMBER_ID o WHATSAPP_ACCESS_TOKEN no configurados; no se envía el mensaje (modo dev).'
    );
    logger.info('Payload que se hubiera enviado a WhatsApp:', JSON.stringify(payload));
    return;
  }

  await axios.post(
    `https://graph.facebook.com/v20.0/${phoneNumberId}/messages`,
    payload,
    { headers: { Authorization: `Bearer ${accessToken}` } }
  );
}

module.exports = { luciaResponseToWhatsappPayload, sendWhatsappMessage };
