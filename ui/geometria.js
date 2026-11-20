// Stamp geometry of the UI, without DOM or pdf.js state: a mirror of
// firma_pdf.py (TAMANOS_SELLO, POSICIONES_PREDETERMINADAS, giro_de_sello,
// fraccion_en_marco, fraccion_sin_rotar, rect_sello). tests/test_ui_paridad.py
// runs this module with Node and compares it with Python within 0.01 pt.
//
// Every page has a layout frame turned by θ (`giroDeSello`): a page displayed
// in portrait is laid out as if stored with /Rotate 0, one displayed in
// landscape as if stored with /Rotate 270. Positions { x, y } are the stamp's
// lower-left corner in fractions of that frame; rects are in unrotated PDF
// space (pt, origin bottom left), as pdf.js `page.view` and firma_pdf use.

// Fixed stamp size per type, in pt (firma_pdf.TAMANOS_SELLO).
export const TAMANOS_SELLO = Object.freeze({
  firma: Object.freeze({ ancho: 160, alto: 40 }),
  visto_bueno: Object.freeze({ ancho: 140, alto: 110 }),
});

// Default position per type, in frame fractions: on A4, right edge at 560 pt
// and bottom edge at 30 pt (firma_pdf.POSICIONES_PREDETERMINADAS).
export const POSICIONES_PREDETERMINADAS = Object.freeze(
  Object.fromEntries(
    Object.entries(TAMANOS_SELLO).map(([tipo, { ancho }]) => [tipo, Object.freeze({ x: (560 - ancho) / 595, y: 30 / 842 })]),
  ),
);

const limitar = (v, min, max) => Math.min(Math.max(v, min), max);

// /Rotate rounded to a quarter turn in 0..270, like giro_de_sello.
export function normalizarRotacion(rotacion) {
  const r = Math.round((Number(rotacion) || 0) / 90) * 90;
  return ((r % 360) + 360) % 360;
}

// Layout frame rotation θ of a page with this view and /Rotate (giro_de_sello).
export function giroDeSello(view, rotacion) {
  const ancho = view[2] - view[0];
  const alto = view[3] - view[1];
  const r = normalizarRotacion(rotacion);
  const [anchoVisto, altoVisto] = r === 0 || r === 180 ? [ancho, alto] : [alto, ancho];
  return altoVisto >= anchoVisto ? r : (r + 90) % 360;
}

// Fractions of the unrotated view -> fractions of the frame turned by `giro` (fraccion_en_marco).
export function fraccionEnMarco(x, y, giro) {
  if (giro === 90) return [y, 1 - x];
  if (giro === 180) return [1 - x, 1 - y];
  if (giro === 270) return [1 - y, x];
  return [x, y];
}

// Inverse of fraccionEnMarco (fraccion_sin_rotar).
export function fraccionSinRotar(u, v, giro) {
  if (giro === 90) return [1 - v, u];
  if (giro === 180) return [1 - u, 1 - v];
  if (giro === 270) return [v, 1 - u];
  return [u, v];
}

// Width and height of the layout frame, in pt.
export function tamanoMarco(view, giro) {
  const ancho = view[2] - view[0];
  const alto = view[3] - view[1];
  return giro === 90 || giro === 270 ? [alto, ancho] : [ancho, alto];
}

// Frame point (pt from the frame's lower-left corner) -> unrotated point (firma_pdf._desde_marco).
function desdeMarco(u, v, view, giro) {
  const [x0, y0, x1, y1] = view;
  if (giro === 90) return [x1 - v, y0 + u];
  if (giro === 180) return [x1 - u, y1 - v];
  if (giro === 270) return [x0 + v, y1 - u];
  return [x0 + u, y0 + v];
}

// Inverse of desdeMarco: unrotated point -> frame point.
function haciaMarco(x, y, view, giro) {
  const [x0, y0, x1, y1] = view;
  if (giro === 90) return [y - y0, x1 - x];
  if (giro === 180) return [x1 - x, y1 - y];
  if (giro === 270) return [y1 - y, x - x0];
  return [x - x0, y - y0];
}

// Stamp rect (x0, y0, x1, y1) in unrotated PDF space, like firma_pdf.rect_sello:
// the historical rule (fixed size; shifted to fit inside) runs in the frame and
// the rect is mapped back, so 90 and 270 swap its width and height. Without
// `rotacion` the frame is the unrotated view (θ = 0).
export function rectSello(posicion, view, tipo, rotacion = null) {
  const giro = rotacion == null ? 0 : giroDeSello(view, rotacion);
  const { ancho, alto } = TAMANOS_SELLO[tipo];
  const [fw, fh] = tamanoMarco(view, giro);
  const u0 = Math.max(0, Math.min(limitar(posicion.x, 0, 1) * fw, fw - ancho));
  const v0 = Math.max(0, Math.min(limitar(posicion.y, 0, 1) * fh, fh - alto));
  const a = desdeMarco(u0, v0, view, giro);
  const b = desdeMarco(u0 + ancho, v0 + alto, view, giro);
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}

// Frame-fraction position { x, y } of a rect in unrotated space: what
// rectSello (and firma_pdf.rect_sello) turns back into that rect.
export function posicionDeRect(rect, view, rotacion = null) {
  const giro = rotacion == null ? 0 : giroDeSello(view, rotacion);
  const [fw, fh] = tamanoMarco(view, giro);
  const a = haciaMarco(rect[0], rect[1], view, giro);
  const b = haciaMarco(rect[2], rect[3], view, giro);
  return {
    x: limitar(Math.min(a[0], b[0]) / fw, 0, 1),
    y: limitar(Math.min(a[1], b[1]) / fh, 0, 1),
  };
}

// Clockwise turn (degrees) of the stamp's content on screen. The appearance is
// turned counterclockwise by θ in unrotated space and the viewer turns the page
// clockwise by its /Rotate: 0 on pages displayed in portrait, 270 (reads
// upward) on pages displayed in landscape. `rotacionMostrada` is the rotation
// the viewer applies (pdf.js viewport), when it differs from the one signed.
export function giroEnPantalla(view, rotacion, rotacionMostrada = rotacion) {
  return (((normalizarRotacion(rotacionMostrada) - giroDeSello(view, rotacion)) % 360) + 360) % 360;
}

// Unrotated rect -> pixel box { left, top, width, height } in a pdf.js viewport.
export function rectEnPixeles(vp, rect) {
  const a = vp.convertToViewportPoint(rect[0], rect[1]);
  const b = vp.convertToViewportPoint(rect[2], rect[3]);
  return {
    left: Math.min(a[0], b[0]),
    top: Math.min(a[1], b[1]),
    width: Math.abs(b[0] - a[0]),
    height: Math.abs(b[1] - a[1]),
  };
}

// Pixel box in a pdf.js viewport -> unrotated rect (x0, y0, x1, y1).
export function rectDesdePixeles(vp, r) {
  const a = vp.convertToPdfPoint(r.left, r.top);
  const b = vp.convertToPdfPoint(r.left + r.width, r.top + r.height);
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}
