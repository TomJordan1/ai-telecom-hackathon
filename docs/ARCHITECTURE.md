# Arquitectura

```
                     ┌─────────────────────┐
                     │   DeepSeek API       │
                     │  (modelo deepseek-chat) │
                     └─────────▲────────────┘
                               │ JSON mode
                     ┌─────────┴────────────┐
                     │   core/chatEngine.js  │
                     │  (persona + historial │
                     │   + contrato JSON)    │
                     └───▲────────▲────────▲─┘
                         │        │        │
        ┌────────────────┘        │        └────────────────┐
        │                         │                          │
┌───────┴───────┐        ┌────────┴────────┐        ┌────────┴────────┐
│ mobileApp      │        │ whatsapp         │        │ website          │
│ routes.js      │        │ webhook.js       │        │ widget.js        │
│ (API interna)  │        │ + adapter.js     │        │ + corsConfig.js  │
└───────┬───────┘        └────────┬────────┘        └────────┬────────┘
        │                         │                          │
   App móvil Movistar      WhatsApp Business API      Website Movistar
```

## Flujo de un mensaje

1. El canal recibe el mensaje crudo (JSON de la app, webhook de Meta, o
   POST del widget web) y extrae: `sessionId`, texto del usuario, y
   opcionalmente contexto del cliente ya autenticado.
2. Llama a `chatEngine.handleIncomingMessage(...)`.
3. `chatEngine` arma el prompt final: personalidad base (`persona.js`) +
   matiz de tono del canal + contrato de formato (`responseSchema.js`) +
   historial reciente de la conversación.
4. Llama a `deepseekClient.callDeepSeek(...)` con `response_format:
   json_object` para forzar salida JSON válida.
5. Valida la respuesta contra el esquema (`validateLuciaResponse`). Si algo
   viene mal formado, se usa un fallback seguro en vez de romper el canal.
6. El canal traduce la respuesta estructurada a su formato nativo:
   - WhatsApp: texto simple o botones interactivos (máx. 3).
   - App móvil: se devuelve el JSON tal cual, la app decide cómo pintarlo.
   - Website: igual que la app, consumido por el widget JS de ejemplo.

## Por qué salida estructurada y no texto plano

- Permite **UI real** (botones, tarjetas) en vez de que el usuario tenga
  que escribir todo.
- Permite **enrutar internamente** por `intent` (ej: mandar "billing" a un
  flujo distinto de "technical_support" más adelante).
- Permite **detectar automáticamente cuándo escalar a un humano**
  (`escalate_to_human`) sin depender de parsear frases como "quiero hablar
  con una persona" en el backend — esa decisión ya la toma el modelo con
  el contexto completo de la conversación.
- Es la base necesaria para las **plantillas con imágenes** que se van a
  agregar después: solo se suma un nuevo tipo de bloque, no hay que
  rediseñar el pipeline.

## Extender con plantillas de imágenes (futuro)

1. En `responseSchema.js`, documentar el nuevo tipo de bloque, ej:
   ```json
   { "type": "image", "image_url": "...", "caption": "..." }
   ```
2. En `responseTemplates.js`, agregar `createImageBlock(...)`.
3. En cada adapter de canal, agregar el caso para renderizar ese bloque
   (WhatsApp ya soporta mensajes de tipo `image` de forma nativa en su
   API; la app y la website solo necesitan pintar un `<img>`/componente).
4. Decidir si las imágenes las genera el modelo (dándole URLs válidas en
   el contexto) o si son plantillas fijas seleccionadas por `intent`
   (recomendado al inicio: más predecible y controlado).
