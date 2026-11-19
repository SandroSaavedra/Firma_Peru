"""Pruebas de `validacion.py` con PDF firmados por el firmante simulado."""
import pytest
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

from conftest import A4, construir_pdf
from firma_pdf import OpcionesFirma, firmar_pdf
from validacion import validar_pdf


@pytest.fixture
def pdf_firmado(tmp_path, pdf_multipagina, firmante):
    salida = tmp_path / "firmado.pdf"
    firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma())
    return salida


def test_pdf_sin_firmas_devuelve_lista_vacia(tmp_path):
    ruta = tmp_path / "simple.pdf"
    ruta.write_bytes(construir_pdf([{"media_box": A4}]))
    assert validar_pdf(ruta, permitir_red=False) == []


def test_firma_intacta(pdf_firmado):
    (firma,) = validar_pdf(pdf_firmado, permitir_red=False)
    assert firma["campo"] == "FirmaDNIe"
    assert firma["firmante"] == "PRUEBA FIRMANTE FIR 12345678"
    assert firma["integridad"] == "intacta"
    assert firma["criptografia_valida"] is True
    assert firma["cubre_todo"] is True
    assert firma["modificaciones"] == "ninguna"
    assert firma["fecha_firma"]
    assert firma["subfilter"] == "/ETSI.CAdES.detached"
    # el certificado de prueba no está en el almacén raíz de Windows
    assert firma["confianza"] == "no_confiable"
    assert firma["estado"] == "advertencia"
    assert isinstance(firma["resumen"], str) and firma["resumen"]


def test_firma_confiable_con_raiz_extra(pdf_firmado, material_prueba):
    (firma,) = validar_pdf(pdf_firmado, permitir_red=False, raices_extra=[material_prueba[1]])
    assert firma["confianza"] == "confiable"
    assert firma["estado"] == "valida"


def test_copia_alterada_se_reporta_modificada(tmp_path, pdf_firmado):
    datos = pdf_firmado.read_bytes()
    assert datos.count(b"Pagina de prueba") >= 1
    alterado = tmp_path / "alterado.pdf"
    alterado.write_bytes(datos.replace(b"Pagina de prueba", b"Pagina de pruebX", 1))

    (firma,) = validar_pdf(alterado, permitir_red=False)
    assert firma["error"] is None  # detectado por la validación, no por una excepción
    assert firma["integridad"] == "modificada"
    assert firma["estado"] == "invalida"


def test_segunda_firma_reporta_modificaciones_permitidas(tmp_path, pdf_firmado, firmante):
    segunda = tmp_path / "segunda.pdf"
    firmar_pdf(pdf_firmado, segunda, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))

    primera, ultima = validar_pdf(segunda, permitir_red=False)
    assert primera["integridad"] == "intacta"
    assert primera["cubre_todo"] is False
    assert primera["modificaciones"] == "permitidas"
    assert ultima["modificaciones"] == "ninguna"


# --- Cambios posteriores: sellos permitidos, alteraciones no -------------------


@pytest.fixture
def pdf_todas(tmp_path, pdf_multipagina, firmante):
    """Firmado con «todas»: campo en la página 3, /Stamp + /Link en las páginas 1 y 2."""
    salida = tmp_path / "todas.pdf"
    firmar_pdf(pdf_multipagina, salida, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))
    return salida


def _revision(entrada, salida, cambio):
    """Agrega a `entrada` una revisión incremental (sin firmar) con `cambio(writer)`."""
    with open(entrada, "rb") as inf:
        writer = IncrementalPdfFileWriter(inf, strict=False)
        cambio(writer)
        with open(salida, "wb") as outf:
            writer.write(outf)
    return salida


def _anotacion(writer, indice, subtipo):
    pagina_ref = writer.find_page_for_modification(indice)[0]
    annots = pagina_ref.get_object()["/Annots"]
    (ref,) = [a for a in annots if a.get_object()["/Subtype"] == subtipo]
    return pagina_ref, annots, ref


def test_dos_firmas_todas_reportan_cambios_permitidos(tmp_path, pdf_todas, firmante):
    segunda = tmp_path / "segunda.pdf"
    firmar_pdf(pdf_todas, segunda, firmante, "PRUEBA", OpcionesFirma(paginas="todas", tipo="visto_bueno"))
    tercera = tmp_path / "tercera.pdf"
    firmar_pdf(segunda, tercera, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))

    resultado = validar_pdf(tercera, permitir_red=False)
    assert [f["integridad"] for f in resultado] == ["intacta"] * 3
    assert [f["modificaciones"] for f in resultado] == ["permitidas", "permitidas", "ninguna"]
    assert [f["nivel_modificacion"] for f in resultado[:2]] == ["ANNOTATIONS", "ANNOTATIONS"]
    assert all(f["estado"] != "invalida" for f in resultado)


def test_cambio_de_contenido_de_una_pagina_no_se_permite(tmp_path, pdf_todas):
    def cambiar_contenido(writer):
        pagina_ref = writer.find_page_for_modification(0)[0]
        flujo = generic.StreamObject(stream_data=b"BT /F1 18 Tf 72 720 Td (Pagina ALTERADA) Tj ET")
        pagina_ref.get_object()[generic.pdf_name("/Contents")] = writer.add_object(flujo)
        writer.update_container(pagina_ref.get_object())

    alterado = _revision(pdf_todas, tmp_path / "alterado.pdf", cambiar_contenido)
    (firma,) = validar_pdf(alterado, permitir_red=False)
    assert firma["integridad"] == "intacta"  # la revisión firmada no cambió...
    assert firma["modificaciones"] == "no_permitidas"  # ...pero lo agregado después no está permitido
    assert firma["estado"] == "invalida"


def test_quitar_un_sello_no_se_permite(tmp_path, pdf_todas):
    def quitar_sello(writer):
        pagina_ref, annots, sello = _anotacion(writer, 0, "/Stamp")
        annots.remove(sello)
        writer.update_container(pagina_ref.get_object())

    alterado = _revision(pdf_todas, tmp_path / "sin_sello.pdf", quitar_sello)
    (firma,) = validar_pdf(alterado, permitir_red=False)
    assert firma["modificaciones"] == "no_permitidas"
    assert firma["estado"] == "invalida"


def test_mover_un_sello_firmado_no_se_permite(tmp_path, pdf_todas):
    def mover_sello(writer):
        _, _, sello = _anotacion(writer, 0, "/Stamp")
        sello.get_object()[generic.pdf_name("/Rect")] = generic.ArrayObject(
            generic.FloatObject(v) for v in (10, 10, 170, 50)
        )
        writer.mark_update(sello)

    alterado = _revision(pdf_todas, tmp_path / "movido.pdf", mover_sello)
    (firma,) = validar_pdf(alterado, permitir_red=False)
    assert firma["modificaciones"] == "no_permitidas"


def test_cambiar_la_apariencia_firmada_no_se_permite(tmp_path, pdf_todas):
    def cambiar_apariencia(writer):
        _, _, sello = _anotacion(writer, 0, "/Stamp")
        apariencia = sello.get_object()["/AP"].raw_get("/N")
        flujo = apariencia.get_object()
        flujo.strip_filters()
        flujo._data = flujo._encoded_data = flujo.data + b"\nq 1 0 0 RG 0 0 160 40 re S Q"
        writer.mark_update(apariencia)

    alterado = _revision(pdf_todas, tmp_path / "apariencia.pdf", cambiar_apariencia)
    (firma,) = validar_pdf(alterado, permitir_red=False)
    assert firma["modificaciones"] == "no_permitidas"


def test_agregar_un_widget_que_no_es_de_firma_no_se_permite(tmp_path, pdf_todas):
    # Un /Widget nuevo sin campo de firma no es una «anotación» permitida.
    def agregar_widget(writer):
        pagina_ref = writer.find_page_for_modification(0)[0]
        widget = generic.DictionaryObject(
            {
                generic.pdf_name("/Type"): generic.pdf_name("/Annot"),
                generic.pdf_name("/Subtype"): generic.pdf_name("/Widget"),
                generic.pdf_name("/Rect"): generic.ArrayObject(generic.FloatObject(v) for v in (0, 0, 50, 50)),
            }
        )
        writer.register_annotation(pagina_ref, writer.add_object(widget))

    alterado = _revision(pdf_todas, tmp_path / "widget.pdf", agregar_widget)
    (firma,) = validar_pdf(alterado, permitir_red=False)
    assert firma["modificaciones"] == "no_permitidas"
