/**
 * historial.js - Logica de la pagina de historial de comparaciones.
 */

const API_BASE = "";

// ---------------------------------------------------------------------------
// Auth guard
// ---------------------------------------------------------------------------
function getToken() {
  return localStorage.getItem("ocr_token");
}

function getUser() {
  try { return JSON.parse(localStorage.getItem("ocr_user") || "{}"); }
  catch { return {}; }
}

function authHeaders() {
  return { "Authorization": `Bearer ${getToken()}` };
}

async function requireAuth() {
  const token = getToken();
  if (!token) { window.location.replace("/login.html"); return false; }
  try {
    const r = await fetch("/api/auth/me", { headers: { "Authorization": `Bearer ${token}` } });
    if (!r.ok) { localStorage.clear(); window.location.replace("/login.html"); return false; }
    return true;
  } catch {
    window.location.replace("/login.html"); return false;
  }
}

// Escapa texto antes de insertarlo en innerHTML: nombres y observaciones vienen del Excel y del OCR
// (datos que el usuario no controla) y no deben interpretarse como HTML.
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// ---------------------------------------------------------------------------
// Estado
// ---------------------------------------------------------------------------
let allRows = [];
let pendingDeleteId = null;

// ---------------------------------------------------------------------------
// DOM
// ---------------------------------------------------------------------------
const histLoading = document.getElementById("hist-loading");
const histEmpty = document.getElementById("hist-empty");
const histTableContainer = document.getElementById("hist-table-container");
const histTbody = document.getElementById("hist-tbody");
const histSearch = document.getElementById("hist-search");
const histStats = document.getElementById("hist-stats");
const navUsername = document.getElementById("nav-username");
const btnLogout = document.getElementById("btn-logout");

const modalDelete = document.getElementById("modal-delete");
const modalDeleteClose = document.getElementById("modal-delete-close");
const modalCancel = document.getElementById("modal-cancel");
const modalConfirmDelete = document.getElementById("modal-confirm-delete");

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
(async () => {
  const ok = await requireAuth();
  if (!ok) return;

  const user = getUser();
  if (navUsername) navUsername.textContent = user.nombre || user.username || "";

  await cargarHistorial();
})();

btnLogout?.addEventListener("click", () => {
  localStorage.clear();
  window.location.replace("/login.html");
});

// ---------------------------------------------------------------------------
// Cargar historial
// ---------------------------------------------------------------------------
async function cargarHistorial() {
  histLoading.style.display = "block";
  histEmpty.style.display = "none";
  histTableContainer.style.display = "none";

  try {
    const resp = await fetch(`${API_BASE}/api/historial`, { headers: authHeaders() });
    if (resp.status === 401) { localStorage.clear(); window.location.replace("/login.html"); return; }
    if (!resp.ok) throw new Error("Error al cargar el historial.");

    allRows = await resp.json();
    histLoading.style.display = "none";

    if (!allRows.length) {
      histEmpty.style.display = "block";
      return;
    }

    histTableContainer.style.display = "block";
    renderStats(allRows);
    renderTabla(allRows);
  } catch (err) {
    const aviso = document.createElement("p");
    aviso.style.color = "var(--bad)";
    aviso.textContent = err.message;
    histLoading.replaceChildren(aviso);
  }
}

// ---------------------------------------------------------------------------
// Metricas generales
// ---------------------------------------------------------------------------
function renderStats(rows) {
  const totalParticipantes = rows.reduce((s, r) => s + (r.total_excel || 0), 0);
  const totalCoinciden = rows.reduce((s, r) => s + (r.coinciden || 0), 0);
  const tasa = totalParticipantes ? Math.round((totalCoinciden * 100) / totalParticipantes) : 0;
  const conTiempo = rows.filter((r) => r.tiempo_segundos);
  const tiempoMedio = conTiempo.length
    ? conTiempo.reduce((s, r) => s + r.tiempo_segundos, 0) / conTiempo.length : 0;

  const cards = [
    { ic: "history",   label: "Comparaciones",          value: String(rows.length),            note: "guardadas en este equipo" },
    { ic: "users",     label: "Participantes revisados", value: totalParticipantes.toLocaleString("es-CO"), note: "sumando todas las comparaciones" },
    { ic: "percent",   label: "Coincidencia global",    value: `${tasa}%`,                      note: `${totalCoinciden.toLocaleString("es-CO")} verificados` },
    { ic: "timer",     label: "Tiempo medio de OCR",    value: tiempoMedio ? formatDuration(tiempoMedio) : "—", note: "por comparación" },
  ];
  // Contenido generado por la aplicacion (numeros y textos fijos), sin datos del usuario
  histStats.innerHTML = cards.map((c) => `
    <div class="stat">
      <div class="stat-top"><span>${c.label}</span><span data-icon="${c.ic}" data-size="18"></span></div>
      <strong>${c.value}</strong>
      <small>${c.note}</small>
    </div>`).join("");
  UI.hydrateIcons(histStats);
  histStats.classList.remove("hidden");
}

function formatDuration(seconds) {
  if (seconds < 60) return `${seconds.toFixed(0)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m} min ${String(s).padStart(2, "0")} s`;
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
function splitFecha(fecha) {
  const [dia, hora] = String(fecha || "").split(" ");
  return { dia: dia || "—", hora: (hora || "").slice(0, 5) };
}

function renderTabla(rows) {
  if (!rows.length) {
    histTbody.innerHTML = `
      <tr><td colspan="4" class="empty-state-cell">
        <span class="empty-state-icon">${icon("search", 28)}</span>
        <strong>Sin resultados</strong>
        Ninguna comparación coincide con la búsqueda.
      </td></tr>`;
    return;
  }

  histTbody.innerHTML = rows.map(r => {
    const total = r.total_excel || 0;
    const ok = r.coinciden || 0;
    const warn = r.nombre_no_coincide || 0;
    const inc = r.documento_incompleto || 0;
    const bad = r.no_encontrado_en_pdf || 0;
    const pct = total ? (ok === total ? 100 : Math.min(99, Math.round((ok * 100) / total))) : 0;
    const pctTone = pct >= 90 ? "" : pct >= 60 ? "mid" : "low";
    const w = (n) => (total ? ((n / total) * 100).toFixed(2) : 0);
    const tiempo = r.tiempo_segundos ? formatDuration(r.tiempo_segundos) : "—";
    const { dia, hora } = splitFecha(r.fecha_comparacion);
    // Todo dato interpolado va escapado; los botones usan data-* (no onclick="...('${id}')"),
    // porque escapar HTML no protege el codigo JavaScript de un manejador en linea.
    const id = escapeHtml(r.id);
    const pdf = escapeHtml(r.nombre_pdf);
    const xls = escapeHtml(r.nombre_excel);

    return `
      <tr data-id="${id}">
        <td class="cell-date"><strong>${escapeHtml(dia)}</strong><span>${escapeHtml(hora)}</span>
          <span class="dur">${icon("timer", 13)}${tiempo}</span></td>
        <td class="cell-files">
          <div class="file-line" title="${pdf}"><span data-icon="file-text" data-size="15"></span><span>${pdf}</span></div>
          <div class="file-line is-excel" title="${xls}"><span data-icon="table" data-size="15"></span><span>${xls}</span></div>
        </td>
        <td class="cell-result">
          <div class="meter" role="img" aria-label="${ok} coinciden, ${warn + inc} para revisar, ${bad} no encontrados de ${total}">
            ${ok ? `<span class="m-ok" style="width:${w(ok)}%"></span>` : ""}
            ${warn ? `<span class="m-warn" style="width:${w(warn)}%"></span>` : ""}
            ${inc ? `<span class="m-inc" style="width:${w(inc)}%"></span>` : ""}
            ${bad ? `<span class="m-bad" style="width:${w(bad)}%"></span>` : ""}
          </div>
          <div class="meter-text">
            <span><b>${ok}</b> de ${total} coinciden</span>
            ${warn + inc ? `<span>· ${warn + inc} por revisar</span>` : ""}
            ${bad ? `<span>· ${bad} sin encontrar</span>` : ""}
            <span class="pct ${pctTone}">${pct}%</span>
          </div>
        </td>
        <td>
          <div class="actions-cell">
            <button type="button" class="icon-btn is-primary" data-action="ver" data-id="${id}" data-icon="eye" aria-label="Ver detalle" title="Ver detalle"></button>
            <button type="button" class="icon-btn" data-action="informe" data-id="${id}" data-icon="file-down" aria-label="Descargar informe PDF" title="Descargar informe PDF"></button>
            <button type="button" class="icon-btn" data-action="pdf" data-id="${id}" data-icon="file-text" aria-label="Descargar PDF original" title="Descargar PDF original"></button>
            <button type="button" class="icon-btn" data-action="excel" data-id="${id}" data-icon="table" aria-label="Descargar Excel original" title="Descargar Excel original"></button>
            <button type="button" class="icon-btn is-danger" data-action="eliminar" data-id="${id}" data-icon="trash" aria-label="Eliminar comparación" title="Eliminar comparación"></button>
          </div>
        </td>
      </tr>`;
  }).join("");
  UI.hydrateIcons(histTbody);
}

// Delegacion de eventos: un solo manejador para todos los botones de la tabla
histTbody.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-action]");
  if (!btn) return;
  const id = btn.dataset.id;
  switch (btn.dataset.action) {
    case "ver": verDetalle(id); break;
    case "informe": descargarReporte(id, btn); break;
    case "pdf": descargarPdf(id); break;
    case "excel": descargarExcel(id); break;
    case "eliminar": confirmarEliminar(id); break;
  }
});

// ---------------------------------------------------------------------------
// Busqueda
// ---------------------------------------------------------------------------
histSearch?.addEventListener("input", () => {
  const q = histSearch.value.trim().toLowerCase();
  if (!q) { renderTabla(allRows); return; }
  const filtered = allRows.filter(r =>
    r.nombre_pdf?.toLowerCase().includes(q) ||
    r.nombre_excel?.toLowerCase().includes(q) ||
    r.fecha_comparacion?.toLowerCase().includes(q)
  );
  renderTabla(filtered);
});

// ---------------------------------------------------------------------------
// Acciones
// ---------------------------------------------------------------------------
async function verDetalle(id) {
  const resp = await fetch(`${API_BASE}/api/historial/${id}/detalle`, { headers: authHeaders() });
  if (!resp.ok) { UI.toast("No se pudo cargar el detalle de la comparación.", "error"); return; }
  const data = await resp.json();

  // Guardar en sessionStorage y abrir la pagina principal con ese resultado
  sessionStorage.setItem("historial_detalle", JSON.stringify(data));
  window.location.href = "/index.html?modo=historial";
}

async function descargarReporte(id, btn) {
  if (btn) { btn.classList.add("is-loading"); btn.disabled = true; }
  try {
    const resp = await fetch(`${API_BASE}/api/historial/${id}/reporte`, { headers: authHeaders() });
    if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || "Error"); }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `informe_${id.slice(0,8)}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
    UI.toast("Informe PDF generado.", "success");
  } catch (err) {
    UI.toast(`No se pudo generar el informe: ${err.message}`, "error");
  } finally {
    if (btn) { btn.classList.remove("is-loading"); btn.disabled = false; }
  }
}

async function descargarPdf(id) {
  await _descargarArchivo(`${API_BASE}/api/historial/${id}/pdf`, `doc_${id.slice(0,8)}.pdf`);
}

async function descargarExcel(id) {
  await _descargarArchivo(`${API_BASE}/api/historial/${id}/excel`, `excel_${id.slice(0,8)}.xls`);
}

async function _descargarArchivo(url, fallbackName) {
  try {
    const resp = await fetch(url, { headers: authHeaders() });
    if (!resp.ok) { UI.toast("No se pudo descargar el archivo: ya no está disponible en este equipo.", "error"); return; }
    const disposition = resp.headers.get('content-disposition') || '';
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : fallbackName;
    const blob = await resp.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(blobUrl);
  } catch (err) {
    UI.toast(`Error descargando el archivo: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Eliminacion (modal accesible: foco inicial, Escape y clic fuera para cerrar)
// ---------------------------------------------------------------------------
let ultimoFocoAntesDelModal = null;

function abrirModal() {
  ultimoFocoAntesDelModal = document.activeElement;
  modalDelete.classList.remove("hidden");
  modalCancel.focus();
}

function cerrarModal() {
  modalDelete.classList.add("hidden");
  pendingDeleteId = null;
  if (ultimoFocoAntesDelModal && ultimoFocoAntesDelModal.focus) ultimoFocoAntesDelModal.focus();
}

function confirmarEliminar(id) {
  pendingDeleteId = id;
  abrirModal();
}

[modalDeleteClose, modalCancel].forEach(el => el?.addEventListener("click", cerrarModal));
modalDelete.addEventListener("click", (ev) => { if (ev.target === modalDelete) cerrarModal(); });
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !modalDelete.classList.contains("hidden")) cerrarModal();
});

modalConfirmDelete?.addEventListener("click", async () => {
  if (!pendingDeleteId) return;
  const id = pendingDeleteId;
  modalDelete.classList.add("hidden");
  pendingDeleteId = null;

  const resp = await fetch(`${API_BASE}/api/historial/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });

  if (resp.ok || resp.status === 204) {
    allRows = allRows.filter(r => r.id !== id);
    renderTabla(allRows);
    renderStats(allRows);
    UI.toast("Comparación eliminada junto con sus archivos.", "success");
    if (!allRows.length) {
      histTableContainer.style.display = "none";
      histStats.classList.add("hidden");
      histEmpty.style.display = "block";
    }
  } else {
    UI.toast("No se pudo eliminar la comparación.", "error");
  }
});
