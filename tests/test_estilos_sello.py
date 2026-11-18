"""
Pruebas de los tipos de sello («Firma» con logo y «Visto bueno» con marca de
verificación): formato de fecha, /Reason, apariencia del campo y logo.
"""
import datetime

import pytest
from PIL import Image
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext

import firma_pdf
from firma_pdf import ErrorFirma, OpcionesFirma, fecha_sello, firmar_pdf

LIMA = datetime.timezone(datetime.timedelta(hours=-5))


def _leer(ruta):
    return PdfFileReader(open(ruta, "rb"), strict=False)


def _anotacion_firma(reader, indice):
    pagina = reader.find_page_for_modification(indice)[0].get_object()
    for anot in pagina.get("/Annots", []):
        anot = anot.get_object()
        if anot.get("/FT") == "/Sig" or "/Parent" in anot and anot["/Parent"].get("/FT") == "/Sig":
            return anot
    return None


def _subtipos_xobject(flujo, vistos=None) -> set[str]:
    """Subtipos de todos los XObject usados (recursivamente) por un flujo de apariencia."""
    vistos = set() if vistos is None else vistos
    encontrados = set()
    recursos = flujo.get("/Resources")
    if recursos is None:
        return encontrados
    for valor in recursos.get_object().get("/XObject", {}).values():
        objeto = valor.get_object()
        if id(objeto) in vistos:
            continue
        vistos.add(id(objeto))
        encontrados.add(str(objeto.get("/Subtype")))
        if objeto.get("/Subtype") == "/Form":
            encontrados |= _subtipos_xobject(objeto, vistos)
    return encontrados


def _apariencia(reader, indice):
    anot = _anotacion_firma(reader, indice)
    assert anot is not None, "no hay campo de firma"
    return anot["/AP"]["/N"].get_object()


@pytest.fixture
def logo_png(tmp_path):
    ruta = tmp_path / "logo.png"
    imagen = Image.new("RGBA", (120, 80), (30, 90, 160, 255))
    for x in range(40, 80):
        for y in range(20, 60):
            imagen.putpixel((x, y), (255, 255, 255, 0))
    imagen.save(ruta)
    return ruta


# --- Formato de fecha ---------------------------------------------------------


def test_fecha_del_sello_firma():
    dt = datetime.datetime(2026, 10, 27, 10, 38, 55, tzinfo=LIMA)
    assert fecha_sello("firma", dt) == "2026/10/27 10:38:55-0500"


def test_fecha_del_sello_visto_bueno():
    dt = datetime.datetime(2026, 10, 27, 10, 25, 52, tzinfo=LIMA)
    assert fecha_sello("visto_bueno", dt) == "27.10.2026 10:25:52 -05:00"


def test_tipo_desconocido_se_rechaza(tmp_path, pdf_multipagina, firmante):
    with pytest.raises(ErrorFirma):
        firmar_pdf(pdf_multipagina, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(tipo="otro"))


def test_motivos_predeterminados():
    assert firma_pdf.MOTIVOS_PREDETERMINADOS == {
        "firma": "Soy el autor del documento",
        "visto_bueno": "Doy V° B°",
    }


# --- Firma con cada tipo -----------------------------------------------------


@pytest.mark.parametrize("tipo", ["firma", "visto_bueno"])
def test_motivo_predeterminado_va_en_reason(tmp_path, pdf_multipagina, firmante, tipo):
    salida = tmp_path / "s.pdf"
    firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(tipo=tipo, paginas="ultima"))
    reader = _leer(salida)
    firmas = reader.embedded_signatures
    assert len(firmas) == 1
    assert firmas[0].sig_object["/Reason"] == firma_pdf.MOTIVOS_PREDETERMINADOS[tipo]
    assert _apariencia(reader, 2).data  # flujo de apariencia no vacío


@pytest.mark.parametrize("tipo", ["firma", "visto_bueno"])
def test_motivo_editado_va_en_reason_y_lugar_se_mantiene(tmp_path, pdf_multipagina, firmante, tipo):
    salida = tmp_path / "s.pdf"
    opciones = OpcionesFirma(tipo=tipo, motivo="Revisado 100%", lugar="Lima, Perú")
    resultado = firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA FIRMANTE FIR 12345678", opciones)
    assert resultado.paginas_selladas == [0, 1, 2]
    reader = _leer(salida)
    firma = reader.embedded_signatures[0]
    assert firma.sig_object["/Reason"] == "Revisado 100%"
    assert firma.sig_object["/Location"] == "Lima, Perú"


def test_firma_con_logo_incluye_la_imagen(tmp_path, pdf_multipagina, firmante, logo_png):
    salida = tmp_path / "s.pdf"
    firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(tipo="firma", logo=str(logo_png)))
    assert "/Image" in _subtipos_xobject(_apariencia(_leer(salida), 2))


def test_firma_sin_logo_no_incluye_imagen(tmp_path, pdf_multipagina, firmante):
    salida = tmp_path / "s.pdf"
    firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(tipo="firma"))
    assert "/Image" not in _subtipos_xobject(_apariencia(_leer(salida), 2))


def test_visto_bueno_ignora_el_logo(tmp_path, pdf_multipagina, firmante, logo_png):
    salida = tmp_path / "s.pdf"
    firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(tipo="visto_bueno", logo=str(logo_png)))
    assert "/Image" not in _subtipos_xobject(_apariencia(_leer(salida), 2))


def _opacidades_de_fondo(flujo) -> list[tuple[float, float]]:
    """(/CA, /ca) de los estados gráficos del fondo del sello (BackgroundGS)."""
    estados = flujo["/Resources"].get("/ExtGState", {})
    return [
        (float(gs.get_object()["/CA"]), float(gs.get_object()["/ca"]))
        for nombre, gs in estados.items()
        if str(nombre) == "/BackgroundGS"
    ]


@pytest.fixture
def check_png(tmp_path):
    ruta = tmp_path / "check.png"
    Image.new("RGB", (300, 300), (250, 250, 250)).save(ruta)
    return ruta


def test_visto_bueno_con_imagen_la_dibuja_opaca(tmp_path, pdf_multipagina, firmante, check_png):
    salida = tmp_path / "s.pdf"
    firmar_pdf(
        pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(tipo="visto_bueno", imagen_vb=str(check_png))
    )
    apariencia = _apariencia(_leer(salida), 2)
    assert "/Image" in _subtipos_xobject(apariencia)
    assert _opacidades_de_fondo(apariencia) == [(1.0, 1.0)]


def test_visto_bueno_sin_imagen_usa_la_marca_vectorial_opaca(tmp_path, pdf_multipagina, firmante):
    salida = tmp_path / "s.pdf"
    firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(tipo="visto_bueno"))
    apariencia = _apariencia(_leer(salida), 2)
    assert "/Image" not in _subtipos_xobject(apariencia)
    assert _opacidades_de_fondo(apariencia) == [(1.0, 1.0)]
    assert b" re " not in apariencia.data  # sin borde


def test_firma_ignora_la_imagen_de_visto_bueno(tmp_path, pdf_multipagina, firmante, check_png):
    salida = tmp_path / "s.pdf"
    firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(tipo="firma", imagen_vb=str(check_png)))
    assert "/Image" not in _subtipos_xobject(_apariencia(_leer(salida), 2))


def _se_superponen(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _dentro(interior, caja) -> bool:
    return caja[0] <= interior[0] and caja[1] <= interior[1] and interior[2] <= caja[2] and interior[3] <= caja[3]


@pytest.mark.parametrize("ancho, alto", [(150, 115), (120, 85), (80, 200), (300, 60), (40, 30)])
@pytest.mark.parametrize("dimensiones", [(1254, 1254), (300, 100), None])
def test_geometria_visto_bueno_imagen_arriba_y_texto_abajo(ancho, alto, dimensiones):
    g = firma_pdf.geometria_sello("visto_bueno", ancho, alto, dimensiones)
    caja = (0, 0, ancho, alto)
    assert _dentro(g.area_imagen, caja) and _dentro(g.texto, caja)
    assert _dentro(g.imagen, g.area_imagen)
    assert not _se_superponen(g.area_imagen, g.texto)
    assert g.area_imagen[1] >= g.texto[3]  # la imagen queda encima del texto
    assert g.area_imagen[3] - g.area_imagen[1] >= 0.45 * alto - 0.01  # ~50 % de la altura para la imagen


@pytest.mark.parametrize("dimensiones", [(437, 259), (100, 400)])
def test_geometria_firma_logo_a_la_izquierda_sin_superponer(dimensiones):
    g = firma_pdf.geometria_sello("firma", 260, 75, dimensiones)
    assert not _se_superponen(g.imagen, g.texto)
    assert g.imagen[2] <= g.texto[0]
    assert g.imagen[2] <= 260 * firma_pdf.PROPORCION_LOGO + 0.01


def test_geometria_firma_texto_pegado_al_logo():
    g = firma_pdf.geometria_sello("firma", 260, 75, (1266, 873))
    margen = max(2.0, 75 * 0.06)
    assert g.texto[0] - g.imagen[2] == pytest.approx(margen)


@pytest.mark.parametrize("fondo", [(255, 255, 255, 255), (255, 255, 255, 0), (250, 250, 250, 255)])
def test_recortar_margenes_quita_el_borde_blanco_o_transparente(fondo):
    imagen = Image.new("RGBA", (200, 100), fondo)
    for x in range(30, 170):
        for y in range(10, 80):
            imagen.putpixel((x, y), (200, 0, 0, 255))
    recortada = firma_pdf.recortar_margenes(imagen)
    assert recortada.size == (140, 70)


def test_recortar_margenes_imagen_en_blanco_no_cambia():
    imagen = Image.new("RGB", (50, 40), (255, 255, 255))
    assert firma_pdf.recortar_margenes(imagen).size == (50, 40)


def test_logo_inexistente_se_rechaza(tmp_path, pdf_multipagina, firmante):
    with pytest.raises(ErrorFirma, match="logo"):
        firmar_pdf(
            pdf_multipagina, tmp_path / "s.pdf", firmante, "PRUEBA",
            OpcionesFirma(tipo="firma", logo=str(tmp_path / "no-existe.png")),
        )


def test_visto_bueno_sobre_pdf_firmado_mantiene_la_primera(tmp_path, pdf_multipagina, firmante, material_prueba, logo_png):
    primera = tmp_path / "primera.pdf"
    segunda = tmp_path / "segunda.pdf"
    firmar_pdf(pdf_multipagina, primera, firmante, "PRUEBA", OpcionesFirma(tipo="firma", logo=str(logo_png)))
    resultado = firmar_pdf(primera, segunda, firmante, "PRUEBA", OpcionesFirma(tipo="visto_bueno"))

    assert resultado.modo == "solo_campo"
    reader = _leer(segunda)
    firmas = reader.embedded_signatures
    assert len(firmas) == 2
    assert firmas[1].sig_object["/Reason"] == "Doy V° B°"
    estado = validate_pdf_signature(firmas[0], ValidationContext(trust_roots=[material_prueba[1]]))
    assert estado.intact and estado.valid
    assert estado.docmdp_ok
    assert estado.modification_level <= ModificationLevel.FORM_FILLING


def test_texto_largo_se_reparte_en_varias_lineas():
    lineas = firma_pdf._ajustar_lineas("Firmado digitalmente por: NOMBRE MUY LARGO DEL TITULAR", 30, len)
    assert len(lineas) > 1
    assert all(len(linea) <= 30 for linea in lineas)
    assert " ".join(lineas) == "Firmado digitalmente por: NOMBRE MUY LARGO DEL TITULAR"
    # una palabra más ancha que la caja se corta por caracteres
    assert firma_pdf._ajustar_lineas("ABCDEFGHIJ", 4, len) == ["ABCD", "EFGH", "IJ"]


def test_firma_template_puts_signer_on_its_own_line():
    # Matches ReFirma: "Firmado digitalmente por:" then the signer on the next line.
    assert firma_pdf.PLANTILLAS_SELLO["firma"].startswith("Firmado digitalmente por:\n%(firmante)s\n")
