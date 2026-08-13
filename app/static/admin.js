const API = "/api/v1";

// ---------------------------------------------------------------------------
// Utilidades generales
// ---------------------------------------------------------------------------

function showToast(text) {
    const toast = document.getElementById("toast");
    toast.textContent = text;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2500);
}

async function apiGet(path) {
    const res = await fetch(`${API}${path}`);
    if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
    return res.json();
}

async function apiPost(path, body) {
    const res = await fetch(`${API}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(`POST ${path} -> ${res.status}`);
    return res.json();
}

function fmtFecha(iso) {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleString("es-PE", { dateStyle: "short", timeStyle: "short" });
    } catch {
        return iso;
    }
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    });
});

// ---------------------------------------------------------------------------
// Cola de atención humana (handoff)
// ---------------------------------------------------------------------------

async function loadHandoffQueue() {
    const list = document.getElementById("handoff-list");
    const soloPendientes = document.getElementById("filtro-pendientes").checked;
    list.innerHTML = "<p class='empty-state'>Cargando...</p>";

    try {
        const data = await apiGet(`/admin/handoff-queue?solo_pendientes=${soloPendientes}`);
        if (!data.casos.length) {
            list.innerHTML = "<p class='empty-state'>No hay casos derivados en este momento. 🎉</p>";
            return;
        }
        list.innerHTML = data.casos.map(renderHandoffCard).join("");

        list.querySelectorAll("[data-atender]").forEach((btn) => {
            btn.addEventListener("click", async () => {
                await apiPost(`/admin/handoff-queue/${btn.dataset.atender}/atender`);
                showToast("Caso marcado como atendido");
                loadHandoffQueue();
            });
        });
    } catch (e) {
        list.innerHTML = `<p class="empty-state">Error al cargar: ${e.message}</p>`;
    }
}

function renderHandoffCard(caso) {
    const ctx = caso.handoff_context || {};
    const emociones = (ctx.comentarios_emocionales_pendientes || []).join("; ") || "Ninguno";
    const evidencia = ctx.evidencia_determinista
        ? `Evento: ${ctx.evidencia_determinista.detected_event || "—"} | Variación: S/ ${ctx.evidencia_determinista.variation_amount ?? "—"}`
        : "Sin evidencia de facturación asociada.";
    const historial = (ctx.historial_reciente || [])
        .map((t) => `${t.role === "user" ? "Usuario" : "Lucía"}: ${t.text}`)
        .join("\n") || "Sin turnos previos registrados.";

    return `
    <div class="card">
        <div class="card-title-row">
            <div>
                <div class="card-title">Sesión: ${caso.session_id}</div>
                <div class="card-subtitle">${caso.intent_category} · ${fmtFecha(caso.fecha)}</div>
            </div>
            <span class="badge ${caso.atendido ? "badge-done" : "badge-pending"}">
                ${caso.atendido ? "Atendido" : "Pendiente"}
            </span>
        </div>
        <div class="card-details">
Motivo: ${ctx.motivo || "—"}
Usuario: ${ctx.user_id || "—"}
Último mensaje: "${ctx.ultimo_mensaje || "—"}"
Sentimiento: ${ctx.sentimiento_score ?? "—"}/5  ·  Perfil: ${ctx.perfil_lexico || "—"}
${evidencia}
Comentarios emocionales pendientes: ${emociones}

Historial reciente:
${historial}
        </div>
        ${!caso.atendido ? `<div class="card-actions"><button class="btn-attend" data-atender="${caso.id}">✓ Marcar como atendido</button></div>` : ""}
    </div>`;
}

document.getElementById("filtro-pendientes").addEventListener("change", loadHandoffQueue);

// ---------------------------------------------------------------------------
// Cuarentena de casos
// ---------------------------------------------------------------------------

async function loadCuarentena() {
    const list = document.getElementById("cuarentena-list");
    list.innerHTML = "<p class='empty-state'>Cargando...</p>";

    try {
        const data = await apiGet("/admin/cuarentena");
        if (!data.casos.length) {
            list.innerHTML = "<p class='empty-state'>No hay casos en cuarentena.</p>";
            return;
        }
        list.innerHTML = data.casos.map(renderCuarentenaCard).join("");

        list.querySelectorAll("[data-validar]").forEach((btn) => {
            btn.addEventListener("click", async () => {
                const casoId = btn.dataset.validar;
                const textarea = document.getElementById(`solucion-${casoId}`);
                const solucionEditada = textarea ? textarea.value.trim() : null;
                await apiPost(`/admin/validar/${casoId}`, {
                    validado_por: "AGENTE_MOVISTAR",
                    solucion_editada: solucionEditada || null,
                });
                showToast("Caso promovido a la base de conocimiento");
                loadCuarentena();
            });
        });
    } catch (e) {
        list.innerHTML = `<p class="empty-state">Error al cargar: ${e.message}</p>`;
    }
}

function renderCuarentenaCard(caso) {
    // Extraer texto visible de la solución propuesta
    let solucionTexto = "";
    if (caso.solucion_propuesta) {
        if (typeof caso.solucion_propuesta === "string") {
            solucionTexto = caso.solucion_propuesta;
        } else if (caso.solucion_propuesta.texto) {
            solucionTexto = caso.solucion_propuesta.texto;
        } else if (caso.solucion_propuesta.messages) {
            solucionTexto = caso.solucion_propuesta.messages.map(m => m.text).join("\n");
        }
    }

    // Extraer el mensaje original del usuario (para saber qué preguntó)
    const userMessage = (caso.evidencias && caso.evidencias.user_message) || "";

    return `
    <div class="card">
        <div class="card-title-row">
            <div>
                <div class="card-title">${caso.patron}</div>
                <div class="card-subtitle">${fmtFecha(caso.fecha)}</div>
            </div>
            <span class="badge badge-neutral">Incertidumbre: ${(caso.incertidumbre * 100).toFixed(0)}%</span>
        </div>
        ${userMessage ? `<div class="card-user-message">💬 "${userMessage}"</div>` : ""}
        <div class="stat-row">
            <span>Feedback inmediato: <strong>${caso.feedback_inmediato}</strong></span>
            <span>Feedback posterior: <strong>${caso.feedback_posterior}</strong></span>
        </div>
        <div class="card-solution">
            <label class="solution-label">Solución propuesta (editable):</label>
            <textarea class="solution-textarea" id="solucion-${caso.id}" rows="4">${solucionTexto}</textarea>
        </div>
        <div class="card-actions">
            <button class="btn-approve" data-validar="${caso.id}">✓ Validar y promover</button>
        </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Base de casos (conocimiento validado)
// ---------------------------------------------------------------------------

async function loadBaseCasos() {
    const list = document.getElementById("base-casos-list");
    list.innerHTML = "<p class='empty-state'>Cargando...</p>";

    try {
        const data = await apiGet("/admin/base-casos");
        if (!data.casos.length) {
            list.innerHTML = "<p class='empty-state'>Todavía no hay casos validados.</p>";
            return;
        }
        list.innerHTML = data.casos.map((c) => {
            // Extraer texto de la solución
            let solucionTexto = "";
            if (c.solucion) {
                if (typeof c.solucion === "string") {
                    solucionTexto = c.solucion;
                } else if (c.solucion.texto) {
                    solucionTexto = c.solucion.texto;
                } else if (c.solucion.messages) {
                    solucionTexto = c.solucion.messages.map(m => m.text).join(" ");
                }
            }
            // Extraer contexto del caso (user_id, último mensaje, evento)
            const ctx = c.condiciones || {};
            const userId = ctx.user_id || ctx.origen || "";
            const evento = ctx.detected_event || c.patron;

            return `
            <div class="card">
                <div class="card-title-row">
                    <div>
                        <div class="card-title">${c.patron}</div>
                        <div class="card-subtitle">Validado por ${c.validado_por} · ${fmtFecha(c.fecha_validacion)}${userId ? ` · Usuario: ${userId}` : ""}</div>
                    </div>
                    <span class="badge badge-done">${c.veces_aplicado} usos</span>
                </div>
                ${solucionTexto ? `<div class="card-details" style="margin-top:8px;white-space:pre-wrap;font-size:0.83rem">${solucionTexto}</div>` : ""}
                <div class="stat-row">
                    <span>Tasa de éxito: <strong>${(c.tasa_exito * 100).toFixed(0)}%</strong></span>
                </div>
            </div>`;
        }).join("");
    } catch (e) {
        list.innerHTML = `<p class="empty-state">Error al cargar: ${e.message}</p>`;
    }
}

// ---------------------------------------------------------------------------
// Alertas proactivas
// ---------------------------------------------------------------------------

document.getElementById("run-proactive").addEventListener("click", async () => {
    const result = document.getElementById("proactive-result");
    const btn = document.getElementById("run-proactive");
    btn.disabled = true;
    btn.textContent = "Ejecutando...";
    result.innerHTML = "";

    try {
        const data = await apiPost("/admin/proactive-check");
        showToast(`Barrido completo: ${data.alertas_enviadas} alerta(s) enviada(s)`);

        if (!data.detalle.length) {
            result.innerHTML = `<p class="empty-state">Se revisaron ${data.usuarios_revisados} usuarios. Ninguno tiene alertas próximas a vencer.</p>`;
        } else {
            result.innerHTML = `<p class="card-subtitle" style="margin-bottom:10px">Usuarios revisados: ${data.usuarios_revisados}</p>` +
                data.detalle.map((d) => `
                <div class="card">
                    <div class="card-title-row">
                        <div class="card-title">${d.user_id}</div>
                        <span class="badge ${d.estado === "enviado" ? "badge-done" : "badge-pending"}">${d.estado}</span>
                    </div>
                    <div class="card-details">${d.mensaje_enviado || "Sin canal de contacto registrado."}</div>
                </div>`).join("");
        }
    } catch (e) {
        result.innerHTML = `<p class="empty-state">Error al ejecutar el barrido: ${e.message}</p>`;
    } finally {
        btn.disabled = false;
        btn.textContent = "▶ Ejecutar barrido ahora";
    }
});

// ---------------------------------------------------------------------------
// Mapeo de WhatsApp & Notificaciones
// ---------------------------------------------------------------------------

async function loadCuentasConAlertas() {
    const select = document.getElementById("select-cuenta-alerta");
    if (!select) return;

    try {
        const data = await apiGet("/admin/cuentas-con-alertas");
        if (!data.cuentas || !data.cuentas.length) {
            select.innerHTML = "<option value=''>No se encontraron cuentas con alertas activas</option>";
            return;
        }

        select.innerHTML = "<option value=''>-- Selecciona una cuenta con Fin de Promoción --</option>" +
            data.cuentas.map(c => {
                const alerta = c.alertas[0] || {};
                const promo = alerta.concepto || "Descuento por vencer";
                const impacto = alerta.impacto_estimado || "";
                return `<option value="${c.user_id}">Cuenta ${c.user_id} (${c.plan_actual}) · ${promo} [${impacto}]</option>`;
            }).join("");
    } catch (e) {
        select.innerHTML = `<option value=''>Error al cargar cuentas: ${e.message}</option>`;
    }
}

document.getElementById("select-cuenta-alerta").addEventListener("change", (e) => {
    const val = e.target.value;
    if (val) {
        document.getElementById("input-wa-account").value = val;
    }
});

async function loadContactos() {
    const list = document.getElementById("contactos-list");
    if (!list) return;
    list.innerHTML = "<p class='empty-state'>Cargando contactos...</p>";

    try {
        const data = await apiGet("/admin/contactos");
        if (!data.contactos || !data.contactos.length) {
            list.innerHTML = "<p class='empty-state'>No hay números de WhatsApp vinculados aún. Usa el formulario de arriba para vincular tu número.</p>";
            return;
        }

        list.innerHTML = data.contactos.map(c => {
            const alertasStr = c.alertas && c.alertas.length
                ? c.alertas.map(a => `⚠️ ${a.concepto} (${a.impacto_estimado})`).join("<br>")
                : "Sin alertas de vencimiento pendientes";

            return `
            <div class="card">
                <div class="card-title-row">
                    <div>
                        <div class="card-title">📱 WhatsApp: <strong>+${c.whatsapp_number || "Sin número"}</strong></div>
                        <div class="card-subtitle">Cuenta Financiera: <strong>${c.user_id}</strong> · Plan: ${c.plan_actual} · Último Recibo: S/ ${c.monto_ultimo_recibo?.toFixed(2) || "0.00"}</div>
                    </div>
                    <span class="badge ${c.total_alertas_activas > 0 ? "badge-pending" : "badge-done"}">
                        ${c.total_alertas_activas > 0 ? `${c.total_alertas_activas} alerta(s) activa(s)` : "Al día"}
                    </span>
                </div>
                <div class="card-details" style="font-size: 0.84rem;">
                    ${alertasStr}
                </div>
                <div class="card-actions">
                    <button class="btn-primary" style="background:#059669;" data-send-alert="${c.user_id}" data-phone="${c.whatsapp_number}">
                        🚀 Enviar Alerta Proactiva
                    </button>
                    <button class="btn-secondary" style="color:var(--danger); border-color:#fca5a5;" data-delete-contact="${c.user_id}">
                        🗑️ Desvincular
                    </button>
                </div>
            </div>`;
        }).join("");

        // Event listeners para botones de acción
        list.querySelectorAll("[data-send-alert]").forEach(btn => {
            btn.addEventListener("click", async () => {
                const userId = btn.dataset.sendAlert;
                const phone = btn.dataset.phone;
                btn.disabled = true;
                btn.textContent = "Enviando...";
                try {
                    const res = await apiPost("/admin/enviar-alerta-manual", {
                        user_id: userId,
                        whatsapp_number: phone
                    });
                    showToast(`Alerta enviada a +${phone}!`);
                } catch (e) {
                    showToast(`Error al enviar: ${e.message}`);
                } finally {
                    btn.disabled = false;
                    btn.textContent = "🚀 Enviar Alerta Proactiva";
                }
            });
        });

        list.querySelectorAll("[data-delete-contact]").forEach(btn => {
            btn.addEventListener("click", async () => {
                const userId = btn.dataset.deleteContact;
                if (confirm(`¿Desvincular la cuenta ${userId}?`)) {
                    const res = await fetch(`${API}/admin/contactos/${userId}`, { method: "DELETE" });
                    if (res.ok) {
                        showToast("Contacto desvinculado");
                        loadContactos();
                    } else {
                        showToast("Error al desvincular");
                    }
                }
            });
        });

    } catch (e) {
        list.innerHTML = `<p class="empty-state">Error al cargar: ${e.message}</p>`;
    }
}

// Botón Vincular WhatsApp
document.getElementById("btn-vincular-wa").addEventListener("click", async () => {
    const phone = document.getElementById("input-wa-number").value.trim();
    const account = document.getElementById("input-wa-account").value.trim();

    if (!phone || !account) {
        alert("Por favor ingresa tanto tu número de WhatsApp como la cuenta financiera.");
        return;
    }

    try {
        const res = await apiPost("/admin/contactos", {
            user_id: account,
            whatsapp_number: phone
        });
        showToast(`✅ Cuenta ${account} vinculada a +${phone}`);
        loadContactos();
    } catch (e) {
        alert(`Error al vincular: ${e.message}`);
    }
});

// Botón Enviar Alerta de Prueba / Proactiva directa
document.getElementById("btn-enviar-test-wa").addEventListener("click", async () => {
    const phone = document.getElementById("input-wa-number").value.trim();
    const account = document.getElementById("input-wa-account").value.trim();
    const btn = document.getElementById("btn-enviar-test-wa");

    if (!phone) {
        alert("Por favor ingresa tu número de WhatsApp destino.");
        return;
    }

    btn.disabled = true;
    btn.textContent = "Enviando a WhatsApp...";

    try {
        const res = await apiPost("/admin/enviar-alerta-manual", {
            user_id: account || null,
            whatsapp_number: phone
        });
        showToast(`🚀 Alerta enviada con éxito a +${phone}!`);
    } catch (e) {
        alert(`Error al enviar mensaje: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = "🚀 Enviar Alerta Proactiva a mi WhatsApp Ahora";
    }
});

// ---------------------------------------------------------------------------
// Carga inicial / refrescar todo
// ---------------------------------------------------------------------------

function loadAll() {
    loadContactos();
    loadCuentasConAlertas();
    loadHandoffQueue();
    loadCuarentena();
    loadBaseCasos();
}

document.getElementById("refresh-all").addEventListener("click", loadAll);

loadAll();

