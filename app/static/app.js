const messagesContainer = document.getElementById('chat-messages');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const loginOverlay = document.getElementById('login-overlay');
const loginIdInput = document.getElementById('login-id');
const loginBtn = document.getElementById('login-btn');
const currentUserDisplay = document.getElementById('current-user-display');

let currentUserId = null;

const sessionId = (() => {
    const guardada = localStorage.getItem('lucia_session_id');
    if (guardada) return guardada;
    const nueva = 'sesion-' + Math.random().toString(36).substr(2, 9);
    localStorage.setItem('lucia_session_id', nueva);
    return nueva;
})();

// Login logic
loginBtn.addEventListener('click', () => {
    const id = loginIdInput.value.trim();
    if (id) {
        currentUserId = id;
        loginOverlay.classList.add('hidden');
        messageInput.disabled = false;
        sendButton.disabled = false;
        currentUserDisplay.textContent = `Usuario: ${id}`;
        
        // Start the conversation silently or send an initial greeting event if we wanted to
        // For now, the static greeting is already there. 
        // We could theoretically fetch history here if we exposed a GET endpoint.
    }
});
loginIdInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') loginBtn.click();
});


function nuevaConversacion() {
    localStorage.removeItem('lucia_session_id');
    location.reload();
}

function addMessage(text, sender, type = 'normal') {
    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', sender);
    if (type === 'evidence') messageDiv.classList.add('evidence');

    const bubble = document.createElement('div');
    bubble.classList.add('bubble');
    bubble.textContent = text;
    
    messageDiv.appendChild(bubble);
    messagesContainer.appendChild(messageDiv);
    scrollToBottom();
    return messageDiv;
}

function addConfidenceBadge(afterMessageDiv, confidenceScore, casoValidado) {
    const badge = document.createElement('div');
    badge.classList.add('confidence-badge');
    badge.classList.add(casoValidado ? 'validated' : 'learning');
    badge.innerHTML = casoValidado
        ? `✓ Caso validado · confianza ${confidenceScore}%`
        : `◌ Caso nuevo, en aprendizaje · confianza ${confidenceScore}%`;
    afterMessageDiv.appendChild(badge);
    scrollToBottom();
}

function addUpsellCard(suggestion) {
    const cardDiv = document.createElement('div');
    cardDiv.classList.add('message', 'bot');
    
    cardDiv.innerHTML = `
        <div class="upsell-card">
            <div class="upsell-title">✨ ${suggestion.nombre}</div>
            <div style="font-size: 0.85rem">${suggestion.beneficios || ''}</div>
            <div style="font-size: 0.8rem; margin-top: 5px; color: var(--text-muted)">Precio: S/ ${suggestion.precio}</div>
            <button class="upsell-button" onclick="alert('¡Plan activado! (Simulación)')">Me interesa</button>
        </div>
    `;
    messagesContainer.appendChild(cardDiv);
    scrollToBottom();
}

function showTyping() {
    const typingDiv = document.createElement('div');
    typingDiv.classList.add('message', 'bot');
    typingDiv.id = 'typing-indicator';
    typingDiv.innerHTML = `<div class="typing-indicator"><span></span><span></span><span></span></div>`;
    messagesContainer.appendChild(typingDiv);
    scrollToBottom();
}

function hideTyping() {
    const indicator = document.getElementById('typing-indicator');
    if (indicator) indicator.remove();
}

function scrollToBottom() {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || !currentUserId) return;

    // UI Update
    addMessage(text, 'user');
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
        
        // Procesar los mensajes en orden con sus delays
        let ultimoMensajeDiv = null;
        for (const msg of data.messages) {
            if (msg.delay_ms > 0) {
                showTyping();
                await sleep(msg.delay_ms);
                hideTyping();
            }
            ultimoMensajeDiv = addMessage(msg.text, 'bot', msg.type);
        }

        // El badge de confianza solo aporta en turnos que evaluaron un caso de
        // facturación (donde hubo un motor determinista + case matcher de por
        // medio). Turnos conversacionales o bloqueados por compliance no lo muestran.
        const esFacturacion = !data.compliance_triggered
            && !['SALUDO', 'DESPEDIDA', 'AGRADECIMIENTO', 'FUERA_DE_DOMINIO', 'SOLICITUD_AGENTE']
                .includes(data.intent_category);
        if (esFacturacion && ultimoMensajeDiv) {
            addConfidenceBadge(ultimoMensajeDiv, data.confidence_score, data.caso_validado);
        }

        // Si hay una sugerencia de plan
        if (data.plan_optimizer_suggestion && data.plan_optimizer_suggestion.available) {
            await sleep(1000);
            addUpsellCard(data.plan_optimizer_suggestion);
        }
        
    } catch (error) {
        hideTyping();
        addMessage("Ocurrió un error al contactar al servidor.", 'bot');
    }
}

sendButton.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
});
