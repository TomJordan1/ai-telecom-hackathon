/**
 * channels/whatsapp/webhook.js
 * ------------------------------------------------------------------
 * Webhook para WhatsApp Business Cloud API (Meta). Maneja:
 *  1) La verificación inicial del webhook (GET, con hub.challenge).
 *  2) Los mensajes entrantes (POST), que se pasan al chatEngine y la
 *     respuesta de Lucía se traduce a bloques nativos de WhatsApp
 *     (texto + botones interactivos) usando adapter.js.
 * ------------------------------------------------------------------
 */

const express = require('express');
const { handleIncomingMessage } = require('../../core/chatEngine');
const { sendWhatsappMessage, luciaResponseToWhatsappPayload } = require('./adapter');
const logger = require('../../utils/logger');

const router = express.Router();

// GET /webhooks/whatsapp - verificación del webhook (requerido por Meta)
router.get('/', (req, res) => {
  const mode = req.query['hub.mode'];
  const token = req.query['hub.verify_token'];
  const challenge = req.query['hub.challenge'];

  if (mode === 'subscribe' && token === process.env.WHATSAPP_VERIFY_TOKEN) {
    logger.info('Webhook de WhatsApp verificado correctamente');
    return res.status(200).send(challenge);
  }
  return res.sendStatus(403);
});

// POST /webhooks/whatsapp - mensajes entrantes
router.post('/', async (req, res) => {
  // Respondemos 200 rápido para que Meta no reintente; el procesamiento
  // real sigue de forma async.
  res.sendStatus(200);

  try {
    const entry = req.body?.entry?.[0];
    const change = entry?.changes?.[0]?.value;
    const message = change?.messages?.[0];

    if (!message) return; // eventos de status (delivered/read), no son mensajes

    const from = message.from; // número de teléfono del usuario, sirve como sessionId
    const userText =
      message.text?.body || message.interactive?.button_reply?.title || '';

    if (!userText) {
      logger.warn('Mensaje de WhatsApp sin texto reconocible', message.type);
      return;
    }

    const luciaResponse = await handleIncomingMessage({
      sessionId: `whatsapp:${from}`,
      channel: 'whatsapp',
      userMessage: userText,
    });

    const payload = luciaResponseToWhatsappPayload(from, luciaResponse);
    await sendWhatsappMessage(payload);
  } catch (err) {
    logger.error('Error procesando mensaje de WhatsApp', err);
  }
});

module.exports = router;
