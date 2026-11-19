"""
Diagnóstico de por qué el DNIe no está disponible, y reintentos de los errores
pasajeros de la tarjeta.

- `recolectar_hechos()`: capa mínima sobre WinSCard (winscard.dll), SOLO
  lectura: lista los lectores, lee su estado y el ATR, y busca el nombre de
  tarjeta registrado para ese ATR (lo registra el minidriver de RENIEC). Nunca
  se conecta a la tarjeta ni pide el PIN, y nunca lanza.
- `diagnosticar(...)`: función PURA que convierte esos hechos (más el
  resultado de enumerar las llaves y los certificados) en un código y un
  mensaje para el usuario.
- `con_reintentos(...)`: repite una operación ante errores pasajeros de la
  tarjeta. Nunca repite errores de PIN ni cancelaciones (bloquearían el DNIe).
"""
from __future__ import annotations

import ctypes
import datetime as dt
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("firmador.dnie")

# Estados de SCARD_READERSTATE.dwEventState
SCARD_STATE_UNAWARE = 0x0
SCARD_STATE_EMPTY = 0x10
SCARD_STATE_PRESENT = 0x20
SCARD_STATE_EXCLUSIVE = 0x80
SCARD_STATE_INUSE = 0x100
SCARD_STATE_MUTE = 0x200
SCARD_SCOPE_USER = 0

# Códigos de WinSCard / CNG
SCARD_E_SHARING_VIOLATION = 0x8010000B
SCARD_E_TIMEOUT = 0x8010000A
SCARD_E_NO_SERVICE = 0x8010001D
SCARD_E_SERVICE_STOPPED = 0x8010001E
SCARD_E_NO_READERS_AVAILABLE = 0x8010002E
SCARD_W_RESET_CARD = 0x80100068
SCARD_W_REMOVED_CARD = 0x80100069
NTE_NOT_FOUND = 0x80090011

_CODIGOS_SIN_SERVICIO = {SCARD_E_NO_SERVICE, SCARD_E_SERVICE_STOPPED}

# Reintentos: solo condiciones pasajeras de la tarjeta o del lector.
REINTENTABLES = frozenset({SCARD_E_SHARING_VIOLATION, SCARD_W_RESET_CARD, SCARD_W_REMOVED_CARD, NTE_NOT_FOUND})
# Jamás se repiten: PIN incorrecto o bloqueado y cancelaciones (repetir podría bloquear el DNIe).
NUNCA_REINTENTAR = frozenset({0x8010006B, 0x8010006C, 0x8010006E, 0x800704C7})
REINTENTOS = 3
ESPERA = 1.0  # segundos entre intentos

MENSAJES = {
    "sin_servicio": "El servicio de tarjetas inteligentes de Windows no está activo.",
    "sin_lector": "No se detecta ningún lector de tarjetas. Conéctelo e intente de nuevo.",
    "sin_tarjeta": "El lector está conectado pero no tiene el DNIe insertado.",
    "tarjeta_muda": "El DNIe no responde. Retírelo y vuelva a insertarlo.",
    "en_uso_exclusivo": "Otro programa está usando el DNIe. Ciérrelo e intente de nuevo.",
    "sin_minidriver": "El DNIe está insertado pero falta el software del DNIe de RENIEC en esta PC.",
    "sin_certificados": (
        "El DNIe se lee correctamente pero no tiene certificados digitales activos. "
        "Debe activarlos o renovarlos en RENIEC."
    ),
    "certificado_vencido": "El certificado de firma del DNIe venció el {fecha}. Debe renovarlo en RENIEC.",
    "ok": "El DNIe está listo para firmar.",
}


@dataclass
class Lector:
    """Un lector de tarjetas y lo que WinSCard dice de él."""

    nombre: str
    estado: int  # SCARD_STATE_* (dwEventState)
    atr: bytes = b""
    # Nombre registrado para el ATR: "" = ninguno (falta el minidriver), None = no se pudo consultar.
    tarjeta: str | None = None

    def __str__(self) -> str:
        banderas = [n for n, b in _BANDERAS if self.estado & b]
        return (
            f"«{self.nombre}» estado=0x{self.estado:X} ({'|'.join(banderas) or '-'}), "
            f"ATR={self.atr.hex() or '-'}, tarjeta={self.tarjeta!r}"
        )


_BANDERAS = [
    ("EMPTY", SCARD_STATE_EMPTY),
    ("PRESENT", SCARD_STATE_PRESENT),
    ("EXCLUSIVE", SCARD_STATE_EXCLUSIVE),
    ("INUSE", SCARD_STATE_INUSE),
    ("MUTE", SCARD_STATE_MUTE),
]


@dataclass
class Certificado:
    """Lo que el diagnóstico necesita de un certificado del DNIe."""

    es_de_firma: bool
    no_despues: dt.datetime


def _resultado(codigo: str, **datos) -> tuple[str, str]:
    return codigo, MENSAJES[codigo].format(**datos)


def diagnosticar(
    *,
    error_lectores: int | None = None,
    lectores: list[Lector] | None = None,
    estado_llaves: int | None = None,
    llaves: int | None = None,
    certificados: list[Certificado] | None = None,
    ahora: dt.datetime | None = None,
) -> tuple[str, str | None]:
    """
    (código, mensaje para el usuario) a partir de hechos simples. Función pura.

    `error_lectores`: código de SCardEstablishContext / SCardListReaders (None si
    funcionó). `lectores`: None si no se consultaron. `estado_llaves`: código de
    la enumeración de llaves (0 o None si funcionó); `llaves`: cuántas tienen
    certificado. `certificados`: los encontrados.

    Precedencia, de lo más básico a lo más específico: servicio, lector,
    tarjeta insertada, tarjeta que responde, minidriver, uso exclusivo,
    certificados ausentes y certificado vencido. Si los hechos no alcanzan
    para afirmar nada devuelve ("desconocido", None) y se usa el error original.
    """
    if error_lectores in _CODIGOS_SIN_SERVICIO:
        return _resultado("sin_servicio")
    if error_lectores == SCARD_E_NO_READERS_AVAILABLE or (error_lectores is None and lectores == []):
        return _resultado("sin_lector")

    if error_lectores is None and lectores:
        presentes = [l for l in lectores if l.estado & SCARD_STATE_PRESENT]
        if not presentes:
            mudos = any(l.estado & SCARD_STATE_MUTE for l in lectores)
            return _resultado("tarjeta_muda" if mudos else "sin_tarjeta")
        responden = [l for l in presentes if not l.estado & SCARD_STATE_MUTE]
        if not responden:
            return _resultado("tarjeta_muda")
        con_software = [l for l in responden if l.tarjeta != ""]
        if not con_software:
            return _resultado("sin_minidriver")
        if estado_llaves == SCARD_E_SHARING_VIOLATION or all(
            l.estado & SCARD_STATE_EXCLUSIVE for l in con_software
        ):
            return _resultado("en_uso_exclusivo")
        if estado_llaves == NTE_NOT_FOUND or llaves == 0:
            return _resultado("sin_certificados")

    if certificados:
        de_firma = [c for c in certificados if c.es_de_firma]
        ahora = ahora or dt.datetime.now(dt.timezone.utc)
        if de_firma and all(c.no_despues < ahora for c in de_firma):
            vencio = max(c.no_despues for c in de_firma)
            return _resultado("certificado_vencido", fecha=f"{vencio:%d/%m/%Y}")

    if estado_llaves not in (None, 0):
        return "desconocido", None
    return _resultado("ok")


# --- reintentos --------------------------------------------------------------------


def reintentable(error: BaseException) -> bool:
    codigo = getattr(error, "codigo", None)
    return codigo in REINTENTABLES and codigo not in NUNCA_REINTENTAR


def con_reintentos(operacion, *, reintentos=REINTENTOS, espera=ESPERA, dormir=time.sleep, descripcion="Operación"):
    """
    Ejecuta `operacion()`; ante un error pasajero (`REINTENTABLES`) la repite
    hasta `reintentos` veces, con `espera` segundos entre intentos. Cualquier
    otro error se propaga de inmediato. No usar para firmar (NCryptSignHash).
    """
    intento = 0
    while True:
        intento += 1
        try:
            resultado = operacion()
        except Exception as e:  # noqa: BLE001 - solo se reintentan los códigos conocidos
            if not reintentable(e):
                raise
            if intento > reintentos:
                logger.warning("%s: intento %d falló (0x%08X); no se reintenta más.", descripcion, intento, e.codigo)
                raise
            logger.warning(
                "%s: intento %d de %d falló con 0x%08X (%s); se reintenta en %.1f s.",
                descripcion, intento, reintentos + 1, e.codigo, e, espera,
            )
            dormir(espera)
            continue
        if intento > 1:
            logger.info("%s: correcto en el intento %d.", descripcion, intento)
        return resultado


# --- WinSCard (solo lectura) -------------------------------------------------------


def _multicadena(texto: str) -> list[str]:
    """Lista de una multicadena de Windows («a\\0b\\0\\0»)."""
    return [parte for parte in (texto or "").split("\0") if parte]


class _EstadoLector(ctypes.Structure):  # SCARD_READERSTATEW
    _fields_ = [
        ("szReader", ctypes.c_wchar_p),
        ("pvUserData", ctypes.c_void_p),
        ("dwCurrentState", ctypes.c_uint32),
        ("dwEventState", ctypes.c_uint32),
        ("cbAtr", ctypes.c_uint32),
        ("rgbAtr", ctypes.c_ubyte * 36),
    ]


def _winscard():
    from ctypes import wintypes

    dll = ctypes.WinDLL("winscard.dll")
    contexto = ctypes.c_size_t  # SCARDCONTEXT es un ULONG_PTR
    dll.SCardEstablishContext.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(contexto)]
    dll.SCardListReadersW.argtypes = [contexto, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    dll.SCardGetStatusChangeW.argtypes = [contexto, wintypes.DWORD, ctypes.POINTER(_EstadoLector), wintypes.DWORD]
    dll.SCardListCardsW.argtypes = [
        contexto, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)
    ]
    dll.SCardReleaseContext.argtypes = [contexto]
    for nombre in ("SCardEstablishContext", "SCardListReadersW", "SCardGetStatusChangeW", "SCardListCardsW",
                   "SCardReleaseContext"):
        getattr(dll, nombre).restype = ctypes.c_long
    return dll, contexto, wintypes


def _leer_multicadena(funcion, *previos) -> tuple[int, list[str]]:
    """Llama dos veces a una función SCardList*W: tamaño y luego datos."""
    from ctypes import wintypes

    tam = wintypes.DWORD(0)
    status = funcion(*previos, None, ctypes.byref(tam)) & 0xFFFFFFFF
    if status != 0:
        return status, []
    buf = ctypes.create_unicode_buffer(max(tam.value, 2))
    status = funcion(*previos, buf, ctypes.byref(tam)) & 0xFFFFFFFF
    if status != 0:
        return status, []
    return 0, _multicadena(buf[: tam.value])


def recolectar_hechos() -> dict:
    """
    {"error_lectores": código o None, "lectores": [Lector] o None}. Solo lectura
    (sin conectarse a la tarjeta ni pedir PIN). Nunca lanza: si algo falla
    inesperadamente, lo registra y devuelve lo que se sepa.
    """
    hechos = {"error_lectores": None, "lectores": None}
    try:
        dll, tipo_contexto, wintypes = _winscard()
    except Exception as e:  # noqa: BLE001
        logger.warning("WinSCard no disponible para el diagnóstico: %s", e)
        hechos["error_lectores"] = -1
        return hechos
    contexto = tipo_contexto(0)
    status = dll.SCardEstablishContext(SCARD_SCOPE_USER, None, None, ctypes.byref(contexto)) & 0xFFFFFFFF
    if status != 0:
        hechos["error_lectores"] = status
        return hechos
    try:
        status, nombres = _leer_multicadena(dll.SCardListReadersW, contexto, None)
        if status == SCARD_E_NO_READERS_AVAILABLE:
            hechos["lectores"] = []
        elif status != 0:
            hechos["error_lectores"] = status
            return hechos
        else:
            hechos["lectores"] = [_leer_lector(dll, contexto, n) for n in nombres]
    except Exception as e:  # noqa: BLE001 - el diagnóstico nunca debe ocultar el error original
        logger.warning("Fallo inesperado al consultar los lectores: %s", e)
        hechos["error_lectores"] = hechos["error_lectores"] or -1
        hechos["lectores"] = None
    finally:
        try:
            dll.SCardReleaseContext(contexto)
        except Exception:  # noqa: BLE001
            pass
    return hechos


def _leer_lector(dll, contexto, nombre: str) -> Lector:
    estado = _EstadoLector(szReader=nombre, dwCurrentState=SCARD_STATE_UNAWARE)
    status = dll.SCardGetStatusChangeW(contexto, 0, ctypes.byref(estado), 1) & 0xFFFFFFFF
    if status not in (0, SCARD_E_TIMEOUT):
        logger.warning("SCardGetStatusChangeW falló con 0x%08X en «%s».", status, nombre)
        return Lector(nombre=nombre, estado=0, tarjeta=None)
    atr = bytes(estado.rgbAtr[: min(estado.cbAtr, 36)])
    lector = Lector(nombre=nombre, estado=estado.dwEventState, atr=atr)
    if atr and estado.dwEventState & SCARD_STATE_PRESENT:
        buf_atr = (ctypes.c_ubyte * len(atr)).from_buffer_copy(atr)
        status, tarjetas = _leer_multicadena(dll.SCardListCardsW, contexto, buf_atr, None, 0)
        if status == 0:
            lector.tarjeta = tarjetas[0] if tarjetas else ""
        else:
            logger.warning("SCardListCardsW falló con 0x%08X para el ATR %s.", status, atr.hex())
    return lector
