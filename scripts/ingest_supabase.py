"""
Ingesta de políticas de facturación a Supabase (pgvector).

Vectoriza el corpus de políticas de telecomunicaciones y lo inserta en la tabla
`documentos_politicas`, que es la que consulta `app/services/rag.py`.

REQUISITOS PREVIOS
  1. Haber ejecutado `scripts/setup_supabase.sql` en el Editor SQL de Supabase.
  2. Tener configuradas en `.env`:
       SUPABASE_URL, SUPABASE_KEY  (se recomienda la clave service_role)
       EMBEDDING_PROVIDER=openai + OPENAI_API_KEY   (1536 dimensiones)
       o EMBEDDING_PROVIDER=local                    (384 dimensiones)

USO
    python scripts/ingest_supabase.py               # ingesta incremental
    python scripts/ingest_supabase.py --reset       # borra los chunks de esta fuente y reingesta
    python scripts/ingest_supabase.py --dry-run     # vectoriza y valida, sin escribir en Supabase

El script es idempotente con `--reset`: elimina primero los registros de la
misma `fuente` para no acumular duplicados en cada corrida.
"""

import argparse
import os
import sys
from typing import Any, Dict, List

# Permite importar `app.*` al ejecutar el script directamente desde la raíz.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.services import embeddings as embeddings_service  # noqa: E402

FUENTE = "manual_politicas_v1"
TAMANO_LOTE = 20  # inserciones por request; suficiente para este corpus

# -----------------------------------------------------------------------------
# Corpus de políticas
# -----------------------------------------------------------------------------
# Las categorías coinciden a propósito con los `detected_event` que produce el
# motor determinista, para poder filtrar la búsqueda por el evento ya detectado.
# Cada entrada es un chunk autocontenido: se redacta para que tenga sentido leída
# de forma aislada, porque el retriever la puede devolver sin sus vecinas.

DOCUMENTOS: List[Dict[str, Any]] = [
    # --- PRORRATEO / CAMBIO DE PLAN -----------------------------------------
    {
        "categoria": "PRORRATEO_CAMBIO_PLAN",
        "contenido": (
            "Prorrateo por cambio de plan: cuando un cliente cambia de plan en medio "
            "del ciclo de facturación, el recibo no cobra el precio completo de ninguno "
            "de los dos planes. Se cobra la parte proporcional de cada plan según los "
            "días efectivamente usados. Por eso en el detalle del recibo aparecen dos "
            "cargos parciales en lugar de un único cargo fijo: uno corresponde a los días "
            "con el plan anterior y el otro a los días con el plan nuevo."
        ),
        "metadata": {"tema": "prorrateo", "escenario_reto": 1},
    },
    {
        "categoria": "PRORRATEO_CAMBIO_PLAN",
        "contenido": (
            "Cómo leer un recibo con prorrateo: la suma de los cargos parciales del mes "
            "de transición puede ser distinta tanto del recibo anterior como del que se "
            "emitirá el mes siguiente. El recibo del mes posterior al cambio ya refleja "
            "un único cargo fijo con la tarifa completa del plan nuevo, sin fracciones."
        ),
        "metadata": {"tema": "prorrateo", "escenario_reto": 1},
    },
    {
        "categoria": "PRORRATEO_CAMBIO_PLAN",
        "contenido": (
            "Cambio de plan: al migrar a un plan de mayor o menor valor, la nueva tarifa "
            "rige desde la fecha efectiva del cambio, no desde el inicio del ciclo. "
            "Los beneficios del plan nuevo (velocidad, servicios incluidos) se activan "
            "en la fecha del cambio. Un cambio de plan por sí solo no genera penalidades "
            "salvo que exista un compromiso de permanencia vigente informado al contratar."
        ),
        "metadata": {"tema": "cambio_plan", "escenario_reto": 5},
    },

    # --- REDUCCIÓN DE TARIFA -------------------------------------------------
    {
        "categoria": "REDUCCION_TARIFA",
        "contenido": (
            "Reducción del monto facturado: un recibo puede bajar respecto al mes previo "
            "porque terminó de pagarse la última cuota de un equipo financiado, porque se "
            "activó un nuevo descuento, porque se migró a un plan de menor tarifa o porque "
            "dejaron de facturarse consumos adicionales del periodo anterior. La baja "
            "también debe explicarse con el concepto concreto que dejó de cobrarse."
        ),
        "metadata": {"tema": "reduccion_tarifa"},
    },

    # --- FIN DE PROMOCIÓN / DESCUENTO ---------------------------------------
    {
        "categoria": "FIN_PROMOCION",
        "contenido": (
            "Fin de promoción o descuento: los descuentos promocionales tienen una "
            "duración definida y comunicada al momento de la contratación. Cuando la "
            "promoción termina, el descuento deja de aplicarse y el cargo fijo del plan "
            "vuelve a su precio regular de lista. No se trata de un aumento de tarifa ni "
            "de un cobro nuevo: es el precio regular que ya estaba informado, sin el "
            "descuento temporal que venía restándose."
        ),
        "metadata": {"tema": "fin_promocion", "escenario_reto": 4},
    },
    {
        "categoria": "FIN_PROMOCION",
        "contenido": (
            "Identificación del fin de un descuento en el recibo: en los recibos con "
            "promoción vigente aparece una línea de descuento con signo negativo que "
            "reduce el total. En el primer recibo posterior al vencimiento esa línea "
            "desaparece, mientras el cargo fijo se mantiene igual. La diferencia entre "
            "ambos recibos equivale exactamente al monto del descuento que ya no aplica."
        ),
        "metadata": {"tema": "fin_promocion", "escenario_reto": 4},
    },
    {
        "categoria": "FIN_PROMOCION",
        "contenido": (
            "Aviso previo al vencimiento de una promoción: cuando el sistema detecta que "
            "un descuento vigente está por terminar, corresponde informarlo de forma "
            "proactiva antes de que se emita el recibo afectado, indicando la fecha de "
            "término y el impacto estimado. El cliente puede entonces evaluar alternativas "
            "del catálogo vigente antes de que el cambio ocurra."
        ),
        "metadata": {"tema": "alerta_proactiva", "escenario_reto": 4},
    },

    # --- CUOTA DE EQUIPO FINANCIADO -----------------------------------------
    {
        "categoria": "CUOTA_EQUIPO",
        "contenido": (
            "Cuota de equipo financiado: cuando el cliente adquiere un equipo (router, "
            "repetidor, decodificador u otro dispositivo) en modalidad financiada, el "
            "precio total se divide en un número fijo de cuotas mensuales que se cargan "
            "al recibo del servicio. La cuota es independiente del cargo fijo del plan y "
            "aparece como un concepto separado en el detalle del recibo."
        ),
        "metadata": {"tema": "cuota_equipo", "escenario_reto": 2},
    },
    {
        "categoria": "CUOTA_EQUIPO",
        "contenido": (
            "Duración de las cuotas de equipo: la cuota se cobra únicamente durante el "
            "número de meses pactado (por ejemplo, 6 o 12 cuotas). Una vez pagada la "
            "última cuota, el concepto desaparece del recibo y el monto total baja de "
            "forma automática, sin necesidad de que el cliente realice ningún trámite. "
            "El detalle del recibo suele indicar el número de cuota actual sobre el total."
        ),
        "metadata": {"tema": "cuota_equipo", "escenario_reto": 2},
    },

    # --- RECONEXIÓN POR MOROSIDAD -------------------------------------------
    {
        "categoria": "RECONEXION_MOROSIDAD",
        "contenido": (
            "Cargo de reconexión: cuando el servicio ha sido suspendido por falta de pago "
            "y luego se restablece, se aplica un cargo único de reconexión que cubre la "
            "reactivación del servicio. Es un cargo por evento, no recurrente: aparece "
            "solo en el recibo posterior a la reactivación y no se repite en los "
            "siguientes ciclos si el cliente mantiene sus pagos al día."
        ),
        "metadata": {"tema": "reconexion", "escenario_reto": 3},
    },
    {
        "categoria": "RECONEXION_MOROSIDAD",
        "contenido": (
            "Suspensión por deuda pendiente: la suspensión del servicio ocurre tras el "
            "vencimiento del plazo de pago informado. Durante la suspensión el cargo fijo "
            "del plan puede seguir devengándose según las condiciones del contrato. "
            "En situaciones de deuda pendiente o reconexión no corresponde ofrecer "
            "productos adicionales ni cambios de plan comerciales: la prioridad es "
            "explicar con claridad el estado de cuenta y las opciones de regularización."
        ),
        "metadata": {"tema": "reconexion", "escenario_reto": 3},
    },

    # --- POLÍTICAS GENERALES ------------------------------------------------
    {
        "categoria": "GENERAL",
        "contenido": (
            "Principio de transparencia en facturación: toda variación en el monto de un "
            "recibo debe poder explicarse con al menos un concepto facturado concreto y "
            "verificable. Si no existe un concepto que justifique la diferencia, la "
            "consulta debe escalarse a revisión humana en lugar de ofrecer una explicación "
            "estimada o aproximada al cliente."
        ),
        "metadata": {"tema": "transparencia"},
    },
    {
        "categoria": "GENERAL",
        "contenido": (
            "Composición de un recibo: el monto total resulta de la suma del cargo fijo "
            "del plan, más cargos variables o por evento (consumos adicionales, "
            "reconexión, cuotas de equipo), menos descuentos vigentes. Cualquier "
            "explicación de una variación debe apoyarse en el detalle de conceptos "
            "facturados del periodo, comparando contra el mismo detalle del periodo previo."
        ),
        "metadata": {"tema": "composicion_recibo"},
    },
    {
        "categoria": "GENERAL",
        "contenido": (
            "Ciclo de facturación: el recibo cubre un periodo mensual definido. Los "
            "cambios solicitados dentro del ciclo (cambio de plan, activación de "
            "servicios, adquisición de equipos) se reflejan en el recibo correspondiente "
            "al periodo en que ocurrieron, y pueden generar montos parciales en ese primer "
            "recibo antes de estabilizarse en el ciclo siguiente."
        ),
        "metadata": {"tema": "ciclo_facturacion"},
    },
    {
        "categoria": "GENERAL",
        "contenido": (
            "Moneda y formato: los montos de facturación se expresan en soles peruanos "
            "(PEN) con el símbolo S/ y dos decimales, usando punto como separador decimal. "
            "Ejemplo de formato correcto: S/ 119.90."
        ),
        "metadata": {"tema": "moneda"},
    },
    {
        "categoria": "GENERAL",
        "contenido": (
            "Derivación a atención humana: corresponde derivar la consulta a un asesor "
            "cuando el cliente lo solicita explícitamente, cuando no hay datos suficientes "
            "para explicar una variación con certeza, o cuando la conversación involucra "
            "reclamos formales o riesgo legal. La derivación debe incluir el contexto ya "
            "recopilado para que el cliente no tenga que repetir su caso desde cero."
        ),
        "metadata": {"tema": "derivacion"},
    },
]


def _crear_cliente():
    """Crea el cliente de Supabase validando la configuración antes."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        print("[ERROR] Falta configuración: define SUPABASE_URL y SUPABASE_KEY en tu archivo .env")
        sys.exit(1)

    try:
        from supabase import create_client
    except ImportError:
        print("[ERROR] La librería 'supabase' no está instalada. Ejecuta: pip install -r requirements.txt")
        sys.exit(1)

    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


def _validar_embeddings():
    """Falla temprano y con un mensaje claro si no se pueden generar embeddings."""
    if not embeddings_service.embeddings_disponibles():
        proveedor = embeddings_service.proveedor_activo()
        print(f"[ERROR] El proveedor de embeddings '{proveedor}' no está disponible.")
        if proveedor == embeddings_service.PROVEEDOR_OPENAI:
            print("        Configura OPENAI_API_KEY en .env (DeepSeek no ofrece embeddings),")
            print("        o cambia a EMBEDDING_PROVIDER=local.")
        else:
            print("        Instala el modelo local con: pip install sentence-transformers")
        sys.exit(1)


def _lotes(items: List[Any], tamano: int):
    for i in range(0, len(items), tamano):
        yield items[i:i + tamano]


def ingest(reset: bool = False, dry_run: bool = False) -> int:
    """
    Vectoriza e inserta el corpus. Retorna la cantidad de registros insertados.
    """
    print("=" * 70)
    print("  Ingesta de políticas de facturación a Supabase (pgvector)")
    print("=" * 70)

    _validar_embeddings()
    print(f"Embeddings: {embeddings_service.describir_configuracion()}")

    dim_esperada = embeddings_service.dimension_esperada()
    print(f"Documentos a procesar: {len(DOCUMENTOS)}")
    print(f"Fuente: {FUENTE}")
    print("-" * 70)

    # 1. Vectorización (en lote: una sola llamada al proveedor)
    contenidos = [doc["contenido"] for doc in DOCUMENTOS]
    print("Generando embeddings...")
    try:
        vectores = embeddings_service.embed_documents(contenidos)
    except Exception as e:
        print(f"[ERROR] No se pudieron generar los embeddings: {e}")
        sys.exit(1)

    if len(vectores) != len(DOCUMENTOS):
        print(f"[ERROR] Se esperaban {len(DOCUMENTOS)} vectores y se obtuvieron {len(vectores)}.")
        sys.exit(1)

    dim_real = len(vectores[0])
    print(f"Embeddings generados: {len(vectores)} vectores de {dim_real} dimensiones")

    if dim_esperada and dim_real != dim_esperada:
        print(f"[AVISO] La dimensión real ({dim_real}) no coincide con la esperada ({dim_esperada}).")
    print(
        f"[RECORDATORIO] La columna 'embedding' en Supabase debe estar declarada como "
        f"VECTOR({dim_real}) o la inserción fallará."
    )

    # 2. Armado de filas
    filas = [
        {
            "contenido": doc["contenido"],
            "categoria": doc.get("categoria", "GENERAL"),
            "fuente": FUENTE,
            "embedding": vector,
            "metadata": doc.get("metadata", {}),
        }
        for doc, vector in zip(DOCUMENTOS, vectores)
    ]

    if dry_run:
        print("-" * 70)
        print("[DRY RUN] No se escribió nada en Supabase. Resumen por categoría:")
        _imprimir_resumen_categorias(filas)
        return 0

    # 3. Escritura en Supabase
    client = _crear_cliente()

    if reset:
        print(f"Eliminando registros previos de la fuente '{FUENTE}'...")
        try:
            client.table("documentos_politicas").delete().eq("fuente", FUENTE).execute()
            print("Registros previos eliminados.")
        except Exception as e:
            print(f"[ERROR] No se pudieron eliminar los registros previos: {e}")
            sys.exit(1)

    insertados = 0
    for numero_lote, lote in enumerate(_lotes(filas, TAMANO_LOTE), start=1):
        try:
            respuesta = client.table("documentos_politicas").insert(lote).execute()
            cantidad = len(respuesta.data or [])
            insertados += cantidad
            print(f"  Lote {numero_lote}: {cantidad} registro(s) insertado(s)")
        except Exception as e:
            print(f"[ERROR] Falló la inserción del lote {numero_lote}: {e}")
            print("        Revisa que hayas ejecutado scripts/setup_supabase.sql y que")
            print("        la dimensión del vector coincida con la de la tabla.")
            sys.exit(1)

    print("-" * 70)
    print(f"Ingesta completada: {insertados}/{len(filas)} registros insertados en 'documentos_politicas'.")
    _imprimir_resumen_categorias(filas)
    print("=" * 70)
    print("Siguiente paso: pon USE_MOCK_RAG=False en tu .env para activar el RAG real.")
    return insertados


def _imprimir_resumen_categorias(filas: List[Dict[str, Any]]):
    conteo: Dict[str, int] = {}
    for fila in filas:
        conteo[fila["categoria"]] = conteo.get(fila["categoria"], 0) + 1
    for categoria in sorted(conteo):
        print(f"  - {categoria}: {conteo[categoria]} chunk(s)")


def main():
    parser = argparse.ArgumentParser(
        description="Ingesta de políticas de facturación a Supabase (pgvector)."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Elimina los registros previos de esta misma fuente antes de insertar.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Genera y valida los embeddings sin escribir nada en Supabase.",
    )
    args = parser.parse_args()

    ingest(reset=args.reset, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
