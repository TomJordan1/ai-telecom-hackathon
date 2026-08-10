# Lucía — Copiloto de Transparencia de Facturación

> Asistente conversacional de facturación B2C para telecomunicaciones, construido para un hackathon en Perú. Explica variaciones de recibo con evidencia verificable, separando de forma estricta un motor determinista (que calcula) de un LLM (que solo redacta).

## Tabla de contenidos

- [Qué hace](#qué-hace)
- [Arquitectura](#arquitectura)
- [Stack tecnológico](#stack-tecnológico)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Instalación](#instalación)
- [Variables de entorno](#variables-de-entorno)
- [Configuración de Supabase](#configuración-de-supabase)
- [Ejecución y datos de prueba](#ejecución-y-datos-de-prueba)
- [API](#api)
- [Canales](#canales)
- [Memoria conversacional](#memoria-conversacional)
- [Panel de administración](#panel-de-administración)
- [Despliegue en Render](#despliegue-en-render)
- [Guion de demo](#guion-de-demo)
- [Limitaciones conocidas](#limitaciones-conocidas)
- [Licencia](#licencia)

## Qué hace

Lucía es un orquestador expuesto vía `POST /api/v1/chat` que responde consultas sobre variaciones de recibo. En lugar de respuestas genéricas de call center, explica la causa concreta del cambio con el monto y el concepto facturado que lo justifica.

El principio de diseño es la **separación estricta de responsabilidades**: montos, fechas, variaciones y reglas de negocio los produce exclusivamente un motor determinista en Python/SQL. El LLM recibe esos datos como solo lectura y únicamente los traduce a lenguaje natural. Una capa de validación posterior corrige la salida del modelo si se desvía.

Cubre cinco escenarios de facturación: fin de promoción, prorrateo por cambio de plan, cuota de equipo financiado, reconexión por morosidad y reducción de tarifa.

Capacidades destacadas:

- **Motor determinista**: calcula la variación entre recibos, detecta el evento causal y produce evidencia trazable.
- **Memoria contextual y emocional** persistida en base de datos: bitácora acotada de la conversación y comentarios emocionales con caducidad y consolidación.
- **Pre-filtro de compliance** por expresiones regulares: bloquea riesgo legal, insultos y datos sensibles antes de que el mensaje llegue a cualquier componente de IA.
- **Índice de incertidumbre determinista**: si no hay certeza suficiente, deriva a un agente humano con el contexto ya empaquetado.
- **Cross-selling restrictivo**: exige cuatro condiciones simultáneas y un plan verificado contra el catálogo real. Si no hay candidato real, no se ofrece nada aunque el modelo lo sugiera.
- **Alertas proactivas**: avisa antes de que venza una promoción, con el impacto estimado.
- **Aprendizaje supervisado**: los casos nuevos pasan por cuarentena y feedback antes de volverse conocimiento reutilizable.
- **Auditoría estructurada**: cada decisión del orquestador queda registrada (evento, componentes invocados, incertidumbre, latencia) sin almacenar el texto de las respuestas.

Ver [`plan.md`](./plan.md) para la especificación arquitectónica original.

## Arquitectura

Cinco capas desacopladas:

```
1. Entrada           FastAPI · Web / WhatsApp Cloud API / Telegram
2. Orquestación      orchestrator.py · memoria de sesión · enrutamiento de intención
3. Determinismo      deterministic.py · compliance · cálculo · gatillo comercial
4. Conocimiento      case_matcher.py · rag.py (Supabase pgvector) · cuarentena
5. Lenguaje          llm.py (DeepSeek vía LangChain) · persona.py
```

Flujo de un turno de facturación:

1. Se carga el estado de la sesión desde la base de datos.
2. Pre-filtro de compliance. Si dispara, corta antes de la IA.
3. Enrutamiento de intención: facturación, solicitud de agente o conversacional.
4. El motor determinista calcula el payload de hechos y detecta el evento.
5. Se busca un caso validado en `base_casos`. Si no hay, se consulta el RAG.
6. Se calcula la incertidumbre; si supera el umbral, deriva a un humano.
7. Se evalúa el gatillo comercial contra el catálogo real.
8. El LLM redacta usando solo datos verificados; la salida se valida y corrige.
9. Se actualiza la memoria y se registra la auditoría.

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| API | FastAPI + Uvicorn |
| Configuración | Pydantic v2 + pydantic-settings |
| Base de datos | PostgreSQL (Supabase) vía SQLAlchemy 2.0 · SQLite como alternativa local |
| Búsqueda vectorial | `pgvector` con índice HNSW y similitud de coseno |
| Embeddings | `all-MiniLM-L6-v2` local vía `fastembed` (384 dims, default) u OpenAI `text-embedding-3-small` (1536 dims) |
| LLM | DeepSeek Chat vía LangChain (interfaz compatible con OpenAI) |
| Mensajería | WhatsApp Cloud API y Telegram Bot API (HTTP directo) |
| Frontend | HTML/CSS/JS estático servido por FastAPI |

## Estructura del proyecto

```
ai-telecom-hackathon/
├── app/
│   ├── main.py                     # Bootstrap, CORS, estáticos, migraciones
│   ├── core/
│   │   ├── config.py                # Configuración por variables de entorno
│   │   └── schemas.py               # Modelos Pydantic de request/response
│   ├── db/
│   │   ├── models.py                # 8 tablas SQLAlchemy
│   │   ├── database.py              # Engine agnóstico al motor + migraciones ligeras
│   │   └── crud.py                  # Acceso a datos y lógica de memoria
│   ├── api/
│   │   ├── routes.py                # POST /api/v1/chat
│   │   ├── whatsapp.py              # Webhook de WhatsApp (firma + mapeo de número)
│   │   └── knowledge.py             # Feedback, cuarentena, admin, alertas
│   ├── services/
│   │   ├── orchestrator.py          # Orquestador del flujo
│   │   ├── deterministic.py         # Cálculo de facturación y compliance
│   │   ├── intent_classifier.py     # Enrutamiento de intención
│   │   ├── uncertainty_calculator.py# Índice de incertidumbre → handoff
│   │   ├── case_matcher.py          # Coincidencia con base de casos
│   │   ├── feedback_handler.py      # Ciclo cuarentena → base de casos
│   │   ├── rag.py                   # Recuperación semántica en Supabase
│   │   ├── embeddings.py            # Proveedor único de embeddings
│   │   ├── llm.py                   # Generación con DeepSeek / mock
│   │   ├── persona.py               # Tono y registro lingüístico
│   │   ├── proactive_alerts.py      # Alertas proactivas salientes
│   │   ├── whatsapp_sender.py       # Envío por WhatsApp Cloud API
│   │   └── telegram_sender.py       # Envío por Telegram Bot API
│   └── static/                      # Chat web + panel de administración
├── scripts/
│   ├── generate_mock_data.py        # Seed de datos de prueba
│   ├── setup_supabase.sql           # Esquema pgvector + función RPC
│   ├── ingest_supabase.py           # Ingesta de políticas al índice vectorial
│   └── telegram_bot.py              # Bot de Telegram (proceso aparte)
├── plan.md                          # Especificación arquitectónica original
├── requirements.txt
└── .env.example
```

## Instalación

Requiere **Python 3.11+** (el código usa sintaxis como `str | None`).

```powershell
git clone <url-del-repositorio>
cd ai-telecom-hackathon

python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
```

Luego edita `.env` con tus credenciales.

## Variables de entorno

| Variable | Descripción | Default |
|---|---|---|
| `DATABASE_URL` | Cadena SQLAlchemy. Postgres de Supabase en producción, SQLite en local. | `sqlite:///./lucia_brain.db` |
| `DEEPSEEK_API_KEY` | Key de DeepSeek para la generación de texto. | — |
| `USE_MOCK_LLM` | `True` usa un generador simulado en vez de llamar a DeepSeek. | `True` |
| `SUPABASE_URL` | URL del proyecto (Project Settings → API). | — |
| `SUPABASE_KEY` | Clave de API. Usar la `service_role`, solo en backend. | — |
| `USE_MOCK_RAG` | `True` devuelve contexto simulado sin consultar Supabase. | `True` |
| `EMBEDDING_PROVIDER` | `local` (384 dims, sin costo) u `openai` (1536 dims). | `local` |
| `EMBEDDING_MODEL` | Vacío = default del proveedor. | — |
| `OPENAI_API_KEY` | Solo si `EMBEDDING_PROVIDER=openai`. La de DeepSeek no sirve: su API no tiene endpoint de embeddings. | — |
| `RAG_MATCH_THRESHOLD` | Similitud mínima (0–1) para aceptar un chunk. | `0.5` |
| `RAG_MATCH_COUNT` | Cantidad de chunks a recuperar. | `3` |
| `WHATSAPP_TOKEN` | Access token de la app de Meta. | — |
| `WHATSAPP_PHONE_ID` | Phone number ID (no el número). | — |
| `WHATSAPP_VERIFY_TOKEN` | Token que defines tú para validar el webhook. | `lucia_hackathon_secret` |
| `WHATSAPP_APP_SECRET` | App Secret de Meta. Habilita la verificación de firma de los eventos entrantes. | — |
| `WHATSAPP_API_VERSION` | Versión de la Graph API. Meta retira las antiguas. | `v26.0` |
| `TELEGRAM_TOKEN` | Token del bot de `@BotFather`. Dejar sin definir si no se usa. | — |

El sistema arranca sin ninguna credencial: con `USE_MOCK_LLM=True` y `USE_MOCK_RAG=True` funciona completo en modo simulado, interpolando los mismos datos deterministas verificados.

> No definas `TELEGRAM_TOKEN` con un valor de ejemplo. Cualquier cadena no vacía se considera válida y el código intentará llamar a la API de Telegram.

## Configuración de Supabase

Un mismo proyecto de Supabase aloja las dos cosas: los datos operacionales (recibos, memoria, casos, auditoría) y el índice vectorial de políticas.

### 1. Crear el esquema vectorial

En **SQL Editor → New query**, pega [`scripts/setup_supabase.sql`](./scripts/setup_supabase.sql) y ejecútalo. Crea la tabla `documentos_politicas`, el índice HNSW, la función RPC `match_documentos` y habilita RLS.

> La dimensión del vector debe coincidir con el modelo de embeddings. El script viene en `VECTOR(384)` para `all-MiniLM-L6-v2`. Para usar OpenAI, cambia `384` por `1536` en los **dos** lugares donde aparece (la columna y el parámetro de la función) y recrea la tabla con `DROP TABLE documentos_politicas CASCADE;`, porque una columna `VECTOR` no se puede redimensionar.

### 2. Obtener la cadena de conexión

En **Project Settings → Database → Connection string → URI**, usa la del **Connection pooler**, no la directa: la directa resuelve solo por IPv6 en proyectos nuevos y muchos hosts no la alcanzan.

```env
DATABASE_URL=postgresql+psycopg2://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
```

> La clave `service_role` omite RLS y da acceso total al proyecto. Va solo en el backend, nunca en el frontend, y `.env` está en `.gitignore`.

### 3. Ingestar el corpus de políticas

```powershell
python scripts/ingest_supabase.py            # vectoriza e inserta
python scripts/ingest_supabase.py --reset    # borra los chunks de la misma fuente y reingesta
python scripts/ingest_supabase.py --dry-run  # valida los embeddings sin escribir
```

El corpus son 16 chunks de políticas que cubren los cinco escenarios más transparencia general. Las categorías coinciden con los `detected_event` del motor determinista, lo que permite filtrar por evento. Con `local` no hace falta ninguna API key: `fastembed` descarga el modelo (~80 MB) en la primera ejecución y lo cachea.

Verificación:

```sql
select categoria, count(*) from documentos_politicas group by categoria order by categoria;
```

### Comportamiento ante fallos

`retrieve_context()` nunca lanza excepción ni bloquea `POST /chat`. Ante falta de credenciales, error de red o ninguna coincidencia sobre el umbral, degrada a un bloque de políticas generales y la conversación continúa. El motivo queda en los logs con el prefijo `[RAG]`.

El contexto recuperado se inyecta marcado como referencia conceptual, no como fuente de cifras.

## Ejecución y datos de prueba

Sembrar los datos (crea las tablas si no existen):

```powershell
python scripts/generate_mock_data.py
```

Siembra cinco clientes, uno por escenario, con dos recibos cada uno:

| `user_id` | Escenario |
|---|---|
| `user_a_fin_promo` | Fin de promoción |
| `user_b_prorrateo` | Prorrateo por cambio de plan |
| `user_c_equipo` | Cuota de equipo financiado |
| `user_d_reconexion` | Reconexión por morosidad |
| `user_e_alerta_proactiva` | Promoción venciendo en 5 días |

También carga el catálogo de planes, las reglas de compliance y contactos con números ficticios. Es idempotente respecto al catálogo: si `catalogo_planes` ya tiene filas, no inserta nada.

Levantar el servidor:

```powershell
uvicorn app.main:app --reload
```

- `/` → chat web
- `/static/admin.html` → panel de administración
- `/docs` → Swagger UI
- `/health` → estado y versión

> **Seguridad**: el CORS está abierto (`allow_origins=["*"]`) y ningún endpoint tiene autenticación, incluidos los de `/api/v1/admin/*`. Aceptable para una demo; revisar antes de cualquier exposición pública estable.

## API

### `POST /api/v1/chat`

```json
{
  "session_id": "demo-1",
  "user_id": "user_a_fin_promo",
  "message": "por que subio mi recibo?",
  "channel": "web"
}
```

Respuesta abreviada:

```json
{
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

`intent_category` en turnos de facturación es el `detected_event` del motor determinista (`FIN_PROMOCION`, `PRORRATEO_CAMBIO_PLAN`, `CUOTA_EQUIPO`, `RECONEXION_MOROSIDAD`, `REDUCCION_TARIFA`, `SIN_CAMBIOS`), no una etiqueta generada por el modelo. Es un valor estable sobre el que se puede programar.

### Otros endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/api/v1/feedback` | Feedback inmediato o posterior sobre un caso. |
| `POST` | `/api/v1/followup/{caso_id}` | Genera el mensaje de seguimiento de un caso. |
| `GET` | `/api/v1/admin/cuarentena` | Casos pendientes de validación. |
| `POST` | `/api/v1/admin/validar/{caso_id}` | Promueve un caso a la base de conocimiento. |
| `GET` | `/api/v1/admin/base-casos` | Casos validados. |
| `GET` | `/api/v1/admin/handoff-queue` | Cola de atención humana. |
| `POST` | `/api/v1/admin/handoff-queue/{id}/atender` | Marca un caso como atendido. |
| `POST` | `/api/v1/admin/proactive-check` | Dispara el barrido de alertas proactivas. |

## Canales

### Web

Chat estático en `app/static/`, con selector de usuario para alternar entre escenarios. La sesión se guarda en `localStorage`, así que al recargar o cerrar el navegador se retoma la misma conversación con su memoria. El botón **↻** de la cabecera inicia una conversación nueva.

### WhatsApp Cloud API

1. Crea una app en [Meta for Developers](https://developers.facebook.com/) con el producto WhatsApp.
2. Copia el access token, el Phone number ID y la versión de la API desde **WhatsApp → API Setup** a tu `.env`.
3. En **Configuration → Webhook**, configura la Callback URL `https://<tu-dominio>/webhook/whatsapp` con tu `WHATSAPP_VERIFY_TOKEN`.
4. En **Webhook fields**, suscríbete a `messages`. Sin esta suscripción la verificación pasa pero no llega ningún mensaje.
5. Agrega tu número a la lista **To** de API Setup: el número de prueba solo conversa con destinatarios preautorizados.

El número entrante se resuelve contra `contactos_usuario` para atender a cada cliente con sus propios recibos. La comparación es por dígitos, con tolerancia de prefijo país por los últimos 9. Si el número no está registrado, se usa un cliente de respaldo para que la conversación siga siendo coherente.

Los eventos entrantes se validan con la firma `X-Hub-Signature-256` calculada sobre el cuerpo crudo del request. Si `WHATSAPP_APP_SECRET` no está definido, el webhook acepta el evento y lo advierte en el log.

Para pruebas locales necesitas un túnel HTTPS (ngrok o similar), porque Meta no puede alcanzar `127.0.0.1`.

### Telegram

Crea un bot con [`@BotFather`](https://t.me/BotFather), define `TELEGRAM_TOKEN` y, con el servidor corriendo, ejecuta el bot en otra terminal:

```powershell
python scripts/telegram_bot.py
```

## Memoria conversacional

La memoria vive en `historial_interacciones`, indexada por `session_id`, y persiste entre reinicios y despliegues:

- `historial_conversacion`: bitácora acotada a 12 turnos. Permite que Lucía no repita una explicación ya dada.
- `comentarios_emocionales`: frases con carga emocional detectadas por expresiones regulares, con caducidad de 14 días, tope de 5 y marca de referenciado para no repetirlas indefinidamente.
- `score_sentimiento` y `estado_resolucion`: señales que alimentan el gatillo comercial.

Para comprobar que persiste de verdad, la prueba útil no es cerrar el navegador (podría estar en RAM) sino **reiniciar el servidor** y continuar la misma sesión. También puedes inspeccionar la fila directamente:

```sql
select session_id, comentarios_emocionales, historial_conversacion, score_sentimiento
from historial_interacciones order by updated_at desc;
```

El guardado y la recuperación son deterministas. Que Lucía *mencione* el comentario emocional en su redacción es una instrucción del prompt, y el modelo la cumple casi siempre pero no de forma garantizada.

## Panel de administración

En `/static/admin.html`, cuatro secciones:

- **Cola de Atención Humana**: turnos derivados, con el contexto ya empaquetado para que el agente no pida repetir el caso.
- **Casos en Cuarentena**: consultas sin solución validada previa, esperando feedback.
- **Base de Conocimiento**: soluciones validadas y su reutilización.
- **Alertas Proactivas**: dispara el barrido de promociones por vencer.

## Despliegue en Render

El servicio corre como Web Service con este start command, que debe usar el puerto asignado por la plataforma:

```
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Las variables de entorno se cargan en **Environment** del servicio; `.env` no se sube al repositorio. En producción, `USE_MOCK_LLM` y `USE_MOCK_RAG` van en `False` y `DATABASE_URL` apunta al pooler de Supabase.

Tres cosas del plan gratuito que conviene tener en cuenta:

- **El disco es efímero**, por eso los datos operacionales van a Postgres y no a SQLite.
- **La instancia se duerme** por inactividad y tarda entre 30 y 60 segundos en despertar. Con embeddings locales, la primera consulta además descarga el modelo. Antes de una demo, pégale a `/health` y manda un mensaje de calentamiento, o el primer evento de WhatsApp puede irse a timeout.
- **512 MB de RAM.** Medido con el modelo local cargado, el proceso ronda los 291 MB. Entra, pero con margen acotado; si aparecen reinicios por memoria, cambiar a `EMBEDDING_PROVIDER=openai` libera la mayor parte.

## Guion de demo

Desde el chat web, seleccionando cada usuario:

| Usuario | Mensaje |
|---|---|
| `user_a_fin_promo` | ¿Por qué subió mi recibo este mes? |
| `user_b_prorrateo` | ¿Por qué me cobraron dos montos distintos? |
| `user_c_equipo` | ¿Qué es este cargo de cuota de equipo? |
| `user_d_reconexion` | ¿Por qué tengo un cargo de reconexión? |
| `user_e_alerta_proactiva` | Mi recibo está igual, ¿todo bien? |

El último es el más ilustrativo: responde que no hay cambios y avisa que el descuento vence en cinco días con el impacto estimado, calculado por el motor determinista.

Para mostrar las salvaguardas:

- *"quiero hablar con un asesor"* → deriva de inmediato y el caso aparece en la cola del panel.
- *"voy a denunciar esto a indecopi"* → el pre-filtro de compliance bloquea antes de llegar a la IA.
- *"gracias, quedó clarísimo"* → el sentimiento sube pero la oferta comercial no aparece si el catálogo no tiene un plan que sea mejora real. Es el blindaje anti-alucinación funcionando.

Para memoria emocional, frases que activan el detector: *"la verdad estoy cansado de que mi recibo suba todos los meses"*, *"siempre pasa lo mismo"*, *"sé que no es tu culpa"*.

## Limitaciones conocidas

- **Sin autenticación de cliente**: no se verifica la identidad de quien escribe. En WhatsApp, un número no registrado en `contactos_usuario` cae a un cliente de respaldo. Un flujo productivo pediría DNI más un segundo factor antes de exponer datos de facturación.
- **Sin autenticación de API**: ningún endpoint la requiere, incluidos los de administración.
- **Alertas y seguimientos manuales**: no hay scheduler; se disparan por endpoint para mantener la demo controlable.
- **Corpus de políticas acotado**: la búsqueda vectorial es real, pero el corpus son las 16 políticas base del script de ingesta. Un caso productivo requeriría los manuales completos y calibrar el umbral con datos reales.
- **Sin filtro por categoría en el orquestador**: el retriever lo soporta, pero se consultan todas las categorías para no perder recall.
- **Migraciones manuales**: no se usa Alembic; las columnas nuevas se agregan en `run_lightweight_migrations()`.
- **`base_casos` empieza vacía**: el ciclo de aprendizaje requiere que un agente valide casos desde el panel antes de que haya conocimiento reutilizable.

## Licencia

Proyecto de hackathon. Define la licencia que corresponda antes de distribuir o reutilizar el código.
