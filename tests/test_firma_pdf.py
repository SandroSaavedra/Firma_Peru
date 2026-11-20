"""Pruebas del núcleo de firma (`firma_pdf.py`) con el firmante CNG simulado."""
import pytest
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext

import firma_pdf
from conftest import A4, CARTA_HORIZONTAL, construir_pdf
from firma_pdf import ErrorFirma, OpcionesFirma, firmar_pdf

A3 = (0, 0, 842, 1191)


def test_script_estado_usa_las_frases_de_acrobat():
    script = firma_pdf.script_estado_firma("FirmaDNIe", 596)
    assert script.isascii()
    texto = script.encode("ascii").decode("unicode_escape")  # á -> á, \n -> salto
    for frase in (
        "La firma es VÁLIDA, firmada por ",
        "La validez de la firma es DESCONOCIDA, firmada por ",
        "La firma NO ES VÁLIDA, firmada por ",
        "- No ha habido modificaciones en el documento desde que se firmó.",
        "- La identidad del firmante es válida.",
        "- No se ha podido verificar la identidad del firmante.",
        "Fecha: ",
        "El campo de esta firma está en la página 597.",
    ):
        assert frase in texto, frase


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


def _anotaciones(reader, indice, subtipo):
    return [ref.get_object() for ref in _pagina(reader, indice).get("/Annots", []) if ref.get_object().get("/Subtype") == subtipo]


def _rect(anot):
    return tuple(float(v) for v in anot["/Rect"])


def _muestra(apariencia_sello, apariencia):
    """
    The /Stamp appearance is the field appearance itself or, on a page whose
    layout frame rotation differs from the field page's, a wrapper form
    XObject that draws it turned (`firma_pdf._apariencia_girada`).
    """
    if apariencia_sello.reference == apariencia:
        return True
    envoltura = apariencia_sello.get_object()
    sello = envoltura.get("/Resources", {}).get("/XObject", {}).raw_get("/Sello") if "/Matrix" in envoltura else None
    return sello is not None and sello.reference == apariencia


def _sellos_de(reader, indice, campo):
    """Anotaciones /Stamp de la página que muestran la apariencia del campo de firma `campo`."""
    apariencia = _ap_normal(campo).reference
    return [s for s in _anotaciones(reader, indice, "/Stamp") if _muestra(_ap_normal(s), apariencia)]


def _rect_del_sello(reader, indice):
    """/Rect del sello de la página: el widget del campo de firma o, en las demás páginas, la anotación /Stamp."""
    (anot,) = [w for w, _ in _widgets_de_firma(reader, indice)] or _anotaciones(reader, indice, "/Stamp")
    return _rect(anot)


def _comprobar_sello_y_enlace(reader, indice, campo, pagina_firma, rect_esperado):
    """
    La página tiene un /Stamp bloqueado con la apariencia del campo y, encima, un
    /Link que al hacer clic muestra (JavaScript de Acrobat) el estado de ESA firma.
    """
    (sello,) = _sellos_de(reader, indice, campo)
    assert int(sello["/F"]) == 4 | 64 | 128 | 512  # imprimible, solo lectura, bloqueado, contenido bloqueado
    assert sello.raw_get("/P").reference == reader.find_page_for_modification(indice)[0].reference
    assert _rect(sello) == pytest.approx(rect_esperado, abs=0.01)
    assert f"página {pagina_firma + 1}" in str(sello["/Contents"])
    llamada = f'getField("{campo["/T"]}")'  # el estado de la firma de ESTE sello
    enlaces = [e for e in _anotaciones(reader, indice, "/Link") if llamada in str(e["/A"].get("/JS", ""))]
    (enlace,) = enlaces
    assert _rect(enlace) == pytest.approx(rect_esperado, abs=0.01)
    assert int(enlace["/F"]) == 4
    assert [int(v) for v in enlace["/Border"]] == [0, 0, 0]
    accion = enlace["/A"]
    assert accion["/S"] == "/JavaScript"
    script = str(accion["/JS"])
    assert "signatureValidate()" in script and "signatureInfo()" in script and "app.alert(" in script
    assert f"gina {pagina_firma + 1}" in script  # «página N»: la tilde va como escape JavaScript
    assert script.isascii()
    return sello, enlace


def _un_solo_widget(campo):
    """El campo de firma es su propio (único) widget: sin /Kids."""
    assert "/Kids" not in campo
    assert campo["/Subtype"] == "/Widget"


# --- Posición relativa y tamaño fijo del sello ---------------------------------


def _posicion(x, y):
    return firma_pdf.PosicionRelativa(x=x, y=y)


def _tamano(rect):
    return round(rect[2] - rect[0], 2), round(rect[3] - rect[1], 2)


def test_tamanos_fijos_de_cada_tipo():
    assert firma_pdf.TAMANOS_SELLO == {"firma": (160, 40), "visto_bueno": (140, 110)}


@pytest.mark.parametrize("vista", [A4, CARTA_HORIZONTAL, A3, (10, 20, 300, 200)])
@pytest.mark.parametrize("tipo", ["firma", "visto_bueno"])
def test_sello_mide_lo_mismo_en_cualquier_pagina(tipo, vista):
    rect = firma_pdf.rect_sello(_posicion(0.2, 0.1), vista, tipo)
    assert _tamano(rect) == firma_pdf.TAMANOS_SELLO[tipo]


def test_posicion_relativa_se_mapea_a_puntos_pdf():
    assert firma_pdf.rect_sello(_posicion(0.5, 0.25), (0, 0, 600, 800), "firma") == pytest.approx((300, 200, 460, 240))


def test_posicion_relativa_respeta_origen_de_la_vista():
    assert firma_pdf.rect_sello(_posicion(0.5, 0.25), (10, 20, 610, 820), "firma") == pytest.approx((310, 220, 470, 260))


@pytest.mark.parametrize("tipo, esperado", [("firma", (400, 30, 560, 70)), ("visto_bueno", (420, 30, 560, 140))])
def test_posicion_predeterminada_abajo_a_la_derecha_en_a4(tipo, esperado):
    rect = firma_pdf.rect_sello(firma_pdf.posicion_predeterminada(tipo), A4, tipo)
    assert rect == pytest.approx(esperado, abs=0.01)


def test_sello_que_no_cabe_se_desplaza_dentro_de_la_pagina():
    assert firma_pdf.rect_sello(_posicion(0.95, 0.99), A4, "firma") == pytest.approx((435, 802, 595, 842))


@pytest.mark.parametrize("x, y", [(1.2, 0.1), (0.1, -0.5), (float("nan"), 0.1)])
def test_posicion_fuera_de_rango_se_rechaza(x, y):
    with pytest.raises(ErrorFirma):
        firma_pdf.rect_sello(_posicion(x, y), A4, "firma")


# --- Firma completa -------------------------------------------------------


def test_todas_sella_cada_pagina_y_una_sola_firma_pades(tmp_path, pdf_multipagina, firmante, material_prueba):
    salida = tmp_path / "salida.pdf"
    posicion = _posicion(0.1, 0.1)
    resultado = firmar_pdf(
        pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(posicion=posicion, paginas="todas")
    )

    assert resultado.modo == "contenido"
    assert resultado.paginas_selladas == [0, 1, 2]
    reader = _leer(salida)
    firmas = reader.embedded_signatures
    assert len(firmas) == 1
    assert firmas[0].sig_object["/SubFilter"] == "/ETSI.CAdES.detached"
    estado = validate_pdf_signature(firmas[0], ValidationContext(trust_roots=[material_prueba[1]]))
    assert estado.intact and estado.valid
    assert estado.modification_level in (None, ModificationLevel.NONE)

    # El campo firmado tiene UN solo widget, en la página de firma (clic = estado de la firma).
    campo_firmado = firmas[0].sig_field
    _un_solo_widget(campo_firmado)
    ((widget, campo),) = _widgets_de_firma(reader, 2)
    assert campo["/T"] == resultado.campo and widget.raw_get("/P").reference == reader.find_page_for_modification(2)[0].reference
    assert _rect(widget) == pytest.approx(firma_pdf.rect_sello(posicion, A4, "firma"), abs=0.01)
    assert not _anotaciones(reader, 2, "/Stamp") and not _anotaciones(reader, 2, "/Link")
    # Las demás páginas: un /Stamp bloqueado con la MISMA apariencia (XObject compartido)
    # y un /Link del mismo tamaño que lleva a la página de firma.
    # The landscape Letter page (displayed landscape) gets the vertical stamp: same size, turned.
    for indice, (vista, tamano) in enumerate([(A4, (160, 40)), (CARTA_HORIZONTAL, (40, 160))]):
        assert _widgets_de_firma(reader, indice) == []
        esperado = firma_pdf.rect_sello(posicion, vista, "firma", rotacion=0)
        sello, _ = _comprobar_sello_y_enlace(reader, indice, campo_firmado, 2, esperado)
        assert _tamano(_rect(sello)) == tamano  # mismo tamaño en A4 y en carta
        assert len(_anotaciones(reader, indice, "/Stamp")) == len(_anotaciones(reader, indice, "/Link")) == 1
    for indice in range(3):
        # el sello no se dibuja como contenido de la página
        assert not _tiene_sello_de_contenido(reader, indice)


def test_todas_segunda_firma_mantiene_valida_la_primera(tmp_path, pdf_multipagina, firmante, material_prueba):
    primera = tmp_path / "primera.pdf"
    segunda = tmp_path / "segunda.pdf"
    firmar_pdf(pdf_multipagina, primera, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))
    resultado = firmar_pdf(
        primera, segunda, firmante, "PRUEBA", OpcionesFirma(paginas="todas", tipo="visto_bueno")
    )

    assert resultado.modo == "contenido"
    assert resultado.paginas_selladas == [0, 1, 2]
    reader = _leer(segunda)
    firmas = reader.embedded_signatures
    assert len(firmas) == 2
    _comprobar_firmas_validas(segunda, material_prueba)
    # el visto bueno también sella cada página: su campo en la última y un /Stamp en las demás
    for firma in firmas:
        _un_solo_widget(firma.sig_field)
    assert sorted(c["/T"] for _, c in _widgets_de_firma(reader, 2)) == ["FirmaDNIe", resultado.campo]
    for indice, vista in enumerate([A4, CARTA_HORIZONTAL]):
        assert _widgets_de_firma(reader, indice) == []
        assert len(_anotaciones(reader, indice, "/Stamp")) == 2
        esperado = firma_pdf.rect_sello(
            firma_pdf.posicion_predeterminada("visto_bueno"), vista, "visto_bueno", rotacion=0
        )
        sello, _ = _comprobar_sello_y_enlace(reader, indice, firmas[1].sig_field, 2, esperado)
        assert sorted(_tamano(_rect(sello))) == sorted(firma_pdf.TAMANOS_SELLO["visto_bueno"])


def _comprobar_firmas_validas(ruta, material_prueba):
    """
    Con la validación de la aplicación (`validacion.validar_pdf`): todas las firmas
    íntegras y válidas; las anteriores con cambios permitidos y la última sin cambios.
    """
    import validacion

    resultado = validacion.validar_pdf(ruta, permitir_red=False, raices_extra=[material_prueba[1]])
    for firma in resultado[:-1]:
        assert firma["integridad"] == "intacta" and firma["criptografia_valida"]
        assert firma["modificaciones"] == "permitidas", firma
        assert firma["docmdp_ok"] is True
        assert firma["estado"] == "valida"
    assert resultado[-1]["modificaciones"] == "ninguna" and resultado[-1]["estado"] == "valida"
    return resultado


def test_ultima_solo_pone_el_campo_de_firma(tmp_path, pdf_multipagina, firmante):
    salida = tmp_path / "salida.pdf"
    resultado = firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(paginas="ultima"))

    assert resultado.paginas_selladas == [2]
    reader = _leer(salida)
    assert len(reader.embedded_signatures) == 1
    assert not _tiene_sello_de_contenido(reader, 0)
    assert not _tiene_sello_de_contenido(reader, 1)
    assert _rect_del_campo(reader, 2) is not None


def test_campo_en_otra_pagina_usa_su_vista(tmp_path, pdf_multipagina, firmante):
    salida = tmp_path / "salida.pdf"
    posicion = _posicion(0.5, 0.5)
    resultado = firmar_pdf(
        pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(posicion=posicion, pagina_firma=1)
    )

    assert resultado.pagina_firma == 1
    reader = _leer(salida)
    rect = firma_pdf.rect_sello
    assert _rect_del_campo(reader, 1) == pytest.approx(rect(posicion, CARTA_HORIZONTAL, "firma", rotacion=0), abs=0.01)
    campo = reader.embedded_signatures[0].sig_field
    _comprobar_sello_y_enlace(reader, 0, campo, 1, rect(posicion, A4, "firma"))
    _comprobar_sello_y_enlace(reader, 2, campo, 1, rect(posicion, A4, "firma"))
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
    # Un PDF ya firmado (sin certificación) se sella igual que uno sin firmas.
    assert resultado.modo == "contenido"
    assert resultado.paginas_selladas == [0, 1, 2]
    assert resultado.campo != "FirmaDNIe"  # nombre único

    reader = _leer(segunda)
    firmas = reader.embedded_signatures
    assert len(firmas) == 2
    primera_v, segunda_v = _comprobar_firmas_validas(segunda, material_prueba)
    # Después de la primera solo se agregaron un campo de firma y anotaciones (sellos).
    assert primera_v["nivel_modificacion"] == "ANNOTATIONS"
    assert segunda_v["nivel_modificacion"] in (None, "NONE")

    # Cada firma: un solo widget en la página de firma y un sello + enlace propio en las demás.
    nuevo = firmas[1].sig_field
    _un_solo_widget(nuevo)
    assert sorted(c["/T"] for _, c in _widgets_de_firma(reader, 2)) == ["FirmaDNIe", resultado.campo]
    for indice, vista in enumerate([A4, CARTA_HORIZONTAL]):
        esperado = firma_pdf.rect_sello(firma_pdf.posicion_predeterminada("firma"), vista, "firma", rotacion=0)
        for firma in firmas:
            _comprobar_sello_y_enlace(reader, indice, firma.sig_field, 2, esperado)
        assert len(_anotaciones(reader, indice, "/Stamp")) == len(_anotaciones(reader, indice, "/Link")) == 2


def test_tercera_firma_mantiene_validas_las_anteriores(tmp_path, pdf_multipagina, firmante, material_prueba):
    rutas = [pdf_multipagina] + [tmp_path / f"{n}.pdf" for n in range(1, 4)]
    tipos = ["firma", "visto_bueno", "firma"]
    for entrada, salida, tipo in zip(rutas, rutas[1:], tipos):
        firmar_pdf(entrada, salida, firmante, "PRUEBA", OpcionesFirma(paginas="todas", tipo=tipo))

    reader = _leer(rutas[-1])
    firmas = reader.embedded_signatures
    assert len(firmas) == 3
    _comprobar_firmas_validas(rutas[-1], material_prueba)
    for firma in firmas:
        _un_solo_widget(firma.sig_field)
    assert len(_widgets_de_firma(reader, 2)) == 3
    for indice in range(2):
        assert _widgets_de_firma(reader, indice) == []
        for firma in firmas:
            assert len(_sellos_de(reader, indice, firma.sig_field)) == 1


def test_ultima_en_pdf_ya_firmado_solo_pone_el_campo(tmp_path, pdf_multipagina, firmante):
    primera = tmp_path / "primera.pdf"
    segunda = tmp_path / "segunda.pdf"
    firmar_pdf(pdf_multipagina, primera, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))
    resultado = firmar_pdf(primera, segunda, firmante, "PRUEBA", OpcionesFirma(paginas="ultima"))

    assert resultado.paginas_selladas == [2]
    reader = _leer(segunda)
    assert _widgets_de_firma(reader, 0) == []
    assert len(_anotaciones(reader, 0, "/Stamp")) == 1  # solo el sello de la primera firma
    assert len(_widgets_de_firma(reader, 2)) == 2


@pytest.mark.parametrize("permiso", [fields.MDPPerm.FILL_FORMS, fields.MDPPerm.ANNOTATE])
def test_documento_certificado_con_cambios_permitidos_solo_pone_el_campo(
    tmp_path, pdf_multipagina, firmante, permiso
):
    # Con certificación (DocMDP 2 o 3) se mantiene el campo solo en su página.
    certificado = _certificar(tmp_path, pdf_multipagina, firmante, permiso)
    resultado = firmar_pdf(certificado, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(paginas="todas"))

    assert resultado.modo == "solo_campo"
    assert resultado.paginas_selladas == [2]
    reader = _leer(tmp_path / "s.pdf")
    assert len(reader.embedded_signatures) == 2
    for indice, esperados in enumerate([0, 0, 1]):
        nuevos = [w for w, c in _widgets_de_firma(reader, indice) if c["/T"] == resultado.campo]
        assert len(nuevos) == esperados
        # sin anotaciones extra: DocMDP 2 no permite anotaciones
        assert not _anotaciones(reader, indice, "/Stamp") and not _anotaciones(reader, indice, "/Link")


def _certificar(tmp_path, entrada, firmante, permiso):
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    certificado = tmp_path / "certificado.pdf"
    with open(entrada, "rb") as inf:
        writer = IncrementalPdfFileWriter(inf)
        meta = signers.PdfSignatureMetadata(field_name="Autor", certify=True, docmdp_permissions=permiso)
        with open(certificado, "wb") as outf:
            signers.PdfSigner(meta, signer=firmante).sign_pdf(writer, output=outf)
    return certificado


def test_documento_certificado_sin_cambios_se_rechaza(tmp_path, pdf_multipagina, firmante):
    certificado = _certificar(tmp_path, pdf_multipagina, firmante, fields.MDPPerm.NO_CHANGES)
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


def test_pagina_rotada_sigue_su_orientacion_mostrada(tmp_path, firmante):
    """An A4 page with /Rotate 90 is displayed landscape: the position is in its layout frame (θ = 180)."""
    entrada = tmp_path / "rotada.pdf"
    entrada.write_bytes(construir_pdf([{"media_box": A4, "rotacion": 90}]))
    posicion = _posicion(0.1, 0.2)
    firmar_pdf(entrada, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(posicion=posicion))
    assert firma_pdf.giro_de_sello(A4, 90) == 180
    esperado = firma_pdf.rect_sello(posicion, A4, "firma", rotacion=90)
    assert _rect_del_campo(_leer(tmp_path / "s.pdf"), 0) == pytest.approx(esperado, abs=0.01)


@pytest.mark.parametrize("tipo", ["firma", "visto_bueno"])
def test_sello_tiene_tamano_fijo_en_paginas_de_distinto_tamano(tmp_path, firmante, tipo):
    entrada = tmp_path / "mixto.pdf"
    entrada.write_bytes(construir_pdf([{"media_box": A3}, {"media_box": CARTA_HORIZONTAL}, {"media_box": A4}]))
    salida = tmp_path / "s.pdf"
    firmar_pdf(entrada, salida, firmante, "PRUEBA", OpcionesFirma(tipo=tipo, paginas="todas"))
    reader = _leer(salida)
    ancho, alto = firma_pdf.TAMANOS_SELLO[tipo]
    # the landscape Letter page (displayed landscape) gets the same stamp turned: vertical
    for indice, tamano in enumerate([(ancho, alto), (alto, ancho), (ancho, alto)]):
        assert _tamano(_rect_del_sello(reader, indice)) == tamano


# --- Posición propia por página -----------------------------------------------


def test_posicion_propia_de_una_pagina_solo_cambia_esa_pagina(tmp_path, pdf_multipagina, firmante, material_prueba):
    salida = tmp_path / "salida.pdf"
    documento = _posicion(0.1, 0.1)
    propia = _posicion(0.6, 0.7)
    opciones = OpcionesFirma(posicion=documento, paginas="todas", posiciones_paginas={1: propia})
    resultado = firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", opciones)

    assert resultado.paginas_selladas == [0, 1, 2]
    reader = _leer(salida)
    firmas = reader.embedded_signatures
    assert len(firmas) == 1  # una sola firma: los sellos de las demás páginas son anotaciones de esa firma
    estado = validate_pdf_signature(firmas[0], ValidationContext(trust_roots=[material_prueba[1]]))
    assert estado.intact and estado.valid
    assert estado.modification_level in (None, ModificationLevel.NONE)

    esperados = [
        firma_pdf.rect_sello(documento, A4, "firma", rotacion=0),
        firma_pdf.rect_sello(propia, CARTA_HORIZONTAL, "firma", rotacion=0),
        firma_pdf.rect_sello(documento, A4, "firma", rotacion=0),
    ]
    for indice, (esperado, tamano) in enumerate(zip(esperados, [(160, 40), (40, 160), (160, 40)])):
        rect = _rect_del_sello(reader, indice)
        assert rect == pytest.approx(esperado, abs=0.01)
        assert _tamano(rect) == tamano
    # El sello mide lo mismo en cada página: la apariencia se sigue compartiendo
    # (en la carta horizontal, a través de una envoltura que la gira).
    campo = firmas[0].sig_field
    for indice in range(2):
        _comprobar_sello_y_enlace(reader, indice, campo, 2, esperados[indice])


@pytest.mark.parametrize("paginas", ["todas", "ultima"])
def test_pagina_del_campo_usa_su_posicion_propia(tmp_path, pdf_multipagina, firmante, paginas):
    salida = tmp_path / "salida.pdf"
    documento = _posicion(0.1, 0.1)
    propia = _posicion(0.3, 0.8)
    opciones = OpcionesFirma(posicion=documento, paginas=paginas, posiciones_paginas={2: propia})
    resultado = firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", opciones)

    assert resultado.pagina_firma == 2
    reader = _leer(salida)
    rect = firma_pdf.rect_sello
    assert _rect_del_campo(reader, 2) == pytest.approx(rect(propia, A4, "firma"), abs=0.01)
    if paginas == "todas":
        assert _rect_del_sello(reader, 0) == pytest.approx(rect(documento, A4, "firma"), abs=0.01)


def test_posiciones_propias_en_pdf_ya_firmado_se_respetan(tmp_path, pdf_multipagina, firmante, material_prueba):
    primera = tmp_path / "primera.pdf"
    segunda = tmp_path / "segunda.pdf"
    opciones1 = OpcionesFirma(paginas="todas", posicion=_posicion(0.1, 0.1), posiciones_paginas={1: _posicion(0.5, 0.5)})
    firmar_pdf(pdf_multipagina, primera, firmante, "PRUEBA", opciones1)
    documento = _posicion(0.6, 0.1)
    propias = {0: _posicion(0.2, 0.8), 2: _posicion(0.3, 0.4)}
    opciones = OpcionesFirma(paginas="todas", pagina_firma=0, posicion=documento, posiciones_paginas=propias)
    resultado = firmar_pdf(primera, segunda, firmante, "PRUEBA", opciones)

    assert resultado.modo == "contenido"
    assert resultado.pagina_firma == 0 and resultado.paginas_selladas == [0, 1, 2]
    reader = _leer(segunda)
    vistas = [A4, CARTA_HORIZONTAL, A4]
    campo = reader.embedded_signatures[1].sig_field
    (widget,) = [w for w, c in _widgets_de_firma(reader, 0) if c["/T"] == resultado.campo]
    assert _rect(widget) == pytest.approx(firma_pdf.rect_sello(propias[0], A4, "firma"), abs=0.01)
    for indice, vista in enumerate(vistas[1:], start=1):
        esperado = firma_pdf.rect_sello(propias.get(indice, documento), vista, "firma", rotacion=0)
        _comprobar_sello_y_enlace(reader, indice, campo, 0, esperado)
    _comprobar_firmas_validas(segunda, material_prueba)


@pytest.mark.parametrize(
    "posiciones",
    [
        {3: firma_pdf.PosicionRelativa(0.1, 0.1)},  # página inexistente (el PDF tiene 3)
        {-1: firma_pdf.PosicionRelativa(0.1, 0.1)},
        {"0": firma_pdf.PosicionRelativa(0.1, 0.1)},
        {0: firma_pdf.PosicionRelativa(float("nan"), 0.1)},
        {0: firma_pdf.PosicionRelativa(0.1, 1.5)},
        {0: (0.1, 0.1)},
    ],
)
def test_posiciones_por_pagina_invalidas_se_rechazan(tmp_path, pdf_multipagina, firmante, posiciones):
    salida = tmp_path / "s.pdf"
    with pytest.raises(ErrorFirma):
        firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(posiciones_paginas=posiciones))
    assert not salida.exists()
