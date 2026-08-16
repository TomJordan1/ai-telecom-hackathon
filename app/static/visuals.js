// ---------------------------------------------------------------------------
// Lucía - Visualizaciones de Facturación (Web)
// ---------------------------------------------------------------------------
// Responsabilidad única: recibir el ChatResponse ya verificado por el backend
// y convertirlo en tarjetas HTML/CSS. Este archivo NO calcula variaciones de
// negocio ni consulta ningún endpoint: solo suma/ordena valores ya calculados
// por el motor determinista para poder presentarlos (totales, porcentajes de
// barra). La misma filosofía que app/services/image_renderer.py del lado de
// WhatsApp: una sola fuente de verdad (el backend), dos presentaciones.
//
// Se carga ANTES que app.js en index.html. `messagesContainer` y
// `scrollToBottom` se referencian dentro de funciones (no en el nivel
// superior del archivo), así que para cuando se llaman ya existen: los
// <script> clásicos comparten el mismo ámbito global y se ejecutan en orden.
// ---------------------------------------------------------------------------

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function fmtMonto(n) {
    const num = Number(n) || 0;
    const signo = num < 0 ? '-' : '';
    return `${signo}S/ ${Math.abs(num).toFixed(2)}`;
}

function fmtMontoConSigno(n) {
    const num = Number(n) || 0;
    const signo = num >= 0 ? '+' : '-';
    return `${signo}S/ ${Math.abs(num).toFixed(2)}`;
}

const MESES_CORTOS_VISUAL = {
    '01': 'Ene', '02': 'Feb', '03': 'Mar', '04': 'Abr',
    '05': 'May', '06': 'Jun', '07': 'Jul', '08': 'Ago',
    '09': 'Sep', '10': 'Oct', '11': 'Nov', '12': 'Dic',
};

function mesCorto(monthStr) {
    if (!monthStr || typeof monthStr !== 'string' || !monthStr.includes('-')) {
        return monthStr || '';
    }
    const partes = monthStr.split('-');
    return MESES_CORTOS_VISUAL[partes[1]] || monthStr;
}

// ---------------------------------------------------------------------------
// Visual 1 — "Así cambió tu recibo" (variation_breakdown)
// ---------------------------------------------------------------------------

function renderVariationCard(data) {
    const items = data.variation_breakdown || [];
    if (items.length === 0) return null;

    const currentBreakdown = data.current_bill_breakdown || [];
    const historial = data.historical_bills_summary || [];

    let currentTotal = currentBreakdown.reduce((s, i) => s + Number(i.monto || 0), 0);
    const sumImpacto = items.reduce((s, i) => s + Number(i.impacto || 0), 0);

    let previousTotal;
    if (historial.length > 0) {
        previousTotal = Number(historial[0].amount || 0);
    } else {
        previousTotal = currentTotal - sumImpacto;
    }
    if (!currentTotal) {
        currentTotal = previousTotal + sumImpacto;
    }

    const diferencia = Math.round((currentTotal - previousTotal) * 100) / 100;
    const deltaClass = diferencia > 0.004 ? 'up' : (diferencia < -0.004 ? 'down' : 'neutral');

    let rows = '';
    items.forEach(item => {
        const impacto = Number(item.impacto || 0);
        const itemClass = impacto > 0.004 ? 'up' : (impacto < -0.004 ? 'down' : 'neutral');
        const conceptos = (item.conceptos || []).join(', ');
        rows += `
            <div class="variation-row">
                <div class="variation-info">
                    <strong>${escapeHtml(item.etiqueta)}</strong>
                    ${conceptos ? `<span>${escapeHtml(conceptos)}</span>` : ''}
                </div>
                <div class="variation-impact ${itemClass}">${fmtMontoConSigno(impacto)}</div>
            </div>
        `;
    });

    const card = document.createElement('div');
    card.className = 'visual-card variation-card';
    card.innerHTML = `
        <div class="visual-header">
            <span class="visual-icon">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                    <polyline points="14 2 14 8 20 8"></polyline>
                    <line x1="16" y1="13" x2="8" y2="13"></line>
                    <line x1="16" y1="17" x2="8" y2="17"></line>
                </svg>
            </span>
            <div>
                <div class="visual-title">Así cambió tu recibo</div>
                <div class="visual-subtitle">Comparación con el ciclo anterior</div>
            </div>
        </div>
        <div class="amount-comparison">
            <div class="amount-block">
                <span>Anterior</span>
                <strong>${fmtMonto(previousTotal)}</strong>
            </div>
            <div class="arrow">&#8594;</div>
            <div class="amount-block current">
                <span>Actual</span>
                <strong>${fmtMonto(currentTotal)}</strong>
            </div>
        </div>
        <div class="variation-amount ${deltaClass}">${fmtMontoConSigno(diferencia)}</div>
        <div class="visual-section-label">¿Qué provocó el cambio?</div>
        <div class="variation-list">${rows}</div>
    `;
    return card;
}

// ---------------------------------------------------------------------------
// Visual 2 — "¿En qué se compone tu recibo?" (current_bill_breakdown)
// ---------------------------------------------------------------------------

function renderBreakdownCard(data) {
    const items = data.current_bill_breakdown || [];
    if (items.length === 0) return null;

    const total = items.reduce((s, i) => s + Number(i.monto || 0), 0);
    const maxMonto = Math.max(...items.map(i => Number(i.monto || 0)), 1);

    let rows = '';
    items.forEach(item => {
        const monto = Number(item.monto || 0);
        const pct = Math.max((monto / maxMonto) * 100, 4);
        const conceptos = (item.conceptos || []).join(', ');
        rows += `
            <div class="breakdown-row">
                <div class="breakdown-label">
                    <strong>${escapeHtml(item.etiqueta)}</strong>
                    <span>${fmtMonto(monto)}</span>
                </div>
                <div class="breakdown-bar-track"><div class="breakdown-bar-fill" style="width:${pct}%"></div></div>
                ${conceptos ? `<div class="breakdown-concepts">${escapeHtml(conceptos)}</div>` : ''}
            </div>
        `;
    });

    const card = document.createElement('div');
    card.className = 'visual-card breakdown-card';
    card.innerHTML = `
        <div class="visual-header">
            <span class="visual-icon">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="20" x2="18" y2="10"></line>
                    <line x1="12" y1="20" x2="12" y2="4"></line>
                    <line x1="6" y1="20" x2="6" y2="14"></line>
                </svg>
            </span>
            <div>
                <div class="visual-title">¿En qué se compone tu recibo?</div>
                <div class="visual-subtitle">Desglose por categoría de cargo</div>
            </div>
        </div>
        <div class="breakdown-list">${rows}</div>
        <div class="breakdown-total">
            <span>Total del recibo</span>
            <strong>${fmtMonto(total)}</strong>
        </div>
    `;
    return card;
}

// ---------------------------------------------------------------------------
// Visual 3 — "Evolución de tu recibo" (historical_bills_summary + actual)
// ---------------------------------------------------------------------------

function renderHistoryCard(data) {
    const historial = (data.historical_bills_summary || []).slice().reverse(); // antiguo -> reciente
    const currentTotal = (data.current_bill_breakdown || []).reduce((s, i) => s + Number(i.monto || 0), 0);

    const puntos = historial.map(b => ({
        label: mesCorto(b.month),
        amount: Number(b.amount || 0),
        actual: false,
    }));
    if (currentTotal) {
        puntos.push({ label: 'Actual', amount: currentTotal, actual: true });
    }
    if (puntos.length < 2) return null;

    // Gráfico de LÍNEA, no de barras: cuando los montos son parecidos entre
    // sí (ej. S/80 vs S/89), una barra no logra mostrar diferencia visible
    // aunque el cálculo sea correcto — el ojo detecta pendientes mucho mejor
    // que diferencias de pocos píxeles de altura. Escala en su propio rango
    // (no desde cero) porque es un gráfico de tendencia, no de proporción.
    const W = 400, H = 130, PAD_X = 24, PAD_TOP = 26, PAD_BOTTOM = 8;
    const montos = puntos.map(p => p.amount);
    const min = Math.min(...montos);
    const max = Math.max(...montos);
    const rango = (max - min) || 1;

    const coords = puntos.map((p, i) => {
        const x = puntos.length === 1 ? W / 2 : PAD_X + (i * (W - 2 * PAD_X)) / (puntos.length - 1);
        const yUtil = H - PAD_TOP - PAD_BOTTOM;
        const y = PAD_TOP + yUtil - ((p.amount - min) / rango) * yUtil;
        return { ...p, x, y };
    });

    const polylinePoints = coords.map(c => `${c.x},${c.y}`).join(' ');

    const dots = coords.map(c => `
        <circle cx="${c.x}" cy="${c.y}" r="${c.actual ? 5 : 3.5}"
            fill="${c.actual ? 'var(--primary)' : '#ffffff'}"
            stroke="${c.actual ? 'var(--primary)' : '#a5b4fc'}" stroke-width="2"></circle>
        <text x="${c.x}" y="${c.y - 12}" text-anchor="middle"
            font-size="11" font-weight="700"
            fill="${c.actual ? 'var(--primary)' : '#475569'}">S/${Math.round(c.amount)}</text>
    `).join('');

    const labels = coords.map(c => `
        <div class="history-point-label" style="left:${(c.x / W) * 100}%">
            <span class="${c.actual ? 'current' : ''}">${escapeHtml(c.label)}</span>
        </div>
    `).join('');

    const card = document.createElement('div');
    card.className = 'visual-card history-card';
    card.innerHTML = `
        <div class="visual-header">
            <span class="visual-icon">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline>
                    <polyline points="17 6 23 6 23 12"></polyline>
                </svg>
            </span>
            <div>
                <div class="visual-title">Evolución de tu recibo</div>
                <div class="visual-subtitle">Últimos ciclos facturados</div>
            </div>
        </div>
        <div class="history-line-wrap">
            <svg class="history-line-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
                <polyline points="${polylinePoints}" fill="none" stroke="#a5b4fc" stroke-width="2.5"
                    stroke-linecap="round" stroke-linejoin="round"></polyline>
                ${dots}
            </svg>
            <div class="history-labels-row">${labels}</div>
        </div>
    `;
    return card;
}

// ---------------------------------------------------------------------------
// Selección de un único visual por turno + inserción en el chat
// ---------------------------------------------------------------------------
//
// Igual que en image_renderer.py: se muestra COMO MÁXIMO una infografía por
// respuesta (variación > desglose > histórico), para que el chat no se
// convierta en un dashboard. La prioridad la decide qué datos verificados
// trajo el turno, no una lista fija de intent_category — así no hay que
// tocar este archivo si el backend agrega un evento de facturación nuevo.

function renderVisualizations(data) {
    if (!data || data.requires_human_intervention) return;

    // Antes: se elegía UNA infografía automáticamente y se insertaba en
    // cada turno. Se cambia a "bajo demanda": se ofrecen botones solo para
    // las infografías que sí tienen datos suficientes (cada render*Card
    // devuelve null si no aplica), y el usuario decide cuál ver — nunca se
    // repite sola ni aparece en el primer mensaje (el saludo no trae
    // variation_breakdown / current_bill_breakdown / historical_bills_summary).
    const opciones = [
        { label: 'Por qué cambió', build: () => renderVariationCard(data) },
        { label: 'En qué se compone', build: () => renderBreakdownCard(data) },
        { label: 'Evolución histórica', build: () => renderHistoryCard(data) },
    ];

    const disponibles = opciones.filter(o => o.build() !== null);
    if (disponibles.length === 0) return;

    const row = document.createElement('div');
    row.className = 'message-row bot';

    const avatarSpacer = document.createElement('div');
    avatarSpacer.className = 'bot-avatar-mini';
    avatarSpacer.style.visibility = 'hidden';

    const wrap = document.createElement('div');
    wrap.className = 'bubble-wrap bot';

    const botonera = document.createElement('div');
    botonera.className = 'visual-picker';

    disponibles.forEach(o => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'visual-picker-btn';
        btn.textContent = o.label;
        btn.addEventListener('click', () => {
            const card = o.build(); // se reconstruye fresco, no se reutiliza el nodo
            if (!card) return;
            insertVisualCard(card);
            btn.disabled = true;
            btn.classList.add('used');
        });
        botonera.appendChild(btn);
    });

    wrap.appendChild(botonera);
    row.appendChild(avatarSpacer);
    row.appendChild(wrap);

    messagesContainer.appendChild(row);
    scrollToBottom();
}

function insertVisualCard(card) {
    const row = document.createElement('div');
    row.className = 'message-row bot';

    const avatarSpacer = document.createElement('div');
    avatarSpacer.className = 'bot-avatar-mini';
    avatarSpacer.style.visibility = 'hidden';

    const wrap = document.createElement('div');
    wrap.className = 'bubble-wrap bot';
    wrap.style.width = '100%';
    wrap.appendChild(card);

    row.appendChild(avatarSpacer);
    row.appendChild(wrap);

    messagesContainer.appendChild(row);
    scrollToBottom();
}
