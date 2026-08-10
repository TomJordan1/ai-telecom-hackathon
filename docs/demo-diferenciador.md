# Guion de demo: el ciclo de aprendizaje en vivo

Este documento es el guion paso a paso para mostrar el diferenciador real del
producto durante el pitch: **Lucía no responde siempre igual — cada caso que
un asesor valida hace que el siguiente caso equivalente se resuelva con más
certeza y menos dependencia del modelo de lenguaje.**

No es una promesa de diapositiva. Se puede reproducir en vivo, con datos
reales del sistema, en menos de 2 minutos.

## Qué se va a mostrar

Una misma consulta de facturación, resuelta dos veces:

1. La **primera vez**, el patrón no tiene una solución validada todavía →
   Lucía responde igual (la explicación es correcta, viene del motor
   determinista), pero la insignia de confianza sale en **ámbar, 80%** y el
   caso queda registrado en cuarentena.
2. Un asesor **valida el caso** desde el panel de administración — la misma
   acción que haría un agente real de Movistar tras confirmar que la
   explicación fue correcta.
3. La **segunda vez** que alguien pregunta lo mismo, Lucía reutiliza la
   solución ya aprobada. La insignia sale en **verde, 100%**.

Ese salto de 80% a 100%, visible en la propia interfaz, es la reducción de
carga al call center ocurriendo frente al jurado.

## Preparación (antes de la demo)

1. Servidor corriendo: `uvicorn app.main:app --reload`, en `http://127.0.0.1:8000`.
2. Base de datos con los datos de prueba: `python scripts/generate_mock_data.py`.
   Si ya la corriste antes, no hace falta repetirlo (el script es idempotente).
3. Dos pestañas del navegador abiertas:
   - Pestaña A: `http://127.0.0.1:8000/` (chat web)
   - Pestaña B: `http://127.0.0.1:8000/static/admin.html` (panel de administración)
4. En la pestaña A, en el selector de usuario de la cabecera, elige
   **User A (Fin de Promo)**.

> Si esta sesión ya tiene un caso `FIN_PROMOCION` validado de una demo
> anterior, el badge saldrá directamente en verde desde el primer intento.
> Para reiniciar el ciclo antes del pitch: en la pestaña del panel, en
> **Base de Conocimiento**, no hay botón de borrado por diseño (es
> conocimiento validado, no se descarta a la ligera) — usa una base de datos
> nueva (`Remove-Item lucia_brain.db` y vuelve a sembrar) si necesitas el
> ciclo completo desde cero.

## Paso a paso

### 1. Primera consulta — caso nuevo

En el chat web, escribe:

```
¿por qué subió mi recibo este mes?
```

Lucía responde con la explicación (fin de promoción, con el monto exacto y
la evidencia de que el mismo patrón ya ocurrió antes en el historial de 5
meses). Debajo del último mensaje aparece la insignia:

> ◌ Caso nuevo, en aprendizaje · confianza 80%

**Qué decir en ese momento:** *"La explicación es correcta — viene del motor
determinista, no la inventa el modelo. Pero como es la primera vez que el
sistema resuelve este patrón exacto, no lo marca como conocimiento
consolidado todavía. Queda en cuarentena, esperando validación."*

### 2. Mostrar el caso en cuarentena

Cambia a la pestaña del panel de administración y ve a la pestaña
**Casos en Cuarentena**. Debe aparecer un caso con patrón `FIN_PROMOCION` y
la incertidumbre calculada.

**Qué decir:** *"Este es exactamente el caso que Lucía acaba de resolver.
No se promueve solo, y tampoco basta con que el cliente diga 'gracias, quedó
claro' en el momento — el sistema espera confirmación real, incluyendo un
seguimiento posterior, antes de confiar en él."*

### 3. Validar el caso (simula la acción de un asesor)

En esa misma tarjeta, haz clic en **✓ Validar y promover**.

El caso se mueve a **Base de Conocimiento**. Puedes cambiar a esa pestaña
para mostrarlo ahí, con el contador de veces aplicado en cero.

**Qué decir:** *"Con un clic, un agente de Movistar acaba de convertir esta
solución en conocimiento reutilizable. A partir de ahora, cualquier cliente
con este mismo patrón de facturación se beneficia de esta validación."*

### 4. Repetir la misma consulta

Vuelve a la pestaña del chat. Si quieres mostrar que es una sesión distinta
(otro cliente, no memoria de la conversación anterior), usa el botón **↻
Nueva conversación** de la cabecera antes de escribir. Envía el mismo
mensaje:

```
¿por qué subió mi recibo este mes?
```

La respuesta es igual de clara, pero ahora la insignia sale:

> ✓ Caso validado · confianza 100%

**Qué decir:** *"Es la misma pregunta, el mismo tipo de caso — pero ahora el
sistema no tiene que generar la solución desde cero: la reutiliza, con
confianza total. Esto es lo que reduce la dependencia del modelo de lenguaje
con el tiempo, y es la base de por qué el sistema mejora en vez de quedarse
igual desde el día uno."*

## Variante rápida por API (si el chat web no está disponible)

El mismo flujo, con `curl` o Postman, en caso de que la demo deba hacerse
sin interfaz:

```powershell
# 1. Primera consulta (caso nuevo)
curl.exe -X POST http://127.0.0.1:8000/api/v1/chat `
  -H "Content-Type: application/json" `
  -d '{"session_id":"demo-1","user_id":"user_a_fin_promo","message":"por que subio mi recibo?"}'
# -> confidence_score: 80, caso_validado: false

# 2. Buscar el caso en cuarentena
curl.exe http://127.0.0.1:8000/api/v1/admin/cuarentena
# -> copiar el "id" del caso con patron FIN_PROMOCION

# 3. Validarlo
curl.exe -X POST http://127.0.0.1:8000/api/v1/admin/validar/<ID_DEL_CASO> `
  -H "Content-Type: application/json" `
  -d '{"validado_por":"AGENTE_MOVISTAR"}'

# 4. Repetir la consulta (nueva sesion)
curl.exe -X POST http://127.0.0.1:8000/api/v1/chat `
  -H "Content-Type: application/json" `
  -d '{"session_id":"demo-2","user_id":"user_a_fin_promo","message":"por que subio mi recibo?"}'
# -> confidence_score: 100, caso_validado: true
```

## Por qué este ciclo es el diferenciador, no solo una feature

Cualquier chatbot puede explicar un recibo con un LLM y un poco de contexto.
Lo que no es común es que el sistema:

- **Nunca confía en la autopercepción del modelo.** El 80% inicial y el 100%
  final no son números que el LLM reporta sobre sí mismo — se calculan desde
  señales objetivas del backend (¿hay caso validado?, ¿hay datos
  suficientes?, ¿el evento es reconocible?).
- **No promueve conocimiento por volumen, sino por validación supervisada.**
  Un caso con muchos usos pero sin aprobación humana o de feedback posterior
  sigue sin ser "verdad" para el sistema.
- **Hace visible la diferencia al usuario final**, no solo en un log interno:
  la insignia de confianza en el chat web es la prueba de que el aprendizaje
  ocurrió, no una afirmación de marketing.

Esto ataca directamente el problema que el reto plantea (~40% de llamadas por
confusión de recibo): la curva de reducción de llamadas no es plana en el
tiempo, es creciente, porque cada consulta repetida cuesta cada vez menos.
