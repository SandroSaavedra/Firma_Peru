"""
Registro de diagnóstico persistente.

El ejecutable corre sin consola, así que los mensajes de `logging` se guardan
también en `%LOCALAPPDATA%\\FirmadorDNIe\\registros\\firmador.log` (rotativo,
1 MB y 5 copias). Ahí queda dónde se detuvo una firma y por qué. Nunca se
registra el PIN (la aplicación no lo ve) ni el contenido de los documentos.

Un fallo del registro jamás impide usar la aplicación: si la carpeta no se
puede crear o escribir, se sigue solo con la consola.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
import threading
from pathlib import Path

from version import VERSION

NOMBRE_ARCHIVO = "firmador.log"
TAM_MAXIMO = 1_000_000
COPIAS = 5
FORMATO = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"
MARCA = "_registro_firmador"  # atributo de los handlers que agrega este módulo

logger = logging.getLogger("firmador.registro")


def carpeta_registros() -> Path:
    """Carpeta del registro: %LOCALAPPDATA%\\FirmadorDNIe\\registros."""
    base = os.environ.get("LOCALAPPDATA")
    raiz = Path(base) if base else Path.home() / "AppData" / "Local"
    return raiz / "FirmadorDNIe" / "registros"


def _version_pyhanko() -> str:
    try:
        from pyhanko.version import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001 - solo informativo
        return "desconocida"


def _excepcion_no_capturada(tipo, valor, traza):
    """sys.excepthook: deja la traza en el registro."""
    try:
        if issubclass(tipo, KeyboardInterrupt):
            sys.__excepthook__(tipo, valor, traza)
            return
        logging.getLogger("firmador").critical("Excepción no capturada", exc_info=(tipo, valor, traza))
    except Exception:  # noqa: BLE001 - el registro nunca debe fallar
        pass


def _excepcion_en_hilo(args):
    """threading.excepthook: deja la traza (y el nombre del hilo) en el registro."""
    try:
        if args.exc_type is SystemExit:
            return
        nombre = args.thread.name if args.thread is not None else "?"
        logging.getLogger("firmador").critical(
            "Excepción no capturada en el hilo %s",
            nombre,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
    except Exception:  # noqa: BLE001
        pass


def _quitar_handlers_propios(raiz: logging.Logger):
    for handler in list(raiz.handlers):
        if getattr(handler, MARCA, False):
            raiz.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass


def _agregar(raiz: logging.Logger, handler: logging.Handler):
    setattr(handler, MARCA, True)
    handler.setFormatter(logging.Formatter(FORMATO))
    raiz.addHandler(handler)


def configurar_registro(carpeta: Path | None = None) -> Path | None:
    """
    Configura el logger raíz (nivel INFO): consola y archivo rotativo en
    `carpeta` (por omisión `carpeta_registros()`). Instala los excepthooks y
    escribe la cabecera de inicio. Se puede llamar varias veces sin duplicar
    handlers. Devuelve la ruta del archivo o None si solo hay consola. Nunca lanza.
    """
    ruta = None
    try:
        raiz = logging.getLogger()
        raiz.setLevel(logging.INFO)
        _quitar_handlers_propios(raiz)
        if sys.stderr is not None:  # el ejecutable sin consola no tiene stderr
            _agregar(raiz, logging.StreamHandler(sys.stderr))
        destino = Path(carpeta) if carpeta is not None else carpeta_registros()
        try:
            destino.mkdir(parents=True, exist_ok=True)
            archivo = logging.handlers.RotatingFileHandler(
                destino / NOMBRE_ARCHIVO, maxBytes=TAM_MAXIMO, backupCount=COPIAS, encoding="utf-8"
            )
            _agregar(raiz, archivo)
            ruta = destino / NOMBRE_ARCHIVO
        except Exception as e:  # noqa: BLE001 - sin archivo se sigue con la consola
            logger.warning("No se pudo abrir el registro en %s (%s); solo consola.", destino, e)
        sys.excepthook = _excepcion_no_capturada
        threading.excepthook = _excepcion_en_hilo
        logger.info(
            "==== Firma Perú %s inicia (empaquetado: %s) ====", VERSION, bool(getattr(sys, "frozen", False))
        )
        logger.info("Python %s; %s; pyHanko %s", platform.python_version(), platform.platform(), _version_pyhanko())
        logger.info("Registro: %s", ruta or "solo consola")
    except Exception:  # noqa: BLE001 - el registro nunca debe impedir abrir la aplicación
        pass
    return ruta
