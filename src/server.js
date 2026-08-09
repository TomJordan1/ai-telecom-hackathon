/**
 * server.js
 * ------------------------------------------------------------------
 * Punto de entrada. Levanta Express y monta las rutas de cada canal:
 *  - /api/mobile/*     -> App móvil de Movistar
 *  - /webhooks/whatsapp -> WhatsApp Business Cloud API
 *  - /api/website/*    -> Widget embebido en la Website
 * ------------------------------------------------------------------
 */

require('dotenv').config();
const express = require('express');
const rateLimit = require('express-rate-limit');
const logger = require('./utils/logger');

const mobileRoutes = require('./channels/mobileApp/routes');
const whatsappWebhook = require('./channels/whatsapp/webhook');
const websiteWidget = require('./channels/website/widget');

const app = express();
app.use(express.json());

// Rate limit básico para evitar abuso del endpoint de chat (ajustar en prod)
const chatLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 30,
  message: { error: 'Demasiadas solicitudes, intenta de nuevo en un momento.' },
});
app.use('/api/mobile', chatLimiter);
app.use('/api/website', chatLimiter);

app.get('/health', (req, res) => res.json({ status: 'ok', bot: 'Lucía' }));

app.use('/api/mobile', mobileRoutes);
app.use('/webhooks/whatsapp', whatsappWebhook);
app.use('/api/website', websiteWidget);

// Manejador de errores genérico
app.use((err, req, res, next) => {
  logger.error('Error no manejado:', err);
  res.status(500).json({ error: 'Error interno del servidor' });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  logger.info(`Lucía escuchando en el puerto ${PORT}`);
});
