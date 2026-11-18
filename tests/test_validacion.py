"""Pruebas de `validacion.py` con PDF firmados por el firmante simulado."""
import pytest

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
