"""
Registro de diagnóstico (`registro.py`): archivo rotativo, cabecera de inicio,
excepciones no capturadas y tolerancia a una carpeta sin permisos.
"""
import logging
import logging.handlers
import re
import sys
import threading
from pathlib import Path

import pytest

import registro
import version

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def restaurar_registro():
    """Deja el logger raíz y los excepthooks como estaban (no afecta a otras pruebas)."""
    raiz = logging.getLogger()
    handlers, nivel = list(raiz.handlers), raiz.level
    hook_sys, hook_hilos = sys.excepthook, threading.excepthook
    yield
    for h in list(raiz.handlers):
        if h not in handlers:
            raiz.removeHandler(h)
            h.close()
    raiz.setLevel(nivel)
    sys.excepthook, threading.excepthook = hook_sys, hook_hilos


def _texto(ruta: Path) -> str:
    for h in logging.getLogger().handlers:
        h.flush()
    return ruta.read_text(encoding="utf-8")


def _propios() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if getattr(h, registro.MARCA, False)]


def test_crea_el_archivo_con_la_cabecera(tmp_path):
    ruta = registro.configurar_registro(tmp_path / "registros")
    assert ruta == tmp_path / "registros" / "firmador.log"
    texto = _texto(ruta)
    assert version.VERSION in texto
    assert "empaquetado" in texto and "pyHanko" in texto and "Python" in texto
    # Formato: fecha, nivel, [hilo], logger: mensaje
    assert re.search(r"^\d{4}-\d{2}-\d{2} [\d:,]+ INFO \[MainThread\] firmador\.registro: ", texto, re.M)


def test_el_archivo_rota_a_1_mb_con_5_copias(tmp_path):
    registro.configurar_registro(tmp_path)
    archivo = [h for h in _propios() if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(archivo) == 1
    assert archivo[0].maxBytes == 1_000_000 and archivo[0].backupCount == 5
    assert archivo[0].encoding == "utf-8"


def test_dos_llamadas_no_duplican_handlers(tmp_path):
    registro.configurar_registro(tmp_path)
    antes = len(logging.getLogger().handlers)
    ruta = registro.configurar_registro(tmp_path)
    assert len(logging.getLogger().handlers) == antes
    logging.getLogger("firmador").info("línea única ñ")
    assert _texto(ruta).count("línea única ñ") == 1


def test_carpeta_no_escribible_devuelve_none_sin_fallar(tmp_path):
    bloqueo = tmp_path / "soy-un-archivo"
    bloqueo.write_text("x")
    assert registro.configurar_registro(bloqueo / "registros") is None
    assert not any(isinstance(h, logging.FileHandler) for h in _propios())
    logging.getLogger("firmador").info("sigue funcionando")  # no lanza


def test_excepcion_no_capturada_queda_en_el_registro(tmp_path):
    ruta = registro.configurar_registro(tmp_path)
    try:
        raise ValueError("falla de prueba en el hilo principal")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    texto = _texto(ruta)
    assert "Traceback" in texto and "ValueError: falla de prueba en el hilo principal" in texto


def test_excepcion_no_capturada_en_un_hilo_queda_en_el_registro(tmp_path):
    ruta = registro.configurar_registro(tmp_path)

    def _falla():
        raise RuntimeError("falla de prueba en un hilo")

    hilo = threading.Thread(target=_falla, name="hilo-prueba")
    hilo.start()
    hilo.join()
    texto = _texto(ruta)
    assert "hilo-prueba" in texto and "RuntimeError: falla de prueba en un hilo" in texto


def test_carpeta_predeterminada_en_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert registro.carpeta_registros() == tmp_path / "FirmadorDNIe" / "registros"
    monkeypatch.delenv("LOCALAPPDATA")
    assert registro.carpeta_registros() == Path.home() / "AppData" / "Local" / "FirmadorDNIe" / "registros"


def test_build_ps1_toma_la_version_de_version_py():
    script = (RAIZ / "build.ps1").read_text(encoding="utf-8")
    assert "version.py" in script
    assert not re.search(r'\$Version\s*=\s*"\d', script), "la versión no debe estar fija en build.ps1"
    patron = re.search(r"-Pattern '([^']+)'", script).group(1)
    texto = (RAIZ / "version.py").read_text(encoding="utf-8")
    coincidencia = re.search(patron, texto, re.M)
    assert coincidencia and coincidencia.group(1) == version.VERSION


# --- instrumentación de dnie_cng ---------------------------------------------


def test_error_cng_conserva_el_codigo_sin_cambiar_el_mensaje():
    import dnie_cng

    with pytest.raises(dnie_cng.ErrorCNG) as error:
        dnie_cng._verificar(0x8010006E, "NCryptSignHash")
    assert error.value.codigo == 0x8010006E
    assert str(error.value).startswith("NCryptSignHash falló (0x8010006E)")
    assert dnie_cng.ErrorCNG("sin código").codigo is None


def _firmante_cng(monkeypatch, material_prueba, estados):
    import dnie_cng

    llamadas = iter(estados)
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    monkeypatch.setattr(dnie_cng, "_SignHash", lambda *a: next(llamadas))
    llave = dnie_cng.LlaveTarjeta(nombre="llave-prueba", tipo_legado=0, cert=material_prueba[1])
    return dnie_cng.FirmanteCNG(llave)


def _cng(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records if r.name == "firmador.cng")


def test_firma_cng_registra_antes_y_despues_del_pin(monkeypatch, material_prueba, caplog):
    caplog.set_level("INFO")
    firmante = _firmante_cng(monkeypatch, material_prueba, [0, 0])
    resumen = bytes(range(32))
    firmante._firmar_con_handle(1, resumen, "sha256")
    texto = _cng(caplog)
    assert "solicitando firma / PIN" in texto
    assert re.search(r"NCryptSignHash terminó: estado 0x00000000 en \d+ ms", texto)
    assert resumen.hex() not in texto.lower()  # nunca el resumen, solo su longitud
    assert "32 bytes" in texto


def test_firma_cng_fallida_registra_el_estado(monkeypatch, material_prueba, caplog):
    import dnie_cng

    caplog.set_level("INFO")
    firmante = _firmante_cng(monkeypatch, material_prueba, [0, 0x8010006E])
    with pytest.raises(dnie_cng.ErrorCNG):
        firmante._firmar_con_handle(1, bytes(32), "sha256")
    assert re.search(r"NCryptSignHash terminó: estado 0x8010006E en \d+ ms", _cng(caplog))


def test_elegir_llave_registra_la_elegida_o_el_motivo(monkeypatch, material_prueba, caplog):
    import dnie_cng

    caplog.set_level("INFO")
    llave = dnie_cng.LlaveTarjeta(nombre="llave-prueba", tipo_legado=2, cert=material_prueba[1])
    monkeypatch.setattr(dnie_cng, "listar_llaves", lambda: [llave])
    assert dnie_cng.elegir_llave_de_firma() is llave
    texto = _cng(caplog)
    assert "Llave de firma elegida" in texto and "llave-prueba" in texto and "PRUEBA FIRMANTE FIR 12345678" in texto
    with pytest.raises(dnie_cng.ErrorCNG):
        dnie_cng.elegir_llave_de_firma("no-existe")
    assert "0 candidata(s)" in _cng(caplog)


def test_describir_llave_nunca_falla(material_prueba):
    import dnie_cng

    texto = dnie_cng.describir_llave(dnie_cng.LlaveTarjeta(nombre="x", tipo_legado=2, cert=material_prueba[1]))
    assert "es_de_firma=True" in texto and "tipo_legado=2" in texto and "vigencia" in texto
    assert "x" in dnie_cng.describir_llave(dnie_cng.LlaveTarjeta(nombre="x", tipo_legado=0, cert=None))


def _enumeracion_falsa(monkeypatch, estado_enum):
    import dnie_cng

    liberados = []
    monkeypatch.setattr(dnie_cng, "_abrir_proveedor", lambda: dnie_cng._HANDLE(7))
    monkeypatch.setattr(dnie_cng, "_EnumKeys", lambda *a: estado_enum)
    monkeypatch.setattr(dnie_cng, "_FreeObject", lambda h: liberados.append(int(h.value)) or 0)
    monkeypatch.setattr(dnie_cng, "_FreeBuffer", lambda b: 0)
    return liberados


def test_enumeracion_sin_llaves_nte_not_found_da_el_mensaje_amigable(monkeypatch, caplog):
    import dnie_cng

    liberados = _enumeracion_falsa(monkeypatch, 0x80090011)  # NTE_NOT_FOUND
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        dnie_cng.listar_llaves()
    assert str(error.value) == "No se encontró ninguna llave con certificado. ¿Está el DNIe insertado?"
    assert liberados == [7]  # el proveedor se libera
    avisos = [r for r in caplog.records if r.name == "firmador.cng" and r.levelname == "WARNING"]
    assert any("0x80090011" in r.getMessage() and dnie_cng.PROVEEDOR_TARJETA in r.getMessage() for r in avisos)


def test_otro_fallo_de_enumeracion_se_registra_y_se_propaga(monkeypatch, caplog):
    import dnie_cng

    _enumeracion_falsa(monkeypatch, 0x80100069)
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        dnie_cng.listar_llaves()
    assert error.value.codigo == 0x80100069 and str(error.value).startswith("NCryptEnumKeys falló (0x80100069)")
    assert re.search(r"NCryptEnumKeys falló con 0x80100069", _cng(caplog))
