function isNonEmptyString(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function assertValidChatRequest(body) {
  if (!body || typeof body !== 'object') {
    throw new Error('Body inválido');
  }
  if (!isNonEmptyString(body.sessionId)) {
    throw new Error('sessionId es requerido');
  }
  if (!isNonEmptyString(body.message)) {
    throw new Error('message es requerido');
  }
  return true;
}

module.exports = { isNonEmptyString, assertValidChatRequest };
