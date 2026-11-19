"""
Pruebas del modo «Firma real en cada página» (`paginas="cada_pagina"`): una
firma PAdES por página, cada una con su propio campo de UN solo widget.
"""
import io

import pytest
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext

import firma_pdf
import validacion
from conftest import A4, CARTA_HORIZONTAL, construir_pdf
from firma_pdf import ErrorFirma, FirmaCancelada, OpcionesFirma, firmar_pdf

VISTAS = [A4, CARTA_HORIZONTAL, A4]  # las de `pdf_multipagina`


def _leer(ruta):
    with open(ruta, "rb") as f:
        return PdfFileReader(io.BytesIO(f.read()), strict=False)


def _pagina_ref(reader, indice):
    return reader.find_page_for_modification(indice)[0]


def _anotaciones(reader, indice, subtipo):
    pagina = _pagina_ref(reader, indice).get_object()
    return [r.get_object() for r in pagina.get("/Annots", []) if r.get_object().get("/Subtype") == subtipo]


def _rect(anot):
    return tuple(float(v) for v in anot["/Rect"])


def _validas_con_app(ruta, material_prueba):
    """`validacion.validar_pdf`: todas íntegras y válidas; las anteriores con cambios permitidos."""
    resultado = validacion.validar_pdf(ruta, permitir_red=False, raices_extra=[material_prueba[1]])
    for firma in resultado:
        assert firma["integridad"] == "intacta" and firma["criptografia_valida"], firma
        assert firma["estado"] == "valida", firma
    for firma in resultado[:-1]:
        assert firma["modificaciones"] == "permitidas", firma
        assert firma["docmdp_ok"] is True, firma
    assert resultado[-1]["modificaciones"] == "ninguna"
    return resultado


def _campos_por_pagina(reader):
    """{índice de página: [campo de firma (widget único)]} de todas las firmas embebidas."""
    paginas = {}
    for firma in reader.embedded_signatures:
        campo = firma.sig_field
        assert "/Kids" not in campo and campo["/Subtype"] == "/Widget"  # un solo widget
        pagina = campo.raw_get("/P").reference
        indice = next(i for i in range(int(reader.root["/Pages"]["/Count"])) if _pagina_ref(reader, i).reference == pagina)
        paginas.setdefault(indice, []).append(campo)
    return paginas


def test_cada_pagina_es_un_modo_valido():
    assert "cada_pagina" in firma_pdf.PAGINAS_VALIDAS


def test_cada_pagina_pone_una_firma_real_en_cada_pagina(tmp_path, pdf_multipagina, firmante, material_prueba):
    salida = tmp_path / "salida.pdf"
    posicion = firma_pdf.PosicionRelativa(x=0.1, y=0.1)
    propia = firma_pdf.PosicionRelativa(x=0.5, y=0.5)
    resultado = firmar_pdf(
        pdf_multipagina,
        salida,
        firmante,
        "PRUEBA",
        OpcionesFirma(posicion=posicion, posiciones_paginas={1: propia}, paginas="cada_pagina"),
    )

    assert resultado.modo == "contenido"
    assert resultado.paginas_selladas == [0, 1, 2]
    assert resultado.campos == ["FirmaDNIe", "FirmaDNIe_2", "FirmaDNIe_3"]
    reader = _leer(salida)
    firmas = reader.embedded_signatures
    assert [f.field_name for f in firmas] == resultado.campos
    contexto = ValidationContext(trust_roots=[material_prueba[1]])
    for firma in firmas:
        assert firma.sig_object["/SubFilter"] == "/ETSI.CAdES.detached"
        assert firma.sig_object["/Reason"] == firma_pdf.MOTIVOS_PREDETERMINADOS["firma"]
        estado = validate_pdf_signature(firma, contexto, diff_policy=validacion.POLITICA_SIN_CERTIFICACION)
        assert estado.intact and estado.valid
    _validas_con_app(salida, material_prueba)

    por_pagina = _campos_por_pagina(reader)
    for indice, vista in enumerate(VISTAS):
        (campo,) = por_pagina[indice]
        assert campo["/T"] == resultado.campos[indice]
        esperado = firma_pdf.rect_sello(propia if indice == 1 else posicion, vista, "firma")
        assert _rect(campo) == pytest.approx(esperado, abs=0.01)
        assert "/N" in campo["/AP"]  # sello visible propio
        # sin los /Stamp + /Link del modo «todas»
        assert not _anotaciones(reader, indice, "/Stamp") and not _anotaciones(reader, indice, "/Link")


def test_cada_pagina_avisa_el_avance(tmp_path, pdf_multipagina, firmante):
    avance = []
    firmar_pdf(
        pdf_multipagina,
        tmp_path / "s.pdf",
        firmante,
        "PRUEBA",
        OpcionesFirma(paginas="cada_pagina"),
        progreso=lambda hechas, total: avance.append((hechas, total)),
    )
    assert avance == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_cancelar_no_deja_archivo(tmp_path, pdf_multipagina, firmante):
    salida = tmp_path / "salida.pdf"
    avance = []

    def progreso(hechas, total):
        avance.append(hechas)
        return hechas < 2  # False tras la segunda página: cancelar

    with pytest.raises(FirmaCancelada):
        firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"), progreso=progreso)
    assert avance == [0, 1, 2]
    assert not salida.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["entrada.pdf", "AppData"])  # sin temporales
    assert issubclass(FirmaCancelada, ErrorFirma)


def test_error_a_mitad_no_deja_archivo(tmp_path, pdf_multipagina, firmante, monkeypatch):
    salida = tmp_path / "salida.pdf"
    original = type(firmante)._firmar_resumen
    llamadas = []

    def _falla_en_la_segunda(self, resumen, algoritmo):
        llamadas.append(1)
        if len(llamadas) == 2:
            raise RuntimeError("la tarjeta falló")
        return original(self, resumen, algoritmo)

    monkeypatch.setattr(type(firmante), "_firmar_resumen", _falla_en_la_segunda)
    with pytest.raises(RuntimeError):
        firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"))
    assert len(llamadas) == 2  # se detiene en la primera falla: no sigue ni reintenta
    assert not salida.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["entrada.pdf", "AppData"])


def test_error_no_reemplaza_un_archivo_existente(tmp_path, pdf_multipagina, firmante):
    salida = tmp_path / "salida.pdf"
    salida.write_bytes(b"anterior")
    with pytest.raises(FirmaCancelada):
        firmar_pdf(
            pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"), progreso=lambda h, t: h < 1
        )
    assert salida.read_bytes() == b"anterior"


def test_segundo_firmante_cada_pagina_mantiene_validas_todas_las_anteriores(
    tmp_path, pdf_multipagina, firmante, material_prueba
):
    primera = tmp_path / "primera.pdf"
    segunda = tmp_path / "segunda.pdf"
    firmar_pdf(pdf_multipagina, primera, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"))
    resultado = firmar_pdf(
        primera, segunda, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina", tipo="visto_bueno")
    )

    assert resultado.firmas_previas == 3
    assert resultado.modo == "contenido"
    assert resultado.campos == ["FirmaDNIe_4", "FirmaDNIe_5", "FirmaDNIe_6"]  # sin chocar con los anteriores
    validas = _validas_con_app(segunda, material_prueba)
    assert len(validas) == 6
    por_pagina = _campos_por_pagina(_leer(segunda))
    for indice, vista in enumerate(VISTAS):
        primero, segundo = por_pagina[indice]
        assert segundo["/T"] == resultado.campos[indice]
        esperado = firma_pdf.rect_sello(firma_pdf.posicion_predeterminada("visto_bueno"), vista, "visto_bueno")
        assert _rect(segundo) == pytest.approx(esperado, abs=0.01)


def test_cada_pagina_despues_de_una_firma_todas(tmp_path, pdf_multipagina, firmante, material_prueba):
    primera = tmp_path / "primera.pdf"
    segunda = tmp_path / "segunda.pdf"
    firmar_pdf(pdf_multipagina, primera, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))
    resultado = firmar_pdf(primera, segunda, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"))

    assert resultado.campos == ["FirmaDNIe_2", "FirmaDNIe_3", "FirmaDNIe_4"]
    validas = _validas_con_app(segunda, material_prueba)
    assert len(validas) == 4
    reader = _leer(segunda)
    for indice in range(2):  # los /Stamp + /Link de la firma «todas» siguen ahí
        assert len(_anotaciones(reader, indice, "/Stamp")) == len(_anotaciones(reader, indice, "/Link")) == 1


def test_todas_despues_de_cada_pagina(tmp_path, pdf_multipagina, firmante, material_prueba):
    primera = tmp_path / "primera.pdf"
    segunda = tmp_path / "segunda.pdf"
    firmar_pdf(pdf_multipagina, primera, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"))
    firmar_pdf(primera, segunda, firmante, "PRUEBA", OpcionesFirma(paginas="todas", tipo="visto_bueno"))
    assert len(_validas_con_app(segunda, material_prueba)) == 4


def _certificar(tmp_path, entrada, firmante, permiso):
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    certificado = tmp_path / "certificado.pdf"
    with open(entrada, "rb") as inf:
        writer = IncrementalPdfFileWriter(inf)
        meta = signers.PdfSignatureMetadata(field_name="Autor", certify=True, docmdp_permissions=permiso)
        with open(certificado, "wb") as outf:
            signers.PdfSigner(meta, signer=firmante).sign_pdf(writer, output=outf)
    return certificado


@pytest.mark.parametrize("permiso", [fields.MDPPerm.FILL_FORMS, fields.MDPPerm.ANNOTATE])
def test_documento_certificado_recibe_un_campo_por_pagina(tmp_path, pdf_multipagina, firmante, material_prueba, permiso):
    certificado = _certificar(tmp_path, pdf_multipagina, firmante, permiso)
    salida = tmp_path / "s.pdf"
    resultado = firmar_pdf(certificado, salida, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"))

    assert resultado.modo == "solo_campo"
    assert resultado.paginas_selladas == [0, 1, 2]
    validas = validacion.validar_pdf(salida, permitir_red=False, raices_extra=[material_prueba[1]])
    assert len(validas) == 4
    for firma in validas:
        assert firma["integridad"] == "intacta" and firma["criptografia_valida"], firma
        assert firma["modificaciones"] in ("ninguna", "permitidas"), firma
        assert firma["docmdp_ok"] in (True, None), firma
    reader = _leer(salida)
    for indice in range(3):
        assert not _anotaciones(reader, indice, "/Stamp") and not _anotaciones(reader, indice, "/Link")
    por_pagina = _campos_por_pagina(reader)
    for indice in range(3):  # un campo nuevo por página (más el invisible «Autor» de la certificación)
        assert [c["/T"] for c in por_pagina[indice] if c["/T"] != "Autor"] == [resultado.campos[indice]]


def test_documento_certificado_sin_cambios_se_rechaza(tmp_path, pdf_multipagina, firmante):
    certificado = _certificar(tmp_path, pdf_multipagina, firmante, fields.MDPPerm.NO_CHANGES)
    with pytest.raises(ErrorFirma, match="no permite"):
        firmar_pdf(certificado, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"))
    assert not (tmp_path / "s.pdf").exists()


def test_una_sola_pagina(tmp_path, firmante, material_prueba):
    entrada = tmp_path / "una.pdf"
    entrada.write_bytes(construir_pdf([{"media_box": A4}]))
    salida = tmp_path / "s.pdf"
    resultado = firmar_pdf(entrada, salida, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina", tipo="visto_bueno"))
    assert resultado.paginas_selladas == [0] and resultado.campos == ["FirmaDNIe"]
    assert len(_validas_con_app(salida, material_prueba)) == 1


def test_logo_se_incrusta_una_sola_vez(tmp_path, pdf_multipagina, firmante, material_prueba):
    """Con "cada_pagina" el sello de cada firma apunta al MISMO XObject del logo (no se repite por página)."""
    from PIL import Image

    logo = tmp_path / "logo.png"
    Image.new("RGB", (300, 200), (200, 30, 30)).save(logo)
    salida = tmp_path / "s.pdf"
    firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina", logo=str(logo)))

    reader = _leer(salida)
    imagenes = set()
    for firma in reader.embedded_signatures:
        recursos = firma.sig_field["/AP"]["/N"]["/Resources"]["/XObject"]
        refs = [recursos.raw_get(nombre).reference for nombre in recursos]
        assert len(refs) == 1
        imagenes.update(refs)
    assert len(imagenes) == 1
    _validas_con_app(salida, material_prueba)
