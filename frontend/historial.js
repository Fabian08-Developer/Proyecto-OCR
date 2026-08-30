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
    renderTabla(allRows);
  } catch (err) {
    histLoading.innerHTML = `<p style="color:var(--red)">${err.message}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
function renderTabla(rows) {
  if (!rows.length) {
    histTbody.innerHTML = `
      <tr><td colspan="9" class="empty-state-cell">
        <span class="empty-state-icon">🔍</span>
        No se encontraron resultados para la búsqueda.
      </td></tr>`;
    return;
  }

  histTbody.innerHTML = rows.map(r => {
    const total = r.total_excel || 0;
    const coincide = r.coinciden || 0;
    const malo = (r.nombre_no_coincide || 0) + (r.documento_incompleto || 0);
    const noEnc = r.no_encontrado_en_pdf || 0;
    const tiempo = r.tiempo_segundos ? `${r.tiempo_segundos.toFixed(1)}s` : "—";
    const fecha = r.fecha_comparacion || "";

    return `
      <tr data-id="${r.id}">
        <td class="fecha">${fecha}</td>
        <td title="${r.nombre_pdf}">📄 ${truncate(r.nombre_pdf, 30)}</td>
        <td title="${r.nombre_excel}">📊 ${truncate(r.nombre_excel, 30)}</td>
        <td style="text-align:center;font-weight:700">${total}</td>
        <td style="text-align:center;color:var(--green);font-weight:700">${coincide}</td>
        <td style="text-align:center;color:var(--amber);font-weight:700">${malo}</td>
        <td style="text-align:center;color:var(--red);font-weight:700">${noEnc}</td>
        <td style="text-align:center;color:var(--muted);font-size:0.82rem">${tiempo}</td>
        <td>
          <div class="actions-cell">
            <button class="action-btn primary-btn" onclick="verDetalle('${r.id}')">👁 Ver</button>
            <button class="action-btn" onclick="descargarReporte('${r.id}')">📋 Informe</button>
            <button class="action-btn" onclick="descargarPdf('${r.id}')">⬇ PDF</button>
            <button class="action-btn" onclick="descargarExcel('${r.id}')">⬇ Excel</button>
            <button class="action-btn danger" onclick="confirmarEliminar('${r.id}')">🗑</button>
          </div>
        </td>
      </tr>`;
  }).join("");
}

function truncate(str, max) {
  if (!str) return "—";
  return str.length > max ? str.slice(0, max) + "…" : str;
}

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
  if (!resp.ok) { alert("No se pudo cargar el detalle."); return; }
  const data = await resp.json();

  // Guardar en sessionStorage y abrir la pagina principal con ese resultado
  sessionStorage.setItem("historial_detalle", JSON.stringify(data));
  window.location.href = "/index.html?modo=historial";
}

async function descargarReporte(id) {
  const btn = event?.currentTarget;
  if (btn) { btn.textContent = "⏳ Generando…"; btn.disabled = true; }
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
  } catch (err) {
    alert(`Error generando el informe: ${err.message}`);
  } finally {
    if (btn) { btn.textContent = "📋 Informe"; btn.disabled = false; }
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
    if (!resp.ok) { alert('No se pudo descargar el archivo.'); return; }
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
    alert(`Error descargando: ${err.message}`);
  }
}

function confirmarEliminar(id) {
  pendingDeleteId = id;
  modalDelete.classList.remove("hidden");
}

[modalDeleteClose, modalCancel].forEach(el => {
  el?.addEventListener("click", () => {
    modalDelete.classList.add("hidden");
    pendingDeleteId = null;
  });
});

modalConfirmDelete?.addEventListener("click", async () => {
  if (!pendingDeleteId) return;
  modalDelete.classList.add("hidden");
  const id = pendingDeleteId;
  pendingDeleteId = null;

  const resp = await fetch(`${API_BASE}/api/historial/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });

  if (resp.ok || resp.status === 204) {
    allRows = allRows.filter(r => r.id !== id);
    renderTabla(allRows);
    if (!allRows.length) {
      histTableContainer.style.display = "none";
      histEmpty.style.display = "block";
    }
  } else {
    alert("No se pudo eliminar la comparación.");
  }
});
