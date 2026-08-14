"""
Motor determinista de explicación de facturación.

Toda cifra que Lucía comunica nace aquí, nunca en el LLM. El modelo solo redacta
sobre los hechos que este módulo ya calculó y verificó contra la base de datos.

Fuente de datos: el dataset real del desafío (carpeta `disclaimer/`), ingerido
por `scripts/ingest_real_data.py`. Las constantes de clasificación de este
módulo NO son inventadas: son los valores literales que aparecen en las columnas
GRUPO, SUB_GRUPO y CHARGE_CODE_CLASSIFICATION de FACTURACION_CLIENTES.csv.
"""

import re
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from app.db import crud
from datetime import datetime

# La moneda es un hecho determinista, no una inferencia del LLM.
# Sin este dato explícito el modelo puede alucinar símbolos (€, $) al redactar.
MONEDA_CODIGO = "PEN"
MONEDA_SIMBOLO = "S/"


# 1. Pre-Filtro de Cumplimiento
def validate_compliance(message: str, db: Session) -> Optional[str]:
    """
    Evalúa el mensaje del usuario contra los patrones regex de cumplimiento.
    Retorna un mensaje de bloqueo si se dispara una regla, de lo contrario None.
    """
    terminos = crud.get_terminos_restringidos(db)
    for regla in terminos:
        if re.search(regla.patron_regex, message, re.IGNORECASE):
            return regla.mensaje_bloqueo
    return None


# ---------------------------------------------------------------------------
# 2. Taxonomía de cargos (derivada de los valores reales del dataset)
# ---------------------------------------------------------------------------
#
# El brief dimensiona el problema con "recibo actual + 5 recibos previos"
# (lo mismo que ya muestra la App Mi Movistar, pero sin explicarlo). Se trae
# ese mismo horizonte para poder comparar contra el ciclo inmediato anterior
# (causa puntual) y también detectar patrones recurrentes en la ventana de
# 5 ciclos (p. ej. "esto ya pasó 3 veces").
HORIZONTE_RECIBOS = 6  # recibo actual + hasta 5 previos

# Cada cargo del recibo se asigna a EXACTAMENTE UNA de estas categorías. La
# partición es exhaustiva a propósito: así la suma de las variaciones por
# categoría reproduce exactamente la variación total del recibo, y cualquier
# explicación queda respaldada por una descomposición que cuadra al céntimo.
CAT_PLAN = "PLAN"
CAT_PRORRATEO = "PRORRATEO"
CAT_RECONEXION = "RECONEXION"
CAT_FINANCIAMIENTO = "FINANCIAMIENTO"
CAT_PAQUETES = "PAQUETES"
CAT_TRAFICO = "TRAFICO"
CAT_DESCUENTO = "DESCUENTO"
CAT_BONO = "BONO"
CAT_OTROS = "OTROS"

# Etiquetas en lenguaje cliente, alineadas al vocabulario de la atención humana
# Movistar que pide el desafío (prorrateos, reconexiones, etc.).
ETIQUETA_CATEGORIA = {
    CAT_PLAN: "cargo fijo de tu plan",
    CAT_PRORRATEO: "cobro proporcional por días de uso (prorrateo)",
    CAT_RECONEXION: "cargo por reconexión del servicio",
    CAT_FINANCIAMIENTO: "cuota de equipo financiado",
    CAT_PAQUETES: "paquetes y servicios adicionales",
    CAT_TRAFICO: "consumo adicional fuera de tu plan",
    CAT_DESCUENTO: "descuentos aplicados",
    CAT_BONO: "bonos y bonificaciones",
    CAT_OTROS: "otros cargos",
}

# Evento que se reporta cuando la categoría es la causa principal de la subida.
EVENTO_POR_CATEGORIA_SUBIDA = {
    CAT_RECONEXION: "RECONEXION_MOROSIDAD",
    CAT_PRORRATEO: "PRORRATEO_CAMBIO_PLAN",
    CAT_FINANCIAMIENTO: "CUOTA_EQUIPO",
    CAT_PAQUETES: "COMPRA_PAQUETE",
    CAT_TRAFICO: "TRAFICO_ADICIONAL",
    CAT_DESCUENTO: "FIN_PROMOCION",
    CAT_BONO: "FIN_PROMOCION",
    CAT_PLAN: "CAMBIO_PLAN",
    CAT_OTROS: "INCREMENTO_OTROS",
}

# Evento que se reporta cuando la categoría es la causa principal de la bajada.
EVENTO_POR_CATEGORIA_BAJADA = {
    CAT_DESCUENTO: "NUEVO_DESCUENTO",
    CAT_BONO: "NUEVO_DESCUENTO",
    CAT_FINANCIAMIENTO: "FIN_CUOTAS_EQUIPO",
    CAT_PLAN: "REDUCCION_TARIFA",
    CAT_PRORRATEO: "PRORRATEO_CAMBIO_PLAN",
    CAT_PAQUETES: "REDUCCION_TARIFA",
    CAT_TRAFICO: "REDUCCION_TARIFA",
    CAT_RECONEXION: "REDUCCION_TARIFA",
    CAT_OTROS: "REDUCCION_TARIFA",
}

# --- Valores literales del dataset (FACTURACION_CLIENTES.csv) ---

_CLASIF_FINANCIAMIENTO = {"cargo unico financiamiento"}
_SUBGRUPOS_FINANCIAMIENTO = {
    "FINANCIAMIENTO",
    "EQUIPOS",
    "CARGA EXTERNA - DESCUENTO FINANCIAMIENTO",
}

_CLASIF_PAQUETES = {"paquete fija", "cargo unico paquete", "cargo recurrente paquete"}

_CLASIF_TRAFICO = {
    "cargo de uso_fija",
    "cargo de uso exceso",
    "cargo de uso oldi",
    "cargo unico oldi",
}
_GRUPOS_TRAFICO = {"TRAFICO ADICIONAL", "ROAMING"}

_CLASIF_DESCUENTO = {"descuento cargo recurrente", "descuento fija"}
_GRUPOS_DESCUENTO = {"DESCUENTO CARGO RECURRENTE"}

_CLASIF_BONO = {
    "bono recurrente cargo",
    "bono recurrente negativo",
    "bono one shot cargo",
    "bono one shot negativo",
    "bonificacion / gratuidad fija",
}

_CLASIF_PLAN = {
    "cargo recurrente de plan",
    "cargo recurrente de plan neg",
    "plan_fija",
    "cargo recurrente corp de plan",
}
_GRUPOS_PLAN = {"CARGO FIJO", "CARGO FIJO VENCIDO"}


def _normalizar(valor: Any) -> str:
    """Normaliza texto del CSV para comparar sin acentos raros ni mayúsculas."""
    texto = str(valor or "").strip().lower()
    # El CSV fuente llega con mojibake en algunos acentos ("Facturaci¾n",
    # "bonificaci¾n"). Se normalizan los caracteres que sí aparecen en los
    # valores de clasificación para que el matching no dependa del encoding.
    for original, reemplazo in (("¾", "o"), ("ß", "a"), ("ú", "u"), ("Ú", "e"), ("í", "i"), ("±", "n")):
        texto = texto.replace(original, reemplazo)
    return texto


def clasificar_cargo(cargo: Dict[str, Any]) -> str:
    """
    Asigna un cargo a exactamente una categoría, usando los valores reales de
    GRUPO, SUB_GRUPO y CHARGE_CODE_CLASSIFICATION.

    El orden de evaluación importa: las señales más específicas e inequívocas
    (reconexión, prorrateo, financiamiento) se evalúan antes que las genéricas
    (cargo fijo de plan), porque un cargo proporcional también pertenece al
    grupo de cargo fijo y se clasificaría mal si se evaluara al revés.
    """
    grupo = str(cargo.get("GRUPO") or "").strip().upper()
    sub_grupo = str(cargo.get("SUB_GRUPO") or "").strip().upper()
    clasificacion = _normalizar(cargo.get("CHARGE_CODE_CLASSIFICATION"))
    descripcion = _normalizar(cargo.get("CHARGE_CODE_DESC"))

    # 1. Reconexión: grupo dedicado en el dataset, más la descripción del cargo
    #    único equivalente en productos fijos ("Reconexión Mono Internet").
    if "RECONEXION" in grupo or "RECONEXION" in sub_grupo or "reconexion" in descripcion:
        return CAT_RECONEXION

    # 2. Prorrateo: el dataset lo marca explícitamente en el GRUPO
    #    ("CARGO FIJO PROPORCIONAL", "CARGO FIJO PROPORCIONAL VENCIDO").
    if "PROPORCIONAL" in grupo or "PROPORCIONAL" in sub_grupo:
        return CAT_PRORRATEO

    # 3. Equipo financiado
    if clasificacion in _CLASIF_FINANCIAMIENTO or sub_grupo in _SUBGRUPOS_FINANCIAMIENTO:
        return CAT_FINANCIAMIENTO

    # 4. Consumo fuera del plan (tráfico adicional, roaming, larga distancia)
    if grupo in _GRUPOS_TRAFICO or sub_grupo in _GRUPOS_TRAFICO or clasificacion in _CLASIF_TRAFICO:
        return CAT_TRAFICO

    # 5. Descuentos explícitos
    if grupo in _GRUPOS_DESCUENTO or clasificacion in _CLASIF_DESCUENTO:
        return CAT_DESCUENTO

    # 6. Bonos y bonificaciones. En el dataset vienen apareados (un cargo
    #    positivo y su contrapartida negativa), por eso importa el NETO.
    if clasificacion in _CLASIF_BONO or sub_grupo == "BONO":
        return CAT_BONO

    # 7. Paquetes y SVA
    if grupo == "PAQUETES" or clasificacion in _CLASIF_PAQUETES:
        return CAT_PAQUETES

    # 8. Cargo fijo del plan principal
    if clasificacion in _CLASIF_PLAN or grupo in _GRUPOS_PLAN:
        return CAT_PLAN

    return CAT_OTROS


# ---------------------------------------------------------------------------
# 3. Descomposición de la variación
# ---------------------------------------------------------------------------

def _extraer_cargos(conceptos_facturados: Optional[Dict]) -> List[Dict[str, Any]]:
    """Extrae la lista de cargos individuales del recibo virtual."""
    if not conceptos_facturados:
        return []
    cargos = conceptos_facturados.get("cargos")
    return cargos if isinstance(cargos, list) else []


def _monto(cargo: Dict[str, Any]) -> float:
    try:
        return float(cargo.get("CHARGE_TOTAL_AMOUNT") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _totales_por_categoria(cargos: List[Dict[str, Any]]) -> Dict[str, float]:
    """Suma el importe facturado de cada categoría."""
    totales: Dict[str, float] = {}
    for cargo in cargos:
        categoria = clasificar_cargo(cargo)
        totales[categoria] = round(totales.get(categoria, 0.0) + _monto(cargo), 2)
    return totales


def _descripciones_categoria(cargos: List[Dict[str, Any]], categoria: str, maximo: int = 3) -> List[str]:
    """Descripciones reales de los cargos de una categoría, para citar evidencia."""
    descripciones = []
    for cargo in cargos:
        if clasificar_cargo(cargo) != categoria:
            continue
        desc = str(cargo.get("CHARGE_CODE_DESC") or "").strip()
        if desc and desc not in descripciones:
            descripciones.append(desc)
        if len(descripciones) >= maximo:
            break
    return descripciones


def descomponer_variacion(
    cargos_actuales: List[Dict[str, Any]],
    cargos_pasados: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Calcula, categoría por categoría, cuánto aportó cada una a la variación
    del recibo. Devuelve la lista ordenada por impacto absoluto descendente.

    Esta descomposición es la base de la explicación: la suma de los `impacto`
    de todas las categorías equivale exactamente a la variación total del
    recibo, así que ninguna explicación necesita estimar ni redondear nada.
    """
    totales_actuales = _totales_por_categoria(cargos_actuales)
    totales_pasados = _totales_por_categoria(cargos_pasados)

    categorias = set(totales_actuales) | set(totales_pasados)
    componentes = []
    for categoria in categorias:
        actual = totales_actuales.get(categoria, 0.0)
        pasado = totales_pasados.get(categoria, 0.0)
        impacto = round(actual - pasado, 2)
        if abs(impacto) < 0.01:
            continue
        componentes.append({
            "categoria": categoria,
            "etiqueta": ETIQUETA_CATEGORIA.get(categoria, categoria),
            "monto_actual": actual,
            "monto_anterior": pasado,
            "impacto": impacto,
            "conceptos": _descripciones_categoria(
                cargos_actuales if abs(actual) >= abs(pasado) else cargos_pasados,
                categoria,
            ),
        })

    componentes.sort(key=lambda c: abs(c["impacto"]), reverse=True)
    return componentes


def _evidencia_componente(componente: Dict[str, Any]) -> str:
    """Redacta una línea de evidencia verificable para un componente."""
    impacto = componente["impacto"]
    signo = "+" if impacto > 0 else "-"
    etiqueta = componente["etiqueta"]
    conceptos = componente.get("conceptos") or []
    detalle = f" ({', '.join(conceptos)})" if conceptos else ""

    if componente["categoria"] in (CAT_DESCUENTO, CAT_BONO) and impacto > 0:
        return (
            f"{signo}{MONEDA_SIMBOLO} {abs(impacto):.2f} porque los {etiqueta} bajaron de "
            f"{MONEDA_SIMBOLO} {abs(componente['monto_anterior']):.2f} a "
            f"{MONEDA_SIMBOLO} {abs(componente['monto_actual']):.2f}{detalle}."
        )

    return (
        f"{signo}{MONEDA_SIMBOLO} {abs(impacto):.2f} en {etiqueta}: pasó de "
        f"{MONEDA_SIMBOLO} {componente['monto_anterior']:.2f} a "
        f"{MONEDA_SIMBOLO} {componente['monto_actual']:.2f}{detalle}."
    )


def _detectar_evento(componentes: List[Dict[str, Any]], delta_m: float) -> str:
    """
    Elige el evento causal a partir del componente de mayor impacto que además
    empuja en la misma dirección que la variación total. Ese criterio evita
    reportar como causa una categoría que en realidad amortiguó el cambio.
    """
    if abs(delta_m) < 0.01:
        return "SIN_CAMBIOS"

    mismo_sentido = [c for c in componentes if (c["impacto"] > 0) == (delta_m > 0)]
    if not mismo_sentido:
        return "INCREMENTO_OTROS" if delta_m > 0 else "REDUCCION_TARIFA"

    principal = mismo_sentido[0]["categoria"]
    tabla = EVENTO_POR_CATEGORIA_SUBIDA if delta_m > 0 else EVENTO_POR_CATEGORIA_BAJADA
    return tabla.get(principal, "INCREMENTO_OTROS" if delta_m > 0 else "REDUCCION_TARIFA")


# ---------------------------------------------------------------------------
# 4. Señales complementarias del recibo actual
# ---------------------------------------------------------------------------

def _inferir_plan_desde_cargos(cargos: List[Dict[str, Any]]) -> Optional[str]:
    """
    Identifica el plan del cliente tomando el cargo de plan de mayor importe.
    Una cuenta convergente factura varios cargos de plan (móvil, internet, TV);
    el de mayor importe es el plan principal y es el único que se afirma.
    """
    candidatos = [
        (_monto(c), str(c.get("CHARGE_CODE_DESC") or "").strip())
        for c in cargos
        if clasificar_cargo(c) == CAT_PLAN and _monto(c) > 0
    ]
    candidatos = [(monto, desc) for monto, desc in candidatos if desc]
    if not candidatos:
        return None
    candidatos.sort(reverse=True)
    return candidatos[0][1]


def _charge_code_plan_principal(cargos: List[Dict[str, Any]]) -> Optional[str]:
    """Charge code del plan principal, para comparar tarifas contra el catálogo."""
    candidatos = [
        (_monto(c), str(c.get("CHARGE_CODE_ID") or "").strip())
        for c in cargos
        if clasificar_cargo(c) == CAT_PLAN and _monto(c) > 0
    ]
    candidatos = [(monto, code) for monto, code in candidatos if code]
    if not candidatos:
        return None
    candidatos.sort(reverse=True)
    return candidatos[0][1]


def _formatear_fecha_vencimiento(valor: str) -> Optional[str]:
    """Convierte 'YYYYMMDD' (formato de FECHA-VENCIMIENTO en el CSV) a 'YYYY-MM-DD'."""
    if not valor or not valor.isdigit() or len(valor) != 8:
        return None
    try:
        return datetime.strptime(valor, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _obtener_estado_deuda(conceptos_facturados: Optional[Dict], mes_emision: str = "") -> Optional[Dict[str, str]]:
    """
    Extrae el estado de deuda que viene explícitamente en el CSV (columna DEUDA,
    con valores reales 'SIN DEUDA' / 'CON DEUDA').

    No convierte texto ambiguo en un monto ni asume que la ausencia del campo
    significa saldo cero. Solo expone el valor verificable y el período de la
    factura para poder responder consultas de deuda sin inventar información.

    PERIOD_END_DATE llega vacío/corrupto en el CSV fuente (literal "00:00.0"),
    así que se usa FECHA-VENCIMIENTO como referencia de período cuando es una
    fecha válida, y como último recurso el mes de emisión del recibo.
    """
    info_factura = (conceptos_facturados or {}).get("info_factura", {})
    valor = str(info_factura.get("DEUDA", "")).strip()
    if not valor or valor.lower() in {"nan", "none", "null"}:
        return None

    periodo = _formatear_fecha_vencimiento(str(info_factura.get("FECHA_VENCIMIENTO", "")).strip())
    if not periodo:
        periodo_raw = str(info_factura.get("PERIOD_END_DATE", "")).strip()
        periodo = periodo_raw if periodo_raw and not periodo_raw.lower().endswith(":00.0") else mes_emision

    sin_deuda = valor.upper() in {"SIN DEUDA", "NO TIENE DEUDA", "NO REGISTRA DEUDA"}
    return {
        "valor": valor,
        "estado": "SIN_DEUDA" if sin_deuda else "REPORTADA",
        "periodo": periodo or "",
    }


# --- Notas de crédito y débito (NOTAS_CREDITO.csv) -------------------------
#
# Valores reales de CANCEL_CHARGE_TYPE en el dataset: 'CRD' (nota de crédito,
# 8154 filas) y 'DSC' (nota de débito, 707 filas). El diccionario de datos
# escribe 'CDR', pero el dato real es 'CRD'; se aceptan ambos por robustez.
TIPOS_NOTA_CREDITO = {"CRD", "CDR"}
TIPOS_NOTA_DEBITO = {"DSC"}


def _obtener_ajustes_ciclo(db: Session, conceptos_facturados: Optional[Dict], ciclo: str) -> Optional[Dict[str, Any]]:
    """
    Recupera las notas de crédito/débito emitidas en el ciclo del recibo actual.

    Es una causa de variación que el desafío pide explicar explícitamente y que
    no vive en la tabla de facturación: sin este cruce, un recibo ajustado por
    nota de crédito se explicaría como una bajada "sin causa identificada".
    """
    info_factura = (conceptos_facturados or {}).get("info_factura", {})
    ba_no = str(info_factura.get("BILLING_ARRANGEMENT_KEY") or "").strip()
    if not ba_no or not ciclo:
        return None

    notas = crud.get_notas_credito_por_ciclo(db, ba_no, ciclo)
    if not notas:
        return None

    total_credito = 0.0
    total_debito = 0.0
    detalle = []
    for nota in notas:
        tipo = str(nota.cancel_charge_type or "").strip().upper()
        monto = float(nota.amount or 0.0)
        if tipo in TIPOS_NOTA_CREDITO:
            total_credito += monto
        elif tipo in TIPOS_NOTA_DEBITO:
            total_debito += monto
        detalle.append({
            "tipo": "NOTA_CREDITO" if tipo in TIPOS_NOTA_CREDITO else (
                "NOTA_DEBITO" if tipo in TIPOS_NOTA_DEBITO else tipo
            ),
            "charge_code": nota.charge_code,
            "monto": round(monto, 2),
            "fecha_efectiva": nota.effective_date.isoformat() if nota.effective_date else None,
        })

    return {
        "cantidad": len(notas),
        "total_notas_credito": round(total_credito, 2),
        "total_notas_debito": round(total_debito, 2),
        "detalle": detalle[:10],
    }


# ---------------------------------------------------------------------------
# 5. Alertas proactivas (fin de promoción antes de que ocurra)
# ---------------------------------------------------------------------------
#
# El dataset no trae una columna con la fecha de fin de cada promoción, pero sí
# trae la duración pactada dentro de la propia descripción del cargo, que es un
# dato real y verificable del recibo. Ejemplos literales del CSV:
#   "Bono MTotal 30GB por 6 M (VR S/ 118.56)"  -> 6 meses
#   "Movistar 15GB 6m"                          -> 6 meses
#   "10GB x1meses"                              -> 1 mes
#   "Prom Internet 1 GB x 12m"                  -> 12 meses
#   "Descuento 40% por 6 meses RET"             -> 6 meses
#   "Fidelizacion 20 x 3 Meses"                 -> 3 meses
#
# Cruzando esa duración con la cantidad de ciclos en que el cargo realmente se
# facturó, se sabe si el cliente está consumiendo el último ciclo del beneficio.
# No se inventa ninguna fecha: se informa el ciclo, no un día concreto.

_PATRONES_DURACION = (
    re.compile(r"\bpor\s+(\d{1,2})\s*(?:m|mes|meses)\b", re.IGNORECASE),
    re.compile(r"\bx\s*(\d{1,2})\s*(?:m|mes|meses)\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(?:m|mes|meses)\b", re.IGNORECASE),
)

DURACION_MAXIMA_PROMO_MESES = 36  # descarta números que no son duraciones (GB, tarifas)


def extraer_duracion_promocion(descripcion: str) -> Optional[int]:
    """
    Extrae la duración en meses declarada en la descripción del cargo.
    Devuelve None si la descripción no declara una duración reconocible.
    """
    if not descripcion:
        return None
    # Se retiran los tramos de GB y de valor referencial para no confundir
    # "30GB" o "(VR S/ 118.56)" con una duración en meses.
    limpio = re.sub(r"\d+\s*(?:gb|mb|tb)\b", " ", descripcion, flags=re.IGNORECASE)
    limpio = re.sub(r"\(.*?\)", " ", limpio)

    for patron in _PATRONES_DURACION:
        match = patron.search(limpio)
        if match:
            meses = int(match.group(1))
            if 1 <= meses <= DURACION_MAXIMA_PROMO_MESES:
                return meses
    return None


def _calcular_alertas_promocion(recibos: List[Any]) -> List[Dict[str, Any]]:
    """
    Detecta beneficios con duración declarada que están en su último ciclo.

    `recibos` llega ordenado del más reciente al más antiguo. Para cada bono o
    descuento del recibo actual se cuenta en cuántos ciclos de la ventana
    disponible se facturó ese mismo charge code; si ya alcanzó la duración
    pactada, el próximo recibo llegará sin él.
    """
    if not recibos:
        return []

    actual = recibos[0]
    cargos_actuales = _extraer_cargos(actual.conceptos_facturados or {})

    # Ciclos en los que apareció cada charge code dentro de la ventana.
    ciclos_por_codigo: Dict[str, set] = {}
    for recibo in recibos:
        for cargo in _extraer_cargos(recibo.conceptos_facturados or {}):
            codigo = str(cargo.get("CHARGE_CODE_ID") or "").strip()
            if codigo:
                ciclos_por_codigo.setdefault(codigo, set()).add(recibo.ciclo)

    alertas = []
    vistos = set()
    for cargo in cargos_actuales:
        if clasificar_cargo(cargo) not in (CAT_BONO, CAT_DESCUENTO):
            continue
        # Solo interesan las líneas que efectivamente descuentan dinero: es su
        # desaparición la que encarece el recibo.
        importe = _monto(cargo)
        if importe >= 0:
            continue

        codigo = str(cargo.get("CHARGE_CODE_ID") or "").strip()
        descripcion = str(cargo.get("CHARGE_CODE_DESC") or "").strip()
        if not codigo or codigo in vistos:
            continue

        duracion = extraer_duracion_promocion(descripcion)
        if not duracion:
            continue

        ciclos_facturados = len(ciclos_por_codigo.get(codigo, set()))
        if ciclos_facturados < duracion:
            continue  # el beneficio sigue vigente, no hay nada que avisar

        vistos.add(codigo)
        alertas.append({
            "concepto": descripcion,
            "fecha_fin": _formatear_fecha_vencimiento(
                str((actual.conceptos_facturados or {}).get("info_factura", {}).get("FECHA_VENCIMIENTO", "")).strip()
            ) or actual.mes_emision,
            "impacto_estimado": f"+{MONEDA_SIMBOLO} {abs(importe):.2f}",
            "tipo": "FIN_PROMOCION",
            "duracion_pactada_meses": duracion,
            "ciclos_facturados": ciclos_facturados,
        })

    return alertas


# ---------------------------------------------------------------------------
# 6. Patrón recurrente en la ventana de recibos
# ---------------------------------------------------------------------------

def _detectar_patron_recurrente(detected_event: str, recibos: List[Any]) -> Optional[str]:
    """
    Revisa la ventana completa de recibos para detectar si el mismo tipo de
    variación ya ocurrió antes. Un solo mes de comparación explica la causa
    puntual; esta señal explica el patrón ("esto ya pasó N veces").

    Se reutiliza exactamente la misma descomposición que produjo el evento
    actual, así que la comparación es consistente y no una heurística aparte.
    """
    if detected_event in ("SIN_CAMBIOS", "NUEVO_CLIENTE", "") or len(recibos) < 3:
        return None

    ocurrencias = 1  # el ciclo actual ya cuenta como una ocurrencia
    # Pares (mes_a, mes_b) empezando desde el par anterior al ya analizado.
    for i in range(1, len(recibos) - 1):
        actual, previo = recibos[i], recibos[i + 1]
        delta = round(actual.monto_total - previo.monto_total, 2)
        if abs(delta) < 0.01:
            continue
        componentes = descomponer_variacion(
            _extraer_cargos(actual.conceptos_facturados or {}),
            _extraer_cargos(previo.conceptos_facturados or {}),
        )
        if _detectar_evento(componentes, delta) == detected_event:
            ocurrencias += 1

    if ocurrencias >= 2:
        return (
            f"Este mismo tipo de variación ya se registró {ocurrencias} veces "
            f"en los últimos {len(recibos)} ciclos facturados."
        )
    return None


# ---------------------------------------------------------------------------
# 7. Contexto de órdenes CRM (ORDENES.csv)
# ---------------------------------------------------------------------------
#
# Valores reales de ORDER_ITEM_TYPE_DESC en el dataset. Se agrupan por el evento
# de facturación que ayudan a explicar, para no adjuntar órdenes irrelevantes.
_ORDENES_RELEVANTES = {
    "RECONEXION_MOROSIDAD": (
        "reconectar por cobranza", "suspension cobranza", "suspensión cobranza",
        "reconectar", "suspender", "dar de baja por cobranza", "cambiar cobranza",
    ),
    "PRORRATEO_CAMBIO_PLAN": ("cambiar", "cambiar express", "alta", "alta express"),
    "CAMBIO_PLAN": ("cambiar", "cambiar express"),
    "CUOTA_EQUIPO": ("cambiar", "alta", "cambiar express"),
    "COMPRA_PAQUETE": ("cambiar", "cambiar express"),
}


def _obtener_ordenes_contexto(db: Session, conceptos_facturados: Dict[str, Any], detected_event: str) -> List[Dict[str, Any]]:
    """
    Adjunta las órdenes CRM que dan contexto al evento detectado: cuándo y por
    qué ocurrió la suspensión, el cambio o el alta que explica el cargo.
    """
    tipos_relevantes = _ORDENES_RELEVANTES.get(detected_event)
    if not tipos_relevantes:
        return []

    customer_key = str((conceptos_facturados or {}).get("info_factura", {}).get("CUSTOMER_KEY") or "").strip()
    if not customer_key:
        return []

    contexto = []
    for orden in crud.get_ordenes_por_customer_key(db, customer_key, limit=20):
        tipo = _normalizar(orden.order_type)
        if not any(rel in tipo for rel in tipos_relevantes):
            continue
        contexto.append({
            "tipo": orden.order_type,
            "motivo": orden.order_reason,
            "fecha_inicio": orden.start_date.isoformat() if orden.start_date else None,
            "fecha_fin": orden.completion_date.isoformat() if orden.completion_date else None,
        })
        if len(contexto) >= 5:
            break
    return contexto


# ---------------------------------------------------------------------------
# 8. Punto de entrada: Deterministic Fact Payload
# ---------------------------------------------------------------------------

def _resumen_cargos_actuales(cargos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Desglose del recibo actual por categoría, para que la respuesta pueda
    mostrar "qué me están cobrando" y no solo la variación.
    """
    totales = _totales_por_categoria(cargos)
    resumen = []
    for categoria, monto in sorted(totales.items(), key=lambda kv: abs(kv[1]), reverse=True):
        if abs(monto) < 0.01:
            continue
        resumen.append({
            "categoria": categoria,
            "etiqueta": ETIQUETA_CATEGORIA.get(categoria, categoria),
            "monto": monto,
            "conceptos": _descripciones_categoria(cargos, categoria),
        })
    return resumen


def calculate_billing_facts(user_id: str, db: Session) -> Dict[str, Any]:
    """
    Calcula la variación (Delta M) y genera el Deterministic Fact Payload:
    el único conjunto de hechos que el LLM tiene permitido comunicar.

    `user_id` es la cuenta financiera del cliente (FINANCIAL_ACCOUNT en
    PLANTA_CLIENTES / FINANCIAL_ACCOUNT_KEY en FACTURACION_CLIENTES).
    """
    recibos = crud.get_recibos_by_user(db, user_id, limit=HORIZONTE_RECIBOS)

    if not recibos:
        return {"error": "No hay recibos para este usuario."}

    current_bill = recibos[0]
    recibos_previos = recibos[1:]
    conceptos_actuales = current_bill.conceptos_facturados or {}
    cargos_actuales = _extraer_cargos(conceptos_actuales)

    # Estos hechos se extraen antes de evaluar el historial para que un cliente
    # con un único recibo también pueda consultar su plan o estado de deuda.
    plan_actual = _inferir_plan_desde_cargos(cargos_actuales)
    estado_deuda = _obtener_estado_deuda(conceptos_actuales, mes_emision=current_bill.mes_emision)
    ajustes = _obtener_ajustes_ciclo(db, conceptos_actuales, current_bill.ciclo)

    base = {
        "moneda": MONEDA_CODIGO,
        "simbolo_moneda": MONEDA_SIMBOLO,
        "cuenta_financiera": str(user_id),
        "plan_actual": plan_actual,
        "plan_charge_code": _charge_code_plan_principal(cargos_actuales),
        "estado_deuda": estado_deuda,
        "ajustes_facturacion": ajustes,
        "current_bill": {
            "amount": current_bill.monto_total,
            "issue_date": current_bill.mes_emision,
            "ciclo": current_bill.ciclo,
            "desglose": _resumen_cargos_actuales(cargos_actuales),
        },
        "upcoming_alerts": _calcular_alertas_promocion(recibos),
    }

    if not recibos_previos:
        # Solo tiene un recibo: no hay historial con qué comparar.
        return {
            **base,
            "previous_bills": [],
            "variation_amount": 0.0,
            "variation_percentage": 0.0,
            "detected_event": "NUEVO_CLIENTE",
            "evidence": ["Es el primer ciclo facturado disponible para esta cuenta."],
            "variacion_por_categoria": [],
            "ordenes_contexto": [],
        }

    previous_bill = recibos_previos[0]
    cargos_pasados = _extraer_cargos(previous_bill.conceptos_facturados or {})
    delta_m = round(current_bill.monto_total - previous_bill.monto_total, 2)
    variation_pct = (
        round((delta_m / previous_bill.monto_total) * 100, 2)
        if previous_bill.monto_total > 0 else 0.0
    )

    componentes = descomponer_variacion(cargos_actuales, cargos_pasados)
    detected_event = _detectar_evento(componentes, delta_m)

    # La evidencia cita los componentes que realmente mueven la aguja, con sus
    # montos exactos. El LLM redacta sobre estas frases, no las recalcula.
    evidence = [_evidencia_componente(c) for c in componentes[:4]]

    # Un ajuste financiero del ciclo se reporta como causa cuando explica una
    # bajada sin otra causa de mayor peso.
    if ajustes and ajustes["total_notas_credito"] and delta_m < 0:
        if detected_event in ("REDUCCION_TARIFA", "INCREMENTO_OTROS", "SIN_CAMBIOS"):
            detected_event = "NOTA_CREDITO_AJUSTE"
        evidence.insert(0, (
            f"Se emitieron {ajustes['cantidad']} nota(s) de crédito/débito en este ciclo "
            f"por {MONEDA_SIMBOLO} {abs(ajustes['total_notas_credito']):.2f}."
        ))

    if not evidence:
        evidence = ["El detalle facturado se mantiene sin cambios respecto al ciclo anterior."]

    patron_recurrente = _detectar_patron_recurrente(detected_event, recibos)
    if patron_recurrente:
        evidence.append(patron_recurrente)

    return {
        **base,
        "previous_bills": [
            {"month": r.mes_emision, "amount": r.monto_total, "ciclo": r.ciclo}
            for r in recibos_previos
        ],
        "variation_amount": delta_m,
        "variation_percentage": variation_pct,
        "detected_event": detected_event,
        "evidence": evidence,
        "variacion_por_categoria": componentes,
        "ordenes_contexto": _obtener_ordenes_contexto(db, conceptos_actuales, detected_event),
    }


# ---------------------------------------------------------------------------
# 9. Señales conversacionales deterministas
# ---------------------------------------------------------------------------

def has_pending_followup_question(message: str) -> bool:
    """
    Detecta si el mensaje actual plantea una duda de seguimiento (el cliente
    sigue insatisfecho o tiene algo más que preguntar). Señal determinista
    para la condición 'no_preguntas_pendientes' del gatillo comercial: no se
    debe ofrecer nada mientras el cliente todavía tiene una duda abierta.
    """
    if "?" not in message:
        return False
    marcadores_duda = re.search(
        r"\b(pero|aun|a[uú]n|todav[ií]a|sigo|tambi[eé]n|adem[aá]s|otra duda|otra cosa|no entiendo)\b",
        message,
        re.IGNORECASE
    )
    return marcadores_duda is not None


def extract_emotional_comment(message: str) -> Optional[str]:
    """
    Detecta si el mensaje trae una expresión emocional explícita que merece
    quedar en memoria y ser referenciada cálidamente en un turno futuro
    (resignación, cansancio, "siempre pasa lo mismo", etc). Heurística ligera
    y determinista: no consume una llamada al LLM en cada turno de facturación.
    Retorna la frase a recordar, o None si no hay señal clara.
    """
    patrones = [
        r"\b(ntp|no hay problema|no te preocupes|tranquil[oa])\b.{0,30}\b(dif[ií]cil|complicado|entiendo)\b",
        r"\b(la verdad|sinceramente|honestamente)\b.{0,30}\b(cansad[oa]|frustrad[oa]|molest[oa]|preocupad[oa])\b",
        r"\b(siempre|otra vez|de nuevo)\b.{0,20}\b(lo mismo|igual|pasa esto|pasa lo mismo)\b",
        r"\b(se que no es tu culpa|sé que no es tu culpa|no es tu culpa)\b",
        r"\b(no creo que un bot|dudo que un bot|eres un bot|seguro que un bot|no vas a entender|hablar con un humano|pasame con un humano|bot pueda)\b",
    ]
    for patron in patrones:
        if re.search(patron, message, re.IGNORECASE):
            return message.strip()[:200]
    return None


# Eventos con causa identificada y explicable. Se usan como señal de resolución
# y como lista de patrones "claros" para el índice de incertidumbre.
EVENTOS_RESUELTOS = {
    "FIN_PROMOCION",
    "PRORRATEO_CAMBIO_PLAN",
    "CUOTA_EQUIPO",
    "RECONEXION_MOROSIDAD",
    "REDUCCION_TARIFA",
    "NUEVO_DESCUENTO",
    "FIN_CUOTAS_EQUIPO",
    "COMPRA_PAQUETE",
    "TRAFICO_ADICIONAL",
    "CAMBIO_PLAN",
    "NOTA_CREDITO_AJUSTE",
}


def is_case_resolved(detected_event: str) -> bool:
    """
    ¿La consulta fue clasificada como resuelta? Es decir, ¿se identificó una
    causa concreta y explicable, en vez de un evento ambiguo o inexistente?
    Señal determinista para la condición 'estado_resolucion' del gatillo comercial.
    """
    return detected_event in EVENTOS_RESUELTOS


# ---------------------------------------------------------------------------
# 10. Recomendación comercial verificada contra el catálogo real
# ---------------------------------------------------------------------------

def _capacidad_gb(descripcion: str) -> Optional[float]:
    """
    Extrae los GB incluidos que declara la descripción del plan.
    'RA Movistar Total ilim 125 GB ABR24' -> 125.0
    Devuelve None si la descripción no declara capacidad.
    """
    if not descripcion:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*GB\b", descripcion, re.IGNORECASE)
    return float(match.group(1)) if match else None


def recommend_plan_upgrade(db: Session, plan_charge_code: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Selecciona, de forma determinista, un plan real del catálogo que mejore al
    actual. El LLM nunca elige ni inventa el plan: solo redacta el mensaje
    comercial sobre este dato ya verificado.

    El dataset impone qué se puede afirmar. CATALOGO_OFERTAS solo aporta
    `rate_final` y `TIPO DE RENTA`; el nombre legible viene de la descripción del
    cargo en facturación, y únicamente una minoría de esas descripciones declara
    la capacidad incluida en GB. Por eso hay dos criterios de mejora, ambos
    comprobables, y ninguno inventa beneficios que el dato no respalde:

      1. MAS_CAPACIDAD: ambas descripciones declaran GB y el candidato ofrece más
         por una tarifa igual o menor. Es la mejora más fuerte.
      2. MENOR_TARIFA: no hay capacidad declarada con la que comparar, así que se
         exige tarifa estrictamente menor dentro del mismo tipo de renta, y se
         describe exactamente eso y nada más.

    Devuelve None cuando ninguna de las dos se puede demostrar; en ese caso el
    orquestador desactiva la oferta en lugar de arriesgar una recomendación no
    comprobable.
    """
    if not plan_charge_code:
        return None

    oferta_actual = crud.get_oferta_por_charge_code(db, plan_charge_code)
    if not oferta_actual or not oferta_actual.rate_final:
        # Sin tarifa oficial del plan actual no hay base de comparación.
        return None

    planes = crud.get_planes_ofertables(db)
    if not planes:
        return None

    actual = next((p for p in planes if p["charge_code"] == plan_charge_code), None)
    capacidad_actual = _capacidad_gb(actual["nombre"]) if actual else None
    precio_actual = round(float(oferta_actual.rate_final), 2)
    tipo_renta_actual = (oferta_actual.tipo_renta or "").strip().upper()

    por_capacidad: List[Tuple[float, Dict[str, Any]]] = []
    por_tarifa: List[Tuple[float, Dict[str, Any]]] = []

    for plan in planes:
        if plan["charge_code"] == plan_charge_code:
            continue
        if plan["precio"] > precio_actual:
            continue  # nunca proponer algo más caro
        # Solo se compara dentro del mismo tipo de renta: mezclar renta
        # adelantada con vencida cambia el modelo de cobro, no es una mejora.
        if tipo_renta_actual and plan["tipo_renta"].strip().upper() != tipo_renta_actual:
            continue

        capacidad = _capacidad_gb(plan["nombre"])
        if capacidad is not None and capacidad_actual is not None:
            if capacidad > capacidad_actual:
                por_capacidad.append((capacidad, plan))
            continue

        if plan["precio"] < precio_actual:
            # Se ordena por ahorro: mayor diferencia primero.
            por_tarifa.append((precio_actual - plan["precio"], plan))

    if por_capacidad:
        por_capacidad.sort(key=lambda t: t[0], reverse=True)
        mejor = por_capacidad[0][1]
        capacidad = _capacidad_gb(mejor["nombre"])
        renta = mejor["tipo_renta"].lower()
        return {
            "nombre": mejor["nombre"],
            "precio": mejor["precio"],
            "beneficios": (
                f"{capacidad:.0f} GB incluidos frente a los {capacidad_actual:.0f} GB de tu plan actual"
                + (f", con renta {renta}" if renta else "")
                + f", por una tarifa de {MONEDA_SIMBOLO} {mejor['precio']:.2f}"
            ),
            "motivo": "MAS_CAPACIDAD",
        }

    if por_tarifa:
        por_tarifa.sort(key=lambda t: t[0], reverse=True)
        ahorro, mejor = por_tarifa[0]
        renta = mejor["tipo_renta"].lower()
        return {
            "nombre": mejor["nombre"],
            "precio": mejor["precio"],
            "beneficios": (
                f"tarifa de {MONEDA_SIMBOLO} {mejor['precio']:.2f} frente a los "
                f"{MONEDA_SIMBOLO} {precio_actual:.2f} de tu plan actual "
                f"({MONEDA_SIMBOLO} {ahorro:.2f} menos al mes)"
                + (f", manteniendo la renta {renta}" if renta else "")
            ),
            "motivo": "MENOR_TARIFA",
        }

    return None


# ---------------------------------------------------------------------------
# 11. Gatillo Comercial Estricto
# ---------------------------------------------------------------------------
#
# Lista blanca alineada a los detected_event reales que produce el motor.
# Quedan EXCLUIDOS a propósito:
#   - RECONEXION_MOROSIDAD y NOTA_CREDITO_AJUSTE: implican deuda, mora o un
#     ajuste en disputa; ofrecer algo ahí es inadecuado.
#   - SIN_CAMBIOS, INCREMENTO_OTROS, CONSULTA_GENERAL, NUEVO_CLIENTE: eventos
#     sin causa confirmada, donde no se ofrece nada.
LISTA_BLANCA_CROSS_SELL = [
    "FIN_PROMOCION",
    "PRORRATEO_CAMBIO_PLAN",
    "CUOTA_EQUIPO",
    "REDUCCION_TARIFA",
    "FIN_CUOTAS_EQUIPO",
    "NUEVO_DESCUENTO",
    "CAMBIO_PLAN",
]


def evaluate_cross_sell_eligibility(
    sentiment_score: int,
    estado_resolucion: bool,
    intent_category: str,
    no_preguntas_pendientes: bool,
) -> bool:
    """
    Evalúa las 4 condiciones estrictas para ofrecer planes de mayor valor.
    Las 4 se calculan con señales reales (ver is_case_resolved y
    has_pending_followup_question), no se asumen verdaderas.
    """
    return (
        sentiment_score >= 4
        and estado_resolucion is True
        and intent_category in LISTA_BLANCA_CROSS_SELL
        and no_preguntas_pendientes is True
    )
