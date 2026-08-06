# Especificación Arquitectónica Integral: Copiloto de Transparencia de Facturación (Backend API)

## 1. Visión General y Objetivo del Sistema

El sistema es un orquestador cognitivo expuesto mediante una API REST (`POST /chat`) que procesa consultas de facturación B2C de telecomunicaciones. Su objetivo es transformar la interacción reactiva en un vínculo proactivo y empático (mediante la identidad conversacional "Lucía"), utilizando una arquitectura basada en un motor determinista, una capa de recuperación de conocimiento (RAG) y una capa de generación de lenguaje natural, con el objetivo de minimizar el riesgo de alucinaciones financieras mediante una estricta separación de responsabilidades. Todos los cálculos, montos y fechas son generados exclusivamente por el motor determinista y tratados como información de solo lectura para el LLM.

La solución resuelve el clúster de variación de recibos (~40% del total mensual), reduciendo llamadas al call center (~15%) y aumentando el NPS transaccional (~10%) mediante explicaciones con evidencia, enganche conductual (estilo Duolingo), optimización comercial ética y gestión de memoria contextual asíncrona.

## Arquitectura lógica

La solución se encuentra dividida en cinco capas desacopladas:

1. Capa de entrada y exposición (FastAPI).
2. Capa de orquestación y gestión del estado conversacional.
3. Capa de procesamiento determinista y reglas de negocio.
4. Capa de recuperación de conocimiento (RAG).
5. Capa de generación de lenguaje natural y personalización.

Esta separación permite sustituir componentes específicos (LangChain, LangGraph, LlamaIndex, Semantic Kernel u otros) sin modificar la lógica de negocio central.

---

## 2. Stack Tecnológico (Enfoque MVP / Hackatón)

| Componente | Tecnología | Propósito |
| --- | --- | --- |
| **API Gateway / Core** | FastAPI | Exposición de la API. |
| **Capa de orquestación** | LangChain (MVP) | Gestión del flujo conversacional. |
| **Motor determinista** | Python, SQL y Pandas | Procesamiento matemático y reglas de negocio. |
| **Capa RAG** | ChromaDB + Sentence Transformers | Recuperación contextual y vectorización local. |
| **Capa LLM** | DeepSeek Flash | Generación lingüística. |
| **Validación** | Pydantic | Sanitización estricta. |

---

## 3. Esquema de Datos y Persistencia (SQLite)

### 3.1. Tabla `recibos_cliente` (Mock BrainyBill)

Almacena la verdad absoluta de facturación de la factura actual y los 5 recibos previos. Ningún LLM escribe aquí.

### 3.2. Tabla `historial_interacciones` (Memoria Contextual y Emocional)

Almacena metadatos conversacionales y comentarios emocionales del usuario para referencias cruzadas.

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `session_id` | UUID | Identificador de sesión. |
| `user_id` | String | Identificador del usuario. |
| `comentarios_emocionales` | JSON (Array) | Lista de objetos `{id, text, timestamp, importance (1-5), reference_count (integer), expires_at (timestamp), referenced (boolean)}`. |
| `score_sentimiento` | Integer (1-5) | Nivel de frustración detectado. |
| `perfil_lexico_usuario` | Enum | `FORMAL`, `CASUAL`, `USO_JERGAS`. |
| `estado_resolucion` | Boolean | ¿Se cerró con satisfacción? |

Con el fin de evitar el crecimiento indefinido del contexto, el sistema aplicará mecanismos de expiración, consolidación y resumen de memoria.

### 3.3. Tabla `catalogo_planes` (Mock Comercial)

Almacena ofertas actuales para comparación determinista de cross-selling restrictivo.

### 3.4. Tabla `terminos_restringidos` (Compliance)

Reglas basadas en expresiones regulares (`patron_regex`, `accion_disparador`) para bloquear riesgos legales (`LEGAL_RIESGO`, `INSULTO`, `DATOS_SENSIBLES`) sin intervención de IA.

---

## 4. Flujo de Ejecución del Endpoint (`POST /chat`) con Memoria Contextual

El ciclo de vida de la petición integra de forma transversal la memoria y el procesamiento determinista a través de los siguientes pasos:

### Paso 1: Recepción, Carga de Estado y Memoria

* El cliente realiza un `POST /chat` enviando `session_id`, `user_id` y `message`.
* Se consulta SQLite para recuperar el `deterministic_payload` y los comentarios emocionales con `referenciado: false` de la tabla `historial_interacciones`.
* Se inyectan los comentarios pendientes en el estado del orquestador bajo la clave `pending_emotional_comments`.

### Paso 2: Análisis de Intención, Perfilado y Cumplimiento (Router)

* **Pre-filtro de Cumplimiento:** Antes de cualquier procesamiento semántico, se evalúan expresiones regulares contra la tabla `terminos_restringidos`; si se activa un patrón de `LEGAL_RIESGO`, `INSULTO` o `DATOS_SENSIBLES`, se bloquea el paso a RAG/LLM y se responde con un mensaje estándar de seguridad, registrando el incidente en logs.
* El router clasifica la intención (facturación, reclamo, saludo).
* El módulo de perfilado extrae nuevas frases emocionales del mensaje actual y actualiza el array de pendientes si corresponde.

### Paso 3: Motor Investigador (Capa Determinista)

* Extrae los últimos recibos, calcula variaciones ($\Delta M$), detecta prorrateos, cambios de plan, reconexiones, cuotas de equipos o fin de descuentos.
* Genera el `Deterministic Fact Payload` y evalúa `upcoming_alerts` si faltan $\le 15$ días para el fin de promociones.

### 4.1 Deterministic Fact Payload

```json
{
  "current_bill": {
    "amount": 119.90,
    "issue_date": "2026-08-01"
  },
  "previous_bills": [
    {
      "month": "2026-07",
      "amount": 99.90
    }
  ],
  "variation_amount": 20.00,
  "variation_percentage": 20.02,
  "detected_event": "FIN_PROMOCION",
  "evidence": [
    "PROMOCION_FINALIZADA"
  ],
  "upcoming_alerts": [
    {
      "type": "FIN_PROMOCION",
      "days_remaining": 5
    }
  ]
}
```

Este objeto constituye la fuente de verdad del sistema. El LLM únicamente puede interpretarlo, resumirlo y transformarlo en lenguaje natural. No puede alterarlo, complementarlo ni generar información numérica nueva.

### Paso 4: Búsqueda de Conocimiento (RAG)

El sistema emplea una estrategia híbrida de recuperación basada en búsqueda por similitud, selección `top-k`, filtrado por metadatos y reordenamiento semántico. Durante la fase de demostración se utilizará un umbral de similitud elevado (> 0,85), susceptible de ser ajustado posteriormente mediante experimentación y análisis de rendimiento.

### Paso 5: Evaluación de Fricción, Derivación y Gatillo Comercial

* Evalúa el sentimiento y el contador de intentos. Si la frustración supera umbrales, activa el flag de derivación humana (`requires_human_intervention = true`).
* **Gatillo de Cross-selling Restrictivo (Lógica determinista explícita):** Se evalúa la condición exacta para habilitar ofertas comerciales. El flag `cross_sell_eligible` se activa **solo si** se cumplen **todas** las siguientes condiciones:
  1. `sentiment_score >= 4` (cliente con percepción positiva o neutra alta).
  2. `estado_resolucion == True` (la consulta original fue clasificada como resuelta).
  3. `intent_category` pertenece a la lista blanca comercial (ej. `EXPLICACION_EXITOSA`, `FIN_PROMOCION`, `CAMBIO_PLAN`). Queda explícitamente excluido para `RECLAMO`, `DEUDA_PENDIENTE` o `FALLO_SISTEMA`.
  4. `no_preguntas_pendientes` (el cliente no ha realizado una pregunta de seguimiento en el turno actual que indique insatisfacción o duda adicional).

### Paso 6: Generación Segura con LLM (DeepSeek Flash) e Inyección de Memoria

El sistema construye el prompt combinando:

1. El **Payload Determinista** (matemáticas exactas).
2. La definición **RAG**.
3. El **Bloque de Comentarios Pendientes**: Se inyectan los comentarios no atendidos (ej. *"ntp si es dificil"*) exigiendo al LLM que los referencie de manera cálida y natural al inicio de la respuesta ("Por cierto, no te preocupes...").
4. La bandera `cross_sell_eligible`: si está activa, se inyecta un bloque que instruye al LLM a generar el mensaje comercial ubicado en `plan_optimizer_suggestion`; si está desactivada, se omite completamente cualquier sugerencia de venta, garantizando el cumplimiento de la restricción.

### Paso 7: Validación, Empaquetado y Actualización de Memoria

* Pydantic valida que todos los montos, fechas y porcentajes procedan exclusivamente del motor determinista, reduciendo al mínimo el riesgo de inconsistencias y alucinaciones numéricas.
* Se actualiza SQLite: los comentarios emocionales integrados pasan a `referenciado: true` para evitar repeticiones en turnos futuros.
* Se actualiza el snapshot de la sesión.

### Paso 8: Hand-off Inteligente (Si aplica)

* Si la fricción es alta, empaqueta el historial completo y el array de comentarios emocionales para transferir contexto al CRM Amdocs.

---

## 5. Estructura del Payload JSON de Respuesta (Omnicanal)

El endpoint retorna una estructura semántica unificada para que cualquier canal (App, Web, WhatsApp) renderice la experiencia:

```json
{
  "session_id": "abc-123-def",
  "intent_category": "ALERTA_PROACTIVA_FIN_PROMOCION",
  "requires_human_intervention": false,
  "sentiment_score": 1,
  "messages": [
    {
      "text": "¡Lucía detectó algo importante! ⏰ Tu descuento vence en 5 días. ¿Quieres ver cómo evitarlo?",
      "delay_ms": 0,
      "type": "hook"
    },
    {
      "text": "Por cierto, no te preocupes por el enredo de los recibos, estoy aquí para aclararlo 😊. Tu recibo subió S/ 20.00 porque terminó tu promoción.",
      "delay_ms": 2000,
      "type": "explanation"
    },
    {
      "text": "🔍 *Evidencia:*\n✓ Promoción: Internet Hogar 300 Mbps\n✓ Impacto aplicado: +S/20.00",
      "delay_ms": 1500,
      "type": "evidence"
    }
  ],
  "historical_bills_summary": [
    { "month": "Jun", "amount": 99.90, "change_reason": "Fin de promoción" }
  ],
  "upcoming_alerts": [
    {
      "concepto": "Descuento Internet Hogar",
      "fecha_fin": "2026-08-20",
      "impacto_estimado": "+S/ 25.00",
      "tipo": "FIN_PROMOCION",
      "dias_restantes": 5
    }
  ],
  "plan_optimizer_suggestion": {
    "available": true,
    "mensaje_comercial": "Dato curioso: por los mismos S/ 99.90, Lucía encontró un plan con el doble de velocidad (600 Mbps). ¿Te ayudo a activarlo?",
    "plan_recomendado": {
      "nombre": "Internet 600 Mbps",
      "precio": 99.90,
      "beneficios": "Doble velocidad, Movistar Play incluido"
    }
  },
  "personality_metadata": {
    "hook_used": "URGENCIA_AMIGABLE",
    "lucia_tone": "MAMÁ"
  },
  "handoff_context": null,
  "confidence_score": 99,
  "compliance_triggered": false,
  "timestamp": "2026-08-06T14:30:00Z"
}
```

## Observabilidad y auditoría

El sistema almacenará registros estructurados de:

- Identificador de sesión.
- Intención detectada.
- Evidencia utilizada.
- Reglas de cumplimiento activadas.
- Componentes invocados.
- Latencia total.
- Resultado de las validaciones.
- Decisiones de derivación y venta cruzada.

Esto permitirá reconstruir el flujo completo de decisión y facilitará la auditoría del sistema.

---

## 6. Pautas de Implementación para Agentes (Claude / Antigravity / Kiro)

1. **Separación Estricta de Capas:** No permitir que DeepSeek Flash realice cálculos numéricos. Todo incremento de dinero, montos y fechas debe calcularse previamente en el motor Python/SQLite e inyectarse como variable de solo lectura.
2. **Persistencia de Memoria y Cumplimiento:** Implementar el manejo del array `comentarios_emocionales` en la tabla de interacciones para asegurar que las referencias explícitas funcionen a lo largo del tiempo de vida de la sesión conversacional. Además, se debe priorizar el **pre-filtro de cumplimiento** (vía Regex) antes de cualquier procesamiento de IA, y utilizar el modelo local `all-MiniLM-L6-v2` para embeddings con un umbral de similitud > 0.85 en el RAG, eliminando dependencias de APIs externas para la fase de demo.
3. **Escenarios Críticos y Gatillo Comercial:** Asegurar que el motor determinista reconozca y etiquete inequívocamente los 5 escenarios obligatorios del reto: (a) Prorrateos, (b) Cuota de equipo financiado, (c) Reconexión por suspensión morosa, (d) Fin de descuentos, (e) Cambios de plan. Asimismo, implementar la condición booleana explícita para el cross-selling (`sentiment_score >= 4`, `estado_resolucion == True`, `intent_category` en lista blanca y `no_preguntas_pendientes`), asegurando que el `plan_optimizer_suggestion` solo se rellene cuando dicha condición sea verdadera.

