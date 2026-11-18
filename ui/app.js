// Firmador DNIe — lógica de la interfaz.
// Convención del sello (igual que firma_pdf.py): su posición (esquina inferior
// izquierda) va en fracciones 0..1 de `page.view` (CropBox ∩ MediaBox), origen
// abajo-izquierda, espacio PDF sin rotar; su tamaño es fijo por tipo, en pt.
// La conversión pantalla <-> PDF se hace con el viewport de pdf.js, que ya
// considera la rotación de la página.

import * as pdfjsLib from "./vendor/pdfjs/pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL("./vendor/pdfjs/pdf.worker.min.mjs", import.meta.url).href;
const RUTA_PDFJS = new URL("./vendor/pdfjs/", import.meta.url).href;

// Tipos de sello: mismas plantillas, tamaños máximos de letra (pt), tamaños
// fijos (pt) y posiciones predeterminadas (abajo a la derecha en A4) que
// firma_pdf.py (PLANTILLAS_SELLO, _TAM_MAXIMO, TAMANOS_SELLO, POSICIONES_PREDETERMINADAS).
const TIPOS = {
  firma: {
    plantilla: "Firmado digitalmente por:\n{firmante}\nMotivo: {motivo}\nFecha: {fecha}",
    tamMaximo: 9,
    tamano: { ancho: 160, alto: 40 },
    posicion: { x: 400 / 595, y: 30 / 842 },
    ayuda: "Logo a la izquierda y los datos de la firma a la derecha.",
  },
  visto_bueno: {
    plantilla: "Firmado digitalmente por\n{firmante}\nMotivo: {motivo}\nFecha: {fecha}",
    tamMaximo: 8,
    tamano: { ancho: 140, alto: 110 },
    posicion: { x: 420 / 595, y: 30 / 842 },
    ayuda: "Imagen de visto bueno arriba y los datos de la firma debajo, sin superponerse.",
  },
};
// Motivos de cada tipo (lista cerrada; el primero es el predeterminado), como
// firma_pdf.MOTIVOS_POR_TIPO. Python envía los mismos en obtener_config.
const MOTIVOS_INICIALES = {
  firma: ["Soy el autor del documento", "En señal de conformidad"],
  visto_bueno: ["Doy V° B°"],
};
const PROPORCION_LOGO = 0.38;
const PROPORCION_IMAGEN_VB = 0.55; // parte superior del visto bueno reservada a la imagen
const TAM_MINIMO = 3; // pt
const ANCHO_A4 = 595; // pt
const PASOS_ZOOM = [0.5, 0.67, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4];
const MARGEN_VISOR = 56;
const ESPERA_RUTA_SOLTAR = 1200; // ms que se espera la ruta de Python antes de leer el archivo en JS
const TAM_SUBIDA = 512 * 1024; // bytes por fragmento al enviar un PDF arrastrado a Python

const $ = (id) => document.getElementById(id);
const el = {
  app: $("app"),
  docInfo: $("doc-info"), docNombre: $("doc-nombre"), docMeta: $("doc-meta"), chipFirmas: $("chip-firmas"),
  btnCerrarDoc: $("btn-cerrar-doc"),
  cola: $("cola"), colaTotal: $("cola-total"), listaCola: $("lista-cola"), btnAgregar: $("btn-agregar"), btnVaciar: $("btn-vaciar"),
  vacioTitulo: $("vacio-titulo"),
  txtCampoAyuda: $("txt-campo-ayuda"), btnPosicionPaginas: $("btn-posicion-paginas"), btnPosicionTodos: $("btn-posicion-todos"),
  lblGuardarComo: $("lbl-guardar-como"), btnFirmarTexto: $("btn-firmar-texto"),
  dialogoProgreso: $("dialogo-progreso"), dialogoProgresoValor: $("dialogo-progreso-valor"),
  dialogoNota: $("dialogo-nota"), dialogoLista: $("dialogo-lista"),
  tabFirmar: $("tab-firmar"), tabValidar: $("tab-validar"),
  vistaFirmar: $("vista-firmar"), vistaValidar: $("vista-validar"),
  btnAbrir: $("btn-abrir"), btnAbrirVacio: $("btn-abrir-vacio"), btnTema: $("btn-tema"),
  miniaturas: $("lista-miniaturas"),
  scroll: $("lienzo-scroll"), vacio: $("vacio"), vacioTexto: $("vacio-texto"), hoja: $("hoja"), lienzo: $("lienzo"),
  cargador: $("cargador"), cargadorTexto: $("cargador-texto"), cargadorProgreso: $("cargador-progreso"),
  cargadorBarra: $("cargador-barra"),
  sello: $("sello"), selloContenido: $("sello-contenido"), selloMarca: document.querySelector(".sello-marca"),
  selloLogo: $("sello-logo"), selloIcono: $("sello-icono"), selloTexto: $("sello-texto"), selloLineas: $("sello-lineas"), selloEtiqueta: $("sello-etiqueta"), selloMedidas: $("sello-medidas"),
  btnAnt: $("btn-pag-ant"), btnSig: $("btn-pag-sig"), inpPagina: $("inp-pagina"), totalPaginas: $("total-paginas"),
  btnMenos: $("btn-zoom-menos"), btnMas: $("btn-zoom-mas"), btnAjustar: $("btn-ajustar"), btnPagina: $("btn-pagina"), zoomValor: $("zoom-valor"),
  tarjeta: $("tarjeta-dnie"), dnieEstado: $("dnie-estado"), dnieTitular: $("dnie-titular"), dnieDetalle: $("dnie-detalle"),
  dnieEmisor: $("dnie-emisor"), dnieVence: $("dnie-vence"), btnDnie: $("btn-dnie"),
  notaFirmas: $("nota-firmas"), notaFirmasTexto: $("nota-firmas-texto"),
  lblPaginaActual: $("lbl-pagina-actual"),
  txtTipoAyuda: $("txt-tipo-ayuda"), campoLogo: $("campo-logo"),
  campoImagenVb: $("campo-imagen-vb"),
  selMotivo: $("sel-motivo"), txtLugar: $("txt-lugar"),
  chkGuardarComo: $("chk-guardar-como"), txtDestino: $("txt-destino"),
  btnFirmar: $("btn-firmar"),
  btnValidarAbierto: $("btn-validar-abierto"), btnValidarOtro: $("btn-validar-otro"),
  resultados: $("resultados"), validarSub: $("validar-sub"),
  dialogoFondo: $("dialogo"), dialogo: document.querySelector(".dialogo"), dialogoTitulo: $("dialogo-titulo"),
  dialogoTexto: $("dialogo-texto"), dialogoRuta: $("dialogo-ruta"), dialogoAcciones: $("dialogo-acciones"),
  avisos: $("avisos"),
};

const estado = {
  info: null,          // datos del PDF devueltos por Python
  doc: null,           // documento de pdf.js
  pagina: 1,
  total: 0,
  zoom: { ajuste: "pagina", escala: 1 }, // ajuste: "pagina" | "ancho" | null
  caja: { ...TIPOS.firma.posicion }, // posición común { x, y } (la clave «caja» se mantiene por compatibilidad)
  tipo: "firma",       // "firma" | "visto_bueno"
  motivos: { ...MOTIVOS_INICIALES },
  imagenes: { logo: null, imagen_vb: null }, // { nombre, datos (data URL) } o null
  viewport: null,
  vistaPdf: null,
  ticket: 0,
  tareaRender: null,
  dnie: null,
  firmando: false,
  miniaturas: new Map(), // número de página -> { vp, view, marca }
  focoPrevio: null,
  // Cola: Python guarda los documentos; aquí solo su estado. Solo el documento
  // a la vista tiene sus bytes en JS (se cargan al elegirlo).
  cola: [],            // [{ id, nombre, ruta, paginas, firmas, docmdp, valido, estado, mensaje, salida }]
  actualId: null,      // id del documento a la vista
  // id -> { caja?: posición propia del documento { x, y }, pagina?: índice 0.. del campo («Página actual»),
  //        cajasPaginas?: { índice 0..: { x, y } } posición propia de algunas páginas }
  ajustes: {},
  lote: null,          // avance de la firma por lotes { total, indice, hechos, nombre, cancelando }
  carga: 0,            // descarta cargas de documentos que quedaron viejas
  sinArrastrar: false,
};

const ESTADOS_COLA = {
  pendiente: "Pendiente",
  firmando: "Firmando",
  firmado: "Firmado",
  error: "Error",
  omitido: "Omitido",
};
const NOTA_PIN_LOTE =
  "Si Windows le pide el PIN del DNIe, escríbalo en su ventana. Según la tarjeta, puede pedirlo una sola vez o en cada documento.";

// ---------------------------------------------------------------- utilidades

const limitar = (v, min, max) => Math.min(Math.max(v, min), max);

function h(etiqueta, atributos = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  for (const [k, v] of Object.entries(atributos)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") nodo.className = v;
    else if (k.startsWith("on")) nodo.addEventListener(k.slice(2), v);
    else nodo.setAttribute(k, v === true ? "" : v);
  }
  for (const hijo of hijos.flat()) {
    if (hijo === null || hijo === undefined || hijo === false) continue;
    nodo.append(hijo instanceof Node ? hijo : document.createTextNode(String(hijo)));
  }
  return nodo;
}

function icono(nombre, clase = "ic") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", clase);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${nombre}`);
  svg.append(use);
  return svg;
}

function leerPreferencia(clave, porDefecto) {
  try {
    const v = localStorage.getItem(`firmador.${clave}`);
    return v === null ? porDefecto : JSON.parse(v);
  } catch {
    return porDefecto;
  }
}

function guardarPreferencia(clave, valor) {
  try {
    localStorage.setItem(`firmador.${clave}`, JSON.stringify(valor));
  } catch {
    /* almacenamiento no disponible: solo se pierde la preferencia */
  }
}

async function llamar(metodo, ...args) {
  const api = window.pywebview && window.pywebview.api;
  if (!api || typeof api[metodo] !== "function") {
    return { ok: false, mensaje: "La interfaz no está conectada a la aplicación. Ábrala con «python app.py»." };
  }
  try {
    const r = await api[metodo](...args);
    return r ?? { ok: false, mensaje: "Respuesta vacía de la aplicación." };
  } catch (e) {
    return { ok: false, mensaje: (e && e.message) || String(e) };
  }
}

function aviso(texto, error = false) {
  const nodo = h("div", { class: `aviso${error ? " error" : ""}` }, icono(error ? "alerta" : "info"), texto);
  el.avisos.append(nodo);
  setTimeout(() => nodo.remove(), error ? 6000 : 3500);
}

function base64ABytes(b64) {
  if (typeof Uint8Array.fromBase64 === "function") return Uint8Array.fromBase64(b64);
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function bytesABase64(bytes) {
  if (typeof bytes.toBase64 === "function") return bytes.toBase64();
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

function tamanoLegible(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
}

function fechaLegible(iso, conHora = true) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("es-PE", conHora ? { dateStyle: "long", timeStyle: "medium" } : { dateStyle: "long" });
}

function plural(n, uno, varios) {
  return `${n} ${n === 1 ? uno : varios}`;
}

// ---------------------------------------------------------------- tema

function temaActual() {
  const t = document.documentElement.dataset.theme;
  if (t) return t;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function actualizarBotonTema() {
  const oscuro = temaActual() === "dark";
  el.btnTema.setAttribute("aria-label", oscuro ? "Cambiar a tema claro" : "Cambiar a tema oscuro");
}

function iniciarTema() {
  const guardado = leerPreferencia("tema", null);
  if (guardado === "light" || guardado === "dark") document.documentElement.dataset.theme = guardado;
  actualizarBotonTema();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", actualizarBotonTema);
  el.btnTema.addEventListener("click", () => {
    const nuevo = temaActual() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nuevo;
    guardarPreferencia("tema", nuevo);
    actualizarBotonTema();
  });
}

// ---------------------------------------------------------------- vistas

function mostrarVista(vista) {
  const firmar = vista === "firmar";
  el.tabFirmar.setAttribute("aria-selected", String(firmar));
  el.tabValidar.setAttribute("aria-selected", String(!firmar));
  el.tabFirmar.tabIndex = firmar ? 0 : -1;
  el.tabValidar.tabIndex = firmar ? -1 : 0;
  el.vistaFirmar.hidden = !firmar;
  el.vistaValidar.hidden = firmar;
  if (firmar && estado.doc) programarRender();
}

function vistaActual() {
  return el.vistaValidar.hidden ? "firmar" : "validar";
}

// ---------------------------------------------------------------- DNIe

async function consultarDnie() {
  el.tarjeta.dataset.estado = "cargando";
  el.dnieEstado.textContent = "Buscando el DNIe…";
  el.dnieTitular.textContent = "";
  el.btnDnie.disabled = true;
  const r = await llamar("estado_dnie");
  el.btnDnie.disabled = false;
  if (r.ok) {
    estado.dnie = r;
    el.tarjeta.dataset.estado = "ok";
    el.dnieEstado.textContent = "DNIe listo para firmar";
    el.dnieTitular.textContent = r.titular;
    el.dnieEmisor.textContent = r.emisor || "—";
    el.dnieVence.textContent = fechaLegible(r.valido_hasta, false);
    el.dnieDetalle.hidden = false;
  } else {
    estado.dnie = null;
    el.tarjeta.dataset.estado = "error";
    el.dnieEstado.textContent = "DNIe no disponible";
    el.dnieTitular.textContent = r.mensaje || "No se pudo leer el DNIe.";
    el.dnieDetalle.hidden = true;
  }
  actualizarVistaPrevia();
}

// ---------------------------------------------------------------- indicador de carga

// Se muestra tras un breve retraso para que los PDF pequeños no parpadeen.
// `fraccion` (0..1) muestra la barra de progreso; null la oculta.
let temporizadorCarga = null;

function mostrarCarga(texto, fraccion = null) {
  el.cargadorTexto.textContent = texto;
  el.cargadorProgreso.hidden = fraccion === null;
  if (fraccion !== null) el.cargadorBarra.style.width = `${Math.round(Math.min(1, Math.max(0, fraccion)) * 100)}%`;
  if (!el.cargador.hidden || temporizadorCarga) return;
  temporizadorCarga = setTimeout(() => {
    temporizadorCarga = null;
    el.cargador.hidden = false;
  }, 150);
}

function ocultarCarga() {
  clearTimeout(temporizadorCarga);
  temporizadorCarga = null;
  el.cargador.hidden = true;
}

const porcentaje = (f) => `${Math.round(Math.min(1, Math.max(0, f)) * 100)} %`;

// ---------------------------------------------------------------- documento

// Diálogo nativo de selección múltiple: los PDF elegidos se agregan a la cola.
async function agregarDialogo() {
  if (estado.firmando) return;
  const r = await llamar("agregar_pdfs");
  if (!r.ok) {
    if (!r.cancelado) aviso(r.mensaje, true);
    return;
  }
  await despuesDeAgregar(r);
}

async function abrirRuta(ruta) {
  if (estado.firmando) return;
  mostrarCarga("Abriendo documento…");
  const r = await llamar("abrir_ruta", ruta);
  if (!r.ok) {
    ocultarCarga();
    aviso(r.mensaje, true);
    return;
  }
  if (r.duplicado) aviso(`«${r.nombre}» ya está en la lista.`);
  await cargarDocumento(r);
}

function avisarAgregados(r) {
  const validos = r.agregados.filter((i) => i.valido);
  const malos = r.agregados.filter((i) => !i.valido);
  if (r.duplicados.length === 1) aviso(`«${r.duplicados[0]}» ya está en la lista.`);
  else if (r.duplicados.length > 1) aviso(`${r.duplicados.length} documentos ya estaban en la lista y se omitieron.`);
  if (malos.length === 1) aviso(`«${malos[0].nombre}» no se puede firmar: ${malos[0].mensaje}`, true);
  else if (malos.length > 1) aviso(`${malos.length} archivos no son PDF válidos; quedaron marcados con «Error».`, true);
  if (validos.length > 1) aviso(`Se agregaron ${validos.length} documentos a la lista.`);
}

// Tras agregar: un solo archivo se abre (como antes); con varios se abre el
// primero solo si no hay otro documento a la vista.
async function despuesDeAgregar(r) {
  aplicarCola(r.cola);
  avisarAgregados(r);
  const validos = r.agregados.filter((i) => i.valido);
  let abrir = null;
  if (r.agregados.length + r.duplicados.length === 1) abrir = validos[0]?.id || (r.ids_duplicados || [])[0] || null;
  else if (!estado.info) abrir = validos[0]?.id || null;
  if (abrir) {
    mostrarVista("firmar");
    await abrirItem(abrir);
  }
}

async function abrirItem(id) {
  if (estado.firmando || (id === estado.actualId && estado.doc)) return;
  mostrarCarga("Abriendo documento…");
  const r = await llamar("abrir_item", id);
  if (!r.ok) {
    ocultarCarga();
    aviso(r.mensaje, true);
    return;
  }
  mostrarVista("firmar");
  await cargarDocumento(r);
}

async function refrescarCola() {
  const r = await llamar("obtener_cola");
  if (r.ok) aplicarCola(r.cola);
}

// Python manda la cola completa tras cada cambio; aquí se refleja.
function aplicarCola(cola) {
  if (!Array.isArray(cola)) return;
  estado.cola = cola;
  const ids = new Set(cola.map((i) => i.id));
  for (const id of Object.keys(estado.ajustes)) if (!ids.has(id)) delete estado.ajustes[id];
  if (estado.actualId && !ids.has(estado.actualId)) vaciarVisor();
  renderizarCola();
}

function pendientes() {
  return estado.cola.filter((i) => i.valido && (i.estado === "pendiente" || i.estado === "error"));
}

// Con 2 o más documentos por firmar (o uno que no es el que está a la vista) se
// firma por lotes; si no, el documento a la vista, como siempre.
function planFirma() {
  const pend = pendientes();
  if (pend.length >= 2 || (pend.length === 1 && pend[0].id !== estado.actualId)) return { modo: "lote", n: pend.length };
  // Un documento ya firmado en esta sesión no se vuelve a firmar por accidente
  // (antes generaba un «_firmado (2).pdf» duplicado).
  if (estado.info && estado.info.docmdp !== 1 && !actualYaFirmado()) return { modo: "uno" };
  return null;
}

function actualYaFirmado() {
  const item = estado.cola.find((i) => i.id === estado.actualId);
  return !!item && item.estado === "firmado";
}

function filaCola(item) {
  const ajuste = estado.ajustes[item.id];
  const meta = [];
  if (item.valido) meta.push(plural(item.paginas, "pág.", "págs."));
  if (item.firmas > 0) meta.push("ya firmado");
  if (ajuste && (ajuste.caja || tieneCajasPaginas(ajuste))) meta.push("posición propia");
  if (ajuste && ajuste.pagina != null) meta.push(`campo en pág. ${ajuste.pagina + 1}`);
  const etiqueta = ESTADOS_COLA[item.estado] || item.estado;
  const mostrarMensaje = item.mensaje && item.estado !== "firmado";
  return h(
    "li",
    { class: "fila-cola", "data-estado": item.estado, "aria-current": item.id === estado.actualId ? "true" : null },
    h(
      "button",
      {
        class: "fila-cola-abrir",
        type: "button",
        disabled: estado.firmando,
        title: item.salida ? `Firmado: ${item.salida}` : item.ruta || item.nombre,
        "aria-label": `${item.nombre}. ${etiqueta}${mostrarMensaje ? `: ${item.mensaje}` : ""}. ${meta.join(", ")}`,
        onclick: () => abrirItem(item.id),
      },
      h("span", { class: "fila-cola-nombre" }, item.nombre),
      h("span", { class: "fila-cola-meta" }, h("span", { class: "chip-estado", "data-estado": item.estado }, etiqueta), meta.join(" · ")),
      mostrarMensaje ? h("span", { class: "fila-cola-mensaje" }, item.mensaje) : null,
    ),
    h(
      "button",
      {
        class: "btn-icono fila-cola-quitar",
        type: "button",
        disabled: estado.firmando,
        "aria-label": `Quitar ${item.nombre} de la lista`,
        title: "Quitar de la lista",
        onclick: () => quitarItem(item.id),
      },
      icono("cerrar"),
    ),
  );
}

function renderizarCola() {
  const n = estado.cola.length;
  el.cola.hidden = n === 0;
  el.colaTotal.textContent = String(n);
  el.btnAgregar.disabled = estado.firmando;
  el.btnVaciar.disabled = estado.firmando || n === 0;
  el.listaCola.replaceChildren(...estado.cola.map(filaCola));
  const actual = el.listaCola.querySelector('[aria-current="true"]');
  if (actual) actual.scrollIntoView({ block: "nearest" });
  actualizarAjustesVisibles();
  actualizarBotonFirmar();
  actualizarTextoVacio();
}

async function quitarItem(id) {
  if (estado.firmando) return;
  const r = await llamar("quitar_item", id);
  if (!r.ok) aviso(r.mensaje, true);
  aplicarCola(r.cola); // si era el documento a la vista, el visor queda vacío
}

function vaciarCola() {
  if (estado.firmando || !estado.cola.length) return;
  mostrarDialogo({
    tipo: "pregunta",
    titulo: "¿Vaciar la lista de documentos?",
    texto: `Se quitarán ${plural(estado.cola.length, "documento", "documentos")} de la lista. Los archivos no se borran.`,
    acciones: [
      {
        texto: "Vaciar cola",
        icono: "papelera",
        primario: true,
        accion: async () => {
          cerrarDialogo();
          const r = await llamar("vaciar_cola", false);
          if (!r.ok) aviso(r.mensaje, true);
          aplicarCola(r.cola);
        },
      },
      { texto: "Cancelar", accion: cerrarDialogo },
    ],
  });
}

// Cierra el documento a la vista: lo quita de la cola y deja el visor vacío
// (las opciones de firma se mantienen).
async function cerrarDocumento() {
  if (estado.firmando || !estado.info) return;
  const r = await llamar("cerrar_documento");
  if (!r.ok) {
    aviso(r.mensaje, true);
    return;
  }
  vaciarVisor();
  aplicarCola(r.cola);
  (estado.cola.length ? el.listaCola.querySelector("button") : el.btnAbrirVacio)?.focus();
}

function vaciarVisor() {
  estado.carga++;
  estado.ticket++;
  if (estado.tareaRender) estado.tareaRender.cancel();
  estado.tareaRender = null;
  if (observador) observador.disconnect();
  observador = null;
  liberarDocumento(estado.doc);
  Object.assign(estado, { doc: null, info: null, actualId: null, total: 0, pagina: 1, viewport: null, vistaPdf: null });
  estado.miniaturas.clear();
  el.miniaturas.replaceChildren();
  el.app.dataset.estado = "vacio";
  el.vacio.hidden = false;
  el.hoja.hidden = true;
  el.docInfo.hidden = true;
  el.chipFirmas.hidden = true;
  el.notaFirmas.hidden = true;
  el.btnValidarAbierto.disabled = true;
  el.totalPaginas.textContent = "0";
  const radioTodas = document.querySelector('input[name="paginas"][value="todas"]');
  radioTodas.disabled = false;
  radioTodas.checked = leerPreferencia("paginas", "todas") === "todas";
  if (!radioTodas.checked) document.querySelector('input[name="paginas"][value="ultima"]').checked = true;
  document.querySelector('input[name="pagina-firma"][value="ultima"]').checked = true;
  actualizarTextoVacio();
  actualizarAjustesVisibles();
  actualizarBotonFirmar();
}

function actualizarTextoVacio() {
  el.btnAbrirVacio.querySelector("span").textContent = estado.cola.length ? "Agregar PDFs" : "Abrir PDF";
  if (estado.cola.length) {
    el.vacioTitulo.textContent = "Elija un documento de la lista";
    el.vacioTexto.textContent = "Haga clic en un documento de la izquierda para verlo, o agregue más PDF.";
  } else {
    el.vacioTitulo.textContent = "Firme sus PDF con su DNIe";
    el.vacioTexto.textContent = estado.sinArrastrar
      ? "Abra uno o varios documentos PDF desde su equipo."
      : "Arrastre uno o varios documentos PDF a esta ventana o ábralos desde su equipo.";
  }
}

async function descargar(info, alProgreso = null) {
  const partes = [];
  let inicio = 0;
  for (;;) {
    const r = await llamar("leer_fragmento", info.token, inicio);
    if (!r.ok) throw new Error(r.mensaje);
    const bytes = base64ABytes(r.datos);
    partes.push(bytes);
    inicio += bytes.length;
    if (alProgreso && info.tamano) alProgreso(inicio / info.tamano);
    if (r.fin || bytes.length === 0) break;
  }
  const todo = new Uint8Array(inicio);
  let pos = 0;
  for (const p of partes) {
    todo.set(p, pos);
    pos += p.length;
  }
  return todo;
}

// Libera el documento de pdf.js (y su worker). En pdf.js 6 lo hace la tarea de carga.
function liberarDocumento(doc) {
  if (!doc) return;
  try {
    const tarea = typeof doc.destroy === "function" ? doc.destroy() : doc.loadingTask && doc.loadingTask.destroy();
    if (tarea && typeof tarea.catch === "function") tarea.catch(() => {});
  } catch {
    /* ya liberado */
  }
}

async function cargarDocumento(info) {
  const carga = ++estado.carga;
  el.hoja.classList.add("cargando");
  mostrarCarga(`Cargando «${info.nombre}»… 0 %`, 0);
  let doc;
  try {
    const datos = await descargar(info, (f) => {
      if (carga === estado.carga) mostrarCarga(`Cargando «${info.nombre}»… ${porcentaje(f)}`, f);
    });
    if (carga !== estado.carga) return; // se eligió otro documento mientras tanto
    mostrarCarga(`Preparando la vista de ${plural(info.paginas || 0, "página", "páginas")}…`);
    doc = await pdfjsLib.getDocument({
      data: datos,
      cMapUrl: `${RUTA_PDFJS}cmaps/`,
      cMapPacked: true,
      standardFontDataUrl: `${RUTA_PDFJS}standard_fonts/`,
      wasmUrl: `${RUTA_PDFJS}wasm/`,
      iccUrl: `${RUTA_PDFJS}iccs/`,
      isEvalSupported: false,
    }).promise;
  } catch (e) {
    if (carga !== estado.carga) return;
    el.hoja.classList.remove("cargando");
    ocultarCarga();
    aviso(`No se pudo mostrar el PDF: ${e.message || e}`, true);
    return;
  }
  if (carga !== estado.carga) {
    liberarDocumento(doc);
    return;
  }
  liberarDocumento(estado.doc);
  estado.doc = doc;
  estado.info = info;
  estado.actualId = info.id || null;
  estado.total = doc.numPages;
  estado.pagina = 1;
  estado.miniaturas.clear();
  aplicarAjustesDelDocumento();

  el.app.dataset.estado = "documento";
  el.vacio.hidden = true;
  el.hoja.hidden = false;
  el.docInfo.hidden = false;
  el.docNombre.textContent = info.nombre;
  el.docNombre.title = info.ruta || info.nombre;
  el.docMeta.textContent = `${plural(estado.total, "página", "páginas")} · ${tamanoLegible(info.tamano)}`;
  el.totalPaginas.textContent = String(estado.total);
  el.inpPagina.max = String(estado.total);
  el.btnValidarAbierto.disabled = false;

  configurarSegunFirmas();
  actualizarDestino();
  construirMiniaturas();
  renderizarCola();
  await renderizar();
  el.hoja.classList.remove("cargando");
  if (carga === estado.carga) ocultarCarga(); // si hay otra carga en curso, ella lo cierra
  actualizarBotonFirmar();
  if (vistaActual() === "validar") validar();
  refrescarCola(); // la apertura pudo agregar el documento a la cola
}

// ---------------------------------------------------------------- ajustes por documento

function ajusteActual() {
  return estado.actualId ? estado.ajustes[estado.actualId] || null : null;
}

function ajusteEditable() {
  if (!estado.actualId) return null;
  if (!estado.ajustes[estado.actualId]) estado.ajustes[estado.actualId] = {};
  return estado.ajustes[estado.actualId];
}

function tieneCajasPaginas(a) {
  return Boolean(a && a.cajasPaginas && Object.keys(a.cajasPaginas).length);
}

function limpiarAjuste() {
  const a = ajusteActual();
  if (a && a.caja == null && a.pagina == null && !tieneCajasPaginas(a)) delete estado.ajustes[estado.actualId];
}

// Posición del documento a la vista: la propia del documento o la común a todos.
function cajaDocumento() {
  const a = ajusteActual();
  return (a && a.caja) || estado.caja;
}

// Posición del sello en la página n (desde 1): la propia de esa página, si el
// sello se movió en ella, o la del documento.
function cajaDePagina(n) {
  const a = ajusteActual();
  return (a && a.cajasPaginas && a.cajasPaginas[n - 1]) || cajaDocumento();
}

// La posición que se ve y se firma en la página a la vista.
function cajaVisible() {
  return cajaDePagina(estado.pagina);
}

// Al abrir un documento se refleja su «Página actual» (y se va a esa página);
// su posición propia, si la tiene, la muestra cajaVisible().
function aplicarAjustesDelDocumento() {
  const a = ajusteActual();
  const conPagina = Boolean(a && a.pagina != null);
  if (conPagina) {
    a.pagina = limitar(a.pagina, 0, estado.total - 1);
    estado.pagina = a.pagina + 1;
  }
  document.querySelector(`input[name="pagina-firma"][value="${conPagina ? "actual" : "ultima"}"]`).checked = true;
  actualizarAjustesVisibles();
}

function actualizarAjustesVisibles() {
  const varios = estado.cola.length >= 2 && Boolean(estado.info);
  el.txtCampoAyuda.textContent = varios
    ? "Arrastre el sello para ubicarlo; su tamaño es fijo. Lo que mueva se aplica solo a esta página de este documento; " +
      "«Aplicar a todas las páginas» la usa en todo el documento y «Aplicar a todos los documentos», en todos los de la lista. " +
      "El campo de firma va en la última página de cada documento; «Página actual» solo se aplica a este."
    : "Arrastre el sello sobre la página para ubicarlo; su tamaño es fijo. Lo que mueva se aplica solo a esta página; " +
      "«Aplicar a todas las páginas» la usa en todo el documento y «Aplicar a todos los documentos» además la deja como predeterminada.";
}

// La posición de la página a la vista pasa a ser la del documento y las demás
// páginas dejan su posición propia. Los otros documentos no cambian.
function aplicarATodasLasPaginas() {
  if (estado.firmando || !estado.info || !estado.actualId) return;
  const caja = { ...cajaVisible() };
  const a = ajusteEditable();
  a.caja = caja;
  delete a.cajasPaginas;
  posicionarSello();
  actualizarMarcas();
  actualizarMarcasPropias();
  renderizarCola();
  aviso("La posición del sello se aplicó a todas las páginas de este documento.");
}

// La posición de la página a la vista pasa a ser la común (y la preferencia
// guardada) y todos los documentos dejan sus posiciones propias (del documento
// y de cada página); «Página actual» de cada uno se mantiene.
function aplicarPosicionATodos() {
  if (estado.firmando || !estado.info || !estado.actualId) return;
  estado.caja = { ...cajaVisible() };
  guardarPreferencia("caja", estado.caja);
  for (const [id, a] of Object.entries(estado.ajustes)) {
    delete a.caja;
    delete a.cajasPaginas;
    if (a.pagina == null) delete estado.ajustes[id];
  }
  posicionarSello();
  actualizarMarcas();
  actualizarMarcasPropias();
  renderizarCola();
  aviso("La posición del sello se aplicó a todos los documentos.");
}

// Posiciones propias por página para Python: { "índice": { x, y } }, índice desde 0.
function posicionesPaginasParaPython(a) {
  return tieneCajasPaginas(a) ? { ...a.cajasPaginas } : {};
}

function ajustesParaPython() {
  const salida = {};
  for (const [id, a] of Object.entries(estado.ajustes)) {
    const ajuste = {};
    if (a.caja) ajuste.caja = a.caja;
    if (a.pagina != null) ajuste.pagina_firma = a.pagina;
    if (tieneCajasPaginas(a)) ajuste.posiciones_paginas = posicionesPaginasParaPython(a);
    if (Object.keys(ajuste).length) salida[id] = ajuste;
  }
  return salida;
}

function configurarSegunFirmas() {
  const n = estado.info.firmas.length;
  const certificadoSinCambios = estado.info.docmdp === 1;
  const radioTodas = document.querySelector('input[name="paginas"][value="todas"]');
  const radioUltima = document.querySelector('input[name="paginas"][value="ultima"]');

  el.chipFirmas.hidden = n === 0;
  el.chipFirmas.replaceChildren(icono("escudo"), plural(n, "firma", "firmas"));

  el.notaFirmas.hidden = n === 0 && !certificadoSinCambios;
  el.notaFirmas.classList.toggle("nota-peligro", certificadoSinCambios);
  el.notaFirmas.classList.toggle("nota-aviso", !certificadoSinCambios);
  if (certificadoSinCambios) {
    el.notaFirmasTexto.textContent =
      "Este documento está certificado y no admite cambios. No se puede firmar sin invalidar su certificación.";
  } else if (n > 0) {
    el.notaFirmasTexto.textContent =
      `Este PDF ya tiene ${plural(n, "firma", "firmas")}. Para que ${n === 1 ? "siga siendo válida" : "sigan siendo válidas"}, ` +
      "el sello se colocará solo en el campo de la nueva firma.";
  }
  radioTodas.disabled = n > 0;
  if (n > 0) radioUltima.checked = true;
  else radioTodas.checked = leerPreferencia("paginas", "todas") === "todas";
  if (!radioTodas.checked) radioUltima.checked = true;
}

// ---------------------------------------------------------------- render

let temporizadorRender = null;
function programarRender() {
  clearTimeout(temporizadorRender);
  temporizadorRender = setTimeout(renderizar, 60);
}

async function renderizar() {
  if (!estado.doc || vistaActual() !== "firmar") return;
  const ticket = ++estado.ticket;
  const pagina = await estado.doc.getPage(estado.pagina);
  if (ticket !== estado.ticket) return;

  const base = pagina.getViewport({ scale: 1 });
  if (estado.zoom.ajuste) {
    const porAncho = (el.scroll.clientWidth - MARGEN_VISOR) / base.width;
    const porAlto = (el.scroll.clientHeight - MARGEN_VISOR) / base.height;
    const escala = estado.zoom.ajuste === "pagina" ? Math.min(porAncho, porAlto) : porAncho;
    estado.zoom.escala = limitar(escala, 0.25, 4);
  }
  const vp = pagina.getViewport({ scale: estado.zoom.escala });
  estado.viewport = vp;
  estado.vistaPdf = pagina.view;

  el.hoja.style.width = `${Math.floor(vp.width)}px`;
  el.hoja.style.height = `${Math.floor(vp.height)}px`;
  posicionarSello();
  actualizarControles();

  const dpr = window.devicePixelRatio || 1;
  const lienzo = document.createElement("canvas");
  lienzo.id = "lienzo";
  lienzo.width = Math.floor(vp.width * dpr);
  lienzo.height = Math.floor(vp.height * dpr);
  lienzo.style.width = `${Math.floor(vp.width)}px`;
  lienzo.style.height = `${Math.floor(vp.height)}px`;
  lienzo.setAttribute("aria-label", `Página ${estado.pagina} de ${estado.total}`);
  lienzo.setAttribute("role", "img");

  if (estado.tareaRender) estado.tareaRender.cancel();
  const tarea = pagina.render({
    canvas: lienzo,
    viewport: vp,
    transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
  });
  estado.tareaRender = tarea;
  try {
    await tarea.promise;
  } catch (e) {
    if (e && e.name === "RenderingCancelledException") return;
    aviso(`No se pudo dibujar la página: ${e.message || e}`, true);
    return;
  }
  if (ticket !== estado.ticket) return;
  el.lienzo.replaceWith(lienzo);
  el.lienzo = lienzo;
}

function actualizarControles() {
  el.inpPagina.value = String(estado.pagina);
  el.btnAnt.disabled = estado.pagina <= 1;
  el.btnSig.disabled = estado.pagina >= estado.total;
  el.zoomValor.textContent = `${Math.round(estado.zoom.escala * 100)} %`;
  el.btnAjustar.setAttribute("aria-pressed", String(estado.zoom.ajuste === "ancho"));
  el.btnPagina.setAttribute("aria-pressed", String(estado.zoom.ajuste === "pagina"));
  el.lblPaginaActual.textContent = `Página actual (${estado.pagina})`;
  for (const boton of el.miniaturas.querySelectorAll(".miniatura")) {
    const actual = Number(boton.dataset.pagina) === estado.pagina;
    if (actual) boton.setAttribute("aria-current", "page");
    else boton.removeAttribute("aria-current");
  }
  actualizarEtiquetaSello();
  actualizarMarcas();
}

function irAPagina(n) {
  if (!estado.doc) return;
  const nueva = limitar(Math.round(n) || 1, 1, estado.total);
  if (nueva === estado.pagina) {
    actualizarControles();
    return;
  }
  estado.pagina = nueva;
  const ajuste = ajusteActual();
  if (ajuste && ajuste.pagina != null) {
    ajuste.pagina = nueva - 1; // «Página actual» sigue a la página a la vista
    renderizarCola();
  }
  renderizar();
  const actual = el.miniaturas.querySelector(`[data-pagina="${nueva}"]`);
  if (actual) actual.scrollIntoView({ block: "nearest" });
}

function cambiarZoom(direccion) {
  if (!estado.doc) return;
  const actual = estado.zoom.escala;
  let siguiente;
  if (direccion > 0) siguiente = PASOS_ZOOM.find((p) => p > actual + 0.001) ?? PASOS_ZOOM.at(-1);
  else siguiente = [...PASOS_ZOOM].reverse().find((p) => p < actual - 0.001) ?? PASOS_ZOOM[0];
  estado.zoom = { ajuste: null, escala: siguiente };
  renderizar();
}

function ajustarAncho() {
  if (!estado.doc) return;
  estado.zoom.ajuste = "ancho";
  renderizar();
}

function ajustarPagina() {
  if (!estado.doc) return;
  estado.zoom.ajuste = "pagina";
  renderizar();
}

// ---------------------------------------------------------------- miniaturas

let observador = null;
let colaMiniaturas = Promise.resolve();

function construirMiniaturas() {
  if (observador) observador.disconnect();
  el.miniaturas.replaceChildren();
  const doc = estado.doc;
  observador = new IntersectionObserver(
    (entradas) => {
      for (const entrada of entradas) {
        if (!entrada.isIntersecting) continue;
        observador.unobserve(entrada.target);
        const n = Number(entrada.target.dataset.pagina);
        colaMiniaturas = colaMiniaturas.then(() => dibujarMiniatura(doc, n, entrada.target)).catch(() => {});
      }
    },
    { root: el.miniaturas, rootMargin: "200px" },
  );
  for (let n = 1; n <= estado.total; n++) {
    const hoja = h("span", { class: "miniatura-hoja", style: "aspect-ratio: 0.707" });
    const boton = h(
      "button",
      { class: "miniatura", type: "button", "data-pagina": n, "aria-label": `Ir a la página ${n}`, onclick: () => irAPagina(n) },
      hoja,
      h("span", { class: "miniatura-num" }, String(n)),
    );
    actualizarMarcaPropia(boton);
    el.miniaturas.append(h("li", {}, boton));
    observador.observe(boton);
  }
}

// Las páginas cuyo sello tiene posición propia llevan un punto junto al número.
function actualizarMarcaPropia(boton) {
  const a = ajusteActual();
  const n = Number(boton.dataset.pagina);
  const propia = Boolean(a && a.cajasPaginas && a.cajasPaginas[n - 1]);
  boton.classList.toggle("posicion-propia", propia);
  boton.setAttribute("aria-label", propia ? `Ir a la página ${n} (sello con posición propia)` : `Ir a la página ${n}`);
  if (propia) boton.title = "El sello de esta página tiene su propia posición";
  else boton.removeAttribute("title");
}

function actualizarMarcasPropias() {
  for (const boton of el.miniaturas.querySelectorAll(".miniatura")) actualizarMarcaPropia(boton);
}

async function dibujarMiniatura(doc, n, boton) {
  if (doc !== estado.doc) return;
  const pagina = await doc.getPage(n);
  const base = pagina.getViewport({ scale: 1 });
  const ancho = 120;
  const vp = pagina.getViewport({ scale: ancho / base.width });
  const dpr = window.devicePixelRatio || 1;
  const lienzo = document.createElement("canvas");
  lienzo.width = Math.floor(vp.width * dpr);
  lienzo.height = Math.floor(vp.height * dpr);
  await pagina.render({ canvas: lienzo, viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined }).promise;
  if (doc !== estado.doc) return;
  const hoja = boton.querySelector(".miniatura-hoja");
  hoja.style.aspectRatio = `${vp.width} / ${vp.height}`;
  const marca = h("span", { class: "miniatura-marca", hidden: true });
  hoja.replaceChildren(lienzo, marca);
  estado.miniaturas.set(n, { vp, view: pagina.view, marca });
  actualizarMarca(n);
}

function paginaDelCampo() {
  const a = ajusteActual();
  return a && a.pagina != null ? a.pagina + 1 : estado.total;
}

function paginasConSello() {
  const campo = paginaDelCampo();
  const todas = document.querySelector('input[name="paginas"]:checked').value === "todas";
  const conFirmas = estado.info && estado.info.firmas.length > 0;
  return { campo, todas: todas && !conFirmas };
}

function actualizarMarca(n) {
  const datos = estado.miniaturas.get(n);
  if (!datos) return;
  const { campo, todas } = paginasConSello();
  const visible = todas || n === campo;
  datos.marca.hidden = !visible;
  datos.marca.classList.toggle("es-campo", n === campo);
  if (!visible) return;
  const r = cajaAPixeles(datos.vp, datos.view, cajaDePagina(n));
  Object.assign(datos.marca.style, {
    left: `${(r.left / datos.vp.width) * 100}%`,
    top: `${(r.top / datos.vp.height) * 100}%`,
    width: `${(r.width / datos.vp.width) * 100}%`,
    height: `${(r.height / datos.vp.height) * 100}%`,
  });
}

function actualizarMarcas() {
  for (const n of estado.miniaturas.keys()) actualizarMarca(n);
}

// ---------------------------------------------------------------- sello

// Igual que firma_pdf.rect_sello: posición relativa y tamaño fijo del tipo en pt;
// si no cabe, se desplaza hasta quedar dentro de la página.
function rectPdf(view, caja = cajaVisible(), tipo = estado.tipo) {
  const [x0, y0, x1, y1] = view;
  const { ancho, alto } = TIPOS[tipo].tamano;
  const x = Math.max(x0, Math.min(x0 + caja.x * (x1 - x0), x1 - ancho));
  const y = Math.max(y0, Math.min(y0 + caja.y * (y1 - y0), y1 - alto));
  return [x, y, x + ancho, y + alto];
}

function cajaAPixeles(vp, view, caja = cajaVisible()) {
  const [x1, y1, x2, y2] = rectPdf(view, caja);
  const a = vp.convertToViewportPoint(x1, y1);
  const b = vp.convertToViewportPoint(x2, y2);
  return {
    left: Math.min(a[0], b[0]),
    top: Math.min(a[1], b[1]),
    width: Math.abs(b[0] - a[0]),
    height: Math.abs(b[1] - a[1]),
  };
}

// Posición { x, y } (esquina inferior izquierda) del rectángulo en píxeles; el tamaño no se guarda.
function pixelesACaja(r, vp = estado.viewport, view = estado.vistaPdf) {
  const [x0, y0, x1, y1] = view;
  const a = vp.convertToPdfPoint(r.left, r.top);
  const b = vp.convertToPdfPoint(r.left + r.width, r.top + r.height);
  return {
    x: limitar((Math.min(a[0], b[0]) - x0) / (x1 - x0), 0, 1),
    y: limitar((Math.min(a[1], b[1]) - y0) / (y1 - y0), 0, 1),
  };
}

// Posición guardada (preferencia «caja» o posición propia) sin el tamaño de
// versiones anteriores: de una caja antigua { x, y, ancho, alto } se conserva
// su esquina inferior derecha (referida a A4) y el sello toma el tamaño fijo.
function posicionGuardada(c, tipo = estado.tipo) {
  const numero = (v) => typeof v === "number" && Number.isFinite(v);
  if (!c || !numero(c.x) || !numero(c.y)) return { ...TIPOS[tipo].posicion };
  const x = numero(c.ancho) ? c.x + c.ancho - TIPOS[tipo].tamano.ancho / ANCHO_A4 : c.x;
  return { x: limitar(x, 0, 1), y: limitar(c.y, 0, 1) };
}

function rectSello() {
  return {
    left: parseFloat(el.sello.style.left) || 0,
    top: parseFloat(el.sello.style.top) || 0,
    width: parseFloat(el.sello.style.width) || 0,
    height: parseFloat(el.sello.style.height) || 0,
  };
}

function aplicarRect(r) {
  el.sello.style.left = `${r.left}px`;
  el.sello.style.top = `${r.top}px`;
  el.sello.style.width = `${r.width}px`;
  el.sello.style.height = `${r.height}px`;
  actualizarMedidasSello();
  ajustarTextoSello();
}

// Ancho x alto del sello en puntos PDF: el tamaño fijo del tipo.
function actualizarMedidasSello() {
  const { ancho, alto } = TIPOS[estado.tipo].tamano;
  el.selloMedidas.textContent = `${ancho} × ${alto} pt`;
}

function posicionarSello() {
  if (!estado.viewport) return;
  aplicarRect(cajaAPixeles(estado.viewport, estado.vistaPdf));
}

// Mover el sello cambia solo la posición de la página a la vista en este
// documento; «Aplicar a todas las páginas» la extiende al documento y
// «Aplicar a todos los documentos», a todos.
function confirmarCaja() {
  const a = ajusteEditable();
  if (!a) return;
  const primeraVez = !a.caja && !tieneCajasPaginas(a);
  if (!a.cajasPaginas) a.cajasPaginas = {};
  a.cajasPaginas[estado.pagina - 1] = pixelesACaja(rectSello());
  actualizarMarca(estado.pagina);
  const boton = el.miniaturas.querySelector(`[data-pagina="${estado.pagina}"]`);
  if (boton) actualizarMarcaPropia(boton);
  if (primeraVez) renderizarCola(); // marca «posición propia» en la lista
}

function mover(r, dx, dy) {
  const W = estado.viewport.width;
  const H = estado.viewport.height;
  return { ...r, left: limitar(r.left + dx, 0, W - r.width), top: limitar(r.top + dy, 0, H - r.height) };
}

function iniciarArrastreSello() {
  let arrastre = null;
  el.sello.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !estado.viewport) return;
    arrastre = { x: e.clientX, y: e.clientY, r: rectSello() };
    el.sello.setPointerCapture(e.pointerId);
    el.sello.classList.add("activo");
    el.sello.focus({ preventScroll: true });
    e.preventDefault();
  });
  el.sello.addEventListener("pointermove", (e) => {
    if (!arrastre) return;
    const dx = e.clientX - arrastre.x;
    const dy = e.clientY - arrastre.y;
    aplicarRect(mover(arrastre.r, dx, dy));
  });
  const terminar = () => {
    if (!arrastre) return;
    arrastre = null;
    el.sello.classList.remove("activo");
    confirmarCaja();
  };
  el.sello.addEventListener("pointerup", terminar);
  el.sello.addEventListener("pointercancel", terminar);
  el.sello.addEventListener("keydown", (e) => {
    const direcciones = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const d = direcciones[e.key];
    if (!d || !estado.viewport) return;
    e.preventDefault();
    e.stopPropagation();
    const paso = e.shiftKey ? 10 : 2;
    aplicarRect(mover(rectSello(), d[0] * paso, d[1] * paso));
    confirmarCaja();
  });
}

function actualizarEtiquetaSello() {
  if (!estado.info) return;
  const { campo, todas } = paginasConSello();
  const esCampo = estado.pagina === campo;
  el.selloEtiqueta.textContent = esCampo ? "Campo de firma" : todas ? "Sello" : "Sin sello en esta página";
  el.sello.style.opacity = esCampo || todas ? "1" : "0.45";
  el.sello.style.borderStyle = esCampo || todas ? "solid" : "dashed";
}

// ---------------------------------------------------------------- texto del sello

const dos = (n) => String(n).padStart(2, "0");

function desfaseHorario(d, conDosPuntos) {
  const minutos = -d.getTimezoneOffset();
  const signo = minutos >= 0 ? "+" : "-";
  const hh = dos(Math.floor(Math.abs(minutos) / 60));
  const mm = dos(Math.abs(minutos) % 60);
  return conDosPuntos ? `${signo}${hh}:${mm}` : `${signo}${hh}${mm}`;
}

// Mismos formatos que firma_pdf.FORMATOS_FECHA.
function fechaSello(tipo, d = new Date()) {
  const hora = `${dos(d.getHours())}:${dos(d.getMinutes())}:${dos(d.getSeconds())}`;
  if (tipo === "visto_bueno") {
    return `${dos(d.getDate())}.${dos(d.getMonth() + 1)}.${d.getFullYear()} ${hora} ${desfaseHorario(d, true)}`;
  }
  return `${d.getFullYear()}/${dos(d.getMonth() + 1)}/${dos(d.getDate())} ${hora}${desfaseHorario(d, false)}`;
}

function motivoActual() {
  return el.selMotivo.value || estado.motivos[estado.tipo][0];
}

function textoSello() {
  const valores = {
    firmante: estado.dnie ? estado.dnie.titular : "NOMBRE DEL TITULAR",
    motivo: motivoActual(),
    fecha: fechaSello(estado.tipo),
  };
  return TIPOS[estado.tipo].plantilla.replace(/\{(\w+)\}/g, (_, clave) => valores[clave]);
}

const px = (v) => `${v}px`;

// Reproduce la composición de firma_pdf.estilo_sello en píxeles (1 pt = viewport.scale px).
function ajustarTextoSello() {
  const r = rectSello();
  // En páginas con /Rotate el sello se dibuja en el espacio sin rotar, así que
  // su contenido aparece girado junto con la página: la vista previa lo imita.
  const rot = (((estado.viewport ? estado.viewport.rotation : 0) % 360) + 360) % 360;
  const girado = rot === 90 || rot === 270;
  const W = girado ? r.height : r.width;
  const H = girado ? r.width : r.height;
  Object.assign(el.selloContenido.style, {
    width: px(W),
    height: px(H),
    transform: `translate(-50%, -50%) rotate(${rot}deg)`,
  });
  const s = estado.viewport ? estado.viewport.scale : 1;
  const margen = Math.max(2 * s, Math.min(W, H) * 0.06);
  let area;
  if (estado.tipo === "visto_bueno") {
    // Igual que firma_pdf.geometria_sello: imagen en el 55 % superior, alineada
    // a la izquierda y apoyada sobre el texto; el texto, debajo y sin superponerse.
    const separacion = Math.max(1 * s, H * 0.03);
    const limite = H * PROPORCION_IMAGEN_VB;
    const zona = { x0: margen, y0: margen, x1: W - margen, y1: limite - separacion / 2 };
    const icono = el.selloIcono;
    const conImagen = Boolean(estado.imagenes.imagen_vb) && icono.naturalWidth > 0;
    icono.hidden = !conImagen;
    el.selloMarca.toggleAttribute("hidden", conImagen);
    const [ancho0, alto0] = conImagen ? [icono.naturalWidth, icono.naturalHeight] : [100, 100];
    const escala = Math.max(0, Math.min((zona.x1 - zona.x0) / ancho0, (zona.y1 - zona.y0) / alto0));
    const ancho = ancho0 * escala;
    const alto = alto0 * escala;
    Object.assign((conImagen ? icono : el.selloMarca).style, {
      width: px(ancho),
      height: px(alto),
      left: px(zona.x0),
      top: px(zona.y1 - alto),
    });
    area = { izquierda: margen, derecha: margen, arriba: limite + separacion / 2, abajo: margen };
  } else {
    let izquierda = margen;
    const img = el.selloLogo;
    const conLogo = Boolean(estado.imagenes.logo) && img.naturalWidth > 0;
    img.hidden = !conLogo;
    if (conLogo) {
      const anchoLogo = W * PROPORCION_LOGO;
      const escala = Math.max(0, Math.min((anchoLogo - margen) / img.naturalWidth, (H - 2 * margen) / img.naturalHeight));
      const ancho = img.naturalWidth * escala;
      const alto = img.naturalHeight * escala;
      Object.assign(img.style, { width: px(ancho), height: px(alto), left: px(margen), top: px((H - alto) / 2) });
      izquierda = margen + ancho + margen;
    }
    area = { izquierda, derecha: margen, arriba: margen, abajo: margen };
  }
  const anchoArea = Math.max(1, W - area.izquierda - area.derecha);
  const altoArea = Math.max(1, H - area.arriba - area.abajo);
  Object.assign(el.selloTexto.style, {
    left: px(area.izquierda),
    top: px(area.arriba),
    width: px(anchoArea),
    height: px(altoArea),
  });
  // El mayor tamaño de letra (hasta el máximo del tipo) con el que el texto cabe.
  const lineas = el.selloLineas;
  const cabe = (tam) => {
    lineas.style.fontSize = px(tam);
    return lineas.scrollHeight <= altoArea + 0.5;
  };
  const maximo = TIPOS[estado.tipo].tamMaximo * s;
  if (!cabe(maximo)) {
    let menor = TAM_MINIMO * s;
    let mayor = maximo;
    while (mayor - menor > 0.2 * s) {
      const medio = (menor + mayor) / 2;
      if (cabe(medio)) menor = medio;
      else mayor = medio;
    }
    cabe(menor);
  }
}

function actualizarVistaPrevia() {
  el.selloLineas.textContent = textoSello();
  ajustarTextoSello();
}

// ---------------------------------------------------------------- tipo de sello y logo

// Al cambiar de tipo el sello cambia de tamaño: se mantiene su esquina inferior
// derecha (referida a la página a la vista, o a A4 si no hay documento).
function posicionAjustadaAlTipo(c, anterior, tipo) {
  const view = estado.vistaPdf || [0, 0, ANCHO_A4, 842];
  const diferencia = (TIPOS[anterior].tamano.ancho - TIPOS[tipo].tamano.ancho) / (view[2] - view[0]);
  return { x: limitar(c.x + diferencia, 0, 1), y: c.y };
}

function cajaDelTipo(anterior, tipo) {
  estado.caja = posicionAjustadaAlTipo(estado.caja, anterior, tipo);
  for (const a of Object.values(estado.ajustes)) {
    if (a.caja) a.caja = posicionAjustadaAlTipo(a.caja, anterior, tipo);
    for (const [i, c] of Object.entries(a.cajasPaginas || {})) a.cajasPaginas[i] = posicionAjustadaAlTipo(c, anterior, tipo);
  }
  guardarPreferencia("caja", estado.caja);
}

function aplicarTipo(tipo, { cambiarCaja = false, guardar = false } = {}) {
  if (!TIPOS[tipo]) tipo = "firma";
  const anterior = estado.tipo;
  estado.tipo = tipo;
  for (const radio of document.querySelectorAll('input[name="tipo"]')) radio.checked = radio.value === tipo;
  el.sello.dataset.tipo = tipo;
  el.campoLogo.hidden = tipo !== "firma";
  el.campoImagenVb.hidden = tipo !== "visto_bueno";
  el.txtTipoAyuda.textContent = TIPOS[tipo].ayuda;
  // Se ofrecen los motivos del tipo y se elige el predeterminado (el primero).
  el.selMotivo.replaceChildren(...estado.motivos[tipo].map((motivo) => h("option", { value: motivo }, motivo)));
  el.selMotivo.value = estado.motivos[tipo][0];
  if (cambiarCaja && anterior !== tipo) cajaDelTipo(anterior, tipo);
  posicionarSello(); // el sello toma el tamaño fijo del tipo
  actualizarMarcas();
  if (estado.info) actualizarDestino(); // el nombre sugerido cambia con el tipo (_firmado / _vb)
  if (guardar) {
    llamar("guardar_tipo", tipo).then((r) => {
      if (!r.ok) aviso(r.mensaje, true);
    });
  }
  actualizarVistaPrevia();
}

// Imágenes del sello: logo («Firma»; `fija`: es siempre el logo inicial del
// programa, solo se muestra) e imagen de arriba («Visto bueno»; se elige y
// `quitar` es su botón «Quitar imagen»).
const SELECTORES = {
  logo: { prefijo: "logo", sello: "sello-logo", fija: true },
  imagen_vb: {
    prefijo: "imagen-vb", sello: "sello-icono", api: "imagen_vb", nombre: "imagen", quitar: "btn-quitar-imagen-vb",
    elegir: "Elegir imagen…", cambiar: "Cambiar imagen…",
    guardado: "Imagen guardada. Se usará en sus próximos vistos buenos.",
  },
};

function mostrarImagen(clave, imagen) {
  const c = SELECTORES[clave];
  estado.imagenes[clave] = imagen || null;
  const hay = Boolean(estado.imagenes[clave]);
  const vista = $(`${c.prefijo}-img`);
  const sello = $(c.sello);
  vista.hidden = !hay;
  $(`${c.prefijo}-vacio`).toggleAttribute("hidden", hay);
  $(`${c.prefijo}-vista`).classList.toggle("con-logo", hay);
  if (c.quitar) $(c.quitar).hidden = !hay;
  if (!c.fija) $(`btn-${c.prefijo}-texto`).textContent = hay ? c.cambiar : c.elegir;
  if (hay) {
    vista.src = imagen.datos;
    sello.src = imagen.datos;
  } else {
    vista.removeAttribute("src");
    sello.removeAttribute("src");
  }
  actualizarVistaPrevia();
}

async function cargarConfig() {
  const r = await llamar("obtener_config");
  if (!r.ok) return;
  for (const tipo of Object.keys(MOTIVOS_INICIALES)) {
    const lista = r.motivos && r.motivos[tipo];
    if (Array.isArray(lista) && lista.length) estado.motivos[tipo] = lista;
  }
  // Una caja guardada por versiones anteriores se convierte con el tamaño del tipo guardado.
  const guardada = leerPreferencia("caja", null);
  if (guardada !== null) estado.caja = posicionGuardada(guardada, TIPOS[r.tipo] ? r.tipo : "firma");
  aplicarTipo(r.tipo, { cambiarCaja: guardada === null && r.tipo !== estado.tipo });
  mostrarImagen("logo", r.logo);
  mostrarImagen("imagen_vb", r.imagen_vb);
}

async function elegirImagen(clave) {
  const c = SELECTORES[clave];
  const r = await llamar(`elegir_${c.api}`);
  if (!r.ok) {
    if (!r.cancelado) aviso(r.mensaje, true);
    return;
  }
  mostrarImagen(clave, r[clave]);
  aviso(c.guardado);
}

async function quitarImagen(clave) {
  const r = await llamar(`quitar_${SELECTORES[clave].api}`);
  if (!r.ok) {
    aviso(r.mensaje, true);
    return;
  }
  mostrarImagen(clave, null);
}

function actualizarDestino() {
  const plan = planFirma();
  const lote = Boolean(plan && plan.modo === "lote");
  el.lblGuardarComo.textContent = lote ? "Guardar todos en una carpeta" : "Elegir dónde guardar";
  if (lote) {
    const sinCarpeta = pendientes().some((i) => !i.ruta);
    if (el.chkGuardarComo.checked) {
      el.txtDestino.textContent = "Al empezar se le pedirá una carpeta, una sola vez; todos los documentos firmados se guardarán allí.";
    } else if (sinCarpeta) {
      el.txtDestino.textContent =
        "Cada documento se guardará junto a su original como «…_firmado.pdf». Para los arrastrados sin carpeta de origen se le pedirá una carpeta, una sola vez.";
    } else {
      el.txtDestino.textContent = "Cada documento se guardará junto a su original como «…_firmado.pdf», sin reemplazar archivos.";
    }
    return;
  }
  if (!estado.info) {
    el.txtDestino.textContent = "Se guardará junto al original.";
    return;
  }
  if (!estado.info.ruta) {
    // Abierto como bytes (arrastrado sin ruta): no hay carpeta del original.
    el.txtDestino.textContent = "Se le preguntará dónde guardar al firmar (por defecto, en Descargas).";
  } else if (el.chkGuardarComo.checked) {
    el.txtDestino.textContent = "Se le preguntará dónde guardar al firmar.";
  } else {
    const sugeridas = estado.info.salidas_sugeridas || {};
    const nombre = (sugeridas[estado.tipo] || estado.info.salida_sugerida).split(/[\\/]/).pop();
    el.txtDestino.textContent = `Se guardará como «${nombre}» junto al original.`;
  }
}

function actualizarBotonFirmar() {
  const plan = planFirma();
  el.btnFirmarTexto.textContent = estado.firmando
    ? "Firmando…"
    : plan && plan.modo === "lote"
      ? `Firmar ${plural(plan.n, "documento", "documentos")}`
      : !plan && actualYaFirmado()
        ? "Documento ya firmado"
        : "Firmar documento";
  el.btnFirmar.disabled = !plan || estado.firmando;
  el.btnCerrarDoc.disabled = estado.firmando;
  el.btnAbrir.disabled = estado.firmando;
  el.btnAbrirVacio.disabled = estado.firmando;
  const sinDocumento = estado.firmando || !estado.info || !estado.actualId;
  el.btnPosicionPaginas.disabled = sinDocumento;
  el.btnPosicionTodos.disabled = sinDocumento;
  actualizarDestino();
}

// ---------------------------------------------------------------- diálogo

function ponerProgreso(fraccion) {
  const porcentaje = Math.round(limitar(fraccion, 0, 1) * 100);
  el.dialogoProgresoValor.style.width = `${porcentaje}%`;
  el.dialogoProgreso.setAttribute("aria-valuenow", String(porcentaje));
}

function mostrarDialogo({ tipo, titulo, texto, ruta = null, acciones = [], progreso = null, nota = null, lista = [] }) {
  if (el.dialogoFondo.hidden) estado.focoPrevio = document.activeElement;
  el.dialogo.dataset.tipo = tipo;
  el.dialogoTitulo.textContent = titulo;
  el.dialogoTexto.textContent = texto;
  el.dialogoRuta.hidden = !ruta;
  el.dialogoRuta.textContent = ruta || "";
  el.dialogoProgreso.hidden = progreso === null;
  if (progreso !== null) ponerProgreso(progreso);
  el.dialogoNota.hidden = !nota;
  el.dialogoNota.textContent = nota || "";
  el.dialogoLista.hidden = !lista.length;
  el.dialogoLista.replaceChildren(...lista.map((t) => (t instanceof Node ? t : h("li", {}, t))));
  el.dialogoAcciones.replaceChildren(
    ...acciones.map((a) =>
      h(
        "button",
        { class: `btn ${a.primario ? "btn-primario" : "btn-secundario"}`, type: "button", onclick: a.accion },
        a.icono ? icono(a.icono) : null,
        h("span", {}, a.texto),
      ),
    ),
  );
  el.dialogoFondo.hidden = false;
  const primero = el.dialogoAcciones.querySelector("button");
  (primero || el.dialogo).focus();
}

function cerrarDialogo() {
  if (el.dialogo.dataset.tipo === "pin" && estado.firmando) return;
  el.dialogoFondo.hidden = true;
  if (estado.focoPrevio && estado.focoPrevio.focus) estado.focoPrevio.focus();
}

function atraparFoco(e) {
  if (el.dialogoFondo.hidden || e.key !== "Tab") return;
  const enfocables = [...el.dialogo.querySelectorAll("button")];
  if (!enfocables.length) {
    e.preventDefault();
    return;
  }
  const primero = enfocables[0];
  const ultimo = enfocables.at(-1);
  if (e.shiftKey && document.activeElement === primero) {
    e.preventDefault();
    ultimo.focus();
  } else if (!e.shiftKey && document.activeElement === ultimo) {
    e.preventDefault();
    primero.focus();
  }
}

// ---------------------------------------------------------------- firma

function opcionesComunes() {
  return { tipo: estado.tipo, motivo: motivoActual(), lugar: el.txtLugar.value };
}

async function firmar() {
  if (estado.firmando || el.btnFirmar.disabled) return;
  const plan = planFirma();
  if (!plan) return;
  if (plan.modo === "lote") {
    await firmarLote();
    return;
  }
  let destino = null;
  if (el.chkGuardarComo.checked || !estado.info.ruta) {
    const r = await llamar("elegir_destino", estado.tipo);
    if (!r.ok) {
      if (!r.cancelado) aviso(r.mensaje, true);
      return;
    }
    destino = r.ruta;
  }
  const ajuste = ajusteActual();
  const opciones = {
    ...opcionesComunes(),
    caja: cajaDocumento(),
    posiciones_paginas: posicionesPaginasParaPython(ajuste),
    paginas: document.querySelector('input[name="paginas"]:checked').value,
    pagina_firma: ajuste && ajuste.pagina != null ? ajuste.pagina : -1,
    destino,
  };

  estado.firmando = true;
  actualizarBotonFirmar();
  mostrarDialogo({
    tipo: "pin",
    titulo: "Confirme el PIN en la ventana de Windows",
    texto: "Windows abrirá su propio cuadro de diálogo para el PIN del DNIe. Si no lo ve, revise la barra de tareas.",
  });
  const r = await llamar("firmar", opciones);
  estado.firmando = false;
  actualizarBotonFirmar();
  refrescarCola(); // el documento queda «Firmado» (o «Error») en la lista

  if (!r.ok && r.cancelado) {
    // El usuario canceló «Guardar como»: se vuelve al estado listo sin error.
    cerrarDialogo();
    return;
  }
  if (r.ok) {
    const sellos =
      r.modo === "solo_campo"
        ? `El PDF ya tenía ${plural(r.firmas_previas, "firma", "firmas")}; el sello se colocó solo en el campo de firma (página ${r.pagina_firma + 1}).`
        : `Sello en ${plural(r.paginas_selladas.length, "página", "páginas")} de ${r.total_paginas} y 1 firma digital.`;
    mostrarDialogo({
      tipo: "ok",
      titulo: "Documento firmado",
      texto: sellos,
      ruta: r.salida,
      acciones: [
        { texto: "Abrir PDF firmado", icono: "doc", primario: true, accion: () => abrirGenerado(r.salida) },
        { texto: "Mostrar en carpeta", icono: "carpeta", accion: () => mostrarEnCarpeta(r.salida) },
        { texto: "Validar firma", icono: "escudo", accion: () => { cerrarDialogo(); mostrarVista("validar"); validar(r.salida); } },
        { texto: "Cerrar", accion: cerrarDialogo },
      ],
    });
  } else {
    mostrarDialogo({
      tipo: "error",
      titulo: "No se pudo firmar",
      texto: r.mensaje || "Error desconocido.",
      acciones: [
        { texto: "Reintentar", primario: true, accion: () => { cerrarDialogo(); firmar(); } },
        { texto: "Cerrar", accion: cerrarDialogo },
      ],
    });
    consultarDnie();
  }
}

// ---------------------------------------------------------------- firma por lotes

// Firma todos los pendientes en Python, uno tras otro y con una sola sesión del
// DNIe. El avance llega por window.firmador.progresoLote; al final, el resumen.
async function firmarLote() {
  const opciones = {
    ...opcionesComunes(),
    caja: estado.caja,
    // Opción común: no se toma del control, que en un PDF ya firmado se fuerza a «solo la página de firma».
    paginas: leerPreferencia("paginas", "todas"),
    en_carpeta: el.chkGuardarComo.checked,
    ajustes: ajustesParaPython(),
  };
  estado.firmando = true;
  estado.lote = null;
  renderizarCola();
  const r = await llamar("firmar_lote", opciones);
  estado.firmando = false;
  estado.lote = null;
  if (r.cola) aplicarCola(r.cola);
  else refrescarCola();
  renderizarCola();
  if (!r.ok && r.cancelado) {
    if (!el.dialogoFondo.hidden) cerrarDialogo(); // canceló la elección de carpeta
    return;
  }
  if (!r.ok) {
    mostrarDialogo({
      tipo: "error",
      titulo: "No se pudo firmar",
      texto: r.mensaje || "Error desconocido.",
      acciones: [{ texto: "Cerrar", accion: cerrarDialogo }],
    });
    return;
  }
  mostrarResumenLote(r);
  if (r.detenido === "tarjeta") consultarDnie();
}

function mostrarDialogoLote() {
  mostrarDialogo({
    tipo: "pin",
    titulo: "Preparando la firma…",
    texto: "Abriendo el DNIe.",
    progreso: 0,
    nota: NOTA_PIN_LOTE,
    acciones: [{ texto: "Cancelar", accion: cancelarLote }],
  });
  actualizarDialogoLote();
}

function actualizarDialogoLote() {
  const l = estado.lote;
  if (!l || el.dialogo.dataset.tipo !== "pin") return;
  el.dialogoTitulo.textContent = l.indice ? `Firmando ${l.indice} de ${l.total}` : "Preparando la firma…";
  el.dialogoTexto.textContent = l.cancelando
    ? `${l.nombre || "Documento actual"}: se detendrá al terminar este documento.`
    : l.nombre || "Abriendo el DNIe.";
  ponerProgreso(l.total ? l.hechos / l.total : 0);
}

async function cancelarLote() {
  const l = estado.lote;
  if (!l || l.cancelando) return;
  l.cancelando = true;
  const boton = el.dialogoAcciones.querySelector("button");
  if (boton) {
    boton.disabled = true;
    boton.querySelector("span").textContent = "Cancelando…";
  }
  actualizarDialogoLote();
  await llamar("cancelar_lote");
}

function progresoLote(evento) {
  if (!evento) return;
  if (evento.evento === "inicio") {
    estado.lote = { total: evento.total, indice: 0, hechos: 0, nombre: "", cancelando: false };
    mostrarDialogoLote();
    return;
  }
  if (evento.evento !== "archivo") return;
  const item = estado.cola.find((i) => i.id === evento.id);
  if (item) {
    item.estado = evento.estado;
    item.mensaje = evento.mensaje || null;
    if (evento.salida) item.salida = evento.salida;
  }
  if (estado.lote) {
    estado.lote.indice = evento.indice;
    estado.lote.nombre = evento.nombre;
    if (evento.estado !== "firmando") estado.lote.hechos = evento.indice;
  }
  renderizarCola();
  actualizarDialogoLote();
}

function mostrarResumenLote(r) {
  const n = r.firmados.length;
  const m = r.errores.length;
  const k = r.omitidos.length;
  let texto = `${plural(n, "firmado", "firmados")}, ${m} con error, ${plural(k, "omitido", "omitidos")}.`;
  if (r.pendientes) texto += ` ${plural(r.pendientes, "documento quedó pendiente", "documentos quedaron pendientes")}.`;
  if (r.mensaje) texto = `${r.mensaje} ${texto}`;
  // Dónde quedó cada documento firmado, y luego los problemas.
  const lista = [
    ...r.firmados.map((f) =>
      h("li", { class: "salida-lote" }, h("strong", {}, f.nombre), h("span", { class: "salida-ruta" }, f.salida)),
    ),
    ...[...r.errores, ...r.omitidos].map((e) => h("li", { class: "salida-error" }, `${e.nombre}: ${e.mensaje}`)),
  ];
  // «Abrir carpeta» solo tiene sentido si todos se guardaron en la misma carpeta.
  const carpetas = new Set(r.firmados.map((f) => f.salida.replace(/[\\/][^\\/]*$/, "").toLowerCase()));
  const carpetaUnica = n && carpetas.size === 1 ? r.firmados[0].salida.replace(/[\\/][^\\/]*$/, "") : null;
  const acciones = [];
  if (carpetaUnica) {
    acciones.push({ texto: "Abrir carpeta", icono: "carpeta", primario: true, accion: () => mostrarEnCarpeta(r.firmados[0].salida) });
  }
  if (r.pendientes && r.detenido !== "tarjeta") {
    acciones.push({ texto: "Continuar con los pendientes", icono: "pluma", primario: !n, accion: () => { cerrarDialogo(); firmar(); } });
  }
  acciones.push({ texto: "Firmar más documentos", icono: "mas", accion: firmarMas });
  acciones.push({ texto: "Cerrar", accion: cerrarDialogo });
  const conProblemas = r.detenido === "tarjeta" || r.detenido === "pin_cancelado" || n === 0;
  mostrarDialogo({
    // Con errores parciales se usa el ícono de advertencia, no el check de éxito.
    tipo: conProblemas ? "error" : m || r.detenido ? "pregunta" : "ok",
    titulo: r.detenido ? "Firma detenida" : m ? "Firma terminada con errores" : "Documentos firmados",
    texto: carpetaUnica ? `${texto} Guardados en:` : texto,
    ruta: carpetaUnica,
    lista,
    acciones,
  });
}

// Quita de la lista los ya firmados para seguir con otros documentos.
async function firmarMas() {
  cerrarDialogo();
  const r = await llamar("vaciar_cola", true);
  if (!r.ok) {
    aviso(r.mensaje, true);
    return;
  }
  aplicarCola(r.cola);
  const siguiente = pendientes()[0];
  if (!estado.info && siguiente) await abrirItem(siguiente.id);
  else if (!estado.cola.length) agregarDialogo();
}

async function abrirGenerado(ruta) {
  const r = await llamar("abrir_archivo", ruta);
  if (!r.ok) aviso(r.mensaje, true);
}

async function mostrarEnCarpeta(ruta) {
  const r = await llamar("mostrar_en_carpeta", ruta);
  if (!r.ok) aviso(r.mensaje, true);
}

// ---------------------------------------------------------------- validación

const TEXTOS = {
  estado: {
    valida: ["Válida", "ok", "insignia-valida"],
    advertencia: ["Con observaciones", "alerta", "insignia-advertencia"],
    invalida: ["No válida", "error", "insignia-invalida"],
  },
  integridad: { intacta: ["Íntegra", "estado-ok"], modificada: ["Alterada", "estado-mal"] },
  cobertura: {
    documento_completo: "Todo el documento",
    revision_completa: "Su versión del documento",
    parcial: "Parcial o no estándar",
  },
  modificaciones: {
    ninguna: ["Ninguna", "estado-ok"],
    permitidas: ["Permitidas (p. ej., otras firmas)", "estado-ok"],
    no_permitidas: ["No permitidas", "estado-mal"],
    desconocidas: ["No determinadas", "estado-aviso"],
  },
  confianza: {
    confiable: ["De confianza", "estado-ok"],
    no_confiable: ["No verificada en este equipo", "estado-aviso"],
    revocado: ["Certificado revocado", "estado-mal"],
  },
};

function formato(subfilter, tipo) {
  if (tipo === "sello_de_tiempo") return "Sello de tiempo del documento";
  if (subfilter === "/ETSI.CAdES.detached") return "PAdES (CAdES)";
  if (subfilter === "/adbe.pkcs7.detached") return "PKCS #7";
  return subfilter || "—";
}

function dato(titulo, valor, clase = null) {
  return h("div", {}, h("dt", {}, titulo), h("dd", { class: clase }, valor));
}

function tarjetaFirma(f, indice) {
  const [textoEstado, iconoEstado, claseEstado] = TEXTOS.estado[f.estado] || TEXTOS.estado.invalida;
  const [integridad, claseIntegridad] = TEXTOS.integridad[f.integridad] || ["—", null];
  const [modificaciones, claseMod] = TEXTOS.modificaciones[f.modificaciones] || ["—", null];
  const [confianza, claseConf] = TEXTOS.confianza[f.confianza] || ["—", null];
  const origen = f.origen_fecha === "sello_de_tiempo" ? " (sello de tiempo)" : f.fecha_firma ? " (reloj del equipo firmante)" : "";
  return h(
    "article",
    { class: "firma", "aria-label": `Firma ${indice + 1}: ${textoEstado}` },
    h(
      "header",
      { class: "firma-cabecera" },
      h("span", { class: "firma-num", "aria-hidden": "true" }, String(indice + 1)),
      h(
        "div",
        { class: "firma-quien" },
        h("strong", {}, f.firmante || "Firmante desconocido"),
        h("span", {}, f.emisor ? `Emitido por ${f.emisor}` : `Campo ${f.campo}`),
      ),
      h("span", { class: `insignia ${claseEstado}` }, icono(iconoEstado), textoEstado),
    ),
    h("p", { class: "firma-resumen" }, f.resumen),
    h(
      "dl",
      { class: "firma-datos" },
      dato("Fecha de firma", `${fechaLegible(f.fecha_firma)}${origen}`),
      dato("Integridad", integridad, claseIntegridad),
      dato("Modificaciones posteriores", modificaciones, claseMod),
      dato("Confianza del certificado", confianza, claseConf),
      dato("Cubre", TEXTOS.cobertura[f.cobertura] || "—"),
      dato("Formato", formato(f.subfilter, f.tipo)),
    ),
  );
}

async function validar(ruta = null) {
  el.resultados.replaceChildren(
    h("div", { class: "validar-vacio" }, h("span", { class: "girando-chico" }), h("p", {}, "Validando firmas… puede tardar si se consulta la revocación en línea.")),
  );
  const r = await llamar("validar", ruta);
  mostrarValidacion(r);
}

async function validarOtro() {
  const r = await llamar("elegir_y_validar");
  if (!r.ok && r.cancelado) return;
  mostrarValidacion(r);
}

function mostrarValidacion(r) {
  if (!r.ok) {
    el.resultados.replaceChildren(h("div", { class: "validar-vacio" }, icono("alerta"), h("p", {}, r.mensaje)));
    return;
  }
  el.validarSub.textContent = r.nombre;
  el.validarSub.title = r.ruta;
  if (!r.firmas.length) {
    el.resultados.replaceChildren(
      h("div", { class: "validar-vacio" }, icono("escudo"), h("p", {}, "Este PDF no tiene firmas digitales.")),
    );
    return;
  }
  el.resultados.replaceChildren(...r.firmas.map(tarjetaFirma));
}

// ---------------------------------------------------------------- arrastrar y soltar

// Cada «drop» pasa por: "esperando" (las rutas de Python) -> "ruta" (llegaron:
// se abren o agregan por ruta) o "bytes" (no llegaron: cada File se lee en JS y
// se envía a Python). Así el mismo archivo nunca se abre dos veces.
let soltado = null; // { fase, temporizador, archivos }
let subiendo = false;

const nombreDeRuta = (ruta) => String(ruta).split(/[\\/]/).pop().toLowerCase();

function soltarArchivo(e) {
  if (soltado) clearTimeout(soltado.temporizador);
  soltado = null;
  const archivos = [...((e.dataTransfer && e.dataTransfer.files) || [])];
  if (!archivos.length) return; // sin File en JS: quedan solo las rutas que entregue Python
  const pdfs = archivos.filter((a) => /\.pdf$/i.test(a.name));
  let rechazo = null;
  if (estado.firmando) rechazo = ["Espere a que termine la firma para agregar documentos.", false];
  else if (subiendo) rechazo = ["Espere a que terminen de abrirse los documentos anteriores.", false];
  else if (!pdfs.length) rechazo = ["Solo se pueden abrir archivos PDF.", true];
  if (rechazo) {
    soltado = { fase: "rechazado", temporizador: null, archivos: [] }; // también se ignoran las rutas de Python
    aviso(...rechazo);
    return;
  }
  if (pdfs.length < archivos.length) {
    aviso(`Se omitieron ${plural(archivos.length - pdfs.length, "archivo que no es PDF", "archivos que no son PDF")}.`);
  }
  const actual = { fase: "esperando", temporizador: null, archivos: pdfs };
  actual.temporizador = setTimeout(() => {
    if (soltado !== actual || actual.fase !== "esperando") return;
    actual.fase = "bytes";
    abrirArchivosSoltados(pdfs);
  }, ESPERA_RUTA_SOLTAR);
  soltado = actual;
}

// Envía un File a Python por fragmentos. `abrir`: abrirlo (uno solo) o solo agregarlo a la cola.
async function subirArchivo(archivo, abrir) {
  let datos;
  try {
    datos = new Uint8Array(await archivo.arrayBuffer());
  } catch {
    return { ok: false, mensaje: `No se pudo leer «${archivo.name}». Use el botón «Agregar PDFs».` };
  }
  const inicio = await llamar("abrir_bytes_inicio", archivo.name, datos.length);
  if (!inicio.ok) return inicio;
  for (let pos = 0; pos < datos.length; pos += TAM_SUBIDA) {
    mostrarCarga(`Leyendo «${archivo.name}»… ${porcentaje(pos / datos.length)}`, pos / datos.length);
    const r = await llamar("abrir_bytes_fragmento", inicio.token, bytesABase64(datos.subarray(pos, pos + TAM_SUBIDA)));
    if (!r.ok) return r;
  }
  mostrarCarga(`Analizando «${archivo.name}»…`);
  return llamar("abrir_bytes_fin", inicio.token, abrir);
}

// Alternativa sin ruta: un archivo se abre como antes; varios se agregan a la cola.
async function abrirArchivosSoltados(archivos) {
  if (estado.firmando || subiendo || !archivos.length) return;
  subiendo = true;
  const cargaAntesDeSoltar = estado.carga;
  try {
    if (archivos.length === 1) {
      const r = await subirArchivo(archivos[0], true);
      if (!r.ok) {
        aviso(r.mensaje, true);
        return;
      }
      if (estado.firmando) return;
      if (r.duplicado) aviso(`«${r.nombre}» ya está en la lista.`);
      mostrarVista("firmar");
      await cargarDocumento(r);
      return;
    }
    const resultado = { agregados: [], duplicados: [], ids_duplicados: [], cola: null };
    for (const archivo of archivos) {
      const r = await subirArchivo(archivo, false);
      if (!r.ok) {
        aviso(r.mensaje, true);
        continue;
      }
      resultado.cola = r.cola;
      if (r.duplicado) {
        resultado.duplicados.push(r.item.nombre);
        resultado.ids_duplicados.push(r.item.id);
      } else {
        resultado.agregados.push(r.item);
      }
    }
    if (resultado.cola) await despuesDeAgregar(resultado);
  } finally {
    subiendo = false;
    // Si no se llegó a abrir ningún documento (error o solo se agregaron a la
    // cola), se cierra aquí; si se abrió, cargarDocumento ya lo cerró.
    if (estado.carga === cargaAntesDeSoltar) ocultarCarga();
  }
}

// Rutas soltadas (de pywebview) y, si hubo Files sin ruta, esos por bytes.
async function agregarSoltados(rutas, archivosSinRuta) {
  if (rutas.length === 1 && !archivosSinRuta.length) {
    await abrirRuta(rutas[0]);
    return;
  }
  const r = await llamar("agregar_rutas", rutas);
  if (!r.ok) {
    aviso(r.mensaje, true);
    return;
  }
  await despuesDeAgregar(r);
  if (archivosSinRuta.length) await abrirArchivosSoltados(archivosSinRuta);
}

function iniciarArrastrarSoltar() {
  let contador = 0;
  let seguridad = null;
  const terminar = () => {
    contador = 0;
    clearTimeout(seguridad);
    el.app.classList.remove("arrastrando");
  };
  // Fase de captura (true): se ejecuta antes que cualquier listener que
  // detenga la propagación, así el aviso de arrastre nunca queda congelado.
  window.addEventListener(
    "dragenter",
    (e) => {
      if (!e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
      contador++;
      el.app.classList.add("arrastrando");
    },
    true,
  );
  window.addEventListener(
    "dragleave",
    () => {
      contador = Math.max(0, contador - 1);
      if (!contador) terminar();
    },
    true,
  );
  window.addEventListener(
    "dragover",
    (e) => {
      e.preventDefault();
      // Si el arrastre se cancela fuera de la ventana no llega «dragleave»:
      // sin «dragover» durante 1 s se quita el aviso.
      clearTimeout(seguridad);
      seguridad = setTimeout(terminar, 1000);
    },
    true,
  );
  window.addEventListener(
    "drop",
    (e) => {
      e.preventDefault();
      terminar();
      // El File se toma ahora (sincrónicamente): dataTransfer deja de ser
      // legible cuando termina el evento. Se prefiere la ruta completa que
      // entrega Python (pywebview) con window.firmador.alSoltar.
      soltarArchivo(e);
    },
    true,
  );
  window.addEventListener("dragend", terminar, true);
}

window.firmador = {
  // Python entrega las rutas completas de los PDF soltados (una o varias).
  alSoltar: (rutas) => {
    rutas = (Array.isArray(rutas) ? rutas : [rutas]).filter(Boolean);
    let sinRuta = [];
    if (soltado) {
      if (soltado.fase !== "esperando") return; // rechazado o ya se abre por bytes
      clearTimeout(soltado.temporizador);
      soltado.fase = "ruta";
      const conRuta = new Set(rutas.map(nombreDeRuta));
      sinRuta = soltado.archivos.filter((a) => !conRuta.has(a.name.toLowerCase()));
    }
    if (estado.firmando || subiendo || !rutas.length) return;
    mostrarVista("firmar");
    agregarSoltados(rutas, sinRuta);
  },
  alSoltarInvalido: () => {
    if (soltado) clearTimeout(soltado.temporizador);
    soltado = null;
    aviso("Solo se pueden abrir archivos PDF.", true);
  },
  sinArrastrar: () => {
    estado.sinArrastrar = true;
    actualizarTextoVacio();
  },
  progresoLote,
};

// ---------------------------------------------------------------- atajos

function escribiendo(e) {
  const t = e.target;
  return t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement;
}

function atajos(e) {
  atraparFoco(e);
  if (e.key === "Escape" && !el.dialogoFondo.hidden) {
    cerrarDialogo();
    return;
  }
  if (!el.dialogoFondo.hidden) return;
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && e.key.toLowerCase() === "o") {
    e.preventDefault();
    agregarDialogo();
  } else if (ctrl && e.key.toLowerCase() === "w") {
    e.preventDefault();
    cerrarDocumento();
  } else if (ctrl && e.key === "Enter") {
    e.preventDefault();
    mostrarVista("firmar");
    firmar();
  } else if (ctrl && (e.key === "+" || e.key === "=")) {
    e.preventDefault();
    cambiarZoom(1);
  } else if (ctrl && e.key === "-") {
    e.preventDefault();
    cambiarZoom(-1);
  } else if (ctrl && e.key === "0") {
    e.preventDefault();
    ajustarAncho();
  } else if (ctrl && e.key === "9") {
    e.preventDefault();
    ajustarPagina();
  } else if (ctrl && e.key === "1") {
    e.preventDefault();
    mostrarVista("firmar");
  } else if (ctrl && e.key === "2") {
    e.preventDefault();
    mostrarVista("validar");
  } else if (!ctrl && !escribiendo(e) && vistaActual() === "firmar" && estado.doc && e.target !== el.sello) {
    const saltos = { PageDown: 1, PageUp: -1, ArrowRight: 1, ArrowLeft: -1 };
    if (e.key in saltos) {
      e.preventDefault();
      irAPagina(estado.pagina + saltos[e.key]);
    } else if (e.key === "Home") {
      e.preventDefault();
      irAPagina(1);
    } else if (e.key === "End") {
      e.preventDefault();
      irAPagina(estado.total);
    }
  }
}

// ---------------------------------------------------------------- inicio

function iniciar() {
  iniciarTema();
  estado.caja = posicionGuardada(leerPreferencia("caja", null), "firma");
  el.txtLugar.value = leerPreferencia("lugar", "");
  aplicarTipo("firma");
  el.chkGuardarComo.checked = Boolean(leerPreferencia("guardarComo", false));
  actualizarVistaPrevia();
  actualizarDestino();

  el.btnAbrir.addEventListener("click", agregarDialogo);
  el.btnAbrirVacio.addEventListener("click", agregarDialogo);
  el.btnAgregar.addEventListener("click", agregarDialogo);
  el.btnVaciar.addEventListener("click", vaciarCola);
  el.btnCerrarDoc.addEventListener("click", cerrarDocumento);
  el.btnPosicionPaginas.addEventListener("click", aplicarATodasLasPaginas);
  el.btnPosicionTodos.addEventListener("click", aplicarPosicionATodos);
  el.tabFirmar.addEventListener("click", () => mostrarVista("firmar"));
  el.tabValidar.addEventListener("click", () => mostrarVista("validar"));
  for (const tab of [el.tabFirmar, el.tabValidar]) {
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const destino = vistaActual() === "firmar" ? "validar" : "firmar";
      mostrarVista(destino);
      (destino === "firmar" ? el.tabFirmar : el.tabValidar).focus();
    });
  }
  el.chipFirmas.addEventListener("click", () => {
    mostrarVista("validar");
    validar();
  });

  el.btnAnt.addEventListener("click", () => irAPagina(estado.pagina - 1));
  el.btnSig.addEventListener("click", () => irAPagina(estado.pagina + 1));
  el.inpPagina.addEventListener("change", () => irAPagina(Number(el.inpPagina.value)));
  el.btnMenos.addEventListener("click", () => cambiarZoom(-1));
  el.btnMas.addEventListener("click", () => cambiarZoom(1));
  el.btnAjustar.addEventListener("click", ajustarAncho);
  el.btnPagina.addEventListener("click", ajustarPagina);
  new ResizeObserver(() => {
    if (estado.zoom.ajuste) programarRender();
  }).observe(el.scroll);

  el.btnDnie.addEventListener("click", consultarDnie);
  for (const radio of document.querySelectorAll('input[name="paginas"], input[name="pagina-firma"]')) {
    radio.addEventListener("change", () => {
      if (radio.name === "paginas") guardarPreferencia("paginas", radio.value);
      if (radio.name === "pagina-firma") {
        // «Página actual» es un ajuste de este documento; los demás siguen en su última página.
        const a = ajusteEditable();
        if (a) {
          if (radio.value === "actual") a.pagina = estado.pagina - 1;
          else delete a.pagina;
          limpiarAjuste();
          renderizarCola();
        }
      }
      actualizarEtiquetaSello();
      actualizarMarcas();
    });
  }
  for (const radio of document.querySelectorAll('input[name="tipo"]')) {
    radio.addEventListener("change", () => {
      if (radio.checked) aplicarTipo(radio.value, { cambiarCaja: true, guardar: true });
    });
  }
  el.selMotivo.addEventListener("change", actualizarVistaPrevia);
  el.txtLugar.addEventListener("input", () => guardarPreferencia("lugar", el.txtLugar.value));
  for (const [clave, c] of Object.entries(SELECTORES)) {
    if (!c.fija) $(`btn-${c.prefijo}`).addEventListener("click", () => elegirImagen(clave));
    if (c.quitar) $(c.quitar).addEventListener("click", () => quitarImagen(clave));
    $(c.sello).addEventListener("load", ajustarTextoSello);
  }
  el.chkGuardarComo.addEventListener("change", () => {
    guardarPreferencia("guardarComo", el.chkGuardarComo.checked);
    actualizarDestino();
  });
  el.btnFirmar.addEventListener("click", firmar);
  el.btnValidarAbierto.addEventListener("click", () => validar());
  el.btnValidarOtro.addEventListener("click", validarOtro);

  iniciarArrastreSello();
  iniciarArrastrarSoltar();
  document.addEventListener("keydown", atajos);

  const conectar = () => {
    cargarConfig();
    consultarDnie();
    refrescarCola();
  };
  if (window.pywebview && window.pywebview.api) conectar();
  else window.addEventListener("pywebviewready", conectar, { once: true });
}

iniciar();
