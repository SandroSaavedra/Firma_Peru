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
