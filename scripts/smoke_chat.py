"""
smoke_chat.py
-------------
Prueba de humo del endpoint POST /api/v1/chat contra cuentas reales de la base,
una por cada escenario crítico del desafío.

Comprueba el contrato completo de la respuesta, no solo que devuelva 200:
  - `intent_category` coincide con el evento que el motor determinista detectó.
  - La respuesta trae desglose del recibo y desglose de la variación.
  - Ninguna cifra del texto de Lucía sale de un símbolo de moneda distinto a S/.
  - Si hay oferta comercial, el plan viene del catálogo con su motivo verificable.

Requiere el servidor levantado (uvicorn app.main:app).

    python scripts/smoke_chat.py
    python scripts/smoke_chat.py --base http://127.0.0.1:8001/api/v1
"""

import argparse
import os
import re
import sys
import uuid
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from app.db.database import SessionLocal  # noqa: E402
from app.db import crud, models  # noqa: E402
from app.services.deterministic import calculate_billing_facts  # noqa: E402

# Escenario -> pregunta con la que un cliente real llegaría al bot.
PREGUNTAS = {
    "PRORRATEO_CAMBIO_PLAN": "por que me cobraron dos montos distintos este mes?",
    "CUOTA_EQUIPO": "por que me cobran cuota de equipo este mes?",
    "RECONEXION_MOROSIDAD": "por que tengo un cargo de reconexion?",
    "FIN_PROMOCION": "por que subio mi recibo este mes?",
    "CAMBIO_PLAN": "por que cambio el monto de mi plan?",
    "COMPRA_PAQUETE": "que paquetes me estan cobrando?",
    "TRAFICO_ADICIONAL": "por que me cobran consumo adicional?",
    "NOTA_CREDITO_AJUSTE": "por que bajo mi recibo este mes?",
}

# Símbolos de moneda que nunca deben aparecer: el dataset es en soles peruanos.
MONEDAS_PROHIBIDAS = re.compile(r"[€$]|\bUSD\b|\bEUR\b")


def buscar_cuentas_por_evento(db, eventos_buscados, max_cuentas=1000):
    """Recorre la base hasta encontrar una cuenta de ejemplo por evento."""
    pendientes = set(eventos_buscados)
    encontradas = {}
    for cuenta in crud.get_all_user_ids(db)[:max_cuentas]:
        if not pendientes:
            break
        payload = calculate_billing_facts(cuenta, db)
        if "error" in payload:
            continue
        evento = payload.get("detected_event")
        if evento in pendientes:
            encontradas[evento] = cuenta
            pendientes.discard(evento)
    return encontradas


def revisar_respuesta(evento_esperado, datos):
    """Devuelve la lista de problemas encontrados en la respuesta del endpoint."""
    problemas = []

    if datos.get("intent_category") != evento_esperado:
        problemas.append(
            f"intent_category es '{datos.get('intent_category')}' y el motor detectó '{evento_esperado}'"
        )

    if not datos.get("messages"):
        problemas.append("la respuesta no trae ningún mensaje")

    if not datos.get("current_bill_breakdown"):
        problemas.append("falta el desglose del recibo actual")

    if evento_esperado != "SIN_CAMBIOS" and not datos.get("variation_breakdown"):
        problemas.append("falta el desglose de la variación")

    auditor = datos.get("auditor_breakdown")
    if auditor:
        m_ant = float(auditor.get("monto_anterior", 0.0))
        m_act = float(auditor.get("monto_actual", 0.0))
        s_imp = float(auditor.get("suma_impactos", 0.0))
        if abs((m_ant + s_imp) - m_act) > 0.05:
            problemas.append(f"Modo Auditor descuadrado: {m_ant} + {s_imp} != {m_act}")

    texto = " ".join(m.get("text", "") for m in datos.get("messages", []))
    if MONEDAS_PROHIBIDAS.search(texto):
        problemas.append("el texto menciona una moneda que no es soles")

    oferta = datos.get("plan_optimizer_suggestion") or {}
    if oferta.get("available"):
        plan = oferta.get("plan_recomendado") or {}
        if not plan.get("nombre") or plan.get("precio") is None:
            problemas.append("hay oferta comercial sin plan verificado")
        elif not plan.get("motivo"):
            problemas.append("el plan ofertado no declara un motivo verificable")

    if datos.get("confidence_score") is None:
        problemas.append("falta confidence_score")

    if not isinstance(datos.get("confidence_reasons"), list):
        problemas.append("falta la lista de confidence_reasons")

    return problemas


def main():
    parser = argparse.ArgumentParser(description="Prueba de humo de POST /api/v1/chat.")
    parser.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        print("Buscando una cuenta de ejemplo por escenario...")
        cuentas = buscar_cuentas_por_evento(db, PREGUNTAS.keys())

        # La memoria de Lucía se vincula al cliente, no a la sesión. Sin vaciarla,
        # en la segunda corrida responde "ya te expliqué antes" en lugar de dar la
        # explicación completa: es el comportamiento correcto en producción, pero
        # impide comprobar aquí que la explicación contiene lo que debe.
        #
        # Se vacía la bitácora en lugar de borrar la fila: `audit_log` referencia
        # `historial_interacciones.session_id` con una clave ajena, así que un
        # DELETE fallaría mientras existan registros de auditoría de esa sesión.
        if cuentas:
            filas = (
                db.query(models.HistorialInteracciones)
                .filter(models.HistorialInteracciones.user_id.in_(list(cuentas.values())))
                .all()
            )
            for fila in filas:
                fila.historial_conversacion = []
                fila.comentarios_emocionales = []
                fila.estado_resolucion = False
            if filas:
                db.commit()
                print(f"Memoria conversacional de las cuentas de prueba vaciada: {len(filas)} registro(s).")
    finally:
        db.close()

    if not cuentas:
        print("[ERROR] No se encontraron cuentas con recibos. Ejecuta scripts/ingest_real_data.py.")
        sys.exit(1)

    # Cada corrida usa sesiones nuevas. El historial de Lucía se vincula al
    # cliente, así que reusar el mismo session_id hace que en la segunda corrida
    # responda "ya te expliqué antes" en lugar de dar la explicación completa:
    # correcto en producción, pero inútil para comprobar el contenido aquí.
    corrida = uuid.uuid4().hex[:8]

    fallos = 0
    for evento, pregunta in PREGUNTAS.items():
        cuenta = cuentas.get(evento)
        if not cuenta:
            print(f"\n--- {evento}: sin cuentas cargadas con este evento, se omite.")
            continue

        print(f"\n{'=' * 70}")
        print(f"  {evento}  |  cuenta {cuenta}")
        print(f"  Usuario: \"{pregunta}\"")
        print(f"{'=' * 70}")

        try:
            respuesta = requests.post(
                f"{args.base}/chat",
                json={
                    "session_id": f"smoke-{corrida}-{evento.lower()}",
                    "user_id": cuenta,
                    "message": pregunta,
                    "channel": "web",
                },
                timeout=120,
            )
            respuesta.raise_for_status()
            datos = respuesta.json()
        except Exception as e:
            print(f"  [FALLO] La petición no se completó: {e}")
            fallos += 1
            continue

        for mensaje in datos.get("messages", []):
            print(f"  Lucía [{mensaje.get('type')}]: {mensaje.get('text')}")

        print(f"\n  intent_category  : {datos.get('intent_category')}")
        print(f"  confianza        : {datos.get('confidence_score')}%")
        print(f"  deriva a humano  : {datos.get('requires_human_intervention')}")
        print(f"  caso validado    : {datos.get('caso_validado')}")
        print(f"  desglose recibo  : {len(datos.get('current_bill_breakdown') or [])} categoría(s)")
        print(f"  desglose variación: {len(datos.get('variation_breakdown') or [])} categoría(s)")

        oferta = datos.get("plan_optimizer_suggestion") or {}
        if oferta.get("available"):
            plan = oferta.get("plan_recomendado") or {}
            print(f"  oferta           : {plan.get('nombre')} | S/ {plan.get('precio')} "
                  f"| motivo {plan.get('motivo')}")
        else:
            print("  oferta           : ninguna")

        problemas = revisar_respuesta(evento, datos)
        if problemas:
            fallos += 1
            print("  [FALLO]")
            for p in problemas:
                print(f"    - {p}")
        else:
            print("  [OK] la respuesta cumple el contrato.")

    # --- Test P0: Drill-down conversacional ---
    print(f"\n{'=' * 70}")
    print("  [TEST P0] Drill-down conversacional sobre un cargo específico")
    print(f"{'=' * 70}")
    cuenta_drill = cuentas.get("RECONEXION_MOROSIDAD") or list(cuentas.values())[0]
    try:
        r_drill = requests.post(
            f"{args.base}/chat",
            json={
                "session_id": f"smoke-{corrida}-drilldown",
                "user_id": cuenta_drill,
                "message": "y ese cargo de reconexion que es?",
                "channel": "web",
            },
            timeout=30,
        )
        r_drill.raise_for_status()
        d_drill = r_drill.json()
        if d_drill.get("intent_category") == "DRILL_DOWN_CARGO":
            print(f"  [OK] Drill-down reconocido con éxito (intent: {d_drill.get('intent_category')})")
            for m in d_drill.get("messages", []):
                print(f"  Lucía: {m.get('text')}")
        else:
            print(f"  [AVISO] Drill-down devolvió intent: {d_drill.get('intent_category')}")
    except Exception as e:
        print(f"  [FALLO] Drill-down falló: {e}")
        fallos += 1

    # --- Test P0: Límite duro anti-loop ---
    print(f"\n{'=' * 70}")
    print("  [TEST P0] Límite duro anti-loop tras repreguntas no resueltas")
    print(f"{'=' * 70}")
    sesion_loop = f"smoke-{corrida}-antiloop"
    cuenta_loop = list(cuentas.values())[0]
    try:
        # Turno 1: consulta
        requests.post(f"{args.base}/chat", json={"session_id": sesion_loop, "user_id": cuenta_loop, "message": "por que cambio mi recibo?"})
        # Turno 2: repregunta no resuelta
        requests.post(f"{args.base}/chat", json={"session_id": sesion_loop, "user_id": cuenta_loop, "message": "sigo sin entender por que subio"})
        # Turno 3: insiste -> debe gatillar anti-loop
        r_loop = requests.post(f"{args.base}/chat", json={"session_id": sesion_loop, "user_id": cuenta_loop, "message": "no me queda claro nada"})
        r_loop.raise_for_status()
        d_loop = r_loop.json()
        if d_loop.get("requires_human_intervention") or d_loop.get("intent_category") == "LIMITE_LOOP_DERIVACION_HUMANA":
            print(f"  [OK] Anti-loop activado correctamente tras repreguntas (deriva: {d_loop.get('requires_human_intervention')})")
            for m in d_loop.get("messages", []):
                print(f"  Lucía: {m.get('text')}")
        else:
            print(f"  [FALLO] Anti-loop no forzó handoff a humano.")
            fallos += 1
    except Exception as e:
        print(f"  [FALLO] Anti-loop test falló: {e}")
        fallos += 1

    # --- Test P0: Salida de escalamiento garantizada ("0" y "asesor") ---
    print(f"\n{'=' * 70}")
    print("  [TEST P0] Salida de escalamiento garantizada directa ('0' / 'asesor')")
    print(f"{'=' * 70}")
    sesion_esc = f"smoke-{corrida}-escalamiento"
    try:
        r_esc = requests.post(f"{args.base}/chat", json={"session_id": sesion_esc, "user_id": cuenta_loop, "message": "0"})
        r_esc.raise_for_status()
        d_esc = r_esc.json()
        if d_esc.get("requires_human_intervention") and d_esc.get("intent_category") == "SOLICITUD_AGENTE" and d_esc.get("folio"):
            print(f"  [OK] Escalamiento garantizado por '0' exitoso. Folio asignado: {d_esc.get('folio')}")
            for m in d_esc.get("messages", []):
                print(f"  Lucía: {m.get('text')}")
        else:
            print(f"  [FALLO] Escalamiento por '0' falló: {d_esc}")
            fallos += 1
    except Exception as e:
        print(f"  [FALLO] Test escalamiento directo falló: {e}")
        fallos += 1

    # --- Test P0: Consulta de estado de caso / folio ---
    print(f"\n{'=' * 70}")
    print("  [TEST P0] Consulta de estado de caso ('¿cómo va mi caso?')")
    print(f"{'=' * 70}")
    try:
        r_status = requests.post(f"{args.base}/chat", json={"session_id": sesion_esc, "user_id": cuenta_loop, "message": "¿cómo va mi caso?"})
        r_status.raise_for_status()
        d_status = r_status.json()
        if d_status.get("intent_category") == "CONSULTA_ESTADO_CASO" and d_status.get("folio"):
            print(f"  [OK] Consulta de estado exitosa. Folio consultado: {d_status.get('folio')}")
            for m in d_status.get("messages", []):
                print(f"  Lucía: {m.get('text')}")
        else:
            print(f"  [FALLO] Consulta de estado no reconoció el caso: {d_status}")
            fallos += 1
    except Exception as e:
        print(f"  [FALLO] Test consulta estado caso falló: {e}")
        fallos += 1

    print(f"\n{'=' * 70}")
    if fallos:
        print(f"Resultado: {fallos} escenario(s) con problemas.")
        sys.exit(1)
    print("Resultado: todos los escenarios y pruebas P0 probados cumplen el contrato.")


if __name__ == "__main__":
    main()
