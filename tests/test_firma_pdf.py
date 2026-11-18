"""Pruebas del núcleo de firma (`firma_pdf.py`) con el firmante CNG simulado."""
import pytest
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext

import firma_pdf
from conftest import A4, CARTA_HORIZONTAL, construir_pdf
from firma_pdf import CajaRelativa, ErrorFirma, OpcionesFirma, caja_absoluta, firmar_pdf


def _leer(ruta):
    return PdfFileReader(open(ruta, "rb"), strict=False)


def _pagina(reader, indice):
    return reader.find_page_for_modification(indice)[0].get_object()


def _tiene_sello_de_contenido(reader, indice) -> bool:
    xobjects = _pagina(reader, indice)["/Resources"].get("/XObject", {})
    return any(str(nombre).startswith("/Stamp") for nombre in xobjects)


def _rect_del_campo(reader, indice):
    for anot in _pagina(reader, indice).get("/Annots", []):
        anot = anot.get_object()
        if anot.get("/FT") == "/Sig" or "/Parent" in anot and anot["/Parent"].get("/FT") == "/Sig":
            return tuple(float(v) for v in anot["/Rect"])
    return None


def _widgets_de_firma(reader, indice):
    """(widget, campo) de cada widget de firma de la página; el campo es el propio widget o su /Parent."""
    resultado = []
    for ref in _pagina(reader, indice).get("/Annots", []):
        anot = ref.get_object()
        if anot.get("/Subtype") != "/Widget":
            continue
        campo = anot if "/T" in anot else anot["/Parent"]
        if campo.get("/FT") == "/Sig":
            resultado.append((anot, campo))
    return resultado


def _ap_normal(widget):
    return widget["/AP"].raw_get("/N")


# --- Conversión de caja relativa a absoluta --------------------------------


def test_caja_relativa_se_mapea_a_puntos_pdf():
    caja = CajaRelativa(x=0.5, y=0.25, ancho=0.25, alto=0.1)
    assert caja_absoluta(caja, (0, 0, 600, 800)) == pytest.approx((300, 200, 450, 280))


def test_caja_relativa_respeta_origen_de_la_vista():
    caja = CajaRelativa(x=0.5, y=0.25, ancho=0.25, alto=0.1)
    assert caja_absoluta(caja, (10, 20, 610, 820)) == pytest.approx((310, 220, 460, 300))


def test_caja_predeterminada_equivale_a_la_antigua_en_a4():
    x1, y1, x2, y2 = caja_absoluta(OpcionesFirma().caja, A4)
    assert (x1, y1, x2, y2) == pytest.approx((400, 30, 560, 75), abs=0.5)


def test_caja_fuera_de_rango_se_rechaza():
    with pytest.raises(ErrorFirma):
        caja_absoluta(CajaRelativa(x=0.9, y=0.1, ancho=0.3, alto=0.1), A4)


# --- Firma completa -------------------------------------------------------


def test_todas_sella_cada_pagina_y_una_sola_firma_pades(tmp_path, pdf_multipagina, firmante, material_prueba):
    salida = tmp_path / "salida.pdf"
    caja = CajaRelativa(x=0.1, y=0.1, ancho=0.3, alto=0.08)
    resultado = firmar_pdf(
        pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(caja=caja, paginas="todas")
    )

    assert resultado.modo == "contenido"
    assert resultado.paginas_selladas == [0, 1, 2]
    reader = _leer(salida)
    firmas = reader.embedded_signatures
    assert len(firmas) == 1
    assert firmas[0].sig_object["/SubFilter"] == "/ETSI.CAdES.detached"
    estado = validate_pdf_signature(firmas[0], ValidationContext(trust_roots=[material_prueba[1]]))
    assert estado.intact and estado.valid

    # Cada página lleva exactamente un widget del ÚNICO campo firmado (clic = estado de la firma).
    campo_firmado = firmas[0].sig_field
    vistas = [A4, CARTA_HORIZONTAL, A4]
    apariencias = []
    for indice, vista in enumerate(vistas):
        (widget, campo), = _widgets_de_firma(reader, indice)
        assert campo["/T"] == resultado.campo
        assert campo.raw_get("/V").reference == campo_firmado.raw_get("/V").reference
        assert widget.raw_get("/P").reference == reader.find_page_for_modification(indice)[0].reference
        assert int(widget["/F"]) & 4  # imprimible
        assert tuple(float(v) for v in widget["/Rect"]) == pytest.approx(caja_absoluta(caja, vista), abs=0.01)
        apariencias.append(_ap_normal(widget))
        # el sello ya no se dibuja como contenido de la página
        assert not _tiene_sello_de_contenido(reader, indice)
    assert len(campo_firmado["/Kids"]) == 3
    # páginas del mismo tamaño comparten el mismo XObject de apariencia
    assert apariencias[0].reference == apariencias[2].reference
    assert apariencias[1].reference != apariencias[0].reference


def test_todas_segunda_firma_mantiene_valida_la_primera(tmp_path, pdf_multipagina, firmante, material_prueba):
    primera = tmp_path / "primera.pdf"
    segunda = tmp_path / "segunda.pdf"
    firmar_pdf(pdf_multipagina, primera, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))
    resultado = firmar_pdf(
        primera, segunda, firmante, "PRUEBA", OpcionesFirma(paginas="todas", tipo="visto_bueno")
    )

    assert resultado.modo == "solo_campo"
    reader = _leer(segunda)
    firmas = reader.embedded_signatures
    assert len(firmas) == 2
    contexto = ValidationContext(trust_roots=[material_prueba[1]])
    for firma in firmas:
        estado = validate_pdf_signature(firma, contexto)
        assert estado.intact and estado.valid
        assert estado.docmdp_ok
        assert estado.modification_level <= ModificationLevel.FORM_FILLING
    # la segunda firma solo agrega su propio widget en la página de firma
    assert len(_widgets_de_firma(reader, 0)) == 1
    assert len(_widgets_de_firma(reader, 2)) == 2


def test_ultima_solo_pone_el_campo_de_firma(tmp_path, pdf_multipagina, firmante):
    salida = tmp_path / "salida.pdf"
    resultado = firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(paginas="ultima"))

    assert resultado.paginas_selladas == [2]
    reader = _leer(salida)
    assert len(reader.embedded_signatures) == 1
    assert not _tiene_sello_de_contenido(reader, 0)
    assert not _tiene_sello_de_contenido(reader, 1)
    assert _rect_del_campo(reader, 2) is not None


def test_campo_en_otra_pagina_usa_su_tamano(tmp_path, pdf_multipagina, firmante):
    salida = tmp_path / "salida.pdf"
    caja = CajaRelativa(x=0.5, y=0.5, ancho=0.25, alto=0.1)
    resultado = firmar_pdf(
        pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(caja=caja, pagina_firma=1)
    )

    assert resultado.pagina_firma == 1
    reader = _leer(salida)
    assert _rect_del_campo(reader, 1) == pytest.approx(caja_absoluta(caja, CARTA_HORIZONTAL), abs=0.01)
    assert _rect_del_campo(reader, 0) == pytest.approx(caja_absoluta(caja, A4), abs=0.01)
    assert _rect_del_campo(reader, 2) == pytest.approx(caja_absoluta(caja, A4), abs=0.01)
    assert not _tiene_sello_de_contenido(reader, 0)


def test_plantilla_invalida_se_rechaza(tmp_path, pdf_multipagina, firmante):
    with pytest.raises(ErrorFirma):
        firmar_pdf(
            pdf_multipagina,
            tmp_path / "s.pdf",
            firmante,
            "PRUEBA",
            OpcionesFirma(texto_sello="Firmado por %(desconocido)s"),
        )


def test_no_sobrescribe_la_entrada(pdf_multipagina, firmante):
    with pytest.raises(ErrorFirma):
        firmar_pdf(pdf_multipagina, pdf_multipagina, firmante, "PRUEBA", OpcionesFirma())


# --- PDF que ya tiene firmas ------------------------------------------------


def test_segunda_firma_no_invalida_la_primera(tmp_path, pdf_multipagina, firmante, material_prueba):
    primera = tmp_path / "primera.pdf"
    segunda = tmp_path / "segunda.pdf"
    firmar_pdf(pdf_multipagina, primera, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))
    resultado = firmar_pdf(primera, segunda, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))

    assert resultado.firmas_previas == 1
    assert resultado.modo == "solo_campo"
    assert resultado.paginas_selladas == [2]
    assert resultado.campo != "FirmaDNIe"  # nombre único

    reader = _leer(segunda)
    firmas = reader.embedded_signatures
    assert len(firmas) == 2
    contexto = ValidationContext(trust_roots=[material_prueba[1]])
    estado = validate_pdf_signature(firmas[0], contexto)
    assert estado.intact and estado.valid
    assert estado.docmdp_ok
    assert estado.modification_level <= ModificationLevel.FORM_FILLING


def test_documento_certificado_sin_cambios_se_rechaza(tmp_path, pdf_multipagina, firmante):
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    certificado = tmp_path / "certificado.pdf"
    with open(pdf_multipagina, "rb") as inf:
        writer = IncrementalPdfFileWriter(inf)
        meta = signers.PdfSignatureMetadata(
            field_name="Autor", certify=True, docmdp_permissions=fields.MDPPerm.NO_CHANGES
        )
        with open(certificado, "wb") as outf:
            signers.PdfSigner(meta, signer=firmante).sign_pdf(writer, output=outf)

    with pytest.raises(ErrorFirma, match="no permite"):
        firmar_pdf(certificado, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma())


def test_analizar_pdf_reporta_paginas_y_firmas(tmp_path, pdf_multipagina, firmante):
    info = firma_pdf.analizar_pdf(pdf_multipagina)
    assert info.total_paginas == 3
    assert info.firmas == []
    salida = tmp_path / "s.pdf"
    firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma())
    info = firma_pdf.analizar_pdf(salida)
    assert info.firmas == ["FirmaDNIe"]
    assert info.docmdp is None


def test_pagina_rotada_usa_coordenadas_sin_rotar(tmp_path, firmante):
    entrada = tmp_path / "rotada.pdf"
    entrada.write_bytes(construir_pdf([{"media_box": A4, "rotacion": 90}]))
    caja = CajaRelativa(x=0.1, y=0.2, ancho=0.3, alto=0.1)
    firmar_pdf(entrada, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(caja=caja))
    assert _rect_del_campo(_leer(tmp_path / "s.pdf"), 0) == pytest.approx(caja_absoluta(caja, A4), abs=0.01)
