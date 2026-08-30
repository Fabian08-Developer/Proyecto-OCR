const API_BASE = "";

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------
function getToken() { return localStorage.getItem("ocr_token"); }
function getUser() {
  try { return JSON.parse(localStorage.getItem("ocr_user") || "{}"); } catch { return {}; }
}
function authHeaders() { return { "Authorization": `Bearer ${getToken()}` }; }

async function requireAuth() {
  const token = getToken();
  if (!token) { window.location.replace("/login.html"); return false; }
  try {
    const r = await fetch("/api/auth/me", { headers: { "Authorization": `Bearer ${token}` } });
    if (!r.ok) { localStorage.clear(); window.location.replace("/login.html"); return false; }
    return true;
  } catch { window.location.replace("/login.html"); return false; }
}

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
const inputPdf = document.getElementById("input-pdf");
const inputExcel = document.getElementById("input-excel");
const filenamePdf = document.getElementById("filename-pdf");
const filenameExcel = document.getElementById("filename-excel");
const btnProcesar = document.getElementById("btn-procesar");
const progressPanel = document.getElementById("progress-panel");
const errorPanel = document.getElementById("error-panel");
const errorMsg = document.getElementById("error-msg");
const resultsPanel = document.getElementById("results-panel");
const summaryCards = document.getElementById("summary-cards");
const btnExportar = document.getElementById("btn-exportar");
const btnGenerarInforme = document.getElementById("btn-generar-informe");
const resultIdBadge = document.getElementById("result-id-badge");
const navUsername = document.getElementById("nav-username");
const btnLogout = document.getElementById("btn-logout");
const headerSubtitle = document.getElementById("header-subtitle");

// Filtros
const filterDoc = document.getElementById("filter-doc");
const filterNombre = document.getElementById("filter-nombre");
const filterEstado = document.getElementById("filter-estado");
const btnClearFilters = document.getElementById("btn-clear-filters");
const filterCounter = document.getElementById("filter-counter");
const countTabResultados = document.getElementById("count-tab-resultados");
const countTabSobrantes = document.getElementById("count-tab-sobrantes");
const countTabSinNumero = document.getElementById("count-tab-sin-numero");

// ---------------------------------------------------------------------------
// Estado de la aplicacion
// ---------------------------------------------------------------------------
let lastResponse = null;
let lastComparacionId = null;
let filteredResultados = [];
let filteredSobrantes = [];
let filteredSinNumero = [];
let activeTab = "resultados";

// ---------------------------------------------------------------------------
// Inicializacion
// ---------------------------------------------------------------------------
(async () => {
  const ok = await requireAuth();
  if (!ok) return;

  const user = getUser();
  if (navUsername) navUsername.textContent = user.nombre || user.username || "";

  // Modo historial: cargar detalle desde sessionStorage
  const params = new URLSearchParams(window.location.search);
  if (params.get("modo") === "historial") {
    const detalleStr = sessionStorage.getItem("historial_detalle");
    if (detalleStr) {
      sessionStorage.removeItem("historial_detalle");
      const detalle = JSON.parse(detalleStr);
      lastComparacionId = detalle.id || null;
      lastResponse = detalle;
      if (headerSubtitle) {
        headerSubtitle.textContent = `Visualizando comparación del ${detalle.fecha_comparacion || "historial"} · ${detalle.nombre_pdf || ""}`;
      }
      if (resultIdBadge && lastComparacionId) {
        resultIdBadge.textContent = `ID: ${lastComparacionId.slice(0, 8)}…`;
      }
      resetFilters(false);
      renderResults(detalle);
    }
  }
})();

btnLogout?.addEventListener("click", () => {
  localStorage.clear();
  window.location.replace("/login.html");
});

// ---------------------------------------------------------------------------
// Subida de archivos
// ---------------------------------------------------------------------------
function updateFileLabels() {
  filenamePdf.textContent = inputPdf.files[0]?.name || "Ningún archivo seleccionado";
  filenameExcel.textContent = inputExcel.files[0]?.name || "Ningún archivo seleccionado";
  btnProcesar.disabled = !(inputPdf.files[0] && inputExcel.files[0]);
}

inputPdf.addEventListener("change", updateFileLabels);
inputExcel.addEventListener("change", updateFileLabels);

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
function setActiveTab(tabName) {
  activeTab = tabName;
  document.querySelectorAll(".tab-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-content").forEach((c) => {
    c.classList.toggle("hidden", c.id !== `tab-${tabName}`);
  });
  updateCounter();
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
});

// ---------------------------------------------------------------------------
// Labels de estado
// ---------------------------------------------------------------------------
const ESTADO_LABELS = {
  coincide: "Coincide",
  nombre_no_coincide: "Nombre no coincide",
  documento_incompleto: "Datos incompletos",
  no_encontrado_en_pdf: "No encontrado en PDF",
};

function fmt(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

// ---------------------------------------------------------------------------
// Normalizacion para filtros
// ---------------------------------------------------------------------------
function normalizeText(str) {
  if (str === null || str === undefined) return "";
  return String(str).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
}
function normalizeDoc(str) {
  if (str === null || str === undefined) return "";
  return String(str).replace(/[^\d\w]/g, "").toLowerCase();
}

// ---------------------------------------------------------------------------
// Procesamiento principal
// ---------------------------------------------------------------------------
async function procesar() {
  errorPanel.classList.add("hidden");
  resultsPanel.classList.add("hidden");
  progressPanel.classList.remove("hidden");
  btnProcesar.disabled = true;

  const formData = new FormData();
  formData.append("pdf", inputPdf.files[0]);
  formData.append("excel", inputExcel.files[0]);

  try {
    const resp = await fetch(`${API_BASE}/api/comparar`, {
      method: "POST",
      headers: authHeaders(),
      body: formData,
    });

    if (resp.status === 401) { localStorage.clear(); window.location.replace("/login.html"); return; }

    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}));
      throw new Error(detail.detail || `Error HTTP ${resp.status}`);
    }

    const data = await resp.json();
    lastResponse = data;
    lastComparacionId = data.comparacion_id || null;

    if (resultIdBadge && lastComparacionId) {
      resultIdBadge.textContent = `Guardado · ID: ${lastComparacionId.slice(0, 8)}…`;
    }

    resetFilters(false);
    renderResults(data);
  } catch (err) {
    errorMsg.textContent = `Ocurrió un error: ${err.message}`;
    errorPanel.classList.remove("hidden");
  } finally {
    progressPanel.classList.add("hidden");
    btnProcesar.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Render del resumen de tarjetas
// ---------------------------------------------------------------------------
function renderSummary(resumen) {
  const cards = [
    { label: "Registros en Excel",   value: resumen.total_excel,              tab: "resultados", status: "" },
    { label: "Documentos en PDF",    value: resumen.total_pdf_documentos,      tab: "resultados", status: "" },
    { label: "Coinciden",            value: resumen.coinciden,                 tab: "resultados", status: "coincide" },
    { label: "Nombre no coincide",   value: resumen.nombre_no_coincide,        tab: "resultados", status: "nombre_no_coincide" },
    { label: "Datos incompletos",    value: resumen.documento_incompleto,      tab: "resultados", status: "documento_incompleto" },
    { label: "No encontrados en PDF",value: resumen.no_encontrado_en_pdf,      tab: "resultados", status: "no_encontrado_en_pdf" },
    { label: "Sobrantes en PDF",     value: resumen.sobrantes_en_pdf,          tab: "sobrantes",  status: "" },
    { label: "Sin número",           value: resumen.documentos_sin_numero_detectado, tab: "sin-numero", status: "" },
  ];

  summaryCards.innerHTML = cards.map((c) => `
    <div class="summary-card clickable" data-tab="${c.tab}" data-status="${c.status}" title="Clic para filtrar">
      <div class="value">${c.value ?? 0}</div>
      <div class="label">${c.label}</div>
    </div>`).join("");

  document.querySelectorAll(".summary-card.clickable").forEach((card) => {
    card.addEventListener("click", () => {
      const tab = card.dataset.tab;
      const status = card.dataset.status;
      if (tab) setActiveTab(tab);
      if (status !== undefined) {
        filterEstado.value = (filterEstado.value === status && status !== "") ? "" : status;
      }
      applyFilters();
    });
  });
}

// ---------------------------------------------------------------------------
// Render de tablas
// ---------------------------------------------------------------------------
function renderResultsTable(resultados) {
  const tbody = document.querySelector("#table-resultados tbody");
  if (!resultados || resultados.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state-cell">
      <span class="empty-state-icon">🔍</span>
      No se encontraron participantes que coincidan con los filtros aplicados.</td></tr>`;
    return;
  }
  tbody.innerHTML = resultados.map((r) => {
    const nombreOcr = r.ocr?.nombre_completo || "—";
    const fechaNac = r.ocr?.fecha_nacimiento || "—";
    const similitud = r.similitud_nombre !== null && r.similitud_nombre !== undefined
      ? `${Math.round(r.similitud_nombre * 100)}%` : "—";
    return `<tr>
      <td><span class="badge ${r.estado}">${ESTADO_LABELS[r.estado] || r.estado}</span></td>
      <td><strong>${fmt(r.numero_documento)}</strong></td>
      <td>${fmt(r.excel?.nombre_reportado)}</td>
      <td>${fmt(nombreOcr)}</td>
      <td>${fmt(fechaNac)}</td>
      <td>${similitud}</td>
      <td>${fmt(r.observaciones)}</td>
    </tr>`;
  }).join("");
}

function renderSobrantes(sobrantes) {
  const tbody = document.querySelector("#table-sobrantes tbody");
  if (!sobrantes || sobrantes.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty-state-cell">
      <span class="empty-state-icon">🔍</span>
      No se encontraron documentos sobrantes que coincidan con los filtros.</td></tr>`;
    return;
  }
  tbody.innerHTML = sobrantes.map((d) => `<tr>
    <td><strong>${fmt(d.numero_documento)}</strong></td>
    <td>${fmt(d.nombre_completo)}</td>
    <td>${fmt(d.fecha_nacimiento)}</td>
    <td>${fmt((d.paginas_pdf || []).join(", "))}</td>
  </tr>`).join("");
}

function renderSinNumero(docs) {
  const tbody = document.querySelector("#table-sin-numero tbody");
  if (!docs || docs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="3" class="empty-state-cell">
      <span class="empty-state-icon">🔍</span>
      No se encontraron documentos sin número que coincidan con los filtros.</td></tr>`;
    return;
  }
  tbody.innerHTML = docs.map((d) => `<tr>
    <td>${fmt(d.nombre_completo)}</td>
    <td>${fmt(d.fecha_nacimiento)}</td>
    <td>${fmt((d.paginas_pdf || []).join(", "))}</td>
  </tr>`).join("");
}

// ---------------------------------------------------------------------------
// Contador de resultados
// ---------------------------------------------------------------------------
function updateCounter() {
  if (!lastResponse) return;
  const docVal = filterDoc.value.trim();
  const nombreVal = filterNombre.value.trim();
  const estadoVal = filterEstado.value;
  const isFiltering = Boolean(docVal || nombreVal || estadoVal);

  if (activeTab === "resultados") {
    const total = (lastResponse.resultados || []).length;
    filterCounter.textContent = isFiltering
      ? `Mostrando ${filteredResultados.length} de ${total} registros`
      : `Mostrando todos los registros (${total})`;
  } else if (activeTab === "sobrantes") {
    const total = (lastResponse.sobrantes_en_pdf || []).length;
    filterCounter.textContent = isFiltering
      ? `Mostrando ${filteredSobrantes.length} de ${total} sobrantes`
      : `Mostrando todos los sobrantes (${total})`;
  } else {
    const total = (lastResponse.documentos_sin_numero_detectado || []).length;
    filterCounter.textContent = isFiltering
      ? `Mostrando ${filteredSinNumero.length} de ${total} sin número`
      : `Mostrando todos sin número (${total})`;
  }
}

// ---------------------------------------------------------------------------
// Filtros
// ---------------------------------------------------------------------------
function applyFilters() {
  if (!lastResponse) return;
  const docQuery = normalizeDoc(filterDoc.value);
  const nameQuery = normalizeText(filterNombre.value);
  const statusQuery = filterEstado.value;

  document.querySelectorAll(".btn-input-clear").forEach((btn) => {
    const target = document.getElementById(btn.dataset.target);
    btn.style.display = (target && target.value.trim() !== "") ? "flex" : "none";
  });

  filteredResultados = (lastResponse.resultados || []).filter((r) => {
    if (statusQuery && r.estado !== statusQuery) return false;
    if (docQuery) {
      const d1 = normalizeDoc(r.numero_documento);
      const d2 = normalizeDoc(r.ocr?.numero_documento);
      const d3 = normalizeDoc(r.excel?.numero_documento);
      if (!d1.includes(docQuery) && !d2.includes(docQuery) && !d3.includes(docQuery)) return false;
    }
    if (nameQuery) {
      const n1 = normalizeText(r.excel?.nombre_reportado);
      const n2 = normalizeText(r.ocr?.nombre_completo);
      const n3 = normalizeText(r.ocr?.nombres);
      const n4 = normalizeText(r.ocr?.apellidos);
      if (!n1.includes(nameQuery) && !n2.includes(nameQuery) && !n3.includes(nameQuery) && !n4.includes(nameQuery)) return false;
    }
    return true;
  });

  filteredSobrantes = (lastResponse.sobrantes_en_pdf || []).filter((d) => {
    if (statusQuery) return false;
    if (docQuery && !normalizeDoc(d.numero_documento).includes(docQuery)) return false;
    if (nameQuery) {
      const n1 = normalizeText(d.nombre_completo);
      const n2 = normalizeText(d.nombres);
      const n3 = normalizeText(d.apellidos);
      if (!n1.includes(nameQuery) && !n2.includes(nameQuery) && !n3.includes(nameQuery)) return false;
    }
    return true;
  });

  filteredSinNumero = (lastResponse.documentos_sin_numero_detectado || []).filter((d) => {
    if (statusQuery || docQuery) return false;
    if (nameQuery) {
      const n1 = normalizeText(d.nombre_completo);
      const n2 = normalizeText(d.nombres);
      const n3 = normalizeText(d.apellidos);
      if (!n1.includes(nameQuery) && !n2.includes(nameQuery) && !n3.includes(nameQuery)) return false;
    }
    return true;
  });

  renderResultsTable(filteredResultados);
  renderSobrantes(filteredSobrantes);
  renderSinNumero(filteredSinNumero);

  if (countTabResultados) countTabResultados.textContent = filteredResultados.length;
  if (countTabSobrantes)  countTabSobrantes.textContent  = filteredSobrantes.length;
  if (countTabSinNumero)  countTabSinNumero.textContent  = filteredSinNumero.length;

  document.querySelectorAll(".summary-card").forEach((card) => {
    const cs = card.dataset.status;
    card.classList.toggle("active-filter", Boolean(cs && statusQuery === cs));
  });

  updateCounter();
}

function resetFilters(apply = true) {
  filterDoc.value = "";
  filterNombre.value = "";
  filterEstado.value = "";
  if (apply && lastResponse) applyFilters();
}

function renderResults(data) {
  renderSummary(data.resumen);
  applyFilters();
  resultsPanel.classList.remove("hidden");
}

// ---------------------------------------------------------------------------
// Exportar CSV
// ---------------------------------------------------------------------------
function toCsvValue(value) {
  if (value === null || value === undefined) return "";
  return `"${String(value).replace(/"/g, '""')}"`;
}

function exportarCsv() {
  if (!lastResponse) return;
  const list = filteredResultados.length > 0 ? filteredResultados : lastResponse.resultados;
  const rows = [["Estado", "N° Documento", "Nombre Excel", "Nombre OCR", "Fecha Nac. OCR", "Similitud", "Observaciones"]];
  list.forEach((r) => {
    rows.push([
      ESTADO_LABELS[r.estado] || r.estado,
      r.numero_documento,
      r.excel?.nombre_reportado || "",
      r.ocr?.nombre_completo || "",
      r.ocr?.fecha_nacimiento || "",
      r.similitud_nombre ?? "",
      r.observaciones || "",
    ]);
  });
  const csv = rows.map((row) => row.map(toCsvValue).join(",")).join("\n");
  const blob = new Blob([`\ufeff${csv}`], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "resultados_verificacion.csv";
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Generar informe PDF
// ---------------------------------------------------------------------------
async function generarInformePdf() {
  if (!lastComparacionId) { alert("No hay comparación disponible para generar el informe."); return; }

  const btn = btnGenerarInforme;
  const originalText = btn.innerHTML;
  btn.innerHTML = `<span style="display:inline-flex;align-items:center;gap:8px">
    <span style="width:14px;height:14px;border:2px solid rgba(255,255,255,0.4);border-top-color:white;border-radius:50%;animation:spin 0.8s linear infinite;display:inline-block"></span>
    Generando...
  </span>`;
  btn.disabled = true;

  try {
    const resp = await fetch(`${API_BASE}/api/historial/${lastComparacionId}/reporte`, {
      headers: authHeaders(),
    });
    if (resp.status === 401) { localStorage.clear(); window.location.replace("/login.html"); return; }
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "Error generando el informe.");
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `informe_ocr_${lastComparacionId.slice(0, 8)}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.innerHTML = originalText;
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Event Listeners
// ---------------------------------------------------------------------------
filterDoc.addEventListener("input", applyFilters);
filterNombre.addEventListener("input", applyFilters);
filterEstado.addEventListener("change", applyFilters);
btnClearFilters.addEventListener("click", () => resetFilters(true));

document.querySelectorAll(".btn-input-clear").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = document.getElementById(btn.dataset.target);
    if (target) { target.value = ""; applyFilters(); target.focus(); }
  });
});

btnProcesar.addEventListener("click", procesar);
btnExportar.addEventListener("click", exportarCsv);
btnGenerarInforme?.addEventListener("click", generarInformePdf);
