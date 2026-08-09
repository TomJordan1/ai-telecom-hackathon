/**
 * Diagnóstico rápido: verifica que WhatsApp esté bien configurado
 * Uso: node whatsapp-diagnostic.js
 */

require('dotenv').config();

console.log('\n=== DIAGNÓSTICO DE WHATSAPP ===\n');

const checks = {
  'WHATSAPP_PHONE_NUMBER_ID': process.env.WHATSAPP_PHONE_NUMBER_ID,
  'WHATSAPP_ACCESS_TOKEN': process.env.WHATSAPP_ACCESS_TOKEN,
  'WHATSAPP_VERIFY_TOKEN': process.env.WHATSAPP_VERIFY_TOKEN,
  'DEEPSEEK_API_KEY': process.env.DEEPSEEK_API_KEY,
};

let allGood = true;

Object.entries(checks).forEach(([key, value]) => {
  if (!value) {
    console.log(`❌ ${key}: NO CONFIGURADO`);
    allGood = false;
  } else if (value.length < 5) {
    console.log(`⚠️  ${key}: parece demasiado corto (${value.length} caracteres)`);
    allGood = false;
  } else {
    const masked = value.substring(0, 5) + '...' + value.substring(value.length - 5);
    console.log(`✅ ${key}: OK (${masked})`);
  }
});

console.log('\n=== RESUMEN ===\n');
if (allGood) {
  console.log('✅ Toda la configuración parece estar bien.');
  console.log('   Si aún no recibes respuestas:');
  console.log('   1. Verifica que el webhook esté verificado en Meta/Facebook Manager');
  console.log('   2. Mira los logs de tu servidor para errores de API');
  console.log('   3. Prueba enviando un mensaje a WhatsApp');
} else {
  console.log('❌ Hay problemas de configuración.');
  console.log('\nPasos para arreglarlo:');
  console.log('1. Ve a tu archivo .env y verifica estos valores');
  console.log('2. Si no los tienes, obtén los valores en Meta/Facebook Manager:');
  console.log('   - Phone Number ID: App > WhatsApp > Configuration');
  console.log('   - Access Token: App > Settings > User tokens (o Business tokens)');
  console.log('   - Verify Token: puedes usar cualquier string aleatorio que también setees en Meta');
}

console.log('\n');
