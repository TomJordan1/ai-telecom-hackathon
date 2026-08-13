// ---------------------------------------------------------------------------
// Lucía - Copiloto de Facturación Movistar (Frontend App)
// ---------------------------------------------------------------------------

const messagesContainer = document.getElementById('chat-messages');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
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
        sessionBadge.textContent = '👤 Visitante';
        sessionBadge.className = 'session-badge';
    } else {
        sessionBadge.textContent = `📱 Cuenta: ${currentUserId}`;
        sessionBadge.className = 'session-badge client';
    }
}

// ---------------------------------------------------------------------------
// Flujo de Bienvenida Conversacional
// ---------------------------------------------------------------------------

function renderWelcomeFlow() {
    messagesContainer.innerHTML = '';
    updateBadge();

    // 1. Mensaje de presentación de Lucía
    addBotMessage(
        "¡Hola! 👋 Soy **Lucía**, tu asistente inteligente de Movistar.\n\n" +
        "Puedo explicarte los cobros de tu recibo, revisar por qué varió tu facturación o informarte sobre nuestros planes disponibles.\n\n" +
        "Para comenzar: **¿tienes un número de cuenta de Movistar o deseas hacer una consulta libre como visitante?**"
    );

    // 2. Opciones interactivas directamente en el chat
    const choicesDiv = document.createElement('div');
    choicesDiv.className = 'choice-container';
    choicesDiv.id = 'welcome-choices';

    choicesDiv.innerHTML = `
        <button class="btn-choice primary" id="btn-choice-cliente">
            <span style="font-size:1.2rem;">📱</span>
            <div>
                <strong>Sí, tengo mi número de cuenta</strong>
                <span class="choice-desc">Ingresar cuenta y consultar mi recibo</span>
            </div>
        </button>
        <button class="btn-choice" id="btn-choice-visitante">
            <span style="font-size:1.2rem;">🌟</span>
            <div>
                <strong>No soy cliente (Consulta libre)</strong>
                <span class="choice-desc">Ver planes de fibra, móviles y políticas</span>
            </div>
        </button>
        <button class="btn-choice" id="btn-choice-demo">
            <span style="font-size:1.2rem;">⚡</span>
            <div>
                <strong>Probar cuenta demo</strong>
                <span class="choice-desc">Evaluar escenarios de facturación con 1 clic</span>
            </div>
        </button>
    `;

    messagesContainer.appendChild(choicesDiv);
    scrollToBottom();

    // Eventos de botones
    document.getElementById('btn-choice-cliente').addEventListener('click', () => {
        choicesDiv.remove();
        addUserMessage("Sí, tengo mi número de cuenta");
        showTyping();
        setTimeout(() => {
            hideTyping();
            addBotMessage(
                "¡Excelente! Por favor escribe tu **número de cuenta financiera** (ejemplo: `102968745` o `302207847`) o escríbeme directamente tu consulta:"
            );
            messageInput.placeholder = "Escribe tu número de cuenta (ej. 102968745)...";
            messageInput.focus();
            setQuickChips(['103692188', '302207847', '760000053']);
        }, 400);
    });

    document.getElementById('btn-choice-visitante').addEventListener('click', () => {
        choicesDiv.remove();
        setVisitorMode();
        addUserMessage("No soy cliente, quiero hacer una consulta libre");
        showTyping();
        setTimeout(() => {
            hideTyping();
            addBotMessage(
                "¡Bienvenido! 😊 Hemos iniciado una sesión temporal. Puedes preguntarme sobre nuestros planes de internet fibra, planes móviles o dudas generales de facturación. ¿En qué te puedo ayudar hoy?"
            );
            messageInput.placeholder = "Pregunta sobre planes, fibra o cómo contratar...";
            messageInput.focus();
            renderVisitorChips();
        }, 400);
    });

    document.getElementById('btn-choice-demo').addEventListener('click', () => {
        choicesDiv.remove();
        addUserMessage("Quiero probar una cuenta demo");
        showTyping();
        setTimeout(() => {
            hideTyping();
            addBotMessage("Elige uno de los escenarios de prueba del desafío:");
            renderScenarioChoices();
        }, 400);
    });
}

function renderScenarioChoices() {
    const grid = document.createElement('div');
    grid.className = 'scenario-grid';
    grid.id = 'scenario-choices';

    grid.innerHTML = `
        <button class="btn-scenario-pill" data-acc="103692188">🔹 Cuenta 103692188 (Fin de Promoción 10GB)</button>
        <button class="btn-scenario-pill" data-acc="302207847">🔹 Cuenta 302207847 (Fin Descuento 30GB)</button>
        <button class="btn-scenario-pill" data-acc="760000053">🔹 Cuenta 760000053 (Renta Adelantada 125GB)</button>
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
                addBotMessage(`¡Cuenta **${acc}** vinculada con éxito! 🎉 Puedes preguntarme por qué varió tu recibo o solicitar el desglose de cargos.`);
                renderClientChips();
            }, 400);
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
// Chips de Sugerencias
// ---------------------------------------------------------------------------

function renderVisitorChips() {
    setQuickChips([
        '¿Qué planes de fibra óptica tienen?',
        '¿Cómo funciona la facturación Movistar?',
        'Planes móviles disponibles',
        'Tengo una cuenta para consultar'
    ]);
}

function renderClientChips() {
    setQuickChips([
        '¿Por qué subió mi recibo?',
        '¿Qué me están cobrando este mes?',
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
            if (text === 'Tengo una cuenta para consultar') {
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
// Renderizado de Mensajes
// ---------------------------------------------------------------------------

function addBotMessage(text, type = 'normal') {
    const div = document.createElement('div');
    div.className = 'message bot';
    if (type === 'evidence') div.classList.add('evidence');

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    
    // Formateo markdown amigable
    let formatted = text
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/`([^`]+)`/g, '<code style="background:#e2e8f0;padding:2px 6px;border-radius:4px;font-family:monospace;font-size:0.85em;">$1</code>')
        .replace(/\n/g, '<br>');
        
    bubble.innerHTML = formatted;
    div.appendChild(bubble);
    messagesContainer.appendChild(div);
    scrollToBottom();
    return div;
}

function addUserMessage(text) {
    const div = document.createElement('div');
    div.className = 'message user';

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;

    div.appendChild(bubble);
    messagesContainer.appendChild(div);
    scrollToBottom();
    return div;
}

function addConfidenceBadge(parentDiv, confidenceScore, casoValidado) {
    const badge = document.createElement('div');
    badge.className = `confidence-badge ${casoValidado ? 'validated' : 'learning'}`;
    badge.innerHTML = casoValidado
        ? `✓ Caso validado · confianza ${confidenceScore}%`
        : `◌ Caso nuevo, en aprendizaje · confianza ${confidenceScore}%`;
    parentDiv.appendChild(badge);
    scrollToBottom();
}

function addFeedbackButtons(parentDiv) {
    const bar = document.createElement('div');
    bar.className = 'feedback-bar';
    bar.innerHTML = `
        <button class="btn-thumb" title="Respuesta útil">👍 Útil</button>
        <button class="btn-thumb" title="Respuesta no útil">👎 No ayudó</button>
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

    parentDiv.appendChild(bar);
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
    const titulo = esSensible ? '📋 Solicitud en gestión con asesor' : '👤 Derivado a atención humana';
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
    const div = document.createElement('div');
    div.className = 'message bot';
    div.innerHTML = `
        <div class="upsell-card">
            <div class="upsell-title">✨ ${suggestion.nombre}</div>
            <div style="font-size: 0.85rem">${suggestion.beneficios || ''}</div>
            <div style="font-size: 0.8rem; margin-top: 5px; color: var(--text-muted)">Precio estimado: S/ ${suggestion.precio}</div>
            <button class="upsell-button" onclick="alert('¡Solicitud de plan enviada con éxito!')">Me interesa este plan</button>
        </div>
    `;
    messagesContainer.appendChild(div);
    scrollToBottom();
}

function showTyping() {
    const div = document.createElement('div');
    div.className = 'message bot';
    div.id = 'typing-indicator';
    div.innerHTML = `<div class="typing-indicator"><span></span><span></span><span></span></div>`;
    messagesContainer.appendChild(div);
    scrollToBottom();
}

function hideTyping() {
    const el = document.getElementById('typing-indicator');
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

        let ultimoDiv = null;
        for (const msg of data.messages) {
            if (msg.delay_ms > 0) {
                showTyping();
                await sleep(msg.delay_ms);
                hideTyping();
            }
            ultimoDiv = addBotMessage(msg.text, msg.type);
        }

        // Si se evaluó facturación, mostrar badge de confianza y feedback
        const esFacturacion = !data.compliance_triggered
            && !['SALUDO', 'DESPEDIDA', 'AGRADECIMIENTO', 'FUERA_DE_DOMINIO', 'SOLICITUD_AGENTE',
                'CONSULTA_DEUDA', 'CONSULTA_PLAN_ACTUAL', 'CONSULTA_GENERAL_PLANES', 'CONSULTA_SIN_CUENTA']
                .includes(data.intent_category);

        if (esFacturacion && ultimoDiv) {
            addConfidenceBadge(ultimoDiv, data.confidence_score, data.caso_validado);
        }

        if (ultimoDiv) {
            addFeedbackButtons(ultimoDiv);
        }

        if (data.plan_optimizer_suggestion && data.plan_optimizer_suggestion.available) {
            await sleep(600);
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
