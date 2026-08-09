/**
 * channels/mobileApp/routes.js
 * ------------------------------------------------------------------
 * Endpoint pensado para ser consumido por la app móvil de Movistar
 * (iOS/Android) vía HTTPS. La app manda el mensaje del usuario ya
 * autenticado y recibe el JSON estructurado de Lucía, listo para
 * renderizar como burbujas de chat + botones nativos (quick_replies)
 * o tarjetas (cards).
 * ------------------------------------------------------------------
 */

const express = require('express');
const { handleIncomingMessage, resetConversation } = require('../../core/chatEngine');
const { assertValidChatRequest } = require('../../utils/validators');
const logger = require('../../utils/logger');

const router = express.Router();

/**
 * Middleware simple de autenticación por API key interna.
 * En producción esto debería ser el token de sesión del propio
 * usuario de Mi Movistar (JWT), no una key compartida.
 */
function requireMobileApiKey(req, res, next) {
  const key = req.header('x-mobile-api-key');
  if (!process.env.MOBILE_API_KEY || key !== process.env.MOBILE_API_KEY) {
    return res.status(401).json({ error: 'No autorizado' });
  }
  next();
}

router.use(requireMobileApiKey);

// POST /api/mobile/chat
// body: { sessionId, message, userContext? }
router.post('/chat', async (req, res) => {
  try {
    assertValidChatRequest(req.body);
    const { sessionId, message, userContext } = req.body;

    const response = await handleIncomingMessage({
      sessionId: `mobile:${sessionId}`,
      channel: 'mobileApp',
      userMessage: message,
      extraContext: userContext, // ej: "Nombre: ... Plan actual: ..."
    });

    res.json(response);
  } catch (err) {
    logger.error('Error en /api/mobile/chat', err);
    res.status(400).json({ error: err.message });
  }
});

// POST /api/mobile/reset
router.post('/reset', (req, res) => {
  const { sessionId } = req.body || {};
  if (!sessionId) return res.status(400).json({ error: 'sessionId es requerido' });
  resetConversation(`mobile:${sessionId}`);
  res.json({ ok: true });
});

module.exports = router;
