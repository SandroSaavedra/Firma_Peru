"""
Stamp layout by displayed orientation (feature "sello-orientacion-y-reintento", T3).

Every page gets a layout frame rotation θ: a page displayed in portrait is laid
out as if it were stored with /Rotate 0, and a page displayed in landscape as
if it were stored with /Rotate 270. θ = 0 keeps the historical geometry. The
stamp is placed in the frame (positions are fractions of the frame), mapped
back to unrotated page space, and its appearance is rotated by θ so it shows
upright in the frame.

Displayed result: horizontal at the bottom right of portrait-displayed pages,
vertical on the right margin of landscape-displayed pages (the look of the A3
pages stored with /Rotate 270, approved by the user).
"""
import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image

import firma_pdf
import validacion
from conftest import construir_pdf
from firma_pdf import OpcionesFirma, PosicionRelativa, firmar_pdf
from test_firma_por_pagina import _campos_por_pagina, _leer, _rect

A4 = (0, 0, 595, 842)
CARTA = (0, 0, 612, 792)
A4_HORIZONTAL = (0, 0, 842, 595)
A3 = (0, 0, 842, 1191)
A3_HORIZONTAL = (0, 0, 1191, 842)

# The six page classes of the field document: (view, /Rotate, θ, displayed portrait?).
CLASES = [
    pytest.param(A4, 0, 0, True, id="A4-r0"),
    pytest.param(CARTA, 0, 0, True, id="Carta-r0"),
    pytest.param(A4_HORIZONTAL, 270, 270, True, id="A4-horizontal-r270"),
    pytest.param(A3, 270, 0, False, id="A3-r270"),
    pytest.param(A3, 90, 180, False, id="A3-r90"),
    pytest.param(A3_HORIZONTAL, 0, 90, False, id="A3-horizontal-r0"),
]
PAGINAS = [
    {"media_box": A4, "contenido": False},
    {"media_box": CARTA, "contenido": False},
    {"media_box": A4_HORIZONTAL, "rotacion": 270, "contenido": False},
    {"media_box": A3, "rotacion": 270, "contenido": False},
    {"media_box": A3, "rotacion": 90, "contenido": False},
    {"media_box": A3_HORIZONTAL, "contenido": False},
]
VERTICALES = [True, True, True, False, False, False]  # displayed portrait, per page of PAGINAS


def _pdf(tmp_path, paginas=PAGINAS, nombre="clases.pdf"):
    ruta = tmp_path / nombre
    ruta.write_bytes(construir_pdf(paginas))
    return ruta


def _logo(tmp_path):
    ruta = tmp_path / "logo.png"
    Image.new("RGB", (300, 200), (200, 30, 30)).save(ruta)
    return ruta


def _rotacion(spec):
    return spec.get("rotacion", 0)


# --- Independent reference geometry ------------------------------------------------


def _desde_marco(rect, vista, giro):
    """Frame rect -> unrotated rect, written out per θ (independent of firma_pdf)."""
    x0, y0, x1, y1 = vista
    u0, v0, u1, v1 = rect
    if giro == 0:
        return x0 + u0, y0 + v0, x0 + u1, y0 + v1
    if giro == 90:
        return x1 - v1, y0 + u0, x1 - v0, y0 + u1
    if giro == 180:
        return x1 - u1, y1 - v1, x1 - u0, y1 - v0
    return x0 + v0, y1 - u1, x0 + v1, y1 - u0


def _esperado(posicion, vista, rotacion, giro, tipo="firma"):
    """Today's rect_sello math in the frame, mapped back to unrotated space."""
    w, h = vista[2] - vista[0], vista[3] - vista[1]
    marco = (0, 0, w, h) if giro in (0, 180) else (0, 0, h, w)
    return _desde_marco(firma_pdf.rect_sello(posicion, marco, tipo), vista, giro)


def _en_pantalla(rect, vista, rotacion):
    """Unrotated rect -> displayed rect (origin bottom left) for a page with /Rotate `rotacion`."""
    x0, y0, x1, y1 = vista
    w, h = x1 - x0, y1 - y0
    a, b, c, d = rect[0] - x0, rect[1] - y0, rect[2] - x0, rect[3] - y0
    if rotacion == 0:
        return a, b, c, d
    if rotacion == 90:  # the viewer turns the page 90° clockwise
        return b, w - c, d, w - a
    if rotacion == 180:
        return w - c, h - d, w - a, h - b
    return h - d, a, h - b, c


def _tamano_en_pantalla(vista, rotacion):
    w, h = vista[2] - vista[0], vista[3] - vista[1]
    return (w, h) if rotacion in (0, 180) else (h, w)


# --- Rendering (pypdfium2 applies /Rotate like any viewer) --------------------------


def _render(ruta, indice):
    pdf = pdfium.PdfDocument(str(ruta))
    pdf.init_forms()  # signature widgets are drawn only with the form environment
    try:
        return pdf[indice].render(scale=1, draw_annots=True, may_draw_forms=True).to_pil().convert("RGB")
    finally:
        pdf.close()


def _caja(imagen, mascara):
    """Bounding box (left, top, right, bottom) in image pixels of the pixels in `mascara`, plus their centroid."""
    pixeles = np.asarray(imagen, dtype=np.int16)
    ys, xs = np.nonzero(mascara(pixeles))
    assert xs.size, "no ink on the rendered page"
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), (float(xs.mean()), float(ys.mean()))


def _tinta(p):
    return p.min(axis=2) < 200


def _rojo(p):
    return (p[..., 0] > 140) & (p[..., 1] < 110) & (p[..., 2] < 110)


def _texto(p):
    return p.max(axis=2) < 110


def _comprobar_pantalla(ruta, indice, vista, rotacion, vertical, esperado_en_pantalla=None):
    """
    The rendered stamp: inside the expected displayed rect (if given), at the
    bottom right and horizontal on a portrait-displayed page, on the right
    margin and vertical on a landscape-displayed page. The logo (red) is to the
    LEFT of the text when upright and BELOW it when vertical (the approved A3
    look), so a stamp turned the wrong way fails.
    """
    imagen = _render(ruta, indice)
    ancho, alto = imagen.size
    assert (ancho, alto) == pytest.approx(_tamano_en_pantalla(vista, rotacion), abs=1)
    (izq, arriba, der, abajo), _ = _caja(imagen, _tinta)
    _, centro_logo = _caja(imagen, _rojo)
    _, centro_texto = _caja(imagen, _texto)
    if esperado_en_pantalla is not None:
        a, b, c, d = esperado_en_pantalla  # origin bottom left
        assert izq >= a - 2 and der <= c + 2 and arriba >= alto - d - 2 and abajo <= alto - b + 2, (
            (izq, arriba, der, abajo),
            (a, alto - d, c, alto - b),
        )
    if vertical:
        assert der - izq > abajo - arriba, "stamp must be horizontal"
        assert centro_logo[0] < centro_texto[0] - 10, "logo must be left of the text (upright)"
    else:
        assert abajo - arriba > der - izq, "stamp must be vertical"
        assert centro_logo[1] > centro_texto[1] + 10, "logo must be below the text (approved A3 look)"
    return (izq, arriba, der, abajo)


def _intactas(ruta, material_prueba):
    resultado = validacion.validar_pdf(ruta, permitir_red=False, raices_extra=[material_prueba[1]])
    assert resultado
    for firma in resultado:
        assert firma["integridad"] == "intacta" and firma["criptografia_valida"], firma
        assert firma["estado"] == "valida", firma
    for firma in resultado[:-1]:
        assert firma["modificaciones"] == "permitidas", firma
    return resultado


# --- θ and the fraction helpers ------------------------------------------------------


@pytest.mark.parametrize("vista, rotacion, giro, _vertical", CLASES)
def test_giro_por_orientacion_mostrada(vista, rotacion, giro, _vertical):
    assert firma_pdf.giro_de_sello(vista, rotacion) == giro


@pytest.mark.parametrize(
    "vista, rotacion, giro",
    [
        (A4, 180, 180),  # portrait shown upside down: upright in the frame of /Rotate 180
        (A4, 90, 180),  # portrait box shown landscape
        (A4_HORIZONTAL, 90, 90),  # landscape box shown portrait
        (A4_HORIZONTAL, 180, 270),  # landscape box shown landscape
        (A4, -90, 0),  # /Rotate -90 is 270
        ((0, 0, 500, 500), 0, 0),  # a square page counts as portrait
    ],
)
def test_giro_otros_casos(vista, rotacion, giro):
    assert firma_pdf.giro_de_sello(vista, rotacion) == giro


@pytest.mark.parametrize(
    "giro, sin_rotar, en_marco",
    [
        (0, (0.1, 0.2), (0.1, 0.2)),
        (90, (0.1, 0.2), (0.2, 0.9)),
        (180, (0.1, 0.2), (0.9, 0.8)),
        (270, (0.1, 0.2), (0.8, 0.1)),
    ],
)
def test_fracciones_entre_pagina_sin_rotar_y_marco(giro, sin_rotar, en_marco):
    assert firma_pdf.fraccion_en_marco(*sin_rotar, giro) == pytest.approx(en_marco)
    assert firma_pdf.fraccion_sin_rotar(*en_marco, giro) == pytest.approx(sin_rotar)


@pytest.mark.parametrize("giro", [0, 90, 180, 270])
@pytest.mark.parametrize("punto", [(0, 0), (1, 0), (0.25, 0.75), (1, 1)])
def test_fracciones_ida_y_vuelta(giro, punto):
    ida = firma_pdf.fraccion_en_marco(*punto, giro)
    assert firma_pdf.fraccion_sin_rotar(*ida, giro) == pytest.approx(punto)


@pytest.mark.parametrize("giro", [90, 180, 270])
def test_fraccion_en_marco_coincide_con_el_rect(giro):
    """A frame point maps to the same unrotated point as the rect mapping: one convention for UI and core."""
    vista = (10, 20, 610, 1020)
    w, h = 600, 1000
    fw, fh = (w, h) if giro == 180 else (h, w)
    u, v = 0.3, 0.6
    x, y = firma_pdf.fraccion_sin_rotar(u, v, giro)
    punto = _desde_marco((u * fw, v * fh, u * fw, v * fh), vista, giro)
    assert (punto[0], punto[1]) == pytest.approx((10 + x * w, 20 + y * h))


# --- Rect in unrotated space ------------------------------------------------------------


@pytest.mark.parametrize("tipo", ["firma", "visto_bueno"])
@pytest.mark.parametrize("vista, rotacion, giro, _vertical", CLASES)
def test_rect_predeterminado_en_espacio_sin_rotar(vista, rotacion, giro, _vertical, tipo):
    posicion = firma_pdf.posicion_predeterminada(tipo)
    rect = firma_pdf.rect_sello(posicion, vista, tipo, rotacion=rotacion)
    assert rect == pytest.approx(_esperado(posicion, vista, rotacion, giro, tipo))
    ancho, alto = firma_pdf.TAMANOS_SELLO[tipo]
    assert (rect[2] - rect[0], rect[3] - rect[1]) == pytest.approx((ancho, alto) if giro in (0, 180) else (alto, ancho))


def test_rect_a4_horizontal_rotate_270_queda_abajo_a_la_derecha_al_mostrarse():
    rect = firma_pdf.rect_sello(firma_pdf.posicion_predeterminada("firma"), A4_HORIZONTAL, "firma", rotacion=270)
    assert rect == pytest.approx((30, 35, 70, 195))
    # displayed portrait 595 x 842: bottom right, horizontal, same spot as on an A4 /Rotate 0 page
    assert _en_pantalla(rect, A4_HORIZONTAL, 270) == pytest.approx((400, 30, 560, 70))


@pytest.mark.parametrize("vista, rotacion", [(A4, 0), (CARTA, 0), (A3, 270), ((10, 20, 200, 300), 270)])
def test_giro_cero_igual_que_antes(vista, rotacion):
    """θ = 0 pages keep exactly the historical geometry (rect_sello without rotation)."""
    for posicion in (firma_pdf.posicion_predeterminada("firma"), PosicionRelativa(0.2, 0.7), PosicionRelativa(1, 1)):
        assert firma_pdf.rect_sello(posicion, vista, "firma", rotacion=rotacion) == firma_pdf.rect_sello(
            posicion, vista, "firma"
        )


def test_pagina_mas_chica_que_el_sello_girado_usa_la_misma_regla():
    vista = (0, 0, 120, 30)  # landscape, smaller than the vertical stamp
    rect = firma_pdf.rect_sello(PosicionRelativa(0.9, 0.9), vista, "firma", rotacion=0)
    assert rect == pytest.approx(_esperado(PosicionRelativa(0.9, 0.9), vista, 0, 90))


# --- Signed documents: cada_pagina, todas, partes ------------------------------------


def _comprobar_documento_por_campos(ruta, paginas, material_prueba, posicion=None, propias=None):
    reader = _leer(ruta)
    por_pagina = _campos_por_pagina(reader)
    assert sorted(por_pagina) == list(range(len(paginas)))
    for indice, spec in enumerate(paginas):
        (campo,) = por_pagina[indice]
        vista, rotacion = spec["media_box"], _rotacion(spec)
        giro = firma_pdf.giro_de_sello(vista, rotacion)
        pos = (propias or {}).get(indice, posicion or firma_pdf.posicion_predeterminada("firma"))
        esperado = _esperado(pos, vista, rotacion, giro)
        assert _rect(campo) == pytest.approx(esperado, abs=0.01), indice
        _comprobar_pantalla(ruta, indice, vista, rotacion, VERTICALES[indice], _en_pantalla(esperado, vista, rotacion))
    _intactas(ruta, material_prueba)


def test_cada_pagina_sigue_la_orientacion_mostrada(tmp_path, firmante, material_prueba):
    salida = tmp_path / "s.pdf"
    firmar_pdf(_pdf(tmp_path), salida, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina", logo=str(_logo(tmp_path))))
    _comprobar_documento_por_campos(salida, PAGINAS, material_prueba)


def test_cada_pagina_giro_cero_sin_matriz(tmp_path, firmante):
    """θ = 0 appearances are untouched: no /Matrix, the historical 160 x 40 box."""
    salida = tmp_path / "s.pdf"
    firmar_pdf(_pdf(tmp_path), salida, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"))
    por_pagina = _campos_por_pagina(_leer(salida))
    for indice in (0, 1, 3):  # A4, Carta, A3 /Rotate 270
        (campo,) = por_pagina[indice]
        apariencia = campo["/AP"]["/N"]
        assert "/Matrix" not in apariencia
        assert sorted(abs(float(v)) for v in apariencia["/BBox"]) == [0, 0, 40, 160]
    for indice in (2, 4, 5):
        (campo,) = por_pagina[indice]
        assert "/Matrix" in campo["/AP"]["/N"]


def test_cada_pagina_por_partes_sigue_la_orientacion(tmp_path, firmante, material_prueba, monkeypatch):
    monkeypatch.setattr(firma_pdf, "PAGINAS_POR_PARTE", 3)
    resultado = firmar_pdf(
        _pdf(tmp_path), tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina", logo=str(_logo(tmp_path)))
    )
    assert len(resultado.partes) == 2
    for numero, ruta in enumerate(resultado.partes):
        paginas = PAGINAS[numero * 3 : numero * 3 + 3]
        reader = _leer(ruta)
        por_pagina = _campos_por_pagina(reader)
        for local, spec in enumerate(paginas):
            (campo,) = por_pagina[local]
            vista, rotacion = spec["media_box"], _rotacion(spec)
            esperado = _esperado(firma_pdf.posicion_predeterminada("firma"), vista, rotacion, firma_pdf.giro_de_sello(vista, rotacion))
            assert _rect(campo) == pytest.approx(esperado, abs=0.01)
            _comprobar_pantalla(ruta, local, vista, rotacion, VERTICALES[numero * 3 + local], _en_pantalla(esperado, vista, rotacion))
        _intactas(ruta, material_prueba)


def _sello_de_pagina(reader, indice):
    """/Rect of the page's stamp: its signature widget or its /Stamp annotation."""
    pagina = reader.find_page_for_modification(indice)[0].get_object()
    rects = [_rect(a.get_object()) for a in pagina.get("/Annots", []) if a.get_object()["/Subtype"] in ("/Widget", "/Stamp")]
    assert len(rects) == 1, rects
    return rects[0]


@pytest.mark.parametrize("pagina_firma", [-1, 0, 2])
def test_todas_sigue_la_orientacion_mostrada(tmp_path, firmante, material_prueba, pagina_firma):
    """Content stamps (/Stamp reusing the field appearance) follow each page's θ, whatever the field page's θ."""
    salida = tmp_path / "s.pdf"
    firmar_pdf(
        _pdf(tmp_path),
        salida,
        firmante,
        "PRUEBA",
        OpcionesFirma(paginas="todas", pagina_firma=pagina_firma, logo=str(_logo(tmp_path))),
    )
    reader = _leer(salida)
    for indice, spec in enumerate(PAGINAS):
        vista, rotacion = spec["media_box"], _rotacion(spec)
        esperado = _esperado(firma_pdf.posicion_predeterminada("firma"), vista, rotacion, firma_pdf.giro_de_sello(vista, rotacion))
        assert _sello_de_pagina(reader, indice) == pytest.approx(esperado, abs=0.01), indice
        _comprobar_pantalla(salida, indice, vista, rotacion, VERTICALES[indice], _en_pantalla(esperado, vista, rotacion))
    (firma,) = _intactas(salida, material_prueba)
    assert firma["modificaciones"] == "ninguna"


def test_todas_reutiliza_una_apariencia_por_giro(tmp_path, firmante):
    paginas = PAGINAS + PAGINAS  # two pages of each class
    salida = tmp_path / "s.pdf"
    firmar_pdf(_pdf(tmp_path, paginas), salida, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))
    reader = _leer(salida)
    apariencias = set()
    for indice in range(len(paginas) - 1):
        pagina = reader.find_page_for_modification(indice)[0].get_object()
        (sello,) = [a.get_object() for a in pagina["/Annots"] if a.get_object()["/Subtype"] == "/Stamp"]
        apariencias.add(sello["/AP"].raw_get("/N").reference)
    # θ of the field page (90) plus one wrapper per other θ (0, 180, 270)
    assert len(apariencias) == 4


def test_todas_segunda_firma_mantiene_validas_las_anteriores(tmp_path, firmante, material_prueba):
    primera = tmp_path / "primera.pdf"
    segunda = tmp_path / "segunda.pdf"
    firmar_pdf(_pdf(tmp_path), primera, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))
    firmar_pdf(primera, segunda, firmante, "PRUEBA", OpcionesFirma(paginas="todas", tipo="visto_bueno", pagina_firma=2))
    primera_v, segunda_v = _intactas(segunda, material_prueba)
    assert primera_v["nivel_modificacion"] == "ANNOTATIONS"


@pytest.mark.parametrize("paginas", ["ultima", "todas"])
def test_ultima_y_todas_campo_en_pagina_girada(tmp_path, firmante, material_prueba, paginas):
    salida = tmp_path / "s.pdf"
    firmar_pdf(
        _pdf(tmp_path), salida, firmante, "PRUEBA", OpcionesFirma(paginas=paginas, pagina_firma=2, logo=str(_logo(tmp_path)))
    )
    vista, rotacion = A4_HORIZONTAL, 270
    esperado = _esperado(firma_pdf.posicion_predeterminada("firma"), vista, rotacion, 270)
    assert _sello_de_pagina(_leer(salida), 2) == pytest.approx(esperado, abs=0.01)
    _comprobar_pantalla(salida, 2, vista, rotacion, True, _en_pantalla(esperado, vista, rotacion))
    _intactas(salida, material_prueba)


# --- Hand-placed positions are frame fractions ---------------------------------------


def test_posicion_a_mano_en_a4_horizontal_rotate_270(tmp_path, firmante, material_prueba):
    """θ = 270: the frame is the displayed page, so the position is where the user sees the stamp."""
    propia = PosicionRelativa(0.1, 0.5)
    salida = tmp_path / "s.pdf"
    firmar_pdf(
        _pdf(tmp_path),
        salida,
        firmante,
        "PRUEBA",
        OpcionesFirma(paginas="cada_pagina", posiciones_paginas={2: propia}, logo=str(_logo(tmp_path))),
    )
    (campo,) = _campos_por_pagina(_leer(salida))[2]
    rect = _rect(campo)
    en_pantalla = _en_pantalla(rect, A4_HORIZONTAL, 270)
    # displayed 595 x 842: lower-left corner at (10 %, 50 %), 160 x 40, horizontal
    assert en_pantalla == pytest.approx((0.1 * 595, 0.5 * 842, 0.1 * 595 + 160, 0.5 * 842 + 40), abs=0.01)
    caja = _comprobar_pantalla(salida, 2, A4_HORIZONTAL, 270, True, en_pantalla)
    assert caja[0] <= 0.1 * 595 + 12  # the stamp starts at the displayed spot
    _intactas(salida, material_prueba)


def test_posicion_a_mano_en_a3_horizontal_rotate_0(tmp_path, firmante, material_prueba):
    """θ = 90: the frame is the page turned to portrait; the stamp shows vertical at that spot."""
    propia = PosicionRelativa(0.4, 0.3)
    documento = PosicionRelativa(0.5, 0.5)
    salida = tmp_path / "s.pdf"
    firmar_pdf(
        _pdf(tmp_path),
        salida,
        firmante,
        "PRUEBA",
        OpcionesFirma(
            paginas="todas", posicion=documento, posiciones_paginas={5: propia}, pagina_firma=0, logo=str(_logo(tmp_path))
        ),
    )
    reader = _leer(salida)
    rect = _sello_de_pagina(reader, 5)
    # frame 842 x 1191: lower-left (336.8, 357.3), 160 x 40; display = frame turned 90° counterclockwise
    u0, v0 = 0.4 * 842, 0.3 * 1191
    assert rect == pytest.approx((1191 - v0 - 40, u0, 1191 - v0, u0 + 160), abs=0.01)
    _comprobar_pantalla(salida, 5, A3_HORIZONTAL, 0, False, rect)
    # the per-document position is also a frame fraction on every other page
    for indice, spec in enumerate(PAGINAS[:5]):
        vista, rotacion = spec["media_box"], _rotacion(spec)
        esperado = _esperado(documento, vista, rotacion, firma_pdf.giro_de_sello(vista, rotacion))
        assert _sello_de_pagina(reader, indice) == pytest.approx(esperado, abs=0.01), indice
    _intactas(salida, material_prueba)
