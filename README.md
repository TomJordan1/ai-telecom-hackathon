# Lucía — Copiloto de Transparencia de Facturación

> Backend API (FastAPI) para un asistente conversacional de facturación B2C de telecomunicaciones, desarrollado para un hackathon de telecom en Perú. Explica variaciones de recibo con evidencia verificable, minimizando el riesgo de alucinaciones numéricas mediante una separación estricta entre un motor determinista y un LLM.

## Tabla de contenidos

- [Descripción general](#descripción-general)
- [Características principales](#características-principales)
- [Arquitectura](#arquitectura)
- [Stack tecnológico](#stack-tecnológico)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Requisitos previos](#requisitos-previos)
- [Instalación](#instalación)
- [Configuración (variables de entorno)](#configuración-variables-de-entorno)
- [Configuración del RAG (Supabase + pgvector)](#configuración-del-rag-supabase--pgvector)
- [Datos de prueba (mock)](#datos-de-prueba-mock)
- [Ejecución](#ejecución)
- [Uso de la API](#uso-de-la-api)
- [Canales soportados](#canales-soportados)
- [Panel de administración](#panel-de-administración)
- [Escenarios de demo](#escenarios-de-demo)
- [Limitaciones conocidas](#limitaciones-conocidas)
- [Licencia](#licencia)

## Descripción general

**Lucía** es un orquestador cognitivo expuesto mediante una API REST (`POST /api/v1/chat`) que procesa consultas de facturación de clientes de telecomunicaciones. Su objetivo es transformar la interacción reactiva típica de un call center en un vínculo proactivo y empático, resolviendo el clúster de variación de recibos mediante explicaciones respaldadas por evidencia, en lugar de respuestas genéricas.

El principio de diseño central es la **separación estricta de responsabilidades**: todos los cálculos, montos, fechas y reglas de negocio los genera exclusivamente un motor determinista en Python/SQL. El LLM (DeepSeek) nunca calcula ni inventa cifras — solo interpreta, resume y traduce a lenguaje natural la información que el motor determinista ya verificó.

El sistema fue diseñado para cubrir 5 escenarios críticos de facturación:

1. **Prorrateo** por cambio de plan a mitad de ciclo.
2. **Cuota de equipo** financiado (routers, repetidores).
3. **Reconexión** por suspensión morosa.
4. **Fin de descuentos/promociones**.
5. **Cambios de plan**.

Además, gestiona memoria conversacional y emocional por sesión, aplica un pre-filtro de cumplimiento (compliance) antes de cualquier procesamiento de IA, deriva a un agente humano cuando la incertidumbre es alta, y ofrece upsell de planes solo bajo una condición comercial estricta y verificable.

Ver [`plan.md`](./plan.md) para la especificación arquitectónica original y detallada del diseño.

## Características principales

- **Motor determinista de facturación**: calcula variaciones de monto (`Δ`) entre recibos, detecta el evento causal (fin de promo, prorrateo, cuota de equipo, reconexión, etc.) y genera evidencia trazable.
- **Anti-alucinación por diseño**: el LLM recibe los montos y fechas como datos de solo lectura; una capa de validación posterior corrige cualquier desviación antes de responder al usuario.
- **Memoria contextual y emocional**: recuerda comentarios emocionales del usuario (con expiración y consolidación automática) y una bitácora acotada de la conversación, para no repetir explicaciones ya dadas.
- **Pre-filtro de cumplimiento (compliance)**: bloquea mediante expresiones regulares mensajes con riesgo legal, insultos o datos sensibles, antes de que lleguen a cualquier componente de IA.
- **Índice de incertidumbre determinista**: si el sistema no tiene suficiente certeza sobre un caso, deriva automáticamente a un agente humano con todo el contexto empaquetado.
- **Cross-selling ético y restrictivo**: solo se sugiere un plan superior si se cumplen 4 condiciones estrictas simultáneas (sentimiento alto, caso resuelto, tipo de evento en lista blanca, sin preguntas pendientes), y el plan recomendado siempre proviene de una verificación real contra el catálogo.
- **Aprendizaje supervisado de casos**: los casos nuevos pasan por una cuarentena con feedback del usuario antes de convertirse en conocimiento reutilizable validado.
- **Alertas proactivas**: Lucía puede escribir primero al usuario cuando detecta que una promoción está por vencer.
- **Omnicanalidad**: la misma respuesta estructurada se renderiza en Web, WhatsApp y Telegram.
- **Panel de administración**: cola de atención humana, casos en cuarentena, base de conocimiento validada y disparo de alertas proactivas.
- **Auditoría estructurada**: cada decisión del orquestador (intención, evidencia, reglas de compliance, componentes invocados, latencia) queda registrada para poder reconstruir el flujo completo.

## Arquitectura

La solución está dividida en cinco capas desacopladas:

```
┌─────────────────────────────────────────────────────────────┐
│  1. Entrada y exposición (FastAPI)                          │
│     Web / WhatsApp Cloud API / Telegram                     │
├─────────────────────────────────────────────────────────────┤
│  2. Orquestación (services/orchestrator.py)                 │
│     Memoria de sesión · Enrutamiento de intención           │
├─────────────────────────────────────────────────────────────┤
│  3. Motor determinista (services/deterministic.py)          │
│     Compliance · Cálculo de facturación · Gatillo comercial │
├─────────────────────────────────────────────────────────────┤
│  4. Conocimiento (services/case_matcher.py, rag.py)          │
│     Base de casos validados · Cuarentena · RAG               │
├─────────────────────────────────────────────────────────────┤
│  5. Generación de lenguaje (services/llm.py, persona.py)     │
│     DeepSeek (LangChain) · Personalidad y registro           │
└─────────────────────────────────────────────────────────────┘
```

Esta separación permite sustituir componentes específicos (por ejemplo, el proveedor del LLM o el motor de RAG) sin modificar la lógica de negocio central.

### Flujo resumido de una petición `POST /chat`

1. Se carga el estado de la sesión (memoria emocional y conversacional).
2. Se evalúa el pre-filtro de compliance (regex). Si dispara, se bloquea antes de llegar a IA.
3. Se enruta la intención (facturación, solicitud de agente, o conversacional).
4. Si es facturación: el motor determinista calcula el payload de hechos, se busca un caso validado conocido, se calcula el índice de incertidumbre (deriva a humano si es alto), se consulta el contexto de conocimiento (RAG) si no hay caso conocido, y se evalúa si aplica una sugerencia comercial.
5. El LLM genera la respuesta final en lenguaje natural, usando exclusivamente los datos ya verificados.
6. Se valida/corrige la respuesta, se actualiza la memoria de la sesión y se registra la auditoría del turno.

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| API / Core | FastAPI + Uvicorn |
| Validación y configuración | Pydantic v2 + pydantic-settings |
| Base de datos operacional | SQLite vía SQLAlchemy 2.0 |
| Base vectorial (RAG) | Supabase (PostgreSQL + `pgvector`, índice HNSW) |
| Embeddings | `all-MiniLM-L6-v2` local vía `fastembed` (384 dims, default) u OpenAI `text-embedding-3-small` (1536 dims) |
| Orquestación LLM | LangChain (`langchain-core`, `langchain-openai`) |
| LLM | DeepSeek Chat (interfaz compatible con OpenAI) |
| Mensajería saliente | WhatsApp Cloud API, Telegram Bot API (HTTP directo) |
| Bot de Telegram | `python-telegram-bot` (polling) |
| Frontend | HTML/CSS/JS estático, servido por FastAPI |

## Estructura del proyecto

```
ai-telecom-hackathon/
├── app/
│   ├── main.py                    # Bootstrap de FastAPI, CORS, estáticos, migraciones
│   ├── core/
│   │   ├── config.py               # Configuración desde variables de entorno
│   │   └── schemas.py              # Modelos Pydantic de request/response
│   ├── db/
│   │   ├── models.py               # Modelos SQLAlchemy (8 tablas)
│   │   ├── database.py             # Engine, sesión y migraciones ligeras
│   │   └── crud.py                 # Acceso a datos y lógica de memoria
│   ├── api/
│   │   ├── routes.py               # POST /api/v1/chat
│   │   ├── whatsapp.py             # Webhook de WhatsApp
│   │   └── knowledge.py            # Feedback, cuarentena, admin, alertas
│   ├── services/
│   │   ├── orchestrator.py         # Orquestador principal del flujo
│   │   ├── deterministic.py        # Motor determinista de facturación
│   │   ├── intent_classifier.py    # Enrutamiento de intención
│   │   ├── llm.py                  # Generación con DeepSeek / mock
│   │   ├── persona.py              # Personalidad y registro lingüístico
│   │   ├── rag.py                  # Recuperación semántica en Supabase (pgvector)
│   │   ├── embeddings.py           # Proveedor único de embeddings (openai / local)
│   │   ├── case_matcher.py         # Coincidencia con base de casos
│   │   ├── feedback_handler.py     # Ciclo cuarentena → base de casos
│   │   ├── uncertainty_calculator.py # Índice de incertidumbre
│   │   ├── proactive_alerts.py     # Alertas proactivas salientes
│   │   ├── whatsapp_sender.py      # Envío saliente WhatsApp
│   │   └── telegram_sender.py      # Envío saliente Telegram
│   └── static/                     # Frontend web (chat + panel admin)
├── scripts/
│   ├── generate_mock_data.py       # Seed de datos de prueba
│   ├── setup_supabase.sql          # Esquema pgvector + función RPC de búsqueda
│   ├── ingest_supabase.py          # Ingesta de políticas al índice vectorial
│   └── telegram_bot.py             # Bot de Telegram (proceso aparte)
├── plan.md / plan.html             # Especificación arquitectónica original
├── requirements.txt                # Dependencias de Python
├── .env.example                    # Plantilla de variables de entorno
└── lucia_brain.db                  # Base de datos SQLite (generada)
```

## Requisitos previos

- **Python 3.11+** (recomendado; el proyecto usa sintaxis moderna de tipos como `str | None`).
- **pip** para gestión de dependencias.
- Una **API key de DeepSeek** (opcional). Sin ella, el sistema funciona en modo simulado (mock LLM).
- Credenciales de **WhatsApp Cloud API** y/o **Telegram Bot** (opcionales, solo si se quiere probar esos canales).

## Instalación

1. **Clonar el repositorio** y ubicarse en la carpeta del proyecto:

   ```powershell
   git clone <url-del-repositorio>
   cd ai-telecom-hackathon
   ```

2. **Crear y activar un entorno virtual** (recomendado):

   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

3. **Instalar las dependencias**:

   ```powershell
   pip install -r requirements.txt
   ```

4. **Crear el archivo de variables de entorno** a partir de la plantilla:

   ```powershell
   Copy-Item .env.example .env
   ```

   Luego edita `.env` con tus propias credenciales (ver siguiente sección).

5. **Generar los datos de prueba** (crea las tablas y puebla usuarios mock):

   ```powershell
   python scripts/generate_mock_data.py
   ```

## Configuración (variables de entorno)

El archivo `.env` (basado en `.env.example`) controla todo el comportamiento configurable:

| Variable | Descripción | Valor por defecto |
|---|---|---|
| `DEEPSEEK_API_KEY` | API key de DeepSeek para generación real de lenguaje. | — (vacío) |
| `USE_MOCK_LLM` | Si es `True`, se usa un generador simulado en vez de llamar a DeepSeek. Útil para desarrollar sin gastar cuota de API. | `True` |
| `DATABASE_URL` | Cadena de conexión SQLAlchemy. | `sqlite:///./lucia_brain.db` |
| `WHATSAPP_TOKEN` | Token temporal de la app de WhatsApp en Meta for Developers. | — |
| `WHATSAPP_PHONE_ID` | ID del número de teléfono configurado en Meta for Developers. | — |
| `WHATSAPP_VERIFY_TOKEN` | Token que tú defines para validar el webhook de WhatsApp. Debe coincidir con el configurado en Meta. | `lucia_hackathon_secret` |
| `WHATSAPP_API_VERSION` | Versión de la Graph API usada al enviar. Meta retira las antiguas; cópiala del panel de API Setup. | `v26.0` |
| `TELEGRAM_TOKEN` | Token del bot de Telegram, obtenido de `@BotFather`. | — |
| `SUPABASE_URL` | URL del proyecto de Supabase (Project Settings > API). | — |
| `SUPABASE_KEY` | Clave de API de Supabase. Se recomienda la `service_role` (solo backend). | — |
| `USE_MOCK_RAG` | Si es `True`, el RAG devuelve contexto simulado sin consultar Supabase. | `True` |
| `EMBEDDING_PROVIDER` | `local` (384 dims, sin API ni costo) u `openai` (1536 dims). | `local` |
| `EMBEDDING_MODEL` | Modelo de embeddings. Vacío = por defecto del proveedor. | — |
| `OPENAI_API_KEY` | Clave de OpenAI, **solo si** `EMBEDDING_PROVIDER=openai`. No se puede usar la de DeepSeek: su API no tiene endpoint de embeddings. | — |
| `RAG_MATCH_THRESHOLD` | Similitud mínima (0–1) para aceptar un chunk recuperado. | `0.5` |
| `RAG_MATCH_COUNT` | Cantidad de chunks a recuperar (top-k). | `3` |

> Sin `DEEPSEEK_API_KEY` configurada (o con `USE_MOCK_LLM=True`), el sistema sigue siendo completamente funcional: usa un generador de respuestas simulado que interpola los mismos datos deterministas verificados, sin llamar a ningún modelo externo. Lo mismo aplica al RAG con `USE_MOCK_RAG=True`.

## Configuración del RAG (Supabase + pgvector)

La capa de conocimiento cualitativo (políticas de facturación) vive en Supabase, usando la extensión nativa `pgvector` con un índice HNSW y búsqueda por similitud de coseno. Es opcional: con `USE_MOCK_RAG=True` el sistema funciona sin ella.

### 1. Crear el esquema

En tu proyecto de Supabase, abre **SQL Editor > New query**, pega el contenido de [`scripts/setup_supabase.sql`](./scripts/setup_supabase.sql) y ejecútalo. Eso crea:

- La tabla `documentos_politicas` (contenido, categoría, fuente, embedding, metadata).
- El índice HNSW sobre la columna `embedding` para búsqueda por coseno.
- La función RPC `match_documentos(query_embedding, match_threshold, match_count, filter_categoria)`.
- Row Level Security habilitada, de modo que la clave `anon` no pueda leer la tabla.

### 2. Configurar credenciales

En tu `.env`:

```env
SUPABASE_URL=https://tu-proyecto.supabase.co
SUPABASE_KEY=tu-service-role-key
USE_MOCK_RAG=False
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=all-MiniLM-L6-v2
```

No hace falta ninguna API key de embeddings: el proveedor `local` ejecuta `all-MiniLM-L6-v2` con `fastembed` (runtime ONNX, ~80 MB, ya incluido en `requirements.txt`). El modelo se descarga solo en la primera ejecución y queda cacheado.

> **Sobre DeepSeek**: `DEEPSEEK_API_KEY` sirve para la generación de texto, no para embeddings. La API de DeepSeek expone únicamente chat completions, así que no puede vectorizar documentos. Si quieres embeddings por API en lugar de locales, necesitas una key de OpenAI y `EMBEDDING_PROVIDER=openai`.

> **Importante sobre las dimensiones**: el script SQL declara `VECTOR(384)`, que corresponde a `all-MiniLM-L6-v2`. Si cambias a `EMBEDDING_PROVIDER=openai` (1536 dims), debes cambiar `384` por `1536` en los dos lugares donde aparece en el SQL, y recrear la tabla (`DROP TABLE documentos_politicas CASCADE;`) porque una columna `VECTOR` no se puede redimensionar. Si la dimensión de la tabla y la del modelo no coinciden, la inserción falla.

> **Importante sobre las claves**: la `service_role` key omite RLS y da acceso total al proyecto. Úsala solo en el backend, nunca en el frontend, y no la subas al repositorio (`.env` está en `.gitignore`).

### 3. Ingestar el corpus de políticas

```powershell
python scripts/ingest_supabase.py
```

Opciones disponibles:

| Comando | Efecto |
|---|---|
| `python scripts/ingest_supabase.py` | Vectoriza el corpus e inserta los chunks. |
| `python scripts/ingest_supabase.py --reset` | Borra primero los chunks de la misma fuente y reingesta (idempotente). |
| `python scripts/ingest_supabase.py --dry-run` | Genera y valida los embeddings sin escribir en Supabase. |

El corpus cubre las políticas de los 5 escenarios del reto (prorrateo, fin de promoción, cuotas de equipo, reconexión por morosidad y cambio de plan) más políticas generales de transparencia. Las categorías están alineadas con los `detected_event` del motor determinista, de modo que se puede filtrar la búsqueda por el evento ya detectado.

Para verificar la ingesta, ejecuta en el SQL Editor de Supabase:

```sql
SELECT categoria, COUNT(*) AS chunks
FROM documentos_politicas
GROUP BY categoria
ORDER BY categoria;
```

### Comportamiento ante fallos

`retrieve_context()` nunca lanza una excepción ni bloquea `POST /chat`. Si Supabase no responde, faltan credenciales, la librería no está instalada o ninguna coincidencia supera el umbral, el retriever degrada a un bloque de políticas generales de transparencia y la conversación continúa con normalidad. El motivo del fallback queda registrado en los logs con el prefijo `[RAG]`.

Además, el contexto recuperado se inyecta en el prompt marcado explícitamente como material de referencia conceptual, no como fuente de cifras: los montos y fechas siguen viniendo exclusivamente del motor determinista.

### Cambiar a embeddings de OpenAI (opcional)

El proveedor `local` es el default y no requiere API key. Si prefieres los embeddings de OpenAI:

1. En `scripts/setup_supabase.sql`, cambia `VECTOR(384)` por `VECTOR(1536)` en los **dos** sitios: la columna `embedding` y el parámetro `query_embedding` de la función.
2. Recrea la tabla, porque una columna `VECTOR` no se puede redimensionar: `DROP TABLE documentos_politicas CASCADE;` y vuelve a ejecutar el script completo.
3. En `.env`: `EMBEDDING_PROVIDER=openai`, `EMBEDDING_MODEL=text-embedding-3-small` y `OPENAI_API_KEY=sk-...`.
4. Reingesta: `python scripts/ingest_supabase.py --reset`.

El backend de embeddings se elige solo: si `fastembed` está instalado lo usa, y si no cae a `sentence-transformers`. Ambos ejecutan el mismo modelo `all-MiniLM-L6-v2`, así que los vectores son compatibles entre backends.

## Datos de prueba (mock)

El script `scripts/generate_mock_data.py` crea las tablas (si no existen) y siembra 5 usuarios, cada uno representando uno de los escenarios críticos del reto:

| `user_id` | Escenario |
|---|---|
| `user_a_fin_promo` | Fin de promoción/descuento |
| `user_b_prorrateo` | Prorrateo por cambio de plan a mitad de ciclo |
| `user_c_equipo` | Cuota de equipo financiado |
| `user_d_reconexion` | Reconexión por suspensión morosa |
| `user_e_alerta_proactiva` | Promoción activa a punto de vencer (alerta proactiva) |

También popula el catálogo de planes, las reglas de compliance y contactos mock (números ficticios) usados para las alertas proactivas.

El script es **idempotente respecto al catálogo**: si ya existen datos en `catalogo_planes`, no vuelve a insertar nada.

## Ejecución

Levantar el servidor de desarrollo con recarga automática:

```powershell
uvicorn app.main:app --reload
```

Por defecto queda disponible en `http://127.0.0.1:8000`. Al abrir la raíz (`/`) redirige automáticamente al cliente web (`/static/index.html`).

Endpoints útiles para verificar que todo funciona:

- `GET /health` → estado del servicio.
- `GET /` → interfaz de chat web.
- `GET /static/admin.html` → panel de administración.
- `GET /docs` → documentación interactiva (Swagger UI) generada automáticamente por FastAPI.

> **Nota de seguridad**: el CORS está configurado como abierto (`allow_origins=["*"]`) y ningún endpoint (incluidos los de `/api/v1/admin/*`) tiene autenticación. Esto es aceptable para una demo local o de hackathon, pero **debe revisarse antes de cualquier despliegue expuesto a Internet**.

## Uso de la API

### `POST /api/v1/chat`

Endpoint principal de conversación.

**Request:**

```json
{
  "session_id": "demo-session-001",
  "user_id": "user_a_fin_promo",
  "message": "por que subio mi recibo?",
  "channel": "web"
}
```

**Response (resumida):**

```json
{
  "session_id": "demo-session-001",
  "intent_category": "FIN_PROMOCION",
  "requires_human_intervention": false,
  "sentiment_score": 3,
  "messages": [
    { "text": "¡Hola! Soy Lucía...", "delay_ms": 0, "type": "hook" },
    { "text": "Tu recibo subió S/ 20.00 porque...", "delay_ms": 1000, "type": "explanation" }
  ],
  "upcoming_alerts": [],
  "plan_optimizer_suggestion": { "available": false },
  "confidence_score": 90,
  "compliance_triggered": false
}
```

Ejemplo con `curl` (PowerShell):

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/chat `
  -H "Content-Type: application/json" `
  -d '{"session_id":"demo-1","user_id":"user_a_fin_promo","message":"por que subio mi recibo?"}'
```

### Otros endpoints relevantes (`/api/v1/...`)

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/api/v1/feedback` | Registra feedback (👍/👎) inmediato o posterior sobre un caso. |
| `POST` | `/api/v1/followup/{caso_id}` | Genera el mensaje de seguimiento para un caso en cuarentena. |
| `GET` | `/api/v1/admin/cuarentena` | Lista los casos nuevos pendientes de validación. |
| `POST` | `/api/v1/admin/validar/{caso_id}` | Promueve un caso de cuarentena a la base de conocimiento validada. |
| `GET` | `/api/v1/admin/handoff-queue` | Lista los turnos derivados a un agente humano. |
| `POST` | `/api/v1/admin/handoff-queue/{id}/atender` | Marca un caso de la cola de atención como resuelto. |
| `POST` | `/api/v1/admin/proactive-check` | Dispara manualmente el barrido de alertas proactivas. |
| `GET` | `/api/v1/admin/base-casos` | Lista los casos validados en la base de conocimiento. |

La documentación interactiva completa (con esquemas y prueba en vivo) está disponible en `/docs` mientras el servidor está corriendo.

## Canales soportados

### Web
Cliente de chat estático en `app/static/index.html`, con un selector de usuario para alternar entre los 5 escenarios de demo sin necesidad de autenticación.

### WhatsApp Cloud API
1. Configura una app en [Meta for Developers](https://developers.facebook.com/) con el producto WhatsApp.
2. Define `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID` y `WHATSAPP_VERIFY_TOKEN` en `.env`.
3. Configura el webhook en Meta apuntando a `https://<tu-dominio-o-tunel>/webhook/whatsapp`, usando el mismo `WHATSAPP_VERIFY_TOKEN`.
4. Para pruebas locales, usa un túnel (ngrok o similar) para exponer tu servidor.

> Limitación actual: el webhook asocia **todos** los mensajes entrantes de WhatsApp al usuario mock `user_a_fin_promo` (no hay mapeo real de número de teléfono a usuario todavía).

### Telegram
1. Crea un bot con [`@BotFather`](https://t.me/BotFather) y obtén el token.
2. Define `TELEGRAM_TOKEN` en `.env`.
3. Con el servidor principal corriendo (`uvicorn app.main:app --reload`), ejecuta el bot en otra terminal:

   ```powershell
   python scripts/telegram_bot.py
   ```

> Al igual que en WhatsApp, este bot usa un `user_id` mock fijo (`user_a_fin_promo`) para todas las conversaciones.

## Panel de administración

Disponible en `/static/admin.html`, con 4 secciones:

- **Cola de Atención Humana**: turnos derivados a un agente, con el contexto completo ya empaquetado (sentimiento, historial reciente, evidencia determinista).
- **Casos en Cuarentena**: consultas nuevas sin solución validada previa, esperando feedback.
- **Base de Conocimiento**: soluciones ya validadas y su tasa de reutilización/éxito.
- **Alertas Proactivas**: botón para disparar manualmente el barrido de promociones por vencer.

## Escenarios de demo

Con el servidor corriendo y los datos mock generados, prueba estos mensajes desde el cliente web (`/`) seleccionando cada usuario:

| Usuario | Mensaje sugerido |
|---|---|
| `user_a_fin_promo` | "¿Por qué subió mi recibo este mes?" |
| `user_b_prorrateo` | "¿Por qué me cobraron dos montos distintos?" |
| `user_c_equipo` | "¿Qué es este cargo de cuota de equipo?" |
| `user_d_reconexion` | "¿Por qué tengo un cargo de reconexión?" |
| `user_e_alerta_proactiva` | Usa el botón de alertas proactivas en el panel admin para ver el mensaje saliente. |

Para forzar la derivación a un agente humano, escribe algo como *"quiero hablar con un asesor"*. Para probar el pre-filtro de compliance, un mensaje con lenguaje ofensivo o que mencione "denunciar"/"indecopi" activará el bloqueo automático.

## Limitaciones conocidas

- **RAG con corpus acotado**: la recuperación semántica ya es real (Supabase + `pgvector`), pero el corpus indexado son las políticas base incluidas en `scripts/ingest_supabase.py`. Para un caso de uso productivo habría que ingestar los manuales de políticas completos y evaluar el umbral de similitud con datos reales. El diseño original planteaba ChromaDB local; se optó por Supabase para tener búsqueda vectorial gestionada y persistente.
- **Sin partición del contexto por categoría en el orquestador**: el retriever soporta filtrar por categoría de política, pero el orquestador consulta hoy todas las categorías para no perder recall.
- **Sin autenticación**: ningún endpoint de la API (incluidos los de administración) requiere autenticación. No usar en un entorno expuesto públicamente sin agregar esta capa.
- **`user_id` fijo en canales externos**: los webhooks de WhatsApp y el bot de Telegram asocian todos los mensajes entrantes a un único usuario mock, ya que no existe todavía un mapeo real de identidad por canal.
- **Alertas y seguimientos manuales**: no hay un scheduler/cron real; tanto las alertas proactivas como los mensajes de seguimiento de casos se disparan manualmente vía endpoint, pensado para mantener la demo controlable.
- **Migraciones manuales**: no se usa Alembic; los cambios de esquema se aplican a mano en `run_lightweight_migrations()`.

## Licencia

Proyecto desarrollado para un hackathon de telecomunicaciones. Define aquí la licencia que corresponda antes de distribuir o reutilizar el código (por ejemplo, MIT).
