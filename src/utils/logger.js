/**
 * logger.js - logging mínimo y consistente. Reemplazar por Winston/Pino
 * en producción si se necesita más control (niveles, transporte a
 * servicios externos, etc.)
 */
function ts() {
  return new Date().toISOString();
}

module.exports = {
  info: (...args) => console.log(`[${ts()}] [INFO]`, ...args),
  warn: (...args) => console.warn(`[${ts()}] [WARN]`, ...args),
  error: (...args) => console.error(`[${ts()}] [ERROR]`, ...args),
};
