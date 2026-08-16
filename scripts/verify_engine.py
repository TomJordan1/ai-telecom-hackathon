"""
verify_engine.py
----------------
Verificación del motor determinista contra los datos realmente cargados en la
base. Para cada evento detectado busca una cuenta de ejemplo, imprime el payload
que recibiría el LLM y comprueba tres invariantes que sostienen la promesa de
"cero alucinaciones financieras":

  1. La suma de los impactos por categoría reproduce la variación total del
     recibo (la explicación cuadra al céntimo).
  2. Todo evento con causa identificada trae al menos una línea de evidencia.
  3. Ninguna cifra del payload sale de un cálculo sobre datos ausentes.

Se ejecuta sin LLM y sin servidor: es una comprobación directa de la capa de
datos, así que sirve para detectar regresiones tras una reingesta.

    python scripts/verify_engine.py
    python scripts/verify_engine.py --max-cuentas 400 --detalle CUOTA_EQUIPO
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.database import SessionLocal  # noqa: E402
from app.db import crud  # noqa: E402
from app.services.deterministic import (  # noqa: E402
    calculate_billing_facts,
    is_case_resolved,
    recommend_plan_upgrade,
)
from app.services.uncertainty_calculator import calculate_uncertainty, requires_handoff  # noqa: E402

# Escenarios que el desafío pide poder demostrar en vivo.
ESCENARIOS_DESAFIO = {
    "PRORRATEO_CAMBIO_PLAN": "(a) Prorrateos",
    "CUOTA_EQUIPO": "(b) Cuota de equipo financiado",
    "RECONEXION_MOROSIDAD": "(c) Reconexión tras suspensión morosa",
    "FIN_PROMOCION": "(d) Fin de descuentos",
    "CAMBIO_PLAN": "(e) Cambios de plan",
}


def revisar_invariantes(payload) -> list:
    """Devuelve la lista de invariantes incumplidas por este payload."""
    fallos = []

    componentes = payload.get("variacion_por_categoria") or []
    variacion = float(payload.get("variation_amount", 0.0))
    if componentes:
        suma = round(sum(c["impacto"] for c in componentes), 2)
        if abs(suma - variacion) > 0.05:
            fallos.append(
                f"la descomposición suma {suma:.2f} pero la variación total es {variacion:.2f}"
            )

    evento = payload.get("detected_event", "")
    if is_case_resolved(evento) and not payload.get("evidence"):
        fallos.append(f"el evento {evento} se reporta como resuelto pero sin evidencia")

    if payload.get("current_bill", {}).get("amount") is None:
        fallos.append("el recibo actual no tiene monto")

    for alerta in payload.get("upcoming_alerts") or []:
        if not alerta.get("duracion_pactada_meses"):
            fallos.append("hay una alerta de fin de promoción sin duración pactada verificada")

    return fallos


def imprimir_detalle(db, cuenta, payload):
    print(f"\n{'=' * 70}")
    print(f"  Cuenta financiera {cuenta}  ->  {payload.get('detected_event')}")
    print(f"{'=' * 70}")
    actual = payload.get("current_bill", {})
    print(f"Recibo actual: {actual.get('issue_date')} (ciclo {actual.get('ciclo')})  "
          f"S/ {actual.get('amount'):.2f}")
    print(f"Variación vs. ciclo anterior: S/ {payload.get('variation_amount'):+.2f} "
          f"({payload.get('variation_percentage')}%)")
    print(f"Plan identificado: {payload.get('plan_actual')} "
          f"[{payload.get('plan_charge_code')}]")

    deuda = payload.get("estado_deuda")
    if deuda:
        print(f"Estado de deuda: {deuda.get('valor')} (período {deuda.get('periodo')})")

    print("\nDesglose del recibo actual:")
    for item in actual.get("desglose") or []:
        conceptos = ", ".join(item.get("conceptos") or [])[:70]
        print(f"  S/ {item['monto']:>10.2f}  {item['etiqueta']:<45} {conceptos}")

    print("\nAporte de cada categoría a la variación:")
    for item in payload.get("variacion_por_categoria") or []:
        print(f"  S/ {item['impacto']:>+10.2f}  {item['etiqueta']:<45} "
              f"({item['monto_anterior']:.2f} -> {item['monto_actual']:.2f})")

    print("\nEvidencia que recibe el modelo:")
    for linea in payload.get("evidence") or []:
        print(f"  - {linea}")

    ordenes = payload.get("ordenes_contexto") or []
    if ordenes:
        print("\nÓrdenes CRM de contexto:")
        for o in ordenes[:3]:
            print(f"  - {o.get('tipo')} / {o.get('motivo')} ({o.get('fecha_inicio')})")

    ajustes = payload.get("ajustes_facturacion")
    if ajustes:
        print(f"\nAjustes del ciclo: {ajustes['cantidad']} nota(s), "
              f"crédito S/ {ajustes['total_notas_credito']:.2f}, "
              f"débito S/ {ajustes['total_notas_debito']:.2f}")

    alertas = payload.get("upcoming_alerts") or []
    if alertas:
        print("\nAlertas proactivas:")
        for a in alertas[:3]:
            print(f"  - {a['concepto']} | impacto {a['impacto_estimado']} | "
                  f"pactado {a['duracion_pactada_meses']} mes(es), "
                  f"facturado {a['ciclos_facturados']} ciclo(s)")

    incertidumbre = calculate_uncertainty(payload, None, None, False)
    print(f"\nIncertidumbre: {incertidumbre}  ->  "
          f"{'DERIVA A HUMANO' if requires_handoff(incertidumbre) else 'responde Lucía'}  "
          f"(confianza {int((1 - incertidumbre) * 100)}%)")

    plan = recommend_plan_upgrade(db, payload.get("plan_charge_code"))
    print(f"Plan recomendado verificado: {plan if plan else 'ninguno (no demostrable con datos reales)'}")


def main():
    parser = argparse.ArgumentParser(description="Verifica el motor determinista contra la base.")
    parser.add_argument("--max-cuentas", type=int, default=1000,
                        help="Cuántas cuentas recorrer como máximo.")
    parser.add_argument("--detalle", action="append", default=[],
                        help="Evento del que imprimir el payload completo (repetible).")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        cuentas = crud.get_all_user_ids(db)[:args.max_cuentas]
        print(f"Cuentas a revisar: {len(cuentas)}")

        ejemplos = {}
        conteo = {}
        fallos_totales = []
        sin_recibos = 0

        for cuenta in cuentas:
            payload = calculate_billing_facts(cuenta, db)
            if "error" in payload:
                sin_recibos += 1
                continue

            evento = payload.get("detected_event", "DESCONOCIDO")
            conteo[evento] = conteo.get(evento, 0) + 1
            ejemplos.setdefault(evento, (cuenta, payload))

            for fallo in revisar_invariantes(payload):
                fallos_totales.append(f"cuenta {cuenta} ({evento}): {fallo}")

        print(f"\nEventos detectados sobre datos reales de la base:")
        for evento, total in sorted(conteo.items(), key=lambda kv: kv[1], reverse=True):
            cuenta_ej = ejemplos[evento][0]
            print(f"  {evento:24} {total:>5}   ejemplo: {cuenta_ej}")
        if sin_recibos:
            print(f"  (cuentas sin recibos: {sin_recibos})")

        print(f"\nCobertura de los escenarios críticos del desafío:")
        faltantes = []
        for evento, etiqueta in ESCENARIOS_DESAFIO.items():
            if evento in ejemplos:
                print(f"  OK   {etiqueta:<42} cuenta {ejemplos[evento][0]}")
            else:
                print(f"  FALTA {etiqueta:<42} no apareció en la muestra revisada")
                faltantes.append(evento)

        # Los escenarios poco frecuentes (la cuota de equipo existe en 17 de las
        # 18471 cuentas del dataset) pueden no aparecer en una muestra parcial.
        # Eso no es un fallo del motor, así que solo se considera error cuando se
        # revisaron TODAS las cuentas cargadas.
        muestra_completa = len(cuentas) >= len(crud.get_all_user_ids(db))
        if faltantes and not muestra_completa:
            print("  (muestra parcial: vuelve a ejecutar sin --max-cuentas para "
                  "evaluar la cobertura real)")

        print(f"\nInvariantes de integridad: ", end="")
        if fallos_totales:
            print(f"{len(fallos_totales)} incumplimiento(s)")
            for fallo in fallos_totales[:20]:
                print(f"  - {fallo}")
        else:
            print("todas se cumplen en las cuentas revisadas.")

        for evento in args.detalle:
            if evento in ejemplos:
                cuenta, payload = ejemplos[evento]
                imprimir_detalle(db, cuenta, payload)
            else:
                print(f"\n[AVISO] No hay ninguna cuenta cargada con el evento {evento}.")

        if fallos_totales or (faltantes and muestra_completa):
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
