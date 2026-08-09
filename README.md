# Lucía — Asistente virtual de Movistar

Base de un chatbot multicanal (**App móvil**, **WhatsApp**, **Website**) impulsado
por la API de **DeepSeek** (modelo `deepseek-chat`). Este repositorio es el
**esqueleto/base** sobre el cual construir: lógica de negocio real, integración
con sistemas internos de Movistar (facturación, planes, CRM), autenticación
real de usuarios, y más adelante, **plantillas de respuesta con imágenes**.

## ¿Por qué está armado así?

1. **Salida siempre estructurada, nunca texto plano.** Cada respuesta de Lucía
   es un JSON (`reply_text`, `blocks`, `intent`, `escalate_to_human`,
   `confidence`). Esto permite que cada canal la renderice como corresponda
   (botones en WhatsApp, tarjetas en la app, etc.) sin parsear texto libre.
   Ver `src/core/responseSchema.js`.

2. **Personalidad centralizada.** Todo el tono, las reglas de honestidad
   sobre ser IA, y el estilo "humano, conciso, que informa en vez de
   confundir" vive en un solo lugar: `src/config/persona.js`. Cambiar cómo
   habla Lucía en TODOS los canales es editar un solo archivo.

3. **Un motor, tres canales.** `src/core/chatEngine.js` no sabe nada de
   WhatsApp ni de HTTP. Cada canal (`src/channels/*`) es un adaptador
   delgado que traduce el formato de entrada/salida de su plataforma hacia
   el contrato común.

4. **Extensible a futuro sin romper nada.** El campo `blocks` ya soporta
   `quick_replies` y `card`. Para las plantillas con imágenes que mencionas
   que vendrán después, solo hay que:
   - Agregar un nuevo tipo de bloque en `responseSchema.js` (ej. `"image"`).
   - Agregar su fábrica en `src/templates/responseTemplates.js`.
   - Agregar su renderizado en el adapter de cada canal que lo necesite.

## Estructura

```
lucia-chatbot/
├── src/
│   ├── config/
│   │   └── persona.js          # Personalidad de Lucía (el corazón del bot)
│   ├── core/
│   │   ├── deepseekClient.js   # Llamadas a la API de DeepSeek
│   │   ├── chatEngine.js       # Orquesta persona + historial + DeepSeek
│   │   └── responseSchema.js   # Contrato de salida JSON
│   ├── templates/
│   │   └── responseTemplates.js # Bloques reutilizables (quick_replies, card...)
│   ├── channels/
│   │   ├── mobileApp/routes.js
│   │   ├── whatsapp/{webhook.js, adapter.js}
│   │   └── website/{widget.js, corsConfig.js}
│   ├── utils/{logger.js, validators.js}
│   └── server.js               # Punto de entrada Express
├── docs/
│   ├── embed-example.html      # Ejemplo de widget para la Website
│   ├── ARCHITECTURE.md
│   └── PERSONA_GUIDE.md
├── .env.example
└── package.json
```

## Cómo correrlo localmente

```bash
npm install
cp .env.example .env
# edita .env y agrega tu DEEPSEEK_API_KEY
npm run dev
```

Prueba rápida (canal website, sin auth):

```bash
curl -X POST http://localhost:3000/api/website/chat \
  -H "Content-Type: application/json" \
  -d '{"sessionId": "demo-1", "message": "Hola, ¿cuánto cuesta el plan más básico?"}'
```

## Variables de entorno clave

| Variable | Descripción |
|---|---|
| `DEEPSEEK_API_KEY` | API key de DeepSeek |
| `DEEPSEEK_MODEL` | Modelo a usar (`deepseek-chat` por defecto) |
| `WHATSAPP_VERIFY_TOKEN` / `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` | Credenciales de Meta Cloud API |
| `WEBSITE_ALLOWED_ORIGINS` | Dominios permitidos para el widget web |
| `MOBILE_API_KEY` | Key interna que usa la app móvil para autenticar contra este backend |

## Lo que falta a propósito (para que ustedes lo construyan encima)

- Persistencia real de conversaciones (hoy es un `Map` en memoria — ver
  `chatEngine.js`, se pierde al reiniciar el server).
- Integración con los sistemas reales de Movistar (planes, facturación,
  estado de línea) para que `extraContext` no sea un string a mano sino
  datos reales de la cuenta del cliente.
- Autenticación real en el canal móvil (hoy es una API key compartida).
- Handoff real a un agente humano cuando `escalate_to_human: true` (hoy
  solo se refleja en la respuesta, falta conectarlo a un sistema de tickets
  o cola de atención en vivo).
- Plantillas de respuesta con imágenes (mencionado como "más adelante" —
  la base ya está preparada en `responseSchema.js` y `responseTemplates.js`).

## Nota sobre la personalidad de Lucía

Ver `docs/PERSONA_GUIDE.md` para el detalle de las decisiones de tono:
por qué es honesta sobre ser una IA, por qué evita el lenguaje de "call
center", y cómo maneja usuarios que desconfían de los bots.
