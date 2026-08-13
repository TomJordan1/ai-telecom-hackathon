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
    if (type === 'evidence') bubble.style.fontFamily = 'monospace';

    let formatted = text
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/`([^`]+)`/g, '<code style="background:#e2e8f0;padding:2px 6px;border-radius:4px;font-family:monospace;font-size:0.85em;">$1</code>')
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

function addConfidenceBadge(parentWrap, confidenceScore, casoValidado) {
    const badge = document.createElement('div');
    badge.className = `confidence-badge ${casoValidado ? 'validated' : 'learning'}`;
    badge.innerHTML = casoValidado
        ? `Caso validado · Confianza ${confidenceScore}%`
        : `Caso en aprendizaje · Confianza ${confidenceScore}%`;
    parentWrap.appendChild(badge);
    scrollToBottom();
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

        // Si se evaluó facturación, mostrar badge de confianza y feedback
        const esFacturacion = !data.compliance_triggered
            && !['SALUDO', 'DESPEDIDA', 'AGRADECIMIENTO', 'FUERA_DE_DOMINIO', 'SOLICITUD_AGENTE',
                'CONSULTA_DEUDA', 'CONSULTA_PLAN_ACTUAL', 'CONSULTA_GENERAL_PLANES', 'CONSULTA_SIN_CUENTA']
                .includes(data.intent_category);

        if (esFacturacion && ultimoWrap) {
            addConfidenceBadge(ultimoWrap, data.confidence_score, data.caso_validado);
        }

        if (ultimoWrap) {
            addFeedbackButtons(ultimoWrap);
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
