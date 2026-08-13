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

# v2: corpus ampliado para cubrir todos los eventos que produce el motor
# determinista sobre el dataset real (cambio de plan, paquetes, consumo
# adicional, notas de crédito/débito, nuevos descuentos y fin de cuotas).
FUENTE = "manual_politicas_v2"
# Prefijo común a todas las versiones del corpus. Se usa en --reset para no
# dejar chunks de una versión anterior compitiendo en la búsqueda semántica.
PREFIJO_FUENTE = "manual_politicas"
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

    # --- CAMBIO DE PLAN (sin prorrateo como causa principal) -----------------
    {
        "categoria": "CAMBIO_PLAN",
        "contenido": (
            "Cambio del cargo recurrente del plan: cuando el concepto de plan facturado "
            "cambia de un ciclo a otro, el recibo refleja la tarifa del plan nuevo. El "
            "detalle del recibo muestra la descripción del plan vigente en cada periodo, "
            "así que la diferencia se puede verificar comparando ambas descripciones y sus "
            "importes. Si el cambio ocurrió a mitad de ciclo, además aparecerán cargos "
            "proporcionales; si ocurrió al inicio del ciclo, solo cambia el cargo fijo."
        ),
        "metadata": {"tema": "cambio_plan", "escenario_reto": 5},
    },
    {
        "categoria": "CAMBIO_PLAN",
        "contenido": (
            "Renta adelantada y renta vencida: el catálogo de ofertas indica el tipo de "
            "renta de cada plan. En renta adelantada el ciclo se cobra antes de consumirlo; "
            "en renta vencida se cobra después de consumirlo. Al migrar entre planes con "
            "tipos de renta distintos, el primer recibo puede incluir conceptos de ambos "
            "esquemas, lo que explica que el monto de ese ciclo no coincida con la tarifa "
            "de lista de ninguno de los dos planes."
        ),
        "metadata": {"tema": "tipo_renta", "escenario_reto": 5},
    },

    # --- PAQUETES Y SERVICIOS ADICIONALES ------------------------------------
    {
        "categoria": "COMPRA_PAQUETE",
        "contenido": (
            "Paquetes y servicios adicionales: los paquetes de datos, bloques de canales y "
            "servicios de valor agregado se facturan aparte del cargo fijo del plan. Un "
            "paquete de un solo uso aparece únicamente en el recibo del ciclo en que se "
            "compró; un paquete recurrente se repite cada ciclo hasta que se desactiva. "
            "Cada paquete figura como una línea propia en el detalle del recibo."
        ),
        "metadata": {"tema": "paquetes"},
    },
    {
        "categoria": "COMPRA_PAQUETE",
        "contenido": (
            "Alquiler de equipos y puntos adicionales: los puntos adicionales de televisión, "
            "repetidores de señal y otros equipos en alquiler generan un cargo recurrente "
            "mientras el servicio esté activo. No son cuotas de financiamiento: no tienen un "
            "número definido de pagos y se dejan de cobrar cuando se solicita la baja del "
            "servicio adicional."
        ),
        "metadata": {"tema": "paquetes"},
    },

    # --- CONSUMO FUERA DEL PLAN ----------------------------------------------
    {
        "categoria": "TRAFICO_ADICIONAL",
        "contenido": (
            "Consumo adicional fuera del plan: cuando se supera lo incluido en el plan o se "
            "usa un servicio no comprendido en él, se factura como consumo adicional. Se "
            "cobra por uso efectivo del periodo, así que varía de un ciclo a otro y no es un "
            "cargo recurrente. Aparece en el detalle del recibo con el tipo de consumo que "
            "lo originó."
        ),
        "metadata": {"tema": "trafico_adicional"},
    },
    {
        "categoria": "TRAFICO_ADICIONAL",
        "contenido": (
            "Roaming internacional y larga distancia: el uso del servicio en el extranjero y "
            "las llamadas de larga distancia se facturan según el destino y el consumo del "
            "periodo, salvo que exista un paquete específico que los cubra. Al ser cargos "
            "por uso, solo aparecen en los recibos de los ciclos en que hubo consumo y no se "
            "repiten en los siguientes."
        ),
        "metadata": {"tema": "roaming"},
    },

    # --- NOTAS DE CRÉDITO Y DÉBITO ------------------------------------------
    {
        "categoria": "NOTA_CREDITO_AJUSTE",
        "contenido": (
            "Notas de crédito: una nota de crédito es un ajuste que reduce el monto "
            "facturado. Se emite para corregir un cobro que no correspondía, aplicar una "
            "compensación acordada o reversar un concepto ya facturado. Se asocia al ciclo "
            "de facturación en que se emitió y al concepto que corrige, por lo que su efecto "
            "se ve reflejado en el recibo de ese ciclo."
        ),
        "metadata": {"tema": "notas_credito"},
    },
    {
        "categoria": "NOTA_CREDITO_AJUSTE",
        "contenido": (
            "Notas de débito: una nota de débito es un ajuste que incrementa el monto "
            "facturado, y se emite cuando un concepto quedó sin cobrar o se cobró por debajo "
            "de lo que correspondía. Tanto las notas de crédito como las de débito responden "
            "a exigencias contables y tributarias: cada ajuste queda documentado con su "
            "fecha efectiva y su importe, y se puede verificar contra el recibo del ciclo."
        ),
        "metadata": {"tema": "notas_debito"},
    },

    # --- NUEVOS DESCUENTOS Y FIN DE CUOTAS ----------------------------------
    {
        "categoria": "NUEVO_DESCUENTO",
        "contenido": (
            "Nuevo descuento o bonificación aplicada: cuando se activa un descuento de "
            "fidelización, retención o captación, aparece en el recibo como una línea con "
            "importe negativo que reduce el total. La descripción del concepto suele indicar "
            "la duración pactada del beneficio, por ejemplo un porcentaje o un monto fijo "
            "durante un número determinado de meses."
        ),
        "metadata": {"tema": "nuevo_descuento"},
    },
    {
        "categoria": "FIN_CUOTAS_EQUIPO",
        "contenido": (
            "Fin del financiamiento de un equipo: al pagarse la última cuota pactada, el "
            "concepto de financiamiento desaparece del recibo y el monto total baja sin que "
            "el cliente tenga que hacer ningún trámite. Esta bajada es esperada y definitiva: "
            "el cargo no reaparecerá en los ciclos siguientes salvo que se adquiera un nuevo "
            "equipo financiado."
        ),
        "metadata": {"tema": "cuota_equipo", "escenario_reto": 2},
    },

    # --- POLÍTICAS GENERALES ------------------------------------------------
    {
        "categoria": "GENERAL",
        "contenido": (
            "Lectura del detalle de un recibo por grupos de cargo: el detalle agrupa los "
            "conceptos según su naturaleza (cargo fijo del plan, cargo proporcional por días "
            "de uso, cargo por reconexión, paquetes, consumo adicional, bonos y descuentos). "
            "Comparar el mismo grupo entre dos recibos consecutivos permite aislar qué parte "
            "del recibo cambió, en lugar de comparar únicamente el monto total."
        ),
        "metadata": {"tema": "composicion_recibo"},
    },
    {
        "categoria": "GENERAL",
        "contenido": (
            "Bonos con cargo y contrapartida: algunos beneficios se registran en el recibo "
            "con dos líneas, una positiva por el valor referencial del bono y otra negativa "
            "que lo compensa. El efecto neto de ese par de líneas es lo que realmente afecta "
            "al monto a pagar, así que la explicación debe basarse en el neto y no en la "
            "línea positiva leída de forma aislada."
        ),
        "metadata": {"tema": "composicion_recibo"},
    },
    {
        "categoria": "GENERAL",
        "contenido": (
            "Estado de deuda del recibo: el recibo informa explícitamente si la cuenta "
            "registra deuda pendiente y su fecha de vencimiento. Cuando ese dato no está "
            "disponible, corresponde declararlo así al cliente en lugar de deducir un saldo: "
            "afirmar que no hay deuda sin el dato verificado es tan incorrecto como inventar "
            "un monto."
        ),
        "metadata": {"tema": "deuda"},
    },
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
        # Se eliminan TODAS las versiones de este corpus, no solo la actual.
        # Borrar únicamente los registros de FUENTE dejaría vivos los chunks de
        # versiones anteriores (manual_politicas_v1), que seguirían compitiendo
        # en la búsqueda por similitud con textos ya desactualizados.
        print(f"Eliminando versiones previas del corpus (prefijo '{PREFIJO_FUENTE}')...")
        try:
            respuesta = (
                client.table("documentos_politicas")
                .delete()
                .like("fuente", f"{PREFIJO_FUENTE}%")
                .execute()
            )
            print(f"Registros previos eliminados: {len(respuesta.data or [])}")
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
