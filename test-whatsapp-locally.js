#!/usr/bin/env node

/**
 * test-whatsapp-locally.js
 * =======================
 * Simula un mensaje de WhatsApp sin necesidad de que el servidor esté en producción
 * o sin tener que enviarlo desde WhatsApp real.
 * 
 * Uso:
 *   npm install axios dotenv  # si no los tienes
 *   node test-whatsapp-locally.js
 */

const axios = require('axios');
require('dotenv').config();

const WEBHOOK_URL = process.env.WEBHOOK_URL || 'http://localhost:3000/webhooks/whatsapp';
const TEST_PHONE = '+5491112345678'; // Número de prueba
const TEST_MESSAGE = 'Hola Lucía, ¿qué planes tienes?'; // Mensaje de prueba

console.log('\n=== TEST DE WEBHOOK DE WHATSAPP ===\n');
console.log(`📱 Enviando mensaje simulado a: ${WEBHOOK_URL}`);
console.log(`💬 Mensaje: "${TEST_MESSAGE}"`);
console.log(`📞 Número: ${TEST_PHONE}`);
console.log('\n');

/**
 * Estructura que Meta envía al webhook
 * (Simplificada para testing)
 */
const whatsappPayload = {
  entry: [
    {
      id: 'entry_id_123',
      changes: [
        {
          value: {
            messages: [
              {
                from: TEST_PHONE,
                id: 'msg_id_' + Date.now(),
                timestamp: Math.floor(Date.now() / 1000).toString(),
                type: 'text',
                text: {
                  body: TEST_MESSAGE,
                },
              },
            ],
            contacts: [
              {
                profile: {
                  name: 'Usuario Test',
                },
                wa_id: TEST_PHONE,
              },
            ],
            metadata: {
              display_phone_number: '34911111111',
              phone_number_id: process.env.WHATSAPP_PHONE_NUMBER_ID || '1234567890123456',
            },
          },
          field: 'messages',
        },
      ],
    },
  ],
  object: 'whatsapp_business_account',
};

async function sendTestMessage() {
  try {
    console.log('📨 Enviando petición POST al webhook...\n');

    const response = await axios.post(WEBHOOK_URL, whatsappPayload, {
      headers: {
        'Content-Type': 'application/json',
      },
      timeout: 10000,
    });

    console.log('✅ Webhook respondió con status:', response.status);
    console.log('\n📊 Respuesta del servidor:');
    console.log(JSON.stringify(response.data, null, 2));

    console.log('\n🎉 ¡Petición exitosa!');
    console.log('   - El webhook recibió el mensaje');
    console.log('   - Lucía debería estar procesándolo');
    console.log('   - En 2-3 segundos verifica los logs del servidor para ver la respuesta');

  } catch (err) {
    console.error('❌ Error al enviar el webhook:\n');

    if (err.code === 'ECONNREFUSED') {
      console.error('❌ No se pudo conectar al servidor.');
      console.error('   → ¿El servidor está corriendo? (npm start)');
      console.error(`   → ¿Puerto correcto? (intenta: http://localhost:3000/health)`);
    } else if (err.response) {
      console.error(`❌ Servidor respondió con error ${err.response.status}:`);
      console.error(JSON.stringify(err.response.data, null, 2));
    } else {
      console.error('❌ Error:', err.message);
    }

    process.exit(1);
  }
}

// Ejecuta el test
sendTestMessage();
