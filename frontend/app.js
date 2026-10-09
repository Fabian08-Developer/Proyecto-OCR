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
const filesizePdf = document.getElementById("filesize-pdf");
const filesizeExcel = document.getElementById("filesize-excel");
const dzPdf = document.getElementById("dz-pdf");
const dzExcel = document.getElementById("dz-excel");
const filePdfBox = document.getElementById("file-pdf");
const fileExcelBox = document.getElementById("file-excel");
const btnProcesar = document.getElementById("btn-procesar");
const progressPanel = document.getElementById("progress-panel");
const progressElapsed = document.getElementById("progress-elapsed");
const errorPanel = document.getElementById("error-panel");
const errorMsg = document.getElementById("error-msg");
const resultsPanel = document.getElementById("results-panel");
const resultHero = document.getElementById("result-hero");
const summaryCards = document.getElementById("summary-cards");
const btnExportar = document.getElementById("btn-exportar");
const btnGenerarInforme = document.getElementById("btn-generar-informe");
const resultIdBadge = document.getElementById("result-id-badge");
const navUsername = document.getElementById("nav-username");
const btnLogout = document.getElementById("btn-logout");
const headerSubtitle = document.getElementById("header-subtitle");
const stepper = document.getElementById("stepper");

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
let progressTimer = null;

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
      const eyebrow = document.querySelector(".eyebrow");
      if (eyebrow) eyebrow.textContent = "Comparación guardada";
      // Vista de solo lectura: no hay nada que cargar; se ofrece volver o empezar otra verificación
      document.getElementById("upload-panel")?.classList.add("hidden");
      stepper?.classList.add("hidden");
      document.getElementById("back-link")?.classList.remove("hidden");
      document.getElementById("new-link")?.classList.remove("hidden");
      if (headerSubtitle) {
        headerSubtitle.textContent = `Visualizando comparación del ${detalle.fecha_comparacion || "historial"} · ${detalle.nombre_pdf || ""}`;
      }
      if (resultIdBadge && lastComparacionId) {
        resultIdBadge.textContent = `ID: ${lastComparacionId.slice(0, 8)}…`;
      }
      resetFilters(false);
      renderResults(detalle);
      setStep(3);
    }
  }
})();

btnLogout?.addEventListener("click", () => {
  localStorage.clear();
  window.location.replace("/login.html");
});

// ---------------------------------------------------------------------------
// Pasos (stepper)
// ---------------------------------------------------------------------------
function setStep(current) {
  stepper?.querySelectorAll(".step").forEach((li) => {
    const n = Number(li.dataset.step);
    li.classList.toggle("is-active", n === current);
    li.classList.toggle("is-done", n < current);
  });
}

// ---------------------------------------------------------------------------
// Subida de archivos
// ---------------------------------------------------------------------------
const FILE_RULES = {
  pdf:   { input: inputPdf,   ext: [".pdf"],          label: "PDF" },
  excel: { input: inputExcel, ext: [".xls", ".xlsx"], label: "Excel (.xls o .xlsx)" },
};

function validateChosenFile(kind) {
  const rule = FILE_RULES[kind];
  const file = rule.input.files[0];
  if (file && !rule.ext.some((e) => file.name.toLowerCase().endsWith(e))) {
    UI.toast(`"${file.name}" no es válido: se esperaba un archivo ${rule.label}.`, "error");
    rule.input.value = "";
  }
}

function updateFileLabels() {
  [
    [inputPdf, filenamePdf, filesizePdf, dzPdf, filePdfBox],
    [inputExcel, filenameExcel, filesizeExcel, dzExcel, fileExcelBox],
  ].forEach(([input, nameEl, sizeEl, dz, box]) => {
    const file = input.files[0];
    nameEl.textContent = file ? file.name : "Ningún archivo seleccionado";
    sizeEl.textContent = file ? UI.formatBytes(file.size) : "";
    dz.classList.toggle("has-file", Boolean(file));
    box.classList.toggle("hidden", !file);
  });
  btnProcesar.disabled = !(inputPdf.files[0] && inputExcel.files[0]);
}

inputPdf.addEventListener("change", () => { validateChosenFile("pdf"); updateFileLabels(); });
inputExcel.addEventListener("change", () => { validateChosenFile("excel"); updateFileLabels(); });

// Quitar archivo (el botón vive dentro del <label>: se evita abrir el selector)
[["remove-pdf", inputPdf], ["remove-excel", inputExcel]].forEach(([id, input]) => {
  document.getElementById(id)?.addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    input.value = "";
    updateFileLabels();
  });
});

// Resaltado al arrastrar (la propia entrada de archivo cubre la zona y recibe el "soltar")
[dzPdf, dzExcel].forEach((dz) => {
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, () => dz.classList.add("is-dragover")));
  ["dragleave", "drop", "dragend"].forEach((ev) => dz.addEventListener(ev, () => dz.classList.remove("is-dragover")));
});

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
function setActiveTab(tabName) {
  activeTab = tabName;
  document.querySelectorAll(".tab-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === tabName);
    b.setAttribute("aria-selected", b.dataset.tab === tabName ? "true" : "false");
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

// Escapa texto antes de insertarlo en innerHTML: nombres y observaciones vienen del Excel y del OCR
// (datos que el usuario no controla) y no deben interpretarse como HTML.
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// Valor listo para insertar en HTML: "—" si esta vacio y, si no, el texto escapado.
function fmt(value) {
  return value === null || value === undefined || value === "" ? "—" : escapeHtml(value);
}

function statusBadge(estado) {
  return `<span class="badge ${escapeHtml(estado)}">${escapeHtml(ESTADO_LABELS[estado] || estado)}</span>`;
}

function similarityBar(value) {
  if (value === null || value === undefined) return `<span class="cell-muted">—</span>`;
  const pct = Math.max(0, Math.min(100, Math.round(value * 100)));
  const tone = pct >= 85 ? "" : pct >= 72 ? "mid" : "low";
  return `<span class="sim"><span class="sim-track"><span class="${tone}" style="width:${pct}%"></span></span><b>${pct}%</b></span>`;
}

// Nombre del Excel (principal) y, debajo, lo que leyó el OCR. Si las LETRAS difieren (no solo los
// espacios que el OCR pega o separa) la línea del OCR se resalta: es una diferencia real a revisar.
function personCell(nombreExcel, nombreOcr, fechaNac) {
  const compact = (s) => normalizeText(s).replace(/\s+/g, "");
  let ocrLine;
  if (!nombreOcr) {
    ocrLine = `<span class="ocr-line none"><span class="tag-ocr">OCR</span>Sin documento leído en el PDF</span>`;
  } else {
    const diff = compact(nombreExcel) !== compact(nombreOcr);
    const nac = fechaNac ? ` · Nac. ${escapeHtml(fechaNac)}` : "";
    ocrLine = `<span class="ocr-line ${diff ? "diff" : ""}" title="${diff ? "El nombre leído por el OCR difiere del Excel" : "Coincide con el Excel"}">` +
      `<span class="tag-ocr">OCR</span>${escapeHtml(nombreOcr)}${nac}</span>`;
  }
  return `<strong>${fmt(nombreExcel)}</strong>${ocrLine}`;
}

function pageChips(pages) {
  const list = Array.isArray(pages) ? pages : [];
  if (!list.length) return `<span class="cell-muted">—</span>`;
  return list.map((p) => `<span class="chip">p. ${escapeHtml(p)}</span>`).join("");
}

function emptyRow(cols, text) {
  return `<tr><td colspan="${cols}" class="empty-state-cell">
    <span class="empty-state-icon">${icon("search", 28)}</span>
    <strong>Sin resultados</strong>${text}</td></tr>`;
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
function startProgressTimer() {
  const t0 = Date.now();
  progressElapsed.textContent = "00:00";
  clearInterval(progressTimer);
  progressTimer = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    progressElapsed.textContent =
      `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }, 1000);
}

function stopProgressTimer() { clearInterval(progressTimer); }

async function procesar() {
  errorPanel.classList.add("hidden");
  resultsPanel.classList.add("hidden");
  progressPanel.classList.remove("hidden");
  btnProcesar.disabled = true;
  setStep(2);
  startProgressTimer();

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
    setStep(3);
    UI.toast("Verificación completada y guardada en el historial.", "success");
    resultsPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    errorMsg.textContent = err.message;
    errorPanel.classList.remove("hidden");
    setStep(1);
  } finally {
    stopProgressTimer();
    progressPanel.classList.add("hidden");
    btnProcesar.disabled = !(inputPdf.files[0] && inputExcel.files[0]);
  }
}

// ---------------------------------------------------------------------------
// Resultado principal: gráfico de dona + leyenda
// ---------------------------------------------------------------------------
const STATUS_ORDER = [
  { key: "coincide",             field: "coinciden",            label: "Coinciden",              color: "var(--ok-dot)" },
  { key: "nombre_no_coincide",   field: "nombre_no_coincide",   label: "Nombre no coincide",     color: "var(--warn-dot)" },
  { key: "documento_incompleto", field: "documento_incompleto", label: "Datos incompletos",      color: "var(--inc-dot)" },
  { key: "no_encontrado_en_pdf", field: "no_encontrado_en_pdf", label: "No encontrados en PDF",  color: "var(--bad-dot)" },
];

function renderHero(resumen) {
  const total = resumen.total_excel || 0;
  const ok = resumen.coinciden || 0;
  const pct = total === 0 ? 0 : (ok === total ? 100 : Math.min(99, Math.round((ok * 100) / total)));
  const toReview = Math.max(0, total - ok);

  // Geometria del anillo (r = 48 en un viewBox de 120)
  const C = 2 * Math.PI * 48;
  const GAP = 3;
  let acc = 0;
  const segments = STATUS_ORDER.map((s) => {
    const n = resumen[s.field] || 0;
    const len = total ? (n / total) * C : 0;
    const seg = { color: s.color, len, offset: acc, n };
    acc += len;
    return seg;
  }).filter((s) => s.n > 0);
  const single = segments.length === 1;

  const circles = segments.map((s) => {
    const visible = single ? s.len : Math.max(s.len - GAP, 0.5);
    return `<circle class="ring-seg" cx="60" cy="60" r="48" stroke="${s.color}"
      stroke-dasharray="0 ${C.toFixed(2)}" stroke-dashoffset="${(-s.offset).toFixed(2)}"
      data-final="${visible.toFixed(2)} ${C.toFixed(2)}"></circle>`;
  }).join("");

  const headline = total > 0 && ok === total
    ? `<b>Todos</b> los participantes fueron verificados`
    : `<b>${ok} de ${total}</b> participantes verificados`;

  const sub = total > 0 && ok === total
    ? `<span class="tag" style="color:var(--ok)">${icon("check-circle", 17)}Sin diferencias entre el PDF y el Excel</span>`
    : `<span class="tag">${icon("alert-triangle", 17)}${toReview} participante${toReview === 1 ? "" : "s"} requiere${toReview === 1 ? "" : "n"} revisión</span>
       · ${resumen.total_pdf_documentos ?? 0} documentos leídos en el PDF`;

  const legend = STATUS_ORDER.map((s) => {
    const n = resumen[s.field] || 0;
    const share = total ? Math.round((n / total) * 100) : 0;
    return `<button type="button" class="legend-row" data-status="${s.key}" data-tab="resultados"
        title="Clic para filtrar por «${escapeHtml(s.label)}»">
      <span class="legend-dot"></span>
      <span class="legend-label">${s.label}</span>
      <span class="legend-count" data-count="${n}">0</span>
      <span class="legend-bar"><span data-width="${share}"></span></span>
    </button>`;
  }).join("");

  resultHero.innerHTML = `
    <div class="donut" role="img" aria-label="${pct}% de coincidencia: ${ok} de ${total} participantes">
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <circle class="ring-track" cx="60" cy="60" r="48"></circle>
        ${circles}
      </svg>
      <div class="donut-center"><strong data-count="${pct}">0</strong><span>coincidencia</span></div>
    </div>
    <div class="hero-body">
      <h2>${headline}</h2>
      <p class="hero-sub">${sub}</p>
      <div class="legend">${legend}</div>
    </div>`;

  // Animaciones de entrada (dos fotogramas para que la transicion parta de cero)
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    resultHero.querySelectorAll(".ring-seg").forEach((c) => c.setAttribute("stroke-dasharray", c.dataset.final));
    resultHero.querySelectorAll(".legend-bar span").forEach((b) => { b.style.width = `${b.dataset.width}%`; });
  }));
  // El signo % lo pone el CSS (strong::after): countUp reescribe el texto del elemento en cada fotograma
  resultHero.querySelectorAll("[data-count]").forEach((el) => {
    const long = el.matches(".donut-center strong");
    UI.countUp(el, Number(el.dataset.count), reduce ? 0 : (long ? 900 : 700));
  });
}

// ---------------------------------------------------------------------------
// Tarjetas de apoyo
// ---------------------------------------------------------------------------
function renderSummary(resumen) {
  const sobrantes = resumen.sobrantes_en_pdf ?? 0;
  const sinNumero = resumen.documentos_sin_numero_detectado ?? 0;
  const cards = [
    { label: "Registros en Excel",   value: resumen.total_excel ?? 0,           ic: "users",     tab: "resultados", tone: "" },
    { label: "Documentos en PDF",    value: resumen.total_pdf_documentos ?? 0,  ic: "layers",    tab: "resultados", tone: "" },
    { label: "Sobrantes en PDF",     value: sobrantes,                          ic: "file-plus", tab: "sobrantes",  tone: sobrantes > 0 ? "warn" : "" },
    { label: "Sin número detectado", value: sinNumero,                          ic: "file-x",    tab: "sin-numero", tone: sinNumero > 0 ? "warn" : "" },
  ];

  summaryCards.innerHTML = cards.map((c) => `
    <button type="button" class="summary-card clickable" data-tab="${c.tab}" data-status="" data-tone="${c.tone}" title="Clic para ver el detalle">
      <span class="sc-icon">${icon(c.ic, 22)}</span>
      <span class="sc-text">
        <span class="value" data-count="${c.value}">0</span>
        <span class="label">${c.label}</span>
      </span>
    </button>`).join("");

  summaryCards.querySelectorAll("[data-count]").forEach((el) => UI.countUp(el, Number(el.dataset.count), 700));
}

// Un solo manejador para tarjetas y leyenda (se crean de nuevo en cada resultado)
resultsPanel.addEventListener("click", (ev) => {
  const el = ev.target.closest(".summary-card.clickable, .legend-row");
  if (!el) return;
  const tab = el.dataset.tab;
  const status = el.dataset.status;
  if (tab) setActiveTab(tab);
  if (status !== undefined) {
    filterEstado.value = (filterEstado.value === status && status !== "") ? "" : status;
  }
  applyFilters();
});

// ---------------------------------------------------------------------------
// Render de tablas
// ---------------------------------------------------------------------------
function renderResultsTable(resultados) {
  const tbody = document.querySelector("#table-resultados tbody");
  if (!resultados || resultados.length === 0) {
    tbody.innerHTML = emptyRow(5, "No hay participantes que coincidan con los filtros aplicados.");
    return;
  }
  tbody.innerHTML = resultados.map((r) => {
    const nombreOcr = r.ocr?.nombre_completo || "";
    const fechaNac = r.ocr?.fecha_nacimiento || "";
    const obs = r.observaciones || "";
    return `<tr>
      <td>${statusBadge(r.estado)}</td>
      <td class="cell-doc">${fmt(r.numero_documento)}</td>
      <td class="cell-person">${personCell(r.excel?.nombre_reportado, nombreOcr, fechaNac)}</td>
      <td>${similarityBar(r.similitud_nombre)}</td>
      <td><div class="cell-obs ${obs ? "is-alert" : ""}" title="${escapeHtml(obs)}">${fmt(obs)}</div></td>
    </tr>`;
  }).join("");
}

function renderSobrantes(sobrantes) {
  const tbody = document.querySelector("#table-sobrantes tbody");
  if (!sobrantes || sobrantes.length === 0) {
    tbody.innerHTML = emptyRow(4, "No hay documentos sobrantes que coincidan con los filtros.");
    return;
  }
  tbody.innerHTML = sobrantes.map((d) => `<tr>
    <td class="cell-doc">${fmt(d.numero_documento)}</td>
    <td class="cell-name">${fmt(d.nombre_completo)}</td>
    <td class="cell-muted">${fmt(d.fecha_nacimiento)}</td>
    <td>${pageChips(d.paginas_pdf)}</td>
  </tr>`).join("");
}

function renderSinNumero(docs) {
  const tbody = document.querySelector("#table-sin-numero tbody");
  if (!docs || docs.length === 0) {
    tbody.innerHTML = emptyRow(3, "No hay documentos sin número que coincidan con los filtros.");
    return;
  }
  tbody.innerHTML = docs.map((d) => `<tr>
    <td class="cell-name">${fmt(d.nombre_completo)}</td>
    <td class="cell-muted">${fmt(d.fecha_nacimiento)}</td>
    <td>${pageChips(d.paginas_pdf)}</td>
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

  document.querySelectorAll(".summary-card, .legend-row").forEach((card) => {
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

// Avisos de lectura del Excel (filas omitidas, IDs dudosos, hojas ignoradas...).
// Se escribe con textContent: el contenido viene del Excel y no debe interpretarse como HTML.
function renderExcelWarnings(advertencias) {
  const box = document.getElementById("excel-warnings");
  const title = document.getElementById("excel-warnings-title");
  const list = document.getElementById("excel-warnings-list");
  if (!box || !title || !list) return;
  list.replaceChildren();
  if (!advertencias || advertencias.length === 0) {
    box.classList.add("hidden");
    return;
  }
  title.textContent = `Atención: el Excel tuvo ${advertencias.length} advertencia(s) de lectura. ` +
    "Los participantes afectados pueden no estar en los resultados:";
  advertencias.forEach((texto) => {
    const li = document.createElement("li");
    li.textContent = texto;
    list.appendChild(li);
  });
  box.classList.remove("hidden");
}

function renderResults(data) {
  renderExcelWarnings(data.advertencias_excel);
  renderHero(data.resumen);
  renderSummary(data.resumen);
  applyFilters();
  setActiveTab(activeTab);
  resultsPanel.classList.remove("hidden");
}

// ---------------------------------------------------------------------------
// Exportar CSV
// ---------------------------------------------------------------------------
function toCsvValue(value) {
  if (value === null || value === undefined) return "";
  let text = String(value);
  // Inyeccion de formulas: Excel/LibreOffice ejecutan celdas que empiezan con = + - @ (o tab/CR).
  // Los nombres vienen de archivos subidos, asi que se neutraliza anteponiendo una comilla simple.
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
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
  const blob = new Blob([`﻿${csv}`], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "resultados_verificacion.csv";
  a.click();
  URL.revokeObjectURL(url);
  UI.toast(`Se exportaron ${list.length} registros a CSV.`, "success");
}

// ---------------------------------------------------------------------------
// Generar informe PDF
// ---------------------------------------------------------------------------
async function generarInformePdf() {
  if (!lastComparacionId) { UI.toast("No hay una comparación disponible para generar el informe.", "error"); return; }

  const btn = btnGenerarInforme;
  btn.classList.add("is-loading");
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
    UI.toast("Informe PDF generado.", "success");
  } catch (err) {
    UI.toast(`No se pudo generar el informe: ${err.message}`, "error");
  } finally {
    btn.classList.remove("is-loading");
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
