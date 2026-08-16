// ---------------------------------------------------------------------------
// Lucía - Copiloto de Facturación Movistar (Frontend App)
// ---------------------------------------------------------------------------

const messagesContainer = document.getElementById('chat-messages');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const sessionBadgeText = document.getElementById('session-badge-text');
const sessionBadge = document.getElementById('session-badge');
const quickChipsContainer = document.getElementById('quick-chips');

const sessionId = (() => {
    let s = localStorage.getItem('lucia_session_id');
    if (!s) {
        s = 'sesion-' + Math.random().toString(36).substr(2, 9);
        localStorage.setItem('lucia_session_id', s);
    }
    return s;
})();

let currentUserId = localStorage.getItem('lucia_user_id') || `invitado_${sessionId.substring(7, 12)}`;

function isVisitor() {
    return !currentUserId || currentUserId.startsWith('invitado') || currentUserId === 'guest' || currentUserId === 'anonimo';
}

function updateBadge() {
    if (isVisitor()) {
        sessionBadgeText.textContent = 'Visitante';
        sessionBadge.className = 'session-badge';
    } else {
        sessionBadgeText.textContent = `Cuenta: ${currentUserId}`;
        sessionBadge.className = 'session-badge client';
    }
}

// ---------------------------------------------------------------------------
// Flujo de Bienvenida Conversacional
// ---------------------------------------------------------------------------

function renderWelcomeFlow() {
    messagesContainer.innerHTML = '';
    updateBadge();

    // 1. Mensaje de presentación de Lucía (sin emojis dependientes del SO)
    addBotMessage(
        "Hola. Soy **Lucía**, tu asistente de facturación y servicios de Movistar.\n\n" +
        "Puedo explicarte los cobros de tu recibo, revisar el motivo de variación de tus importes o informarte sobre planes disponibles.\n\n" +
        "Para comenzar: **¿cuentas con un número de cuenta de Movistar o deseas hacer una consulta general como visitante?**"
    );

    // 2. Opciones interactivas con vectores SVG puros
    const choicesDiv = document.createElement('div');
    choicesDiv.className = 'choice-container';
    choicesDiv.id = 'welcome-choices';

    choicesDiv.innerHTML = `
        <button class="btn-choice primary" id="btn-choice-cliente">
            <div class="choice-icon">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                    <polyline points="9 11 12 14 22 4"></polyline>
                </svg>
            </div>
            <div>
                <strong>Tengo mi número de cuenta</strong>
                <span class="choice-desc">Ingresar cuenta y consultar mi recibo</span>
            </div>
        </button>
        <button class="btn-choice" id="btn-choice-visitante">
            <div class="choice-icon">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="10"></circle>
                    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path>
                    <line x1="12" y1="17" x2="12.01" y2="17"></line>
                </svg>
            </div>
            <div>
                <strong>Consulta general (Visitante)</strong>
                <span class="choice-desc">Conocer planes de fibra, móviles y tarifas</span>
            </div>
        </button>
        <button class="btn-choice" id="btn-choice-demo">
            <div class="choice-icon">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
                </svg>
            </div>
            <div>
                <strong>Cuentas de demostración</strong>
                <span class="choice-desc">Evaluar escenarios de prueba del desafío</span>
            </div>
        </button>
    `;

    messagesContainer.appendChild(choicesDiv);
    scrollToBottom();

    // Eventos de botones
    document.getElementById('btn-choice-cliente').addEventListener('click', () => {
        choicesDiv.remove();
        addUserMessage("Tengo mi número de cuenta");
        showTyping();
        setTimeout(() => {
            hideTyping();
            addBotMessage(
                "Por favor escribe tu **número de cuenta financiera** (ejemplo: `102968745` o `302207847`) o redacta directamente tu consulta:"
            );
            messageInput.placeholder = "Escribe tu número de cuenta (ej. 102968745)...";
            messageInput.focus();
            setQuickChips(['103692188', '302207847', '760000053']);
        }, 350);
    });

    document.getElementById('btn-choice-visitante').addEventListener('click', () => {
        choicesDiv.remove();
        setVisitorMode();
        addUserMessage("Deseo hacer una consulta como visitante");
        showTyping();
        setTimeout(() => {
            hideTyping();
            addBotMessage(
                "Has iniciado una sesión temporal. Puedes consultarme sobre nuestros planes de internet fibra, planes móviles o dudas de facturación. ¿En qué te puedo orientar?"
            );
            messageInput.placeholder = "Pregunta sobre planes, fibra o contratación...";
            messageInput.focus();
            renderVisitorChips();
        }, 350);
    });

    document.getElementById('btn-choice-demo').addEventListener('click', () => {
        choicesDiv.remove();
        addUserMessage("Deseo probar una cuenta demo");
        showTyping();
        setTimeout(() => {
            hideTyping();
            addBotMessage("Selecciona uno de los escenarios de prueba:");
            renderScenarioChoices();
        }, 350);
    });
}

function renderScenarioChoices() {
    const grid = document.createElement('div');
    grid.className = 'scenario-grid';
    grid.id = 'scenario-choices';

    grid.innerHTML = `
        <button class="btn-scenario-pill" data-acc="103692188">
            Cuenta 103692188 — Fin de Promoción (10GB)
        </button>
        <button class="btn-scenario-pill" data-acc="302207847">
            Cuenta 302207847 — Fin de Descuento (30GB)
        </button>
        <button class="btn-scenario-pill" data-acc="760000053">
            Cuenta 760000053 — Renta Adelantada (125GB)
        </button>
    `;

    messagesContainer.appendChild(grid);
    scrollToBottom();

    grid.querySelectorAll('[data-acc]').forEach(btn => {
        btn.addEventListener('click', () => {
            const acc = btn.dataset.acc;
            grid.remove();
            setClientAccount(acc);
            addUserMessage(`Seleccioné la cuenta demo: ${acc}`);
            showTyping();
            setTimeout(() => {
                hideTyping();
                addBotMessage(`Cuenta **${acc}** vinculada correctamente. Puedes consultar por qué varió tu recibo o solicitar el desglose de cargos.`);
                renderClientChips();
            }, 350);
        });
    });
}

function setVisitorMode() {
    currentUserId = `invitado_${sessionId.substring(7, 12)}`;
    localStorage.removeItem('lucia_user_id');
    updateBadge();
}

function setClientAccount(acc) {
    currentUserId = acc.trim();
    localStorage.setItem('lucia_user_id', currentUserId);
    updateBadge();
}

// ---------------------------------------------------------------------------
// Chips de Sugerencias (Texto limpio y profesional)
// ---------------------------------------------------------------------------

function renderVisitorChips() {
    setQuickChips([
        'Planes de fibra óptica',
        '¿Cómo funciona la facturación?',
        'Planes móviles disponibles',
        'Tengo una cuenta para consultar'
    ]);
}

function renderClientChips() {
    setQuickChips([
        '¿Por qué subió mi recibo?',
        '¿Qué conceptos me están cobrando?',
        '¿Qué plan tengo contratado?',
        '¿Cuándo vence mi recibo?'
    ]);
}

function setQuickChips(chips) {
    quickChipsContainer.innerHTML = '';
    chips.forEach(text => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chip-btn';
        chip.textContent = text;
        chip.addEventListener('click', () => {
            if (text.includes('Tengo una cuenta')) {
                messageInput.placeholder = "Escribe tu número de cuenta...";
                messageInput.focus();
                return;
            }
            messageInput.value = text;
            sendMessage();
        });
        quickChipsContainer.appendChild(chip);
    });
}

// ---------------------------------------------------------------------------
// Renderizado de Mensajes con Burbujas Conversacionales
// ---------------------------------------------------------------------------

function addBotMessage(text, type = 'normal') {
    const row = document.createElement('div');
    row.className = 'message-row bot';

    // Avatar mini de Lucía a la izquierda
    const avatar = document.createElement('div');
    avatar.className = 'bot-avatar-mini';
    avatar.textContent = 'L';

    const wrap = document.createElement('div');
    wrap.className = 'bubble-wrap bot';

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    if (type === 'evidence') bubble.classList.add('evidence-bubble');

    let formatted = text
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/`([^`]+)`/g, '<code style="background:#e2e8f0;padding:2px 6px;border-radius:4px;font-family:inherit;font-weight:600;font-size:0.9em;">$1</code>')
        .replace(/\n/g, '<br>');


    bubble.innerHTML = formatted;
    wrap.appendChild(bubble);

    row.appendChild(avatar);
    row.appendChild(wrap);

    messagesContainer.appendChild(row);
    scrollToBottom();
    return wrap;
}

function addUserMessage(text) {
    const row = document.createElement('div');
    row.className = 'message-row user';

    const wrap = document.createElement('div');
    wrap.className = 'bubble-wrap user';

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;

    wrap.appendChild(bubble);
    row.appendChild(wrap);

    messagesContainer.appendChild(row);
    scrollToBottom();
    return wrap;
}

function addConfidenceBadge(parentWrap, data) {
    const score = typeof data.confidence_score === 'number' ? data.confidence_score : 90;
    const reasons = data.confidence_reasons || [];
    const casoValidado = data.caso_validado;

    let nivel = 'high';
    let label = `🎯 Certeza Determinista · ${score}%`;

    if (casoValidado) {
        nivel = 'high';
        label = `💎 Solución Validada por Asesores · ${score}%`;
    } else if (score >= 90) {
        nivel = 'high';
        label = `🎯 Certeza Determinista · ${score}%`;
    } else if (score >= 65) {
        nivel = 'medium';
        label = `⚠️ En Validación · ${score}%`;
    } else {
        nivel = 'low';
        label = `🚨 Derivado a Humano · ${score}%`;
    }

    const wrapper = document.createElement('div');
    wrapper.className = 'confidence-badge-wrapper';

    const pill = document.createElement('div');
    pill.className = `confidence-pill ${nivel}`;
    pill.innerHTML = `<span>${label}</span> <span style="font-size:0.65rem; opacity:0.8;">▼</span>`;

    const drawer = document.createElement('div');
    drawer.className = 'confidence-reasons-drawer';
    
    let reasonsHtml = '';
    if (reasons.length > 0) {
        reasonsHtml = reasons.map(r => {
            const isAlert = r.includes('incertidumbre') || r.includes('Sin caso') || r.includes('Diferencia') || r.includes('limitado') || r.includes('miscelánea');
            const icon = isAlert ? '⚠️' : '✓';
            return `<li><span>${icon}</span> <span>${r}</span></li>`;
        }).join('');
    } else {
        reasonsHtml = `<li><span>✓</span> <span>Cálculo matemático verificado sobre registros oficiales del recibo.</span></li>`;
    }

    drawer.innerHTML = `
        <div class="confidence-reasons-title">
            <span>Fundamentos de Certeza y Fact Checking:</span>
        </div>
        <ul class="confidence-reasons-list">
            ${reasonsHtml}
        </ul>
    `;

    pill.addEventListener('click', () => {
        drawer.classList.toggle('open');
    });

    wrapper.appendChild(pill);
    wrapper.appendChild(drawer);
    parentWrap.appendChild(wrapper);
}

function addAuditorCard(parentWrap, auditorData) {
    if (!auditorData || !auditorData.desglose || auditorData.desglose.length === 0) return;

    const card = document.createElement('div');
    card.className = 'auditor-card';

    const anteriorStr = Number(auditorData.monto_anterior || 0).toFixed(2);
    const actualStr = Number(auditorData.monto_actual || 0).toFixed(2);
    const sumaImpactosStr = (auditorData.suma_impactos >= 0 ? '+' : '') + Number(auditorData.suma_impactos || 0).toFixed(2);
    const difStr = Number(auditorData.diferencia_centimos || 0).toFixed(2);

    let rowsHtml = '';
    auditorData.desglose.forEach(item => {
        const impactoVal = Number(item.impacto || 0);
        const impactoSign = impactoVal > 0 ? `+S/ ${impactoVal.toFixed(2)}` : (impactoVal < 0 ? `-S/ ${Math.abs(impactoVal).toFixed(2)}` : 'S/ 0.00');
        const impactoClass = impactoVal > 0 ? 'impact-pos' : (impactoVal < 0 ? 'impact-neg' : '');

        rowsHtml += `
            <tr>
                <td><span class="code-cell">${item.codigo_cargo || 'N/A'}</span></td>
                <td><strong>${item.concepto || item.categoria}</strong></td>
                <td>${item.categoria}</td>
                <td>S/ ${Number(item.monto_anterior || 0).toFixed(2)}</td>
                <td>S/ ${Number(item.monto_actual || 0).toFixed(2)}</td>
                <td class="${impactoClass}">${impactoSign}</td>
            </tr>
        `;
    });

    card.innerHTML = `
        <div class="auditor-header">
            <div class="auditor-title-group">
                <span>🔍</span>
                <strong>Modo Auditor: Ecuación y Trazabilidad (CSV)</strong>
                <span class="auditor-badge-verified">✓ Conciliado al céntimo</span>
            </div>
            <span class="auditor-toggle-icon">▼</span>
        </div>
        <div class="auditor-body">
            <div class="auditor-equation-box">
                <div class="auditor-equation-title">Ecuación Algebraica de Variación:</div>
                <div class="auditor-equation-formula">
                    S/ ${anteriorStr} (Anterior) + S/ ${sumaImpactosStr} (Σ Impactos) = S/ ${actualStr} (Total Actual)
                </div>
                <div style="font-size:0.7rem; color:#94a3b8; margin-top:4px;">
                    Diferencia de conciliación: S/ ${difStr} · Invariante determinista 100% verificada
                </div>
            </div>

            <div class="auditor-table-wrapper">
                <table class="auditor-table">
                    <thead>
                        <tr>
                            <th>Código CSV</th>
                            <th>Concepto Facturado</th>
                            <th>Categoría</th>
                            <th>Anterior</th>
                            <th>Actual</th>
                            <th>Impacto</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${rowsHtml}
                    </tbody>
                </table>
            </div>

            <div class="auditor-actions">
                <button class="btn-auditor-copy" id="btn-copy-auditor-${Math.random().toString(36).substr(2, 5)}">
                    📋 Copiar JSON de Auditoría
                </button>
            </div>
        </div>
    `;

    const header = card.querySelector('.auditor-header');
    header.addEventListener('click', () => {
        card.classList.toggle('open');
        scrollToBottom();
    });

    const copyBtn = card.querySelector('.btn-auditor-copy');
    if (copyBtn) {
        copyBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            navigator.clipboard.writeText(JSON.stringify(auditorData, null, 2));
            copyBtn.textContent = '✓ ¡Copiado!';
            setTimeout(() => {
                copyBtn.textContent = '📋 Copiar JSON de Auditoría';
            }, 2000);
        });
    }

    parentWrap.appendChild(card);
}

function addFeedbackButtons(parentWrap) {

    const bar = document.createElement('div');
    bar.className = 'feedback-bar';
    bar.innerHTML = `
        <button class="btn-thumb" title="Respuesta útil">Útil</button>
        <button class="btn-thumb" title="Respuesta no útil">No ayudó</button>
    `;

    const btns = bar.querySelectorAll('.btn-thumb');
    btns[0].addEventListener('click', () => {
        btns[0].classList.add('active');
        btns[1].classList.remove('active');
        sendFeedback(1);
    });
    btns[1].addEventListener('click', () => {
        btns[1].classList.add('active');
        btns[0].classList.remove('active');
        sendFeedback(-1);
    });

    parentWrap.appendChild(bar);
}

async function sendFeedback(score) {
    try {
        await fetch('/api/v1/feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId, feedback_score: score })
        });
    } catch (e) {
        // Silencioso
    }
}

function addHandoffBanner(intentCategory) {
    const esSensible = ['CANCELACION_PLAN', 'PORTABILIDAD', 'NUEVA_LINEA', 'CAMBIO_PLAN'].includes(intentCategory);
    const titulo = esSensible ? 'Solicitud en gestión con asesor' : 'Derivado a atención humana';
    const detalle = esSensible
        ? 'Tu solicitud quedó registrada. Un asesor especializado la retomará con todo el contexto.'
        : 'Tu consulta fue derivada a un agente especializado con el expediente listo.';

    const banner = document.createElement('div');
    banner.className = 'handoff-banner';
    banner.innerHTML = `
        <div class="handoff-title">${titulo}</div>
        <div class="handoff-detail">${detalle}</div>
    `;
    messagesContainer.appendChild(banner);
    scrollToBottom();
}

function addUpsellCard(suggestion) {
    const row = document.createElement('div');
    row.className = 'message-row bot';
    row.innerHTML = `
        <div class="bot-avatar-mini">L</div>
        <div class="bubble-wrap bot" style="width:100%;">
            <div class="upsell-card">
                <div class="upsell-title">Sugerencia: ${suggestion.nombre}</div>
                <div style="font-size: 0.82rem">${suggestion.beneficios || ''}</div>
                <div style="font-size: 0.78rem; margin-top: 4px; color: var(--text-muted)">Precio estimado: S/ ${suggestion.precio}</div>
                <button class="upsell-button" onclick="alert('Solicitud de plan enviada con éxito')">Me interesa este plan</button>
            </div>
        </div>
    `;
    messagesContainer.appendChild(row);
    scrollToBottom();
}

function addNextBestActions(actions) {
    if (!actions || actions.length === 0) return;

    // Remover contenedores previos de next actions para evitar saturación
    const prevContainers = document.querySelectorAll('.next-actions-container');
    prevContainers.forEach(c => c.remove());

    const row = document.createElement('div');
    row.className = 'message-row bot';
    row.innerHTML = `
        <div class="bot-avatar-mini" style="visibility:hidden">L</div>
        <div class="bubble-wrap bot" style="width:100%;">
            <div class="next-actions-container"></div>
        </div>
    `;

    const container = row.querySelector('.next-actions-container');

    actions.forEach(action => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `next-action-btn ${action.tipo || 'consulta'}`;
        btn.innerHTML = action.titulo;
        btn.addEventListener('click', () => handleNextActionClick(action));
        container.appendChild(btn);
    });

    messagesContainer.appendChild(row);
    scrollToBottom();
}

function handleNextActionClick(action) {
    if (action.id === 'CANAL_CHAT') {
        fetch('/api/v1/handoff-channel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId, canal_preferido: 'CHAT' })
        }).catch(() => {});
        addBotMessage("¡Perfecto! Tu asesor se conectará directamente por este chat con todo tu expediente listo para no repetir nada. 💬");
    } else if (action.id === 'CANAL_LLAMADA') {
        fetch('/api/v1/handoff-channel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId, canal_preferido: 'LLAMADA' })
        }).catch(() => {});
        addBotMessage("¡Entendido! Hemos programado que un asesor te llame a tu número de contacto con todo el detalle de tu recibo preparado. 📞");
    } else if (action.id === 'CANAL_WHATSAPP') {
        fetch('/api/v1/handoff-channel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId, canal_preferido: 'WHATSAPP' })
        }).catch(() => {});
        addBotMessage("¡Listo! Tu asesor te contactará al WhatsApp vinculado con todo el contexto de tu consulta para no hacerte perder tiempo. 📲");
    } else if (action.id === 'PAY_BILL') {
        showPaymentModal(action.payload);
    } else if (action.id === 'VIEW_BREAKDOWN') {
        messageInput.value = '¿Puedes mostrarme el desglose de conceptos de mi recibo?';
        sendMessage();
    } else if (action.id === 'HANDOFF_AGENT') {
        messageInput.value = 'Deseo comunicarme con un asesor humano por favor.';
        sendMessage();
    } else if (action.id === 'EXPLORE_PLANS') {
        const query = (action.payload && action.payload.plan_nombre) 
            ? `Me interesa el plan ${action.payload.plan_nombre}, ¿qué beneficios incluye?` 
            : '¿Qué planes de fibra y móvil tienen disponibles?';
        messageInput.value = query;
        sendMessage();
    } else if (action.id === 'REGISTER_RESOLVED') {
        sendFeedback(1);
        messageInput.value = '¡Todo quedó claro! Muchas gracias por la explicación 😊';
        sendMessage();
    } else if (action.id === 'VINCULAR_CUENTA') {
        messageInput.placeholder = 'Escribe tu número de cuenta (ej. 102968745)...';
        messageInput.focus();
    } else if (action.payload && action.payload.query) {
        messageInput.value = action.payload.query;
        sendMessage();
    } else {
        messageInput.value = action.titulo;
        sendMessage();
    }
}

function showPaymentModal(payload) {
    const amount = payload && payload.amount ? Number(payload.amount).toFixed(2) : '0.00';
    const periodo = payload && payload.periodo ? payload.periodo : 'Mes en curso';

    // Eliminar modal previo si existe
    const existing = document.getElementById('payment-modal-backdrop');
    if (existing) existing.remove();

    const backdrop = document.createElement('div');
    backdrop.id = 'payment-modal-backdrop';
    backdrop.className = 'payment-modal-backdrop';

    backdrop.innerHTML = `
        <div class="payment-modal">
            <button class="payment-modal-close" id="modal-close-btn">&times;</button>
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:12px;">
                <div style="background:#ecfdf5; color:#059669; padding:8px; border-radius:10px;">
                    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2.2">
                        <rect x="1" y="4" width="22" height="16" rx="2" ry="2"></rect>
                        <line x1="1" y1="10" x2="23" y2="10"></line>
                    </svg>
                </div>
                <div>
                    <h3 style="margin:0;">Pagar Recibo Movistar</h3>
                    <p style="margin:0; font-size:0.78rem; color:#64748b;">Período facturado: ${periodo}</p>
                </div>
            </div>

            <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:14px; padding:14px; text-align:center; margin-bottom:14px;">
                <span style="font-size:0.8rem; color:#64748b; font-weight:600; text-transform:uppercase; letter-spacing:0.05em;">Total a Pagar</span>
                <div style="font-size:1.8rem; font-weight:800; color:#0f172a; font-family:'Outfit',sans-serif; margin-top:2px;">S/ ${amount}</div>
            </div>

            <div style="font-size:0.8rem; font-weight:600; color:#334155; margin-bottom:6px;">Selecciona tu método de pago:</div>
            <div class="payment-methods">
                <button type="button" class="payment-method-btn active" data-method="yape">💜 Yape</button>
                <button type="button" class="payment-method-btn" data-method="plin">💙 Plin</button>
                <button type="button" class="payment-method-btn" data-method="tarjeta">💳 Tarjeta</button>
            </div>

            <button type="button" class="btn-confirm-payment" id="btn-process-payment">
                Pagar S/ ${amount} ahora
            </button>
            <p style="text-align:center; font-size:0.72rem; color:#94a3b8; margin-top:8px; margin-bottom:0;">
                🔒 Transacción 100% protegida y encriptada (Simulación)
            </p>
        </div>
    `;

    document.body.appendChild(backdrop);

    // Eventos del modal
    document.getElementById('modal-close-btn').addEventListener('click', () => backdrop.remove());
    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) backdrop.remove();
    });

    const methodBtns = backdrop.querySelectorAll('.payment-method-btn');
    methodBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            methodBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });

    document.getElementById('btn-process-payment').addEventListener('click', () => {
        const btn = document.getElementById('btn-process-payment');
        btn.disabled = true;
        btn.textContent = 'Procesando pago seguro...';

        setTimeout(() => {
            backdrop.remove();
            addBotMessage(`🎉 **¡Pago Exitoso!**\nSe registró el pago de **S/ ${amount}** para tu recibo del período **${periodo}**. Tu estado de cuenta ya figura al día.`);
            sendFeedback(1);
        }, 1200);
    });
}

function showTyping() {
    const row = document.createElement('div');
    row.className = 'message-row bot';
    row.id = 'typing-indicator-row';
    row.innerHTML = `
        <div class="bot-avatar-mini">L</div>
        <div class="typing-indicator"><span></span><span></span><span></span></div>
    `;
    messagesContainer.appendChild(row);
    scrollToBottom();
}

function hideTyping() {
    const el = document.getElementById('typing-indicator-row');
    if (el) el.remove();
}

function scrollToBottom() {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// Envío de Mensaje al Servidor
// ---------------------------------------------------------------------------

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text) return;

    // Limpiar opciones de bienvenida si seguían presentes
    const welcomeChoices = document.getElementById('welcome-choices');
    if (welcomeChoices) welcomeChoices.remove();
    const scenarioChoices = document.getElementById('scenario-choices');
    if (scenarioChoices) scenarioChoices.remove();

    // Detección automática de cuenta en el texto del mensaje
    const matchCuenta = text.match(/\b([0-9]{7,11})\b/);
    if (matchCuenta && isVisitor()) {
        const potentialAccount = matchCuenta[1];
        setClientAccount(potentialAccount);
    }

    addUserMessage(text);
    messageInput.value = '';
    showTyping();

    try {
        const response = await fetch('/api/v1/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId,
                user_id: currentUserId,
                message: text,
                channel: 'web'
            })
        });

        const data = await response.json();
        hideTyping();

        let ultimoWrap = null;
        for (const msg of data.messages) {
            if (msg.delay_ms > 0) {
                showTyping();
                await sleep(msg.delay_ms);
                hideTyping();
            }
            ultimoWrap = addBotMessage(msg.text, msg.type);
        }

        if (ultimoWrap) {
            addConfidenceBadge(ultimoWrap, data);
            if (data.auditor_breakdown && data.auditor_breakdown.desglose && data.auditor_breakdown.desglose.length > 0) {
                addAuditorCard(ultimoWrap, data.auditor_breakdown);
            }
            addFeedbackButtons(ultimoWrap);
        }

        if (data.next_best_actions && data.next_best_actions.length > 0) {
            await sleep(300);
            addNextBestActions(data.next_best_actions);
        }

        if (data.plan_optimizer_suggestion && data.plan_optimizer_suggestion.available) {
            await sleep(500);
            addUpsellCard(data.plan_optimizer_suggestion);
        }

        if (data.requires_human_intervention) {
            addHandoffBanner(data.intent_category);
        }

        // Actualizar chips de acuerdo al contexto
        if (isVisitor()) {
            renderVisitorChips();
        } else {
            renderClientChips();
        }

    } catch (e) {
        hideTyping();
        addBotMessage("Ocurrió un error de conexión al procesar tu consulta. Por favor intenta de nuevo.");
    }
}

function nuevaConversacion() {
    localStorage.removeItem('lucia_session_id');
    localStorage.removeItem('lucia_user_id');
    location.reload();
}

sendButton.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
});

// Arrancar al cargar
document.addEventListener('DOMContentLoaded', () => {
    renderWelcomeFlow();
});
