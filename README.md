# Lucía — Copiloto de Transparencia de Facturación

Asistente conversacional que explica variaciones de recibo a clientes de telecomunicaciones. Su objetivo es que cada cambio de monto quede justificado con el concepto facturado concreto que lo produjo, en lugar de respuestas genéricas.

## No es un chatbot que responde. Es un sistema que aprende a resolver, y siempre sabe cuándo no sabe.

La mayoría de asistentes de facturación explican igual el día 1 que el día 100: cada consulta se resuelve desde cero, con el mismo esfuerzo del modelo de lenguaje y el mismo nivel de riesgo de que algo salga mal. Lucía está diseñada para lo contrario: **cada caso que un asesor valida hace que el siguiente caso igual se resuelva más rápido, con más certeza y sin volver a depender tanto del LLM.**

Esto se sostiene en tres decisiones de diseño, no en una sola feature:

1. **Confianza calculada, no declarada.** El sistema nunca le pregunta al modelo "¿qué tan seguro estás?". El índice de incertidumbre se construye desde señales objetivas del backend — ¿hay un caso ya validado?, ¿hay datos suficientes?, ¿el evento es reconocible? — y decide con esos números cuándo derivar a un humano en vez de improvisar una respuesta.
2. **Aprendizaje supervisado, no memorización ciega.** El sistema no cachea texto. Aprende *patrones* de problema → solución. Un caso nuevo pasa por cuarentena; solo se promueve a conocimiento reutilizable cuando el feedback *posterior* (no solo el "👍" del momento) confirma que funcionó, o un asesor humano lo aprueba desde el panel de administración.
3. **Esto es medible en vivo, no solo una promesa de diapositiva.** La misma consulta, resuelta dos veces: la primera vez sale con confianza 80% (caso nuevo, va a cuarentena); después de que un asesor la valide desde el panel, la segunda consulta idéntica sale con confianza 100% — y la propia interfaz lo muestra con una insignia verde (✓ *Caso validado*) o ámbar (◌ *Caso nuevo, en aprendizaje*). Es la reducción de carga al call center ocurriendo frente a quien prueba el producto, no una cifra proyectada.

El diseño se apoya en una **separación estricta de responsabilidades**: los montos, fechas, variaciones y reglas de negocio los calcula un motor determinista en Python/SQL, y el modelo de lenguaje solo traduce esa información a lenguaje natural. El LLM nunca calcula ni decide, y una capa de validación posterior corrige su salida si se desvía de los datos verificados. Esta separación es lo que hace posible el punto 1: la incertidumbre no depende de que el modelo "se sienta seguro", depende de hechos verificables.

## Tabla de contenidos

- [Qué hace](#qué-hace)
- [Arquitectura](#arquitectura)
- [Stack tecnológico](#stack-tecnológico)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Instalación](#instalación)
  - [Requisitos previos](#requisitos-previos)
  - [Paso 1: obtener el código](#paso-1-obtener-el-código)
  - [Paso 2: entorno virtual](#paso-2-entorno-virtual)
  - [Paso 3: dependencias](#paso-3-dependencias)
  - [Paso 4: archivo de configuración](#paso-4-archivo-de-configuración)
  - [Paso 5: elegir base de datos](#paso-5-elegir-base-de-datos)
  - [Paso 6: sembrar datos de prueba](#paso-6-sembrar-datos-de-prueba)
  - [Paso 7: levantar el servidor](#paso-7-levantar-el-servidor)
  - [Verificación final](#verificación-final)
  - [Problemas frecuentes](#problemas-frecuentes)
- [Variables de entorno](#variables-de-entorno)
- [Activar la búsqueda vectorial (RAG)](#activar-la-búsqueda-vectorial-rag)
- [Activar el modelo de lenguaje real](#activar-el-modelo-de-lenguaje-real)
- [API](#api)
- [Canales](#canales)
- [Memoria conversacional](#memoria-conversacional)
- [Panel de administración](#panel-de-administración)
- [Despliegue](#despliegue)
- [Escenarios de prueba](#escenarios-de-prueba)
- [Limitaciones conocidas](#limitaciones-conocidas)
- [Licencia](#licencia)

## Qué hace

El sistema expone `POST /api/v1/chat` y atiende consultas sobre el recibo del cliente. Cubre cinco causas de variación:

| Evento | Qué explica |
|---|---|
| `FIN_PROMOCION` | Un descuento temporal terminó y el cargo volvió a su precio regular |
| `PRORRATEO_CAMBIO_PLAN` | Cambio de plan a mitad de ciclo, cobrado proporcionalmente por días de uso |
| `CUOTA_EQUIPO` | Cuota mensual de un equipo financiado |
| `RECONEXION_MOROSIDAD` | Cargo único por reactivar un servicio suspendido |
| `REDUCCION_TARIFA` | El recibo bajó, y también hay que explicar por qué |

Capacidades adicionales:

- **Motor determinista**: calcula la variación entre recibos, detecta el evento causal y produce evidencia trazable.
- **Memoria contextual y emocional** persistida en base de datos: bitácora acotada de la conversación y comentarios emocionales con caducidad y consolidación.
- **Pre-filtro de cumplimiento**: bloquea por expresiones regulares los mensajes con riesgo legal, insultos o datos sensibles antes de que lleguen a cualquier componente de IA.
- **Índice de incertidumbre determinista**: si no hay certeza suficiente sobre el caso, deriva a un agente humano con el contexto ya empaquetado.
- **Venta cruzada restrictiva**: exige cuatro condiciones simultáneas y un plan verificado contra el catálogo real. Si no hay candidato real, no se ofrece nada aunque el modelo lo sugiera.
- **Alertas proactivas**: avisa antes de que venza una promoción, con el impacto estimado.
- **Aprendizaje supervisado**: los casos nuevos pasan por cuarentena y feedback antes de convertirse en conocimiento reutilizable.
- **Auditoría estructurada**: cada decisión del orquestador queda registrada (evento detectado, componentes invocados, incertidumbre, latencia) sin guardar el texto de las respuestas.

Ver [`plan.md`](./plan.md) para la especificación arquitectónica original.

## Arquitectura

Cinco capas desacopladas:

```
1. Entrada           FastAPI · Web / WhatsApp Cloud API / Telegram
2. Orquestación      orchestrator.py · memoria de sesión · enrutamiento de intención
3. Determinismo      deterministic.py · cumplimiento · cálculo · gatillo comercial
4. Conocimiento      case_matcher.py · rag.py (pgvector) · cuarentena de casos
5. Lenguaje          llm.py (DeepSeek vía LangChain) · persona.py
```

Flujo de un turno de facturación:

1. Se carga el estado de la sesión desde la base de datos.
2. Pre-filtro de cumplimiento. Si dispara, corta antes de la IA.
3. Enrutamiento de intención: facturación, solicitud de agente o conversacional.
4. El motor determinista calcula el payload de hechos y detecta el evento.
5. Se busca un caso ya validado en `base_casos`. Si no hay, se consulta el RAG.
6. Se calcula la incertidumbre; si supera el umbral, deriva a un humano.
7. Se evalúa el gatillo comercial contra el catálogo real de planes.
8. El LLM redacta usando solo datos verificados; la salida se valida y corrige.
9. Se actualiza la memoria de sesión y se registra la auditoría del turno.

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| API | FastAPI + Uvicorn |
| Configuración | Pydantic v2 + pydantic-settings |
| Base de datos | PostgreSQL vía SQLAlchemy 2.0, con SQLite como alternativa local |
| Búsqueda vectorial | `pgvector` con índice HNSW y similitud de coseno |
| Embeddings | `all-MiniLM-L6-v2` local vía `fastembed` (384 dims, default) u OpenAI `text-embedding-3-small` (1536 dims) |
| Modelo de lenguaje | DeepSeek Chat vía LangChain (interfaz compatible con OpenAI) |
| Mensajería | WhatsApp Cloud API y Telegram Bot API |
| Frontend | HTML/CSS/JS estático servido por FastAPI |

## Estructura del proyecto

```
ai-telecom-hackathon/
├── app/
│   ├── main.py                      # Bootstrap, CORS, estáticos, migraciones
│   ├── core/
│   │   ├── config.py                 # Configuración por variables de entorno
│   │   └── schemas.py                # Modelos Pydantic de request/response
│   ├── db/
│   │   ├── models.py                 # Tablas del dataset + tablas operacionales
│   │   ├── database.py               # Engine agnóstico al motor + migraciones ligeras
│   │   └── crud.py                   # Acceso a datos y lógica de memoria
│   ├── api/
│   │   ├── routes.py                 # POST /api/v1/chat
│   │   ├── whatsapp.py               # Webhook (firma + mapeo de número a cliente)
│   │   └── knowledge.py              # Feedback, cuarentena, administración, alertas
│   ├── services/
│   │   ├── orchestrator.py           # Orquestador del flujo
│   │   ├── deterministic.py          # Cálculo de facturación y cumplimiento
│   │   ├── intent_classifier.py      # Enrutamiento de intención
│   │   ├── uncertainty_calculator.py # Índice de incertidumbre → derivación
│   │   ├── case_matcher.py           # Coincidencia con la base de casos
│   │   ├── feedback_handler.py       # Ciclo cuarentena → base de casos
│   │   ├── rag.py                    # Recuperación semántica en pgvector
│   │   ├── embeddings.py             # Proveedor único de embeddings
│   │   ├── llm.py                    # Generación con DeepSeek o simulada
│   │   ├── persona.py                # Tono y registro lingüístico
│   │   ├── proactive_alerts.py       # Alertas proactivas salientes
│   │   ├── whatsapp_sender.py        # Envío por WhatsApp Cloud API
│   │   └── telegram_sender.py        # Envío por Telegram Bot API
│   └── static/                       # Chat web + panel de administración
├── disclaimer/                       # Dataset del desafío (5 CSV + diccionario)
├── scripts/
│   ├── ingest_real_data.py           # Ingesta del dataset con cobertura de escenarios
│   ├── verify_engine.py              # Verifica el motor y sus invariantes contra la base
│   ├── smoke_chat.py                 # Prueba de humo de POST /chat por escenario
│   ├── find_scenarios.py             # Busca cuentas por escenario en el CSV completo
│   ├── db_status.py                  # Tablas existentes y conteo de filas
│   ├── reset_db.py                   # Limpieza de esquema y de estado conversacional
│   ├── setup_supabase.sql            # Esquema pgvector + función RPC de búsqueda
│   ├── ingest_supabase.py            # Ingesta de políticas al índice vectorial
│   └── telegram_bot.py               # Bot de Telegram (proceso aparte)
├── plan.md                           # Especificación arquitectónica original
├── requirements.txt
└── .env.example
```

## Instalación

La instalación mínima no requiere ninguna credencial: el sistema arranca en modo simulado con SQLite y responde con datos deterministas reales. Después puedes activar la búsqueda vectorial y el modelo de lenguaje por separado.

Los comandos están en PowerShell (Windows). En Linux o macOS cambia `.\venv\Scripts\Activate.ps1` por `source venv/bin/activate` y `Copy-Item` por `cp`.

### Requisitos previos

| Requisito | Versión | Verificar con |
|---|---|---|
| Python | 3.11 o superior | `python --version` |
| pip | cualquiera reciente | `pip --version` |
| Git | cualquiera | `git --version` |

Python 3.11 es el mínimo porque el código usa sintaxis de tipos moderna (`str | None`). Si tienes varias versiones instaladas en Windows, `py -0p` las lista y puedes forzar una con `py -3.12 -m venv venv`.

### Paso 1: obtener el código

```powershell
git clone https://github.com/<usuario>/ai-telecom-hackathon.git
cd ai-telecom-hackathon
```

### Paso 2: entorno virtual

Aísla las dependencias del proyecto de tu Python global.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

El prompt debe quedar prefijado con `(venv)`.

Si PowerShell rechaza el script con un error de directivas de ejecución, habilita los scripts locales para tu usuario y vuelve a intentar:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Como alternativa, puedes no activar el entorno y llamar al intérprete por ruta completa en todos los comandos: `.\venv\Scripts\python.exe -m uvicorn ...`.

### Paso 3: dependencias

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Tarda unos minutos y descarga alrededor de 250 MB. Instala FastAPI y Uvicorn, SQLAlchemy con el driver de PostgreSQL, el cliente de Supabase, LangChain para hablar con DeepSeek, `fastembed` para los embeddings locales y `python-telegram-bot`.

Comprueba que quedó bien:

```powershell
python -c "import fastapi, sqlalchemy, supabase, fastembed; print('dependencias OK')"
```

### Paso 4: archivo de configuración

```powershell
Copy-Item .env.example .env
```

`.env` guarda toda la configuración y está incluido en `.gitignore`, así que nunca se sube al repositorio. La plantilla viene comentada variable por variable; el detalle está en [Variables de entorno](#variables-de-entorno).

Para la instalación mínima basta con dejar estos tres valores:

```env
DATABASE_URL=sqlite:///./lucia_brain.db
USE_MOCK_LLM=True
USE_MOCK_RAG=True
```

> Importante: no dejes `TELEGRAM_TOKEN` con el valor de ejemplo de la plantilla. Cualquier cadena no vacía se considera un token válido y el código intentará llamar a la API de Telegram. Si no vas a usar ese canal, borra la línea o déjala vacía.

### Paso 5: elegir base de datos

Aquí viven los recibos, la memoria conversacional, la base de casos, la cuarentena y la auditoría.

**Opción A — SQLite (rápida, para desarrollo local).** No requiere nada, ya está en el `.env` del paso anterior. Se crea un archivo `lucia_brain.db` en la raíz del proyecto.

**Opción B — PostgreSQL (persistente, requerida para desplegar).** Necesaria si vas a alojar el proyecto en una plataforma con disco efímero, donde SQLite se borraría en cada reinicio. Usando Supabase:

1. Crea un proyecto en [supabase.com](https://supabase.com) y guarda la contraseña de base de datos que defines al crearlo.
2. Ve a **Project Settings → Database → Connection string → URI** y copia la cadena del **Connection pooler**.
3. Adáptala al formato de SQLAlchemy en tu `.env`:

```env
DATABASE_URL=postgresql+psycopg2://postgres.<ref-del-proyecto>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
```

Usa la cadena del pooler y no la conexión directa (`db.<ref>.supabase.co:5432`): en proyectos nuevos esa resuelve solo por IPv6 y muchas redes y plataformas de hosting no la alcanzan.

Verifica la conexión antes de continuar:

```powershell
python -c "from app.db.database import engine; print('conectado a', engine.dialect.name)"
```

Debe imprimir `conectado a postgresql`. Si falla por autenticación, la contraseña es la de la base de datos, no la de tu cuenta de Supabase; se puede resetear en esa misma pantalla.

### Paso 6: ingestar el dataset del desafío

Los datos vienen del dataset real entregado en el reto, en `disclaimer/`. No hay generación de datos ficticios: si un dato no está en el dataset, el sistema no lo afirma.

```powershell
python scripts/ingest_real_data.py --reset
```

| Archivo de `disclaimer/` | Tabla | Contenido |
|---|---|---|
| `FACTURACION_CLIENTES.csv` | `facturacion_clientes` | Cargos individuales por cuenta y ciclo |
| `PLANTA_CLIENTES.csv` | `planta_clientes` | Servicios activos de cada cuenta |
| `CATALOGO_OFERTAS.csv` | `catalogo_ofertas` | Tarifa oficial (`rate_final`) y tipo de renta por código de cargo |
| `ORDENES.csv` | `ordenes_cliente` | Historial CRM: suspensiones, reconexiones, cambios, altas |
| `NOTAS_CREDITO.csv` | `notas_credito` | Notas de crédito (`CRD`) y débito (`DSC`) |

La identidad del cliente es su **cuenta financiera** (`FINANCIAL_ACCOUNT` en la planta, `FINANCIAL_ACCOUNT_KEY` en facturación): la facturación se emite por cuenta y agrupa todas sus líneas móvil, internet, voz y TV.

Opciones:

| Flag | Efecto |
|---|---|
| `--reset` | Vacía las tablas del dataset antes de insertar. Sin él, el script no hace nada si ya hay cargos. |
| `--max-users N` | Cuántas cuentas cargar. Por defecto 1000; `0` carga las 18 471 del archivo. |

La selección de cuentas **no** es "las primeras N del archivo". Los escenarios del reto no están repartidos de forma uniforme (hay 1652 cuentas con prorrateo pero solo 17 con cuota de equipo financiado), así que el script clasifica las cuentas con el mismo motor que usa la aplicación y reserva una cuota por escenario antes de rellenar hasta el límite. De otro modo habría escenarios imposibles de demostrar.

Para ver qué cuentas de la base sirven para cada escenario:

```powershell
python scripts/verify_engine.py
```

Además de listar una cuenta de ejemplo por evento, comprueba que la descomposición de la variación cuadre al céntimo con la diferencia entre recibos. Con `--detalle EVENTO` imprime el payload completo que recibiría el modelo.

Otros scripts de datos:

| Script | Para qué |
|---|---|
| `scripts/db_status.py` | Qué tablas existen y cuántas filas tiene cada una |
| `scripts/reset_db.py` | Eliminar tablas obsoletas, vaciar el estado conversacional o recrear el esquema |
| `scripts/find_scenarios.py` | Buscar cuentas por escenario en el CSV completo, antes de ingerir |

### Paso 7: levantar el servidor

```powershell
uvicorn app.main:app --reload
```

El flag `--reload` reinicia el proceso cuando cambias un archivo `.py`. Los cambios en `.env` **no** disparan el reinicio: hay que detener y volver a arrancar.

Queda disponible en `http://127.0.0.1:8000`:

| Ruta | Contenido |
|---|---|
| `/` | Chat web (redirige a `/static/index.html`) |
| `/static/admin.html` | Panel de administración |
| `/docs` | Documentación interactiva de la API |
| `/health` | Estado y versión del servicio |

### Verificación final

```powershell
curl.exe http://127.0.0.1:8000/health
```

Debe responder algo como:

```json
{"status":"ok","project":"Copiloto de Transparencia (Lucía)","version":"0.2.0"}
```

Y una consulta completa de facturación. Primero pide una cuenta con historial:

```powershell
curl.exe http://127.0.0.1:8000/api/v1/cuenta-demo
```

Con esa cuenta:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/chat `
  -H "Content-Type: application/json" `
  -d '{"session_id":"prueba-1","user_id":"<CUENTA>","message":"por que subio mi recibo?"}'
```

En la respuesta, `intent_category` debe ser el evento que el motor detectó para esa cuenta (`FIN_PROMOCION`, `PRORRATEO_CAMBIO_PLAN`, `CUOTA_EQUIPO`…) y `requires_human_intervention` debe ser `false`. Si devuelve `DERIVACION_INCERTIDUMBRE`, es que no encuentra recibos para esa cuenta: revisa que el paso 6 haya corrido contra la misma base que apunta `DATABASE_URL`.

Para validar los ocho escenarios de golpe, con el servidor levantado:

```powershell
python scripts/smoke_chat.py
```

### Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `ModuleNotFoundError` al arrancar | El entorno virtual no está activo, o las dependencias se instalaron en el Python global. Actívalo y repite el paso 3. |
| `Activate.ps1 cannot be loaded` | Directiva de ejecución de PowerShell. Ver el paso 2. |
| `intent_category: DERIVACION_INCERTIDUMBRE` en todas las consultas | La base no tiene recibos. Corre el paso 6 apuntando a la misma `DATABASE_URL`. |
| `connection to server ... failed` | Cadena de conexión mal formada, contraseña incorrecta, o estás usando la conexión directa en vez del pooler. |
| `TypeError: connect() got an unexpected keyword argument 'check_same_thread'` | Versión antigua del código con una `DATABASE_URL` de PostgreSQL. Actualiza el repositorio. |
| Los cambios del `.env` no tienen efecto | `--reload` solo vigila archivos `.py`. Reinicia el servidor. |
| El puerto 8000 está ocupado | Arranca en otro puerto: `uvicorn app.main:app --reload --port 8001`. |

## Variables de entorno

| Variable | Descripción | Default |
|---|---|---|
| `DATABASE_URL` | Cadena de conexión SQLAlchemy. | `sqlite:///./lucia_brain.db` |
| `DEEPSEEK_API_KEY` | Key de DeepSeek para la generación de texto. | — |
| `USE_MOCK_LLM` | `True` usa un generador simulado en lugar de llamar a DeepSeek. | `True` |
| `SUPABASE_URL` | URL del proyecto (Project Settings → API). | — |
| `SUPABASE_KEY` | Clave de API. Usar la `service_role`, solo en el backend. | — |
| `USE_MOCK_RAG` | `True` devuelve contexto simulado sin consultar la base vectorial. | `True` |
| `EMBEDDING_PROVIDER` | `local` (384 dims, sin costo) u `openai` (1536 dims). | `local` |
| `EMBEDDING_MODEL` | Vacío usa el modelo por defecto del proveedor. | — |
| `OPENAI_API_KEY` | Solo si `EMBEDDING_PROVIDER=openai`. La key de DeepSeek no sirve aquí: su API no expone endpoint de embeddings. | — |
| `RAG_MATCH_THRESHOLD` | Similitud mínima (0–1) para aceptar un fragmento recuperado. | `0.5` |
| `RAG_MATCH_COUNT` | Cantidad de fragmentos a recuperar. | `3` |
| `WHATSAPP_TOKEN` | Access token de la app de Meta. | — |
| `WHATSAPP_PHONE_ID` | Phone number ID, no el número de teléfono. | — |
| `WHATSAPP_VERIFY_TOKEN` | Token que defines tú para validar el webhook. | `lucia_hackathon_secret` |
| `WHATSAPP_APP_SECRET` | App Secret de Meta. Habilita la verificación de firma de los eventos entrantes. | — |
| `WHATSAPP_API_VERSION` | Versión de la Graph API. Meta retira las antiguas periódicamente. | `v26.0` |
| `TELEGRAM_TOKEN` | Token del bot. Dejar sin definir si no se usa el canal. | — |

## Activar la búsqueda vectorial (RAG)

La capa de conocimiento cualitativo guarda las políticas de facturación como vectores y recupera las más relevantes para cada consulta. Es opcional: con `USE_MOCK_RAG=True` el sistema funciona sin ella.

### 1. Crear el esquema

En Supabase, abre **SQL Editor → New query**, pega el contenido completo de [`scripts/setup_supabase.sql`](./scripts/setup_supabase.sql) y ejecútalo. Crea la extensión `vector`, la tabla `documentos_politicas`, un índice HNSW para búsqueda por coseno, la función RPC `match_documentos` y habilita Row Level Security.

Comprueba que quedó creado:

```sql
select extname from pg_extension where extname = 'vector';
select proname from pg_proc where proname = 'match_documentos';
```

Ambas consultas deben devolver una fila.

> La dimensión del vector debe coincidir con el modelo de embeddings. El script viene en `VECTOR(384)`, que corresponde a `all-MiniLM-L6-v2`. Para usar OpenAI, cambia `384` por `1536` en los **dos** lugares donde aparece (la columna `embedding` y el parámetro `query_embedding` de la función) y recrea la tabla con `DROP TABLE documentos_politicas CASCADE;`, porque una columna `VECTOR` no se puede redimensionar.

### 2. Configurar credenciales

En **Project Settings → API**, copia el Project URL y la clave `service_role` a tu `.env`:

```env
SUPABASE_URL=https://<ref-del-proyecto>.supabase.co
SUPABASE_KEY=<service_role_key>
USE_MOCK_RAG=False
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=all-MiniLM-L6-v2
```

Con el proveedor `local` no hace falta ninguna key de embeddings: `fastembed` ejecuta el modelo en local con runtime ONNX y lo descarga (~80 MB) en la primera consulta, quedando cacheado.

> La clave `service_role` omite Row Level Security y da acceso total al proyecto. Va solo en el backend y nunca en el frontend. Con la clave `anon` las búsquedas devuelven cero resultados sin lanzar error, porque RLS bloquea la lectura.

### 3. Ingestar el corpus de políticas

```powershell
python scripts/ingest_supabase.py --dry-run   # valida los embeddings sin escribir nada
python scripts/ingest_supabase.py             # vectoriza e inserta
python scripts/ingest_supabase.py --reset     # borra los fragmentos previos de la misma fuente y reingesta
```

Conviene empezar por `--dry-run`: separa el problema de "puedo generar embeddings" del de "puedo escribir en la base". Debe informar 16 vectores de 384 dimensiones.

El corpus son 16 fragmentos de políticas que cubren los cinco escenarios más transparencia general. Las categorías coinciden con los eventos que detecta el motor determinista, lo que permite filtrar la búsqueda por evento.

Verifica la ingesta:

```sql
select categoria, count(*) from documentos_politicas group by categoria order by categoria;
```

### 4. Comprobar que está activo

Reinicia el servidor y haz una consulta de facturación. En los logs debe aparecer una línea con el prefijo `[RAG]`:

```
[RAG] 3 chunk(s) recuperados de Supabase.
```

Otros mensajes posibles y su significado:

| Log | Significado |
|---|---|
| `[RAG] N chunk(s) recuperados` | Funcionando. |
| `[RAG] Sin coincidencias sobre el umbral` | Conecta bien pero nada superó `RAG_MATCH_THRESHOLD`. Suele ser la clave `anon` bloqueada por RLS, o un umbral demasiado alto. |
| `[RAG] SUPABASE_URL/SUPABASE_KEY no configuradas` | Falta configuración, o no reiniciaste después de editar `.env`. |
| `[RAG] Proveedor de embeddings no disponible` | Con `openai`, falta `OPENAI_API_KEY`. |
| `[RAG ERROR] ...` | Error de red, clave inválida o el esquema SQL no se ejecutó. |
| Ninguna línea `[RAG]` | Sigue en `USE_MOCK_RAG=True`, o el caso coincidió con uno ya validado en `base_casos` y el RAG se omite a propósito. |

`retrieve_context()` nunca lanza excepción ni bloquea la conversación: ante cualquier fallo degrada a un bloque de políticas generales y deja el motivo en el log.

## Activar el modelo de lenguaje real

Sin configurar nada, el sistema usa un generador simulado que interpola los mismos datos deterministas verificados. Las respuestas son correctas pero plantilladas.

Para usar DeepSeek, obtén una key en [platform.deepseek.com](https://platform.deepseek.com) y en tu `.env`:

```env
DEEPSEEK_API_KEY=<tu-key>
USE_MOCK_LLM=False
```

Reinicia el servidor. Si la key es inválida o la API falla, el código cae automáticamente al generador simulado en lugar de devolver un error al usuario.

DeepSeek se consume a través de la interfaz compatible con OpenAI, y la salida se fuerza a un esquema Pydantic para que la respuesta siempre tenga la estructura esperada.

## API

### `POST /api/v1/chat`

`user_id` es la cuenta financiera del cliente (`FINANCIAL_ACCOUNT`).

```json
{
  "session_id": "demo-1",
  "user_id": "102917145",
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
    { "text": "Tu recibo subió S/ 16.58 porque...", "delay_ms": 1000, "type": "explanation" }
  ],
  "historical_bills_summary": [
    { "month": "2026-06", "amount": 66.32, "ciclo": "20260605" }
  ],
  "current_bill_breakdown": [
    {
      "categoria": "PLAN",
      "etiqueta": "cargo fijo de tu plan",
      "monto": 82.90,
      "conceptos": ["Plan Elige mas S/ 82.90"]
    }
  ],
  "variation_breakdown": [
    {
      "categoria": "DESCUENTO",
      "etiqueta": "descuentos aplicados",
      "monto_anterior": -16.58,
      "monto_actual": 0.0,
      "impacto": 16.58,
      "conceptos": ["Descuento 20% por 3 meses"]
    }
  ],
  "billing_adjustments": null,
  "upcoming_alerts": [],
  "plan_optimizer_suggestion": { "available": false },
  "confidence_score": 90,
  "caso_validado": false,
  "compliance_triggered": false
}
```

Los `messages` vienen troceados con un `delay_ms` cada uno, para que el cliente los muestre de forma escalonada en lugar de un bloque único.

`current_bill_breakdown` responde a "¿qué me están cobrando?" y `variation_breakdown` a "¿por qué cambió?". Ambos los calcula el motor determinista y el orquestador los sobrescribe después de generar el texto, así que el modelo no puede alterarlos. **La suma de los `impacto` de `variation_breakdown` equivale exactamente a la variación del recibo**, lo que hace que cada explicación sea auditable al céntimo. `conceptos` cita las descripciones literales de los cargos del recibo.

`billing_adjustments` aparece cuando el ciclo tuvo notas de crédito o débito, con el total de cada tipo.

`intent_category`, en turnos de facturación, es el evento detectado por el motor determinista, no una etiqueta generada por el modelo. Es un valor estable sobre el que se puede programar:

| Evento | Significado |
|---|---|
| `PRORRATEO_CAMBIO_PLAN` | Cobro proporcional por días de uso |
| `CUOTA_EQUIPO` | Cuota de un equipo financiado |
| `FIN_CUOTAS_EQUIPO` | Se pagó la última cuota y el cargo desapareció |
| `RECONEXION_MOROSIDAD` | Cargo por reconexión tras suspensión |
| `FIN_PROMOCION` | Un descuento o bono dejó de aplicarse |
| `NUEVO_DESCUENTO` | Se activó un descuento nuevo |
| `CAMBIO_PLAN` | Cambió el cargo recurrente del plan |
| `COMPRA_PAQUETE` | Paquetes o servicios adicionales |
| `TRAFICO_ADICIONAL` | Consumo fuera del plan, roaming o larga distancia |
| `NOTA_CREDITO_AJUSTE` | Ajuste por nota de crédito o débito |
| `REDUCCION_TARIFA` | Bajó el monto sin una causa más específica |
| `SIN_CAMBIOS` | El recibo no varió |
| `NUEVO_CLIENTE` | Solo hay un ciclo, no hay con qué comparar |
| `INCREMENTO_OTROS` | Subió sin causa atribuible: eleva la incertidumbre |

`caso_validado` es la señal visible del ciclo de aprendizaje: `true` si la respuesta reutilizó una solución ya aprobada en `base_casos` (el chat web la muestra como una insignia verde), `false` si se generó desde cero porque todavía no hay conocimiento validado para ese patrón (insignia ámbar). `confidence_score` sube de forma medible entre ambos casos — normalmente de 80% a 100% para el mismo tipo de consulta, una vez que un asesor valida el caso desde el panel de administración.

### Otros endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/api/v1/feedback` | Registra feedback inmediato o posterior sobre un caso. |
| `POST` | `/api/v1/followup/{caso_id}` | Genera el mensaje de seguimiento de un caso. |
| `GET` | `/api/v1/admin/cuarentena` | Casos pendientes de validación. |
| `POST` | `/api/v1/admin/validar/{caso_id}` | Promueve un caso a la base de conocimiento. |
| `GET` | `/api/v1/admin/base-casos` | Casos ya validados. |
| `GET` | `/api/v1/admin/handoff-queue` | Cola de atención humana. |
| `POST` | `/api/v1/admin/handoff-queue/{id}/atender` | Marca un caso como atendido. |
| `POST` | `/api/v1/admin/proactive-check` | Dispara el barrido de alertas proactivas. |

La documentación interactiva completa está en `/docs` mientras el servidor corre.

## Canales

### Web

Chat estático en `app/static/`, con un selector para alternar entre los clientes de prueba. La sesión se guarda en `localStorage`, así que al recargar o cerrar el navegador se retoma la misma conversación con su memoria. El botón **↻** de la cabecera inicia una conversación nueva.

### WhatsApp Cloud API

1. Crea una app en [Meta for Developers](https://developers.facebook.com/) y agrégale el producto WhatsApp. Meta provee un número de prueba gratuito.
2. En **WhatsApp → API Setup**, copia el access token, el Phone number ID y la versión de la API que muestra el ejemplo, y ponlos en tu `.env`.
3. Agrega tu número personal a la lista **To** de esa misma pantalla y confirma el código que recibas. El número de prueba solo conversa con destinatarios preautorizados.
4. Expón el servidor con HTTPS público. En local necesitas un túnel (ngrok o similar), porque Meta no puede alcanzar `127.0.0.1`.
5. En **Configuration → Webhook**, registra la Callback URL `https://<tu-dominio>/webhook/whatsapp` con el mismo valor que tengas en `WHATSAPP_VERIFY_TOKEN`.
6. En **Webhook fields**, suscríbete al campo `messages`. Sin esta suscripción la verificación pasa pero no llega ningún mensaje.

Puedes comprobar la verificación antes de configurarla en Meta:

```powershell
curl.exe "http://127.0.0.1:8000/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=lucia_hackathon_secret&hub.challenge=12345"
```

Debe devolver `12345` en texto plano. Si devuelve 403, el token no coincide.

El número entrante se resuelve contra la tabla `contactos_usuario` para atender a cada cliente con sus propios recibos. La comparación es por dígitos, con tolerancia de prefijo país por los últimos nueve. Si el número no está registrado se usa un cliente de respaldo, de modo que la conversación siga siendo coherente.

Los eventos entrantes se validan con la firma `X-Hub-Signature-256`, calculada sobre el cuerpo crudo del request. Si `WHATSAPP_APP_SECRET` no está definido, el webhook acepta el evento y lo advierte en el log.

### Telegram

Crea un bot con [`@BotFather`](https://t.me/BotFather), pon el token en `TELEGRAM_TOKEN` y, con el servidor corriendo, ejecuta el bot en otra terminal:

```powershell
python scripts/telegram_bot.py
```

Funciona por polling, así que no necesita webhook ni túnel.

## Memoria conversacional

La memoria vive en la tabla `historial_interacciones`, indexada por `session_id`, y persiste entre reinicios y despliegues:

- `historial_conversacion`: bitácora acotada a 12 turnos, para que no se repita una explicación ya dada.
- `comentarios_emocionales`: frases con carga emocional detectadas por expresiones regulares, con caducidad de 14 días, tope de cinco y marca de referenciado para no repetirlas indefinidamente.
- `score_sentimiento` y `estado_resolucion`: señales que alimentan el gatillo comercial.

Para comprobar que persiste de verdad, la prueba útil no es cerrar el navegador (podría estar en memoria del proceso) sino **reiniciar el servidor** y continuar la misma sesión. También puedes inspeccionar la fila directamente:

```sql
select session_id, comentarios_emocionales, historial_conversacion, score_sentimiento
from historial_interacciones order by updated_at desc;
```

El guardado y la recuperación son deterministas. Que el asistente *mencione* el comentario emocional en su redacción es una instrucción del prompt, y el modelo la cumple casi siempre pero no de forma garantizada.

## Panel de administración

En `/static/admin.html`, cuatro secciones:

- **Cola de Atención Humana**: turnos derivados, con el contexto ya empaquetado para que el agente no tenga que pedir al cliente que repita su caso.
- **Casos en Cuarentena**: consultas sin solución validada previa, esperando feedback.
- **Base de Conocimiento**: soluciones validadas y su nivel de reutilización.
- **Alertas Proactivas**: dispara el barrido de promociones por vencer.

## Despliegue

El proyecto corre como un servicio web estándar. El comando de arranque debe enlazar al puerto que asigne la plataforma:

```
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Las variables de entorno se configuran en el panel del proveedor, ya que `.env` no se sube al repositorio. En producción, `USE_MOCK_LLM` y `USE_MOCK_RAG` van en `False`, y `DATABASE_URL` debe apuntar a PostgreSQL.

Consideraciones en planes gratuitos con recursos acotados:

- **Disco efímero**: los datos operacionales deben ir a PostgreSQL, no a SQLite, o se pierden en cada reinicio.
- **Suspensión por inactividad**: la instancia puede tardar entre 30 y 60 segundos en despertar, y con embeddings locales la primera consulta además descarga el modelo. Conviene enviar una petición de calentamiento a `/health` antes de usarlo, o el primer evento de WhatsApp puede irse a timeout.
- **Memoria limitada**: medido con el modelo local cargado, el proceso ronda los 291 MB. Entra en un contenedor de 512 MB pero con margen acotado; si aparecen reinicios por memoria, `EMBEDDING_PROVIDER=openai` libera la mayor parte.

## Escenarios de prueba

Las cuentas no están escritas en el código: son cuentas financieras reales del dataset. Para saber cuál sirve para cada escenario:

```powershell
python scripts/verify_engine.py
```

Devuelve una cuenta de ejemplo por evento detectado. Con esa cuenta, desde el chat web:

| Escenario del reto | Evento | Mensaje |
|---|---|---|
| (a) Prorrateos | `PRORRATEO_CAMBIO_PLAN` | ¿Por qué me cobraron dos montos distintos? |
| (b) Cuota de equipo financiado | `CUOTA_EQUIPO` | ¿Qué es este cargo de cuota de equipo? |
| (c) Reconexión tras suspensión | `RECONEXION_MOROSIDAD` | ¿Por qué tengo un cargo de reconexión? |
| (d) Fin de descuentos | `FIN_PROMOCION` | ¿Por qué subió mi recibo este mes? |
| (e) Cambio de plan | `CAMBIO_PLAN` | ¿Por qué cambió el monto de mi plan? |
| Paquetes | `COMPRA_PAQUETE` | ¿Qué paquetes me están cobrando? |
| Consumo fuera del plan | `TRAFICO_ADICIONAL` | ¿Por qué me cobran consumo adicional? |
| Ajuste financiero | `NOTA_CREDITO_AJUSTE` | ¿Por qué bajó mi recibo este mes? |

Sobre la muestra cargada por defecto (1000 cuentas), la distribución de eventos es aproximadamente: 488 sin cambios, 120 prorrateos, 76 cambios de plan, 62 fin de promoción, 51 reconexiones, 50 reducciones de tarifa, 42 paquetes, 40 consumo adicional, 21 nuevos descuentos, 17 cuotas de equipo y 3 ajustes por nota de crédito.

Consultas que se responden con un dato verificado del recibo, sin pasar por el modelo:

- *"¿tengo deuda?"* → lee la columna `DEUDA` del recibo y su fecha de vencimiento. Si el dato no está, lo dice en lugar de deducir un saldo.
- *"¿qué plan tengo?"* → identifica el cargo de plan de mayor importe del ciclo.

Para ver las salvaguardas:

- *"quiero hablar con un asesor"* → deriva de inmediato y el caso aparece en la cola del panel.
- *"voy a denunciar esto"* → el pre-filtro de cumplimiento bloquea antes de llegar a la IA.
- *"gracias, quedó clarísimo"* → el sentimiento sube, pero la oferta comercial no aparece si el catálogo no tiene un plan que represente una mejora real. Es el blindaje anti-alucinación en acción.

Para la memoria emocional, frases que activan el detector: *"la verdad estoy cansado de que mi recibo suba todos los meses"*, *"siempre pasa lo mismo"*, *"sé que no es tu culpa"*.

Para ver el ciclo de aprendizaje (el diferenciador del producto) en vivo, sigue el guion detallado en [`docs/demo-diferenciador.md`](./docs/demo-diferenciador.md): una consulta nueva sale con insignia ámbar y 80% de confianza, y tras validarla desde el panel, la misma consulta repetida sale con insignia verde y 100%.

## Limitaciones conocidas

- **Sin autenticación del cliente**: no se verifica la identidad de quien escribe. Basta con enviar una cuenta financiera válida para ver su facturación, y en WhatsApp un número no registrado en `contactos_usuario` cae a una cuenta de demostración. Un flujo productivo debería pedir un documento de identidad más un segundo factor antes de exponer datos de facturación.
- **Sin autenticación de la API**: ningún endpoint la requiere, incluidos los de administración, y el CORS está abierto. Revisar antes de cualquier exposición pública estable.
- **Alertas y seguimientos manuales**: no hay planificador de tareas; se disparan por endpoint.
- **Corpus de políticas acotado**: la búsqueda vectorial es real, pero el corpus son los 29 fragmentos del script de ingesta, redactados a partir de los conceptos que aparecen en el dataset. Un caso productivo requeriría los manuales de facturación completos y calibrar el umbral con datos reales.
- **Sin filtro por categoría en el orquestador**: el retriever lo soporta, pero se consultan todas las categorías para no perder cobertura.
- **Migraciones manuales**: no se usa Alembic; las columnas nuevas se agregan en `run_lightweight_migrations()`.
- **`base_casos` empieza vacía**: el ciclo de aprendizaje requiere que un agente valide casos desde el panel antes de que exista conocimiento reutilizable.

### Límites que impone el dataset

Estas no son decisiones de diseño, son restricciones de los datos entregados. Se documentan porque determinan qué puede y qué no puede afirmar el sistema:

- **Sin fecha exacta de fin de promoción.** El dataset no trae esa columna. La duración pactada se lee de la descripción del cargo (`"por 6 M"`, `"x 12m"`) y se cruza con los ciclos ya facturados, así que las alertas se expresan en ciclos, no en días. `dias_restantes` viaja en `null` en lugar de rellenarse con un número inventado.
- **Capacidad del plan casi nunca declarada.** Solo 6 de los 159 planes con tarifa en el catálogo indican los GB incluidos en su descripción. Por eso la recomendación comercial tiene dos criterios: más capacidad cuando ambos planes la declaran, y menor tarifa dentro del mismo tipo de renta cuando no. Si ninguno es demostrable, no se ofrece nada.
- **`PERIOD_START_DATE` y `PERIOD_END_DATE` llegan corruptos** (literal `00:00.0`). Como referencia de período se usa `FECHA-VENCIMIENTO`, y si tampoco es válida, el mes de emisión del ciclo.
- **Acentos con mojibake en el CSV** (`Facturaci¾n`, `mßs`). El motor normaliza esos caracteres al clasificar para que la detección no dependa del encoding, pero las descripciones se citan tal como vienen.
- **`PRIMARY_RESOURCE_VALUE` no existe** en `FACTURACION_CLIENTES.csv` aunque el diccionario de datos lo documenta. No hay teléfono en facturación; el único identificador de línea es `SUBSCRIBER_KEY`, y el teléfono llega solo como hash en la planta.
- **Tres tablas documentadas sin datos.** El diccionario describe `BRAINY_DESCUENTOS_CUOTAS`, `BRAINY_PRORRATEO` y `BRAINY_RECONEXIONES`, pero no se entregaron sus CSV. Traían justo lo que aquí hay que derivar: duración y número de cuota actual, importe del prorrateo con su período, y fecha de corte y reconexión. Con esas tablas, las explicaciones de prorrateo, cuotas y reconexión podrían citar fechas exactas en vez de ciclos.
- **El diccionario dice `CDR` para nota de crédito, pero el dato real es `CRD`.** El código acepta ambos.

## Licencia

Sin licencia definida. Añade la que corresponda antes de distribuir o reutilizar el código.
