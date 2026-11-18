"""
Firma Perú: aplicación de escritorio (pywebview) para ver un PDF, ubicar el
sello, firmarlo con el DNIe a través de Windows CNG y validar firmas.

La interfaz (HTML/CSS/JS en ui/) llama a los métodos públicos de `Api`. Los
errores se devuelven como datos ({"ok": False, "mensaje": ...}), no como
excepciones, para que la interfaz siempre pueda mostrarlos.

El PIN NUNCA pasa por esta aplicación: Windows lo pide en su propio cuadro de
diálogo durante la firma.

Uso:
    python app.py
"""
from __future__ import annotations

import atexit
import base64
import binascii
import ctypes
import hashlib
import io
import json
import logging
import math
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import webview

from dnie_cng import ErrorCNG, FirmanteCNG, SesionFirma, elegir_llave_de_firma
from firma_pdf import (
    MOTIVOS_POR_TIPO,
    TIPOS_SELLO,
    ErrorFirma,
    OpcionesFirma,
    PosicionRelativa,
    analizar_pdf,
    firmar_pdf,
    fondo_blanco_a_transparente,
    recortar_margenes,
)
from validacion import validar_pdf

logger = logging.getLogger("firmador")

TITULO = "Firma Perú"
TAM_FRAGMENTO = 1536 * 1024  # bytes por fragmento al enviar el PDF a la interfaz
TAM_MAXIMO_PDF = 200 * 1024 * 1024  # límite de un PDF recibido como bytes desde la interfaz
VIDA_SUBIDA = 120  # segundos sin actividad tras los cuales se descarta una subida
TAM_MAXIMO_LOGO = 20 * 1024 * 1024  # imagen (logo o visto bueno) que se acepta
LADO_MAXIMO_LOGO = 800  # px: la copia guardada se reduce para no inflar los PDF
# Logo del sello «Firma»: siempre este archivo; no se puede cambiar ni quitar.
# Se empaqueta dentro del ejecutable (ver firmador.spec y `ruta_logo_inicial`).
LOGO_INICIAL = Path("src") / "Emblema peruano con firma digital.png"
NOMBRE_LOGO = "logo.png"  # copia reducida de LOGO_INICIAL en la carpeta de configuración
# Imagen de visto bueno inicial: se copia a la configuración la primera vez.
IMAGEN_VB_INICIAL = Path("src") / "Icono circular con marca roja.png"
# Clave en config.json -> (archivo de la copia, constante con el original, bandera «ya se aplicó»).
# El logo no está aquí: no se elige ni se guarda en config.json (ver `preparar_logo`).
IMAGENES = {
    "imagen_vb": ("imagen_vb.png", "IMAGEN_VB_INICIAL", "imagen_vb_inicial_aplicada"),
}

# Códigos de Windows / tarjeta inteligente frecuentes, con mensaje para el usuario.
_MENSAJES_CNG = {
    0x8010006E: "Se canceló el ingreso del PIN. El documento no se firmó.",
    0x800704C7: "Se canceló la operación. El documento no se firmó.",
    0x8010006B: "El PIN es incorrecto. Después de varios intentos fallidos el DNIe se bloquea.",
    0x8010006C: "El PIN del DNIe está bloqueado. Debe desbloquearse en RENIEC.",
    0x8010000C: "No se detecta el DNIe en el lector. Insértelo e intente de nuevo.",
    0x80100069: "Se retiró el DNIe del lector durante la operación.",
    0x8010002E: "No hay lectores de tarjetas conectados.",
    0x8010001D: "El servicio de tarjetas inteligentes de Windows no está en ejecución.",
    0x80090016: "No se encontró la llave de firma en el DNIe.",
}
# En un lote: el usuario canceló el cuadro del PIN -> se detiene y lo que falta queda pendiente.
_CODIGOS_CANCELADO = {0x8010006E, 0x800704C7}  # SCARD_W_CANCELLED_BY_USER, ERROR_CANCELLED
# En un lote: PIN incorrecto o bloqueado (repetir intentos bloquearía el DNIe) o la tarjeta
# dejó de estar disponible (fallaría igual en todos los documentos siguientes) -> se detiene.
_CODIGOS_DETENER = {
    0x8010006B,  # SCARD_W_WRONG_CHV
    0x8010006C,  # SCARD_W_CHV_BLOCKED
    0x8010000C,  # SCARD_E_NO_SMARTCARD
    0x80100069,  # SCARD_W_REMOVED_CARD
    0x8010002E,  # SCARD_E_NO_READERS_AVAILABLE
    0x8010001D,  # SCARD_E_NO_SERVICE
}
MENSAJE_OCUPADO = "Espere a que termine la firma en curso."
MENSAJE_CANCELADO_LOTE = "Se canceló la firma por lotes. Los documentos restantes quedan pendientes."


def ruta_recursos() -> Path:
    """Carpeta base de los recursos (también dentro del ejecutable de PyInstaller)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


# Window and executable icon, generated from src/peru.png (see firmador.spec).
RUTA_ICONO = ruta_recursos() / "assets" / "firma-peru.ico"


def codigo_cng(error: Exception) -> int | None:
    """Código de Windows / tarjeta inteligente de un ErrorCNG («... (0x8010006E) ...»)."""
    if not isinstance(error, ErrorCNG):
        return None
    coincidencia = re.search(r"\(0x([0-9A-Fa-f]{8})\)", str(error))
    return int(coincidencia.group(1), 16) if coincidencia else None


def mensaje_de_error(error: Exception) -> str:
    """Convierte errores de CNG y de firma en un texto legible."""
    if isinstance(error, ErrorCNG):
        codigo = codigo_cng(error)
        if codigo in _MENSAJES_CNG:
            return _MENSAJES_CNG[codigo]
        return f"Error con el DNIe: {error}"
    if isinstance(error, FileNotFoundError):
        return "No se encontró el archivo. ¿Se movió o se borró?"
    if isinstance(error, PermissionError):
        return "No se pudo escribir el archivo. ¿Está abierto en otro programa?"
    if isinstance(error, ErrorFirma):
        return str(error)
    return f"Error inesperado: {error}"


def accion_tras_error(error: Exception) -> str:
    """Qué hace un lote ante un error: "cancelado", "detener" o "continuar" con el siguiente."""
    codigo = codigo_cng(error)
    if codigo in _CODIGOS_CANCELADO:
        return "cancelado"
    if codigo in _CODIGOS_DETENER:
        return "detener"
    return "continuar"


def clave_de_ruta(ruta) -> str:
    """Identidad de un archivo para no agregarlo dos veces a la cola (Windows no distingue mayúsculas)."""
    return os.path.normcase(str(Path(ruta).resolve()))


def examinar_pdf(archivo: Path):
    """(InfoPdf, tamaño) del PDF; ErrorFirma con un mensaje para el usuario si no sirve."""
    try:
        with open(archivo, "rb") as f:
            cabecera = f.read(1024)
        if not cabecera.lstrip()[:5].startswith(b"%PDF"):
            raise ErrorFirma("El archivo no parece ser un PDF.")
        return analizar_pdf(archivo), Path(archivo).stat().st_size
    except FileNotFoundError as e:
        raise ErrorFirma("No se encontró el archivo.") from e
    except ErrorFirma:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("examinar_pdf")
        raise ErrorFirma(f"No se pudo leer el PDF: {e}") from e


SUFIJOS_SALIDA = {"firma": "_firmado", "visto_bueno": "_vb"}


def ruta_salida_predeterminada(entrada: Path, tipo: str = "firma") -> Path:
    """
    `<nombre>_firmado.pdf` (o `<nombre>_vb.pdf` para el visto bueno) junto al
    original, sin sobrescribir archivos existentes.
    """
    sufijo = SUFIJOS_SALIDA.get(tipo, SUFIJOS_SALIDA["firma"])
    base = entrada.with_name(f"{entrada.stem}{sufijo}.pdf")
    n = 2
    candidata = base
    while candidata.exists():
        candidata = entrada.with_name(f"{entrada.stem}{sufijo} ({n}).pdf")
        n += 1
    return candidata


def carpeta_descargas() -> Path:
    """Carpeta «Descargas» del usuario (o su carpeta personal si no existe)."""
    try:
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        # FOLDERID_Downloads {374DE290-123F-4565-9164-39C4925E467B}
        guid = _GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
        puntero = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(puntero)) == 0:
            try:
                ruta = Path(puntero.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(puntero)
            if ruta.is_dir():
                return ruta
    except Exception:  # noqa: BLE001 - se usan las alternativas
        logger.debug("No se pudo obtener la carpeta Descargas", exc_info=True)
    descargas = Path.home() / "Downloads"
    return descargas if descargas.is_dir() else Path.home()


def nombre_seguro(nombre: str) -> str:
    """Nombre de archivo sin carpetas ni caracteres no válidos en Windows, terminado en .pdf."""
    base = re.split(r"[\\/]", str(nombre or ""))[-1]
    base = re.sub(r'[<>:"|?*\x00-\x1f]', "_", base).strip(" .")
    if not base:
        base = "documento.pdf"
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base


def _fecha(dt) -> str | None:
    return dt.isoformat() if dt else None


# --- configuración persistente (%APPDATA%\FirmadorDNIe\config.json) ----------------


def carpeta_config() -> Path:
    """Carpeta de configuración del usuario; el logo y la imagen de visto bueno se copian aquí."""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "FirmadorDNIe"


def leer_config() -> dict:
    """Configuración guardada; si falta o está dañada, valores predeterminados."""
    config = {"tipo": "firma"}
    for clave, (_, _, bandera) in IMAGENES.items():
        config[clave] = None
        config[bandera] = False
    try:
        datos = json.loads((carpeta_config() / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return config
    if isinstance(datos, dict):
        if datos.get("tipo") in TIPOS_SELLO:
            config["tipo"] = datos["tipo"]
        for clave, (_, _, bandera) in IMAGENES.items():
            if isinstance(datos.get(clave), str):
                config[clave] = datos[clave]
            config[bandera] = datos.get(bandera) is True
    return config


def guardar_config(config: dict):
    carpeta = carpeta_config()
    carpeta.mkdir(parents=True, exist_ok=True)
    temporal = carpeta / "config.json.tmp"
    temporal.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporal, carpeta / "config.json")


def carpeta_aplicacion() -> Path:
    """Carpeta del programa: la del .exe si está empaquetado, si no la del código."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def ruta_imagen_inicial(clave: str) -> Path:
    return carpeta_aplicacion() / globals()[IMAGENES[clave][1]]


def ruta_logo_inicial() -> Path:
    # Va dentro del ejecutable (ver firmador.spec): el logo es obligatorio y el
    # instalador no copia la carpeta src/.
    return ruta_recursos() / LOGO_INICIAL


def preparar_logo() -> Path | None:
    """
    Logo del sello «Firma»: siempre LOGO_INICIAL, incluido en el programa; no se
    puede cambiar ni quitar. Se guarda su copia reducida (`imagen_reducida`)
    en la carpeta de configuración como NOMBRE_LOGO, encima de cualquier logo
    elegido con versiones anteriores (que ya no se usa). Devuelve la ruta que
    se usa al firmar y en la vista previa:
      - None si el logo inicial falta o no es una imagen: el sello va sin
        logo y queda un aviso en el registro.
      - El original si no se puede escribir la copia.
    """
    origen = ruta_logo_inicial()
    if not origen.is_file():
        logger.warning("No se encontró el logo %s; el sello «Firma» irá sin logo.", origen)
        return None
    try:
        return copiar_imagen(origen, "logo")
    except ErrorFirma as e:
        logger.warning("No se pudo usar el logo %s (%s); el sello «Firma» irá sin logo.", origen, e)
        return None
    except OSError as e:
        logger.warning("No se pudo guardar la copia del logo (%s); se usa el original.", e)
        return origen


def preparar_imagenes_iniciales():
    """
    Copia a la configuración, una sola vez, la imagen de visto bueno inicial
    de `src/` (si existe junto al programa; el original no se toca). Si el
    usuario la quita o la cambia, se respeta. El logo no pasa por aquí: ver
    `preparar_logo`.
    """
    for clave, (_, _, bandera) in IMAGENES.items():
        config = leer_config()
        if config[bandera] or config[clave]:
            continue
        origen = ruta_imagen_inicial(clave)
        if not origen.is_file():
            continue
        try:
            destino = copiar_imagen(origen, clave)
            guardar_config({**config, clave: destino.name, bandera: True})
        except (ErrorFirma, OSError) as e:
            logger.warning("No se pudo copiar la imagen inicial %s: %s", origen, e)


def ruta_imagen_guardada(clave: str) -> Path | None:
    """Copia de la imagen elegida (`imagen_vb`) en la carpeta de configuración, si existe."""
    nombre = leer_config()[clave]
    if not nombre:
        return None
    ruta = carpeta_config() / Path(nombre).name  # solo archivos dentro de la carpeta
    return ruta if ruta.is_file() else None


def _imagen_para_interfaz(ruta: Path | None, clave: str = "imagen_vb") -> dict | None:
    """
    Imagen para la vista previa. Al logo se le recortan los márgenes igual que
    al firmar, para que la vista previa coincida con el sello del PDF.
    """
    if ruta is None:
        return None
    try:
        if clave == "logo":
            from PIL import Image

            with Image.open(ruta) as imagen:
                imagen.load()
                buffer = io.BytesIO()
                recortar_margenes(imagen).save(buffer, "PNG")
            datos = buffer.getvalue()
        else:
            datos = ruta.read_bytes()
    except OSError:
        return None
    return {"nombre": ruta.name, "datos": "data:image/png;base64," + base64.b64encode(datos).decode("ascii")}


def imagen_reducida(origen: Path, clave: str = "logo"):
    """
    Imagen (PIL) tal como se guarda en la configuración: reducida a
    LADO_MAXIMO_LOGO px, con transparencia; a la de visto bueno se le quita el
    fondo blanco, igual que al firmar. ErrorFirma si no es una imagen.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        if origen.stat().st_size > TAM_MAXIMO_LOGO:
            raise ErrorFirma(f"La imagen supera {TAM_MAXIMO_LOGO // (1024 * 1024)} MB.")
        with Image.open(origen) as imagen:
            imagen.load()
            transparente = "A" in imagen.getbands() or "transparency" in imagen.info
            copia = imagen.convert("RGBA" if transparente else "RGB")
    except FileNotFoundError as e:
        raise ErrorFirma("No se encontró la imagen.") from e
    except (OSError, UnidentifiedImageError, ValueError) as e:
        raise ErrorFirma("El archivo no es una imagen PNG o JPG válida.") from e
    copia.thumbnail((LADO_MAXIMO_LOGO, LADO_MAXIMO_LOGO))
    if clave == "imagen_vb":
        copia = fondo_blanco_a_transparente(copia)
    return copia


def copiar_imagen(origen: Path, clave: str = "logo") -> Path:
    """Guarda en la carpeta de configuración la copia PNG de `imagen_reducida`."""
    copia = imagen_reducida(origen, clave)
    archivo = NOMBRE_LOGO if clave == "logo" else IMAGENES[clave][0]
    carpeta = carpeta_config()
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / archivo
    temporal = carpeta / (archivo + ".tmp")
    copia.save(temporal, "PNG")
    os.replace(temporal, destino)
    return destino


def motivo_permitido(tipo: str, motivo) -> str | None:
    """
    El motivo, si es uno de la lista cerrada del tipo (MOTIVOS_POR_TIPO); si
    no, None, es decir, el predeterminado del tipo. Solo la interfaz se limita
    a la lista: `firmar_pdf` y la CLI aceptan cualquier motivo.
    """
    motivo = motivo.strip() if isinstance(motivo, str) else ""
    return motivo if motivo in MOTIVOS_POR_TIPO.get(tipo, ()) else None


def posiciones_por_pagina(valor, total_paginas: int | None = None) -> dict[int, PosicionRelativa]:
    """
    Posiciones propias por página que envía la interfaz: {"índice": {x, y}},
    índice desde 0 (en JSON las claves son texto). Se descartan las entradas mal
    formadas, con x o y fuera de 0..1 o de páginas que el documento no tiene:
    esas páginas usan la posición del documento.
    """
    if not isinstance(valor, dict):
        return {}
    resultado = {}
    for clave, caja in valor.items():
        try:
            indice = int(str(clave))
            x, y = float(caja["x"]), float(caja["y"])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if indice < 0 or (total_paginas is not None and indice >= total_paginas):
            continue
        if all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            resultado[indice] = PosicionRelativa(x=x, y=y)
    return resultado


class Api:
    """Métodos expuestos a la interfaz como `window.pywebview.api.*`."""

    def __init__(self):
        self._ventana = None
        # Documento abierto: ruta (None si llegó como bytes), nombre, archivo
        # (copia en disco que se lee para firmar/validar), datos y token.
        self._pdf: dict | None = None
        self._generados: set[str] = set()  # rutas que la interfaz puede abrir/mostrar
        self._ejecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="firma")
        self._firmando = threading.Lock()
        self._subidas: dict[str, dict] = {}  # token -> PDF que la interfaz envía en fragmentos
        self._cerrojo_subidas = threading.Lock()
        self._temporal: Path | None = None  # carpeta privada para PDF recibidos como bytes
        # Cola de documentos. Cada ítem guarda la ruta del archivo (no sus bytes);
        # solo el documento abierto (`_pdf`) tiene sus bytes en memoria.
        self._cola: list[dict] = []
        self._cerrojo_cola = threading.RLock()
        self._cancelar_lote = threading.Event()

    # --- integración con la ventana -----------------------------------------

    def _asignar_ventana(self, ventana):
        self._ventana = ventana

    def _hwnd(self) -> int | None:
        """HWND de la ventana para que el cuadro del PIN aparezca delante."""
        if self._ventana is None:
            return None
        try:
            nativo = getattr(self._ventana, "native", None)
            if nativo is not None:
                return int(nativo.Handle.ToInt64())
        except Exception:  # noqa: BLE001 - se intenta la alternativa
            logger.debug("No se pudo leer el HWND nativo", exc_info=True)
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, TITULO)
            return int(hwnd) or None
        except Exception:  # noqa: BLE001
            return None

    def _al_soltar(self, evento):
        """
        Archivo soltado sobre la ventana. Si pywebview entrega la ruta completa
        se usa (el PDF firmado queda junto al original). Si no, la interfaz lee
        el archivo y lo envía como bytes (`abrir_bytes_*`); por eso aquí no se
        avisa de nada cuando falta la ruta.
        """
        archivos = (evento or {}).get("dataTransfer", {}).get("files", []) or []
        rutas = [f.get("pywebviewFullPath") for f in archivos if isinstance(f, dict) and f.get("pywebviewFullPath")]
        logger.info("Soltar: %d archivo(s) recibido(s), %d con pywebviewFullPath.", len(archivos), len(rutas))
        pdfs = [r for r in rutas if r.lower().endswith(".pdf")]
        if self._ventana is None or not pdfs:
            return  # la interfaz ya avisó si no es PDF, o usará la lectura por bytes
        self._ventana.evaluate_js(f"window.firmador && window.firmador.alSoltar({json.dumps(pdfs)})")

    # --- DNIe -------------------------------------------------------------

    def estado_dnie(self) -> dict:
        """Certificado de firma del DNIe. No pide PIN."""
        try:
            llave = elegir_llave_de_firma()
        except ErrorCNG as e:
            return {"ok": False, "mensaje": mensaje_de_error(e)}
        except Exception as e:  # noqa: BLE001
            logger.exception("estado_dnie")
            return {"ok": False, "mensaje": mensaje_de_error(e)}
        cert = llave.cert
        validez = cert["tbs_certificate"]["validity"]
        hasta = validez["not_after"].native
        return {
            "ok": True,
            "titular": llave.titular,
            "emisor": cert.issuer.native.get("common_name", ""),
            "valido_desde": _fecha(validez["not_before"].native),
            "valido_hasta": _fecha(hasta),
            "algoritmo": f"{cert.public_key.algorithm.upper()} {cert.public_key.bit_size}",
        }

    # --- documento ----------------------------------------------------------

    def abrir_pdf(self) -> dict:
        """Diálogo nativo para elegir un PDF."""
        if self._ventana is None:
            return {"ok": False, "mensaje": "La ventana no está disponible."}
        seleccion = self._ventana.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("Documentos PDF (*.pdf)", "Todos los archivos (*.*)")
        )
        if not seleccion:
            return {"ok": False, "cancelado": True}
        return self.abrir_ruta(seleccion[0])

    def abrir_ruta(self, ruta: str) -> dict:
        """
        Agrega el PDF a la cola (si no estaba) y lo abre. Los bytes se leen luego
        con `leer_fragmento`. Un archivo no válido solo devuelve el error.
        """
        if self._firmando.locked():
            return {"ok": False, "mensaje": MENSAJE_OCUPADO}
        p = Path(ruta)
        existente = self._buscar(clave=clave_de_ruta(p))
        if existente and existente["valido"]:
            return {**self._documento_abierto(existente), "duplicado": True}
        try:
            info, tamano = examinar_pdf(p)
        except ErrorFirma as e:
            return {"ok": False, "mensaje": str(e)}
        if existente:
            self._retirar(existente)  # fila con error de un intento anterior
        item = self._nuevo_item(ruta=p, nombre=p.name, archivo=p, info=info, tamano=tamano)
        return self._documento_abierto(item)

    def _documento_abierto(self, item: dict) -> dict:
        """Carga los bytes del ítem como documento abierto; respuesta común de toda apertura."""
        try:
            datos = Path(item["archivo"]).read_bytes()
        except (OSError, TypeError):
            return {"ok": False, "mensaje": "No se pudo leer el archivo. ¿Se movió o se borró?"}
        token = secrets.token_hex(8)
        self._pdf = {
            "id": item["id"],
            "ruta": item["ruta"],
            "nombre": item["nombre"],
            "archivo": item["archivo"],
            "datos": datos,
            "token": token,
        }
        ruta = Path(item["ruta"]) if item["ruta"] else None
        return {
            "ok": True,
            "id": item["id"],
            "ruta": item["ruta"],
            "nombre": item["nombre"],
            "carpeta": str(ruta.parent) if ruta else None,
            "tamano": len(datos),
            "token": token,
            "paginas": item["paginas"],
            "firmas": list(item["firmas"]),
            "docmdp": item["docmdp"],
            "rotaciones": list(item["rotaciones"]),
            "salida_sugerida": str(self._salida_sugerida()),
            "salidas_sugeridas": {t: str(self._salida_sugerida(t)) for t in SUFIJOS_SALIDA},
        }

    # --- cola de documentos ---------------------------------------------------------

    def _nuevo_item(self, *, ruta, nombre, archivo, info=None, tamano=0, error=None, clave=None) -> dict:
        """Agrega un ítem a la cola. Sin `info` queda como «Error» y nunca se firma."""
        item = {
            "id": secrets.token_hex(8),
            "ruta": str(ruta) if ruta else None,
            "clave": clave or (clave_de_ruta(ruta) if ruta else None),
            "nombre": nombre,
            "archivo": str(archivo) if archivo else None,
            "temporal": ruta is None and archivo is not None,  # copia privada de un PDF recibido como bytes
            "tamano": tamano,
            "paginas": info.total_paginas if info else 0,
            "firmas": list(info.firmas) if info else [],
            "docmdp": info.docmdp if info else None,
            "rotaciones": list(info.rotaciones) if info else [],
            "valido": info is not None,
            "estado": "pendiente",
            "mensaje": None,
            "salida": None,
        }
        if info is None:
            item["estado"], item["mensaje"] = "error", error or "El archivo no es un PDF válido."
        elif info.docmdp == 1:
            item["estado"], item["mensaje"] = "omitido", "El documento está certificado y no admite nuevas firmas."
        with self._cerrojo_cola:
            self._cola.append(item)
        return item

    @staticmethod
    def _item_publico(item: dict) -> dict:
        return {
            "id": item["id"],
            "nombre": item["nombre"],
            "ruta": item["ruta"],
            "paginas": item["paginas"],
            "firmas": len(item["firmas"]),
            "docmdp": item["docmdp"],
            "valido": item["valido"],
            "estado": item["estado"],
            "mensaje": item["mensaje"],
            "salida": item["salida"],
        }

    def _cola_publica(self) -> list[dict]:
        with self._cerrojo_cola:
            return [self._item_publico(i) for i in self._cola]

    def _buscar(self, *, id: str | None = None, clave: str | None = None) -> dict | None:
        with self._cerrojo_cola:
            for item in self._cola:
                if (id is not None and item["id"] == id) or (clave is not None and item["clave"] == clave):
                    return item
        return None

    def _marcar(self, item: dict, estado: str, mensaje: str | None = None, salida: str | None = None):
        with self._cerrojo_cola:
            item["estado"], item["mensaje"] = estado, mensaje
            if salida is not None:
                item["salida"] = salida

    def _retirar(self, item: dict) -> bool:
        """Quita el ítem; borra su copia temporal y, si estaba abierto, libera el documento."""
        with self._cerrojo_cola:
            self._cola = [i for i in self._cola if i is not item]
        actual = bool(self._pdf and self._pdf["id"] == item["id"])
        if actual:
            self._pdf = None
        if item["temporal"]:
            self._borrar_temporal(item["archivo"])
        return actual

    def obtener_cola(self) -> dict:
        return {"ok": True, "cola": self._cola_publica(), "actual": self._pdf["id"] if self._pdf else None}

    def agregar_pdfs(self) -> dict:
        """Diálogo nativo de selección múltiple; los PDF elegidos se agregan a la cola."""
        if self._ventana is None:
            return {"ok": False, "mensaje": "La ventana no está disponible."}
        seleccion = self._ventana.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=("Documentos PDF (*.pdf)", "Todos los archivos (*.*)"),
        )
        if not seleccion:
            return {"ok": False, "cancelado": True}
        return self.agregar_rutas([seleccion] if isinstance(seleccion, str) else list(seleccion))

    def agregar_rutas(self, rutas) -> dict:
        """Agrega varios PDF. Los repetidos se ignoran; los no válidos quedan como «Error»."""
        if self._firmando.locked():
            return {"ok": False, "mensaje": MENSAJE_OCUPADO}
        agregados, duplicados, ids_duplicados = [], [], []
        for ruta in rutas or []:
            if not isinstance(ruta, str) or not ruta:
                continue
            p = Path(ruta)
            existente = self._buscar(clave=clave_de_ruta(p))
            if existente:
                duplicados.append(existente["nombre"])
                ids_duplicados.append(existente["id"])
                continue
            try:
                info, tamano = examinar_pdf(p)
                item = self._nuevo_item(ruta=p, nombre=p.name, archivo=p, info=info, tamano=tamano)
            except ErrorFirma as e:
                item = self._nuevo_item(ruta=p, nombre=p.name, archivo=None, error=str(e))
            agregados.append(self._item_publico(item))
        return {
            "ok": True,
            "agregados": agregados,
            "duplicados": duplicados,
            "ids_duplicados": ids_duplicados,
            "cola": self._cola_publica(),
        }

    def abrir_item(self, id: str) -> dict:
        """Abre (carga en el visor) un documento de la cola."""
        if self._firmando.locked():
            return {"ok": False, "mensaje": MENSAJE_OCUPADO}
        item = self._buscar(id=id)
        if item is None:
            return {"ok": False, "mensaje": "El documento ya no está en la lista."}
        if not item["valido"]:
            return {"ok": False, "mensaje": item["mensaje"]}
        return self._documento_abierto(item)

    def quitar_item(self, id: str) -> dict:
        if self._firmando.locked():
            return {"ok": False, "mensaje": MENSAJE_OCUPADO}
        item = self._buscar(id=id)
        if item is None:
            return {"ok": False, "mensaje": "El documento ya no está en la lista.", "cola": self._cola_publica()}
        actual = self._retirar(item)
        return {"ok": True, "actual_quitado": actual, "cola": self._cola_publica()}

    def cerrar_documento(self) -> dict:
        """Cierra el documento abierto: lo quita de la cola y libera su copia temporal."""
        if self._firmando.locked():
            return {"ok": False, "mensaje": MENSAJE_OCUPADO}
        if self._pdf:
            item = self._buscar(id=self._pdf["id"])
            if item:
                self._retirar(item)
            self._pdf = None
        return {"ok": True, "cola": self._cola_publica()}

    def vaciar_cola(self, solo_firmados: bool = False) -> dict:
        """Quita todos los documentos (o solo los ya firmados: «Firmar más documentos»)."""
        if self._firmando.locked():
            return {"ok": False, "mensaje": MENSAJE_OCUPADO}
        with self._cerrojo_cola:
            items = [i for i in self._cola if not solo_firmados or i["estado"] == "firmado"]
        actual = False
        for item in items:
            actual = self._retirar(item) or actual
        return {"ok": True, "actual_quitado": actual, "cola": self._cola_publica()}

    def _salida_sugerida(self, tipo: str | None = None) -> Path:
        """Junto al original; si el PDF llegó como bytes, en Descargas."""
        tipo = tipo or leer_config()["tipo"]
        if self._pdf["ruta"]:
            return ruta_salida_predeterminada(Path(self._pdf["ruta"]), tipo)
        return ruta_salida_predeterminada(carpeta_descargas() / self._pdf["nombre"], tipo)

    # --- documento recibido como bytes (arrastrar y soltar sin ruta) ----------

    def _carpeta_temporal(self) -> Path:
        if self._temporal is None or not self._temporal.is_dir():
            self._temporal = Path(tempfile.mkdtemp(prefix="firmador-"))
            atexit.register(shutil.rmtree, self._temporal, True)
        return self._temporal

    @staticmethod
    def _borrar_temporal(archivo: str):
        try:
            Path(archivo).unlink(missing_ok=True)
        except OSError:
            logger.debug("No se pudo borrar el temporal", exc_info=True)

    def _subida(self, token: str) -> dict | None:
        """Subida vigente para `token` (descarta las vencidas). Llamar con el cerrojo tomado."""
        ahora = time.monotonic()
        for clave in [k for k, s in self._subidas.items() if ahora - s["ultimo"] > VIDA_SUBIDA]:
            del self._subidas[clave]
        return self._subidas.get(token) if isinstance(token, str) else None

    def abrir_bytes_inicio(self, nombre: str, tamano: int) -> dict:
        """Prepara la recepción de un PDF que la interfaz enviará en fragmentos base64."""
        try:
            tamano = int(tamano)
        except (TypeError, ValueError):
            return {"ok": False, "mensaje": "Tamaño de archivo no válido."}
        if tamano > TAM_MAXIMO_PDF:
            return {
                "ok": False,
                "mensaje": f"El archivo supera el tamaño máximo permitido ({TAM_MAXIMO_PDF // (1024 * 1024)} MB).",
            }
        if tamano < 0:
            return {"ok": False, "mensaje": "Tamaño de archivo no válido."}
        token = secrets.token_hex(16)
        with self._cerrojo_subidas:
            self._subida("")  # limpia las vencidas
            self._subidas[token] = {
                "nombre": nombre_seguro(nombre),
                "tamano": tamano,
                "datos": bytearray(),
                "ultimo": time.monotonic(),
            }
        return {"ok": True, "token": token}

    def abrir_bytes_fragmento(self, token: str, b64: str) -> dict:
        """Agrega un fragmento (base64) a la subida indicada."""
        with self._cerrojo_subidas:
            subida = self._subida(token)
            if subida is None:
                return {"ok": False, "mensaje": "La transferencia del archivo venció o no existe. Intente de nuevo."}
            try:
                trozo = base64.b64decode(b64 or "", validate=True)
            except (binascii.Error, ValueError, TypeError):
                del self._subidas[token]
                return {"ok": False, "mensaje": "Se recibieron datos dañados. Intente de nuevo."}
            if len(subida["datos"]) + len(trozo) > min(subida["tamano"], TAM_MAXIMO_PDF):
                del self._subidas[token]
                return {"ok": False, "mensaje": "El archivo recibido es más grande que lo indicado."}
            subida["datos"] += trozo
            subida["ultimo"] = time.monotonic()
            return {"ok": True, "recibido": len(subida["datos"])}

    def abrir_bytes_fin(self, token: str, abrir: bool = True) -> dict:
        """
        Termina la subida. Con `abrir` (un solo archivo) lo abre y devuelve lo mismo
        que `abrir_ruta`; sin `abrir` (varios archivos soltados) solo lo agrega a la cola.
        """
        with self._cerrojo_subidas:
            subida = self._subida(token)
            if subida is None:
                return {"ok": False, "mensaje": "La transferencia del archivo venció o no existe. Intente de nuevo."}
            del self._subidas[token]
        datos = bytes(subida["datos"])
        if len(datos) != subida["tamano"]:
            return {"ok": False, "mensaje": "El archivo no se recibió completo. Intente de nuevo."}
        if self._firmando.locked():
            return {"ok": False, "mensaje": MENSAJE_OCUPADO}
        nombre = subida["nombre"]
        clave = "bytes:" + hashlib.sha256(datos).hexdigest()  # el mismo contenido no se agrega dos veces
        existente = self._buscar(clave=clave)
        if existente:
            if abrir:
                return {**self._documento_abierto(existente), "duplicado": True}
            return {"ok": True, "duplicado": True, "item": self._item_publico(existente), "cola": self._cola_publica()}
        error, info, archivo = None, None, None
        if not datos.lstrip()[:5].startswith(b"%PDF"):
            error = "El archivo no parece ser un PDF."
        else:
            # El núcleo de firma lee desde disco: se guarda una copia privada temporal.
            archivo = self._carpeta_temporal() / f"{secrets.token_hex(8)}.pdf"
            try:
                archivo.write_bytes(datos)
                info = analizar_pdf(archivo)
            except ErrorFirma as e:
                error = str(e)
            except Exception as e:  # noqa: BLE001
                logger.exception("abrir_bytes_fin")
                error = f"No se pudo leer el PDF: {e}"
            if error:
                self._borrar_temporal(str(archivo))
                archivo = None
        if error and abrir:
            return {"ok": False, "mensaje": error}
        item = self._nuevo_item(
            ruta=None, nombre=nombre, archivo=archivo, info=info, tamano=len(datos), error=error,
            clave=None if error else clave,
        )
        if abrir:
            return self._documento_abierto(item)
        return {"ok": True, "item": self._item_publico(item), "cola": self._cola_publica()}

    def leer_fragmento(self, token: str, inicio: int) -> dict:
        """Devuelve un fragmento del PDF abierto en base64 (evita mensajes enormes)."""
        if not self._pdf or token != self._pdf["token"]:
            return {"ok": False, "mensaje": "El documento ya no está abierto."}
        datos = self._pdf["datos"]
        inicio = max(0, int(inicio))
        trozo = datos[inicio : inicio + TAM_FRAGMENTO]
        return {
            "ok": True,
            "datos": base64.b64encode(trozo).decode("ascii"),
            "fin": inicio + len(trozo) >= len(datos),
        }

    def elegir_destino(self, tipo: str | None = None) -> dict:
        """Diálogo «Guardar como» para el PDF firmado; el nombre sugerido depende del tipo de sello."""
        if self._ventana is None or not self._pdf:
            return {"ok": False, "mensaje": "Abra primero un PDF."}
        sugerida = self._salida_sugerida(tipo if tipo in SUFIJOS_SALIDA else None)
        seleccion = self._ventana.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(sugerida.parent),
            save_filename=sugerida.name,
            file_types=("Documentos PDF (*.pdf)",),
        )
        if not seleccion:
            return {"ok": False, "cancelado": True}
        destino = seleccion if isinstance(seleccion, str) else seleccion[0]
        if not destino.lower().endswith(".pdf"):
            destino += ".pdf"
        return {"ok": True, "ruta": destino}

    # --- tipo de sello, logo y configuración ------------------------------------

    def obtener_config(self) -> dict:
        """
        Último tipo de sello, imágenes para la vista previa (el logo fijo y la
        imagen de visto bueno guardada) y motivos de cada tipo (el primero, el
        predeterminado).
        """
        preparar_imagenes_iniciales()
        config = leer_config()
        return {
            "ok": True,
            "tipo": config["tipo"],
            "logo": _imagen_para_interfaz(preparar_logo(), "logo"),
            "imagen_vb": _imagen_para_interfaz(ruta_imagen_guardada("imagen_vb")),
            "motivos": {tipo: list(motivos) for tipo, motivos in MOTIVOS_POR_TIPO.items()},
        }

    def guardar_tipo(self, tipo: str) -> dict:
        if tipo not in TIPOS_SELLO:
            return {"ok": False, "mensaje": "Tipo de sello no válido."}
        try:
            guardar_config({**leer_config(), "tipo": tipo})
        except OSError as e:
            logger.warning("No se pudo guardar la configuración: %s", e)
            return {"ok": False, "mensaje": "No se pudo guardar la preferencia."}
        return {"ok": True}

    def _elegir_imagen(self, clave: str) -> dict:
        """
        Diálogo para elegir la imagen de visto bueno; se guarda una copia en la
        carpeta de configuración. El logo no se elige: es siempre LOGO_INICIAL.
        """
        if self._ventana is None:
            return {"ok": False, "mensaje": "La ventana no está disponible."}
        seleccion = self._ventana.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("Imágenes (*.png;*.jpg;*.jpeg)",)
        )
        if not seleccion:
            return {"ok": False, "cancelado": True}
        origen = seleccion if isinstance(seleccion, str) else seleccion[0]
        try:
            destino = copiar_imagen(Path(origen), clave)
            guardar_config({**leer_config(), clave: destino.name})
        except ErrorFirma as e:
            return {"ok": False, "mensaje": str(e)}
        except OSError as e:
            logger.warning("No se pudo guardar la imagen: %s", e)
            return {"ok": False, "mensaje": "No se pudo guardar la imagen en la carpeta de configuración."}
        return {"ok": True, clave: _imagen_para_interfaz(destino, clave)}

    def _quitar_imagen(self, clave: str) -> dict:
        """Solo para la imagen de visto bueno: el logo no se puede cambiar ni quitar."""
        try:
            ruta = ruta_imagen_guardada(clave)
            if ruta is not None:
                ruta.unlink(missing_ok=True)
            # La bandera queda en True: la imagen inicial de src/ no vuelve a copiarse.
            guardar_config({**leer_config(), clave: None, IMAGENES[clave][2]: True})
        except OSError as e:
            logger.warning("No se pudo quitar la imagen: %s", e)
            return {"ok": False, "mensaje": "No se pudo quitar la imagen."}
        return {"ok": True}

    def elegir_imagen_vb(self) -> dict:
        return self._elegir_imagen("imagen_vb")

    def quitar_imagen_vb(self) -> dict:
        return self._quitar_imagen("imagen_vb")

    # --- firma ------------------------------------------------------------

    def firmar(self, opciones: dict) -> dict:
        """
        Firma el PDF abierto. Corre en un hilo de trabajo dedicado; durante la
        firma Windows muestra su cuadro de diálogo del PIN.
        """
        if not self._pdf:
            return {"ok": False, "mensaje": "Abra primero un PDF."}
        if not self._firmando.acquire(blocking=False):
            return {"ok": False, "mensaje": "Ya hay una firma en curso."}
        try:
            opciones = dict(opciones or {})
            if not opciones.get("destino") and not self._pdf["ruta"]:
                # Llegó como bytes: no hay «junto al original», se pregunta dónde guardar.
                eleccion = self.elegir_destino()
                if not eleccion.get("ok"):
                    return eleccion  # {"ok": False, "cancelado": True} si el usuario cancela
                opciones["destino"] = eleccion["ruta"]
            return self._ejecutor.submit(self._firmar, opciones).result()
        finally:
            self._firmando.release()

    def _firmar(self, opciones: dict) -> dict:
        item = self._buscar(id=self._pdf["id"])
        entrada = Path(self._pdf["archivo"])
        try:
            preparar_imagenes_iniciales()
            opts = self._opciones_firma(
                opciones,
                opciones.get("caja"),
                int(opciones.get("pagina_firma", -1)),
                opciones.get("posiciones_paginas"),
                item["paginas"] if item else None,
            )
            destino = opciones.get("destino")
            salida = Path(destino) if destino else ruta_salida_predeterminada(entrada, opts.tipo)
            if salida.suffix.lower() != ".pdf":
                raise ErrorFirma("El archivo de salida debe tener extensión .pdf.")

            llave = elegir_llave_de_firma()
            signer = FirmanteCNG(llave, hwnd=self._hwnd())
            resultado = firmar_pdf(entrada, salida, signer, llave.titular, opts)
        except (ErrorCNG, ErrorFirma, OSError, KeyError, ValueError) as e:
            logger.warning("Firma fallida: %s", e)
            self._marcar_tras_error(item, e)
            return {"ok": False, "mensaje": mensaje_de_error(e)}
        except Exception as e:  # noqa: BLE001
            logger.exception("Firma fallida")
            self._marcar_tras_error(item, e)
            return {"ok": False, "mensaje": mensaje_de_error(e)}
        self._generados.add(str(Path(resultado.salida).resolve()))
        if item:
            self._marcar(item, "firmado", None, resultado.salida)
        return {"ok": True, "nombre": Path(resultado.salida).name, **asdict(resultado)}

    def _marcar_tras_error(self, item: dict | None, error: Exception):
        if item is None:
            return
        if accion_tras_error(error) == "cancelado":
            self._marcar(item, "pendiente")
        else:
            self._marcar(item, "error", mensaje_de_error(error))

    @staticmethod
    def _opciones_firma(
        opciones: dict,
        caja: dict | None,
        pagina_firma: int,
        posiciones_paginas=None,
        total_paginas: int | None = None,
    ) -> OpcionesFirma:
        """
        Opciones comunes de la interfaz (tipo, motivo, lugar, páginas) más la
        caja y la página del campo. De la caja solo se usa la posición (x, y):
        el tamaño del sello es fijo por tipo y se ignoran «ancho» y «alto». El
        motivo debe ser uno de la lista del tipo; si no, se usa el predeterminado.
        El logo de «Firma» es siempre el inicial (`preparar_logo`).
        `posiciones_paginas` son las posiciones propias de algunas páginas
        ({"índice": {x, y}}, índice desde 0; ver `posiciones_por_pagina`).
        """
        tipo = opciones.get("tipo") or leer_config()["tipo"]
        return OpcionesFirma(
            posicion=PosicionRelativa(x=float(caja["x"]), y=float(caja["y"])) if caja else None,
            posiciones_paginas=posiciones_por_pagina(posiciones_paginas, total_paginas),
            paginas=opciones.get("paginas", "todas"),
            pagina_firma=pagina_firma,
            tipo=tipo,
            texto_sello=opciones.get("texto_sello") or None,
            motivo=motivo_permitido(tipo, opciones.get("motivo")),
            lugar=(opciones.get("lugar") or "").strip() or None,
            logo=preparar_logo() if tipo == "firma" else None,
            imagen_vb=ruta_imagen_guardada("imagen_vb") if tipo == "visto_bueno" else None,
        )

    # --- firma por lotes ------------------------------------------------------------

    def firmar_lote(self, opciones: dict) -> dict:
        """
        Firma, uno tras otro, los documentos pendientes de la cola (y los que
        quedaron con error de firma) en un solo hilo de trabajo y con UNA sola
        sesión CNG. El avance se envía a la interfaz con `progresoLote`.

        `opciones`: las comunes (tipo, motivo, lugar, paginas, caja), `en_carpeta`
        (guardar todo en una carpeta) y `ajustes` ({id: {caja, pagina_firma,
        posiciones_paginas}}) para los documentos con posición, página del campo
        o posiciones por página propias.
        """
        if not self._firmando.acquire(blocking=False):
            return {"ok": False, "mensaje": "Ya hay una firma en curso."}
        try:
            opciones = dict(opciones or {})
            with self._cerrojo_cola:
                items = [i for i in self._cola if i["valido"] and i["estado"] in ("pendiente", "error")]
            if not items:
                return {"ok": False, "mensaje": "No hay documentos pendientes de firma."}
            en_carpeta = bool(opciones.get("en_carpeta"))
            carpeta = None
            # Los PDF recibidos como bytes no tienen carpeta de origen: se pregunta UNA vez.
            if en_carpeta or any(not i["ruta"] for i in items):
                carpeta = self._elegir_carpeta()
                if carpeta is None:
                    return {"ok": False, "cancelado": True}
            self._cancelar_lote.clear()
            return self._ejecutor.submit(self._firmar_lote, items, opciones, carpeta, en_carpeta).result()
        finally:
            self._firmando.release()

    def cancelar_lote(self) -> dict:
        """Detiene el lote antes del siguiente documento (el actual se termina de escribir)."""
        self._cancelar_lote.set()
        return {"ok": True}

    def _elegir_carpeta(self) -> Path | None:
        if self._ventana is None:
            return None
        seleccion = self._ventana.create_file_dialog(webview.FileDialog.FOLDER, directory=str(carpeta_descargas()))
        if not seleccion:
            return None
        carpeta = Path(seleccion if isinstance(seleccion, str) else seleccion[0])
        return carpeta if carpeta.is_dir() else None

    def _emitir_progreso(self, datos: dict):
        if self._ventana is None:
            return
        try:
            self._ventana.evaluate_js(
                f"window.firmador && window.firmador.progresoLote({json.dumps(datos, ensure_ascii=False)})"
            )
        except Exception:  # noqa: BLE001 - el avance es informativo
            logger.debug("No se pudo enviar el avance del lote", exc_info=True)

    def _firmar_item(self, item, opciones, ajuste, firmante, titular, carpeta, en_carpeta):
        ajuste = ajuste if isinstance(ajuste, dict) else {}
        pagina = ajuste.get("pagina_firma")
        # «Página actual» propia de este documento, limitada a sus páginas; si no, la última.
        pagina_firma = -1 if pagina is None else min(max(int(pagina), 0), item["paginas"] - 1)
        opts = self._opciones_firma(
            opciones,
            ajuste.get("caja") or opciones.get("caja"),
            pagina_firma,
            ajuste.get("posiciones_paginas"),
            item["paginas"],
        )
        if carpeta is not None and (en_carpeta or not item["ruta"]):
            salida = ruta_salida_predeterminada(carpeta / nombre_seguro(item["nombre"]), opts.tipo)
        else:
            salida = ruta_salida_predeterminada(Path(item["ruta"]), opts.tipo)
        return firmar_pdf(Path(item["archivo"]), salida, firmante, titular, opts)

    def _firmar_lote(self, items: list[dict], opciones: dict, carpeta: Path | None, en_carpeta: bool) -> dict:
        total = len(items)
        ajustes = opciones.get("ajustes") or {}
        firmados, errores, procesados = [], [], set()
        detenido = mensaje = None
        firmas_en_sesion = 0
        self._emitir_progreso({"evento": "inicio", "total": total})
        logger.info("Lote: %d documento(s) por firmar.", total)
        try:
            preparar_imagenes_iniciales()
            llave = elegir_llave_de_firma()
            sesion = SesionFirma(llave, self._hwnd())
            try:
                with sesion as firmante:
                    for indice, item in enumerate(items, 1):
                        if self._cancelar_lote.is_set():
                            detenido, mensaje = "cancelado", MENSAJE_CANCELADO_LOTE
                            break
                        base = {
                            "evento": "archivo", "indice": indice, "total": total,
                            "id": item["id"], "nombre": item["nombre"],
                        }
                        self._marcar(item, "firmando")
                        self._emitir_progreso({**base, "estado": "firmando"})
                        try:
                            resultado = self._firmar_item(
                                item, opciones, ajustes.get(item["id"]), firmante, llave.titular, carpeta, en_carpeta
                            )
                        except Exception as e:  # noqa: BLE001 - cada error se clasifica
                            texto = mensaje_de_error(e)
                            accion = accion_tras_error(e)
                            logger.warning("Lote: documento %d de %d con error (%s): %s", indice, total, accion, e)
                            if accion == "cancelado":
                                self._marcar(item, "pendiente")
                                self._emitir_progreso({**base, "estado": "pendiente", "mensaje": texto})
                                detenido, mensaje = "pin_cancelado", texto
                                break
                            procesados.add(item["id"])
                            self._marcar(item, "error", texto)
                            errores.append({"id": item["id"], "nombre": item["nombre"], "mensaje": texto})
                            self._emitir_progreso({**base, "estado": "error", "mensaje": texto})
                            if accion == "detener":
                                detenido, mensaje = "tarjeta", texto
                                break
                            continue
                        procesados.add(item["id"])
                        self._generados.add(str(Path(resultado.salida).resolve()))
                        self._marcar(item, "firmado", None, resultado.salida)
                        firmados.append(
                            {"id": item["id"], "nombre": item["nombre"], "salida": resultado.salida, "modo": resultado.modo}
                        )
                        self._emitir_progreso({**base, "estado": "firmado", "salida": resultado.salida})
            finally:
                firmas_en_sesion = sesion.firmas
        except Exception as e:  # noqa: BLE001 - sin llave o sin sesión no se firma nada
            logger.warning("Lote detenido antes de firmar: %s", e)
            detenido, mensaje = "tarjeta", mensaje_de_error(e)
        # Lo que no se llegó a procesar queda «Pendiente» (también tras cancelar o detenerse).
        pendientes = 0
        for item in items:
            if item["id"] not in procesados:
                self._marcar(item, "pendiente")
                pendientes += 1
        with self._cerrojo_cola:
            omitidos = [
                {"id": i["id"], "nombre": i["nombre"], "mensaje": i["mensaje"]} for i in self._cola if i["estado"] == "omitido"
            ]
            no_validos = [{"id": i["id"], "nombre": i["nombre"], "mensaje": i["mensaje"]} for i in self._cola if not i["valido"]]
        resumen = {
            "total": total,
            "firmados": firmados,
            "errores": errores + no_validos,
            "omitidos": omitidos,
            "pendientes": pendientes,
            "detenido": detenido,
            "mensaje": mensaje,
            "carpeta": str(carpeta) if carpeta else None,
            "firmas_en_sesion": firmas_en_sesion,
        }
        logger.info(
            "Lote terminado: %d firmado(s), %d con error, %d pendiente(s); firmas en la sesión CNG: %d.",
            len(firmados),
            len(errores),
            pendientes,
            firmas_en_sesion,
        )
        self._emitir_progreso({"evento": "fin", "resumen": resumen})
        return {"ok": True, **resumen, "cola": self._cola_publica()}

    # --- validación -------------------------------------------------------

    def validar(self, ruta: str | None = None) -> dict:
        """Valida las firmas del PDF indicado o, si no se indica, del abierto."""
        nombre = None
        visible = ruta
        if not ruta:
            if not self._pdf:
                return {"ok": False, "mensaje": "Abra primero un PDF."}
            ruta = self._pdf["archivo"]
            nombre = self._pdf["nombre"]
            visible = self._pdf["ruta"] or nombre
        try:
            firmas = validar_pdf(ruta)
        except FileNotFoundError:
            return {"ok": False, "mensaje": "No se encontró el archivo."}
        except Exception as e:  # noqa: BLE001
            logger.exception("validar")
            return {"ok": False, "mensaje": f"No se pudo validar el PDF: {e}"}
        return {"ok": True, "ruta": str(visible), "nombre": nombre or Path(ruta).name, "firmas": firmas}

    def elegir_y_validar(self) -> dict:
        if self._ventana is None:
            return {"ok": False, "mensaje": "La ventana no está disponible."}
        seleccion = self._ventana.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("Documentos PDF (*.pdf)",)
        )
        if not seleccion:
            return {"ok": False, "cancelado": True}
        return self.validar(seleccion[0])

    # --- archivos generados -----------------------------------------------

    def _permitida(self, ruta: str) -> Path | None:
        p = Path(ruta).resolve()
        return p if str(p) in self._generados and p.exists() else None

    def abrir_archivo(self, ruta: str) -> dict:
        """Abre un PDF firmado por esta sesión con el visor predeterminado."""
        p = self._permitida(ruta)
        if p is None:
            return {"ok": False, "mensaje": "Solo se pueden abrir los archivos firmados en esta sesión."}
        os.startfile(p)  # noqa: S606 - ruta validada: generada por la propia aplicación
        return {"ok": True}

    def mostrar_en_carpeta(self, ruta: str) -> dict:
        p = self._permitida(ruta)
        if p is None:
            return {"ok": False, "mensaje": "Solo se pueden mostrar los archivos firmados en esta sesión."}
        # Cadena, no lista: explorer.exe solo entiende /select,"ruta". Con una
        # lista, subprocess entrecomilla todo el argumento cuando la ruta tiene
        # espacios y Explorer abre «Documentos». Una ruta de Windows no puede
        # contener comillas, así que no hay inyección posible.
        subprocess.Popen(f'explorer /select,"{p}"')  # noqa: S603,S607
        return {"ok": True}


def _al_iniciar(ventana, api: Api):
    """Se ejecuta cuando la interfaz está lista: registra el arrastrar y soltar."""
    try:
        from webview.dom import DOMEventHandler

        documento = ventana.dom.document
        # stop_propagation=False: si se detiene aquí, el «drop» nunca llega a los
        # listeners de `window` en app.js y el aviso de arrastre queda congelado.
        documento.events.dragover += DOMEventHandler(lambda e: None, True, False, debounce=500)
        documento.events.drop += DOMEventHandler(api._al_soltar, True, False)
        logger.info("Arrastrar y soltar: controladores dragover/drop registrados en el documento.")
    except Exception:  # noqa: BLE001 - sin arrastrar y soltar sigue el botón «Abrir»
        logger.warning("Arrastrar y soltar no disponible", exc_info=True)
        ventana.evaluate_js("window.firmador && window.firmador.sinArrastrar()")


def crear_ventana(api: Api):
    # Windows no siempre registra .mjs; sin esto WebView2 rechaza los módulos de pdf.js.
    mimetypes.add_type("text/javascript", ".mjs")
    mimetypes.add_type("application/wasm", ".wasm")
    indice = ruta_recursos() / "ui" / "index.html"
    ventana = webview.create_window(
        TITULO,
        url=str(indice),
        js_api=api,
        width=1280,
        height=820,
        min_size=(960, 620),
        background_color="#f6f7f9",
        text_select=False,
    )
    api._asignar_ventana(ventana)
    return ventana


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    api = Api()
    ventana = crear_ventana(api)
    webview.start(
        _al_iniciar,
        (ventana, api),
        private_mode=True,
        debug="--debug" in sys.argv,
        icon=str(RUTA_ICONO),
    )


if __name__ == "__main__":
    main()
