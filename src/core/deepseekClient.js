/**
 * deepseekClient.js
 * ------------------------------------------------------------------
 * Cliente delgado para llamar a la API de DeepSeek (modelo estándar
 * "deepseek-chat"). La API es compatible con el formato de OpenAI,
 * así que usamos axios directo contra /chat/completions.
 * ------------------------------------------------------------------
 */

const axios = require('axios');

const DEEPSEEK_BASE_URL = process.env.DEEPSEEK_BASE_URL || 'https://api.deepseek.com';
const DEEPSEEK_MODEL = process.env.DEEPSEEK_MODEL || 'deepseek-chat';

const client = axios.create({
  baseURL: DEEPSEEK_BASE_URL,
  timeout: 20000,
  headers: {
    'Content-Type': 'application/json',
  },
});

/**
 * Llama a DeepSeek pidiendo salida en JSON estricto (JSON mode).
 *
 * @param {Array<{role: 'system'|'user'|'assistant', content: string}>} messages
 * @param {object} options
 * @returns {Promise<object>} objeto JSON ya parseado (sin validar contra el schema de Lucía)
 */
async function callDeepSeek(messages, options = {}) {
  const apiKey = process.env.DEEPSEEK_API_KEY;
  if (!apiKey) {
    throw new Error(
      'Falta DEEPSEEK_API_KEY en las variables de entorno. Revisa tu archivo .env'
    );
  }

  const payload = {
    model: DEEPSEEK_MODEL,
    messages,
    temperature: options.temperature ?? 0.6,
    max_tokens: options.maxTokens ?? 500,
    // JSON mode: fuerza a DeepSeek a devolver un JSON válido.
    // Importante: cuando se usa response_format json_object, el prompt
    // (system o user) DEBE contener la palabra "JSON" explícitamente,
    // cosa que ya hacemos en responseSchema.js.
    response_format: { type: 'json_object' },
  };

  try {
    const { data } = await client.post(
      '/chat/completions',
      payload,
      { headers: { Authorization: `Bearer ${apiKey}` } }
    );

    const content = data?.choices?.[0]?.message?.content;
    if (!content) {
      throw new Error('Respuesta vacía de DeepSeek');
    }

    return JSON.parse(content);
  } catch (err) {
    if (err.response) {
      // Error devuelto por la API de DeepSeek (401, 429, 500, etc.)
      throw new Error(
        `DeepSeek API error ${err.response.status}: ${JSON.stringify(err.response.data)}`
      );
    }
    if (err instanceof SyntaxError) {
      throw new Error('DeepSeek devolvió un JSON inválido: ' + err.message);
    }
    throw err;
  }
}

module.exports = { callDeepSeek, DEEPSEEK_MODEL };
