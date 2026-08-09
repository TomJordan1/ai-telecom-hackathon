/**
 * channels/website/widget.js
 * ------------------------------------------------------------------
 * Endpoint que consume el widget de chat embebido en la Website de
 * Movistar (ver docs/embed-example.html para un ejemplo de integración
 * en el frontend). Usa sessionId anónimo generado en el navegador
 * (localStorage) ya que en la web no siempre hay usuario autenticado.
 * ------------------------------------------------------------------
 */

const express = require('express');
const cors = require('./corsConfig');
const { handleIncomingMessage, resetConversation } = require('../../core/chatEngine');
const { assertValidChatRequest } = require('../../utils/validators');
const logger = require('../../utils/logger');

const router = express.Router();

router.use(cors);

// POST /api/website/chat
// body: { sessionId, message }
router.post('/chat', async (req, res) => {
  try {
    assertValidChatRequest(req.body);
    const { sessionId, message } = req.body;

    const response = await handleIncomingMessage({
      sessionId: `website:${sessionId}`,
      channel: 'website',
      userMessage: message,
    });

    res.json(response);
  } catch (err) {
    logger.error('Error en /api/website/chat', err);
    res.status(400).json({ error: err.message });
  }
});

router.post('/reset', (req, res) => {
  const { sessionId } = req.body || {};
  if (!sessionId) return res.status(400).json({ error: 'sessionId es requerido' });
  resetConversation(`website:${sessionId}`);
  res.json({ ok: true });
});

module.exports = router;
