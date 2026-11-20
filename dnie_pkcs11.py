"""
Firma con el DNIe 3.0 por PKCS#11, con el módulo de IDEMIA que instala el
controlador del DNIe 3.0 de RENIEC (`idplug-pkcs11.dll`).

Por qué: la llave de firma del DNIe 3.0 no acepta el PIN que la aplicación
entrega por CNG (NTE_SILENT_CONTEXT), así que por CNG Windows pide el PIN en
cada página. Por PKCS#11 se hace UN login con el PIN que la aplicación pidió
una vez y, si la llave exige autenticarse en cada uso (CKA_ALWAYS_AUTHENTICATE),
un login de contexto (CKU_CONTEXT_SPECIFIC) con el mismo PIN en memoria antes de
cada firma. El usuario escribe el PIN una sola vez.

Enlace mínimo con ctypes, sin dependencias nuevas. ABI de Cryptoki en Windows:
CK_ULONG es un unsigned long de 32 bits (también en 64 bits), las estructuras
van empaquetadas a 1 byte y las llamadas son cdecl. `_Cryptoki` envuelve la
lista de funciones (C_GetFunctionList); las pruebas la reemplazan por un falso.

- `ruta_modulo()`: FIRMADOR_PKCS11 (si está definida; una ruta que no existe
  desactiva la vía) o `%ProgramFiles%\\IDEMIA\\IDPlugClassic\\DLLs\\idplug-pkcs11.dll`.
  Si no existe, la vía no está disponible; no es un error.
- `buscar_llave(cert_der)`: sin PIN, busca el token que tiene el certificado
  que eligió CNG. Los DNIe 1.0 y 2.0, que este módulo no reconoce, quedan en CNG.
- `abrir_sesion(llave, pin)`: la sesión PKCS#11 ya autenticada, o None si hay
  que seguir con CNG. Un PIN rechazado nunca sigue con CNG (sería otro intento
  del PIN, que podría bloquear el DNIe).

El PIN vive solo en un búfer de ctypes que se llena de ceros al cerrar o en
cuanto falla; nunca se registra, ni va en un error, ni se devuelve.
"""
import ctypes
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dnie_cng import CODIGOS_PIN, ErrorCNG, FirmanteDeSesion, LlaveTarjeta

if sys.platform != "win32":
    raise ImportError("dnie_pkcs11 solo funciona en Windows")

logger = logging.getLogger("firmador.pkcs11")

VARIABLE_MODULO = "FIRMADOR_PKCS11"
_RUTA_IDEMIA = Path("IDEMIA") / "IDPlugClassic" / "DLLs" / "idplug-pkcs11.dll"

# --- Tipos y constantes de Cryptoki (PKCS#11 v2.x) ----------------------------------

CK_ULONG = ctypes.c_ulong  # 32 bits en Windows
CK_RV = CK_ULONG
CK_BBOOL = ctypes.c_ubyte
CK_UNAVAILABLE_INFORMATION = 0xFFFFFFFF

CKR_OK = 0x0
CKR_DEVICE_REMOVED = 0x32
CKR_FUNCTION_NOT_SUPPORTED = 0x54
CKR_PIN_INCORRECT = 0xA0
CKR_PIN_INVALID = 0xA1
CKR_PIN_LEN_RANGE = 0xA2
CKR_PIN_EXPIRED = 0xA3
CKR_PIN_LOCKED = 0xA4
CKR_TOKEN_NOT_PRESENT = 0xE0
CKR_USER_ALREADY_LOGGED_IN = 0x100
CKR_USER_NOT_LOGGED_IN = 0x101
CKR_ATTRIBUTE_SENSITIVE = 0x11
CKR_ATTRIBUTE_TYPE_INVALID = 0x12
CKR_CRYPTOKI_ALREADY_INITIALIZED = 0x191

CKF_TOKEN_PRESENT = 0x1  # de CK_SLOT_INFO
CKF_OS_LOCKING_OK = 0x2  # de CK_C_INITIALIZE_ARGS
CKF_SERIAL_SESSION = 0x4
CKF_PROTECTED_AUTHENTICATION_PATH = 0x100  # de CK_TOKEN_INFO: el lector pide el PIN en su teclado
CKF_USER_PIN_COUNT_LOW = 0x10000
CKF_USER_PIN_FINAL_TRY = 0x20000
CKF_USER_PIN_LOCKED = 0x40000

CKU_USER = 1
CKU_CONTEXT_SPECIFIC = 2

CKO_CERTIFICATE = 1
CKO_PRIVATE_KEY = 3
CKA_CLASS = 0x0
CKA_VALUE = 0x11
CKA_ID = 0x102
CKA_ALWAYS_AUTHENTICATE = 0x202

CKM_RSA_PKCS = 0x1
CKM_SHA256_RSA_PKCS = 0x40

# PIN rechazado: se detiene en el acto y nunca se reintenta ni se prueba CNG.
CODIGOS_PIN_PKCS11 = {CKR_PIN_INCORRECT, CKR_PIN_INVALID, CKR_PIN_LEN_RANGE, CKR_PIN_EXPIRED, CKR_PIN_LOCKED}
# Código de Windows equivalente: la aplicación muestra el mismo mensaje y el lote
# decide igual (detenerse) que con CNG.
_EQUIVALENTE_WINDOWS = {
    CKR_PIN_INCORRECT: 0x8010006B,  # SCARD_W_WRONG_CHV
    CKR_PIN_INVALID: 0x8010006B,
    CKR_PIN_LEN_RANGE: 0x8010006B,
    CKR_PIN_EXPIRED: 0x8010006C,  # SCARD_W_CHV_BLOCKED: hay que ir a RENIEC
    CKR_PIN_LOCKED: 0x8010006C,
    CKR_DEVICE_REMOVED: 0x80100069,  # SCARD_W_REMOVED_CARD
    CKR_TOKEN_NOT_PRESENT: 0x8010000C,  # SCARD_E_NO_SMARTCARD
}
_NOMBRES_CKR = {
    CKR_PIN_INCORRECT: "CKR_PIN_INCORRECT",
    CKR_PIN_INVALID: "CKR_PIN_INVALID",
    CKR_PIN_LEN_RANGE: "CKR_PIN_LEN_RANGE",
    CKR_PIN_EXPIRED: "CKR_PIN_EXPIRED",
    CKR_PIN_LOCKED: "CKR_PIN_LOCKED",
    CKR_DEVICE_REMOVED: "CKR_DEVICE_REMOVED",
    CKR_TOKEN_NOT_PRESENT: "CKR_TOKEN_NOT_PRESENT",
    CKR_USER_ALREADY_LOGGED_IN: "CKR_USER_ALREADY_LOGGED_IN",
    CKR_USER_NOT_LOGGED_IN: "CKR_USER_NOT_LOGGED_IN",
    CKR_FUNCTION_NOT_SUPPORTED: "CKR_FUNCTION_NOT_SUPPORTED",
    0x6: "CKR_FUNCTION_FAILED",
    0x30: "CKR_DEVICE_ERROR",
    0x70: "CKR_MECHANISM_INVALID",
    0xE1: "CKR_TOKEN_NOT_RECOGNIZED",
    0x190: "CKR_CRYPTOKI_NOT_INITIALIZED",
}

# DigestInfo (DER) que precede al hash en una firma RSA PKCS#1 v1.5 (RFC 8017, 9.2).
_PREFIJOS_DIGEST_INFO = {
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}
_LARGO_HASH = {"sha256": 32, "sha384": 48, "sha512": 64}


class CK_VERSION(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("major", ctypes.c_ubyte), ("minor", ctypes.c_ubyte)]


class CK_SLOT_INFO(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("slotDescription", ctypes.c_ubyte * 64),
        ("manufacturerID", ctypes.c_ubyte * 32),
        ("flags", CK_ULONG),
        ("hardwareVersion", CK_VERSION),
        ("firmwareVersion", CK_VERSION),
    ]


class CK_TOKEN_INFO(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("label", ctypes.c_ubyte * 32),
        ("manufacturerID", ctypes.c_ubyte * 32),
        ("model", ctypes.c_ubyte * 16),
        ("serialNumber", ctypes.c_ubyte * 16),
        ("flags", CK_ULONG),
        ("ulMaxSessionCount", CK_ULONG),
        ("ulSessionCount", CK_ULONG),
        ("ulMaxRwSessionCount", CK_ULONG),
        ("ulRwSessionCount", CK_ULONG),
        ("ulMaxPinLen", CK_ULONG),
        ("ulMinPinLen", CK_ULONG),
        ("ulTotalPublicMemory", CK_ULONG),
        ("ulFreePublicMemory", CK_ULONG),
        ("ulTotalPrivateMemory", CK_ULONG),
        ("ulFreePrivateMemory", CK_ULONG),
        ("hardwareVersion", CK_VERSION),
        ("firmwareVersion", CK_VERSION),
        ("utcTime", ctypes.c_ubyte * 16),
    ]


class CK_ATTRIBUTE(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("type", CK_ULONG), ("pValue", ctypes.c_void_p), ("ulValueLen", CK_ULONG)]


class CK_MECHANISM(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("mechanism", CK_ULONG), ("pParameter", ctypes.c_void_p), ("ulParameterLen", CK_ULONG)]


class CK_C_INITIALIZE_ARGS(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("CreateMutex", ctypes.c_void_p),
        ("DestroyMutex", ctypes.c_void_p),
        ("LockMutex", ctypes.c_void_p),
        ("UnlockMutex", ctypes.c_void_p),
        ("flags", CK_ULONG),
        ("pReserved", ctypes.c_void_p),
    ]


# Orden de CK_FUNCTION_LIST (PKCS#11 v2.x) hasta C_Sign; las siguientes no se usan.
_LISTA_DE_FUNCIONES = (
    "C_Initialize", "C_Finalize", "C_GetInfo", "C_GetFunctionList",
    "C_GetSlotList", "C_GetSlotInfo", "C_GetTokenInfo", "C_GetMechanismList", "C_GetMechanismInfo",
    "C_InitToken", "C_InitPIN", "C_SetPIN",
    "C_OpenSession", "C_CloseSession", "C_CloseAllSessions", "C_GetSessionInfo",
    "C_GetOperationState", "C_SetOperationState",
    "C_Login", "C_Logout",
    "C_CreateObject", "C_CopyObject", "C_DestroyObject", "C_GetObjectSize",
    "C_GetAttributeValue", "C_SetAttributeValue",
    "C_FindObjectsInit", "C_FindObjects", "C_FindObjectsFinal",
    "C_EncryptInit", "C_Encrypt", "C_EncryptUpdate", "C_EncryptFinal",
    "C_DecryptInit", "C_Decrypt", "C_DecryptUpdate", "C_DecryptFinal",
    "C_DigestInit", "C_Digest", "C_DigestUpdate", "C_DigestKey", "C_DigestFinal",
    "C_SignInit", "C_Sign",
)  # fmt: skip


class CK_FUNCTION_LIST(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("version", CK_VERSION)] + [(nombre, ctypes.c_void_p) for nombre in _LISTA_DE_FUNCIONES]


_P_ULONG = ctypes.POINTER(CK_ULONG)
_PROTOTIPOS = {
    "C_Initialize": (ctypes.c_void_p,),
    "C_Finalize": (ctypes.c_void_p,),
    "C_GetSlotList": (CK_BBOOL, _P_ULONG, _P_ULONG),
    "C_GetSlotInfo": (CK_ULONG, ctypes.POINTER(CK_SLOT_INFO)),
    "C_GetTokenInfo": (CK_ULONG, ctypes.POINTER(CK_TOKEN_INFO)),
    "C_GetMechanismList": (CK_ULONG, _P_ULONG, _P_ULONG),
    "C_OpenSession": (CK_ULONG, CK_ULONG, ctypes.c_void_p, ctypes.c_void_p, _P_ULONG),
    "C_CloseSession": (CK_ULONG,),
    "C_Login": (CK_ULONG, CK_ULONG, ctypes.c_void_p, CK_ULONG),
    "C_Logout": (CK_ULONG,),
    "C_GetAttributeValue": (CK_ULONG, CK_ULONG, ctypes.POINTER(CK_ATTRIBUTE), CK_ULONG),
    "C_FindObjectsInit": (CK_ULONG, ctypes.POINTER(CK_ATTRIBUTE), CK_ULONG),
    "C_FindObjects": (CK_ULONG, _P_ULONG, CK_ULONG, _P_ULONG),
    "C_FindObjectsFinal": (CK_ULONG,),
    "C_SignInit": (CK_ULONG, ctypes.POINTER(CK_MECHANISM), CK_ULONG),
    "C_Sign": (CK_ULONG, ctypes.c_void_p, CK_ULONG, ctypes.c_void_p, _P_ULONG),
}
_TAM_FIRMA_MAXIMO = 1024  # bytes: RSA de hasta 8192 bits


class ErrorPkcs11(ErrorCNG):
    """
    Una función de Cryptoki devolvió `rv` (CKR_*). `codigo` es el código de
    Windows equivalente si lo hay (PIN, tarjeta retirada), así la aplicación
    muestra el mensaje de siempre; si no, el mismo CKR.
    """

    def __init__(self, operacion: str, rv: int):
        self.rv = rv & 0xFFFFFFFF
        codigo = _EQUIVALENTE_WINDOWS.get(self.rv, self.rv)
        nombre = _NOMBRES_CKR.get(self.rv, "CKR")
        super().__init__(f"{operacion} falló (0x{codigo:08X}) {nombre} [rv 0x{self.rv:X}]", codigo=codigo)


def es_error_de_pin(error: BaseException) -> bool:
    """True si el error es un PIN rechazado (por PKCS#11 o por CNG): nunca se reintenta."""
    if isinstance(error, ErrorPkcs11):
        return error.rv in CODIGOS_PIN_PKCS11
    return isinstance(error, ErrorCNG) and error.codigo in CODIGOS_PIN


def _texto(campo) -> str:
    """Campo de texto de Cryptoki (relleno con espacios, sin terminador)."""
    return bytes(campo).decode("utf-8", errors="replace").rstrip(" \x00")


@dataclass
class InfoSlot:
    descripcion: str
    fabricante: str
    flags: int


@dataclass
class InfoToken:
    etiqueta: str
    fabricante: str
    modelo: str
    serie: str
    flags: int
    pin_minimo: int
    pin_maximo: int


class _Cryptoki:
    """
    La lista de funciones de un módulo PKCS#11 con métodos de Python. Cada
    método lanza `ErrorPkcs11` si la función no devuelve CKR_OK. No guarda
    estado de la tarjeta: eso lo hacen `buscar_llave` y `SesionPkcs11`.
    """

    def __init__(self, ruta: str):
        self.ruta = ruta
        self._dll = ctypes.CDLL(ruta)  # cdecl
        obtener = self._dll.C_GetFunctionList
        obtener.argtypes = [ctypes.POINTER(ctypes.POINTER(CK_FUNCTION_LIST))]
        obtener.restype = CK_RV
        lista = ctypes.POINTER(CK_FUNCTION_LIST)()
        rv = obtener(ctypes.byref(lista)) & 0xFFFFFFFF
        if rv != CKR_OK:
            raise ErrorPkcs11("C_GetFunctionList", rv)
        if not lista:
            raise ErrorPkcs11("C_GetFunctionList", CKR_FUNCTION_NOT_SUPPORTED)
        self._lista = lista.contents
        self.version = f"{self._lista.version.major}.{self._lista.version.minor}"
        # La lista guarda la dirección de su propio C_GetFunctionList: si coincide con
        # la exportada, el empaquetado (_pack_ = 1) de la estructura es el correcto.
        self.lista_coherente = self._lista.C_GetFunctionList == ctypes.cast(obtener, ctypes.c_void_p).value
        self._f = {}
        for nombre, argumentos in _PROTOTIPOS.items():
            direccion = getattr(self._lista, nombre)
            if not direccion:
                raise ErrorPkcs11(nombre, CKR_FUNCTION_NOT_SUPPORTED)
            self._f[nombre] = ctypes.CFUNCTYPE(CK_RV, *argumentos)(direccion)

    def _llamar(self, nombre, *argumentos, permitidos=()) -> int:
        rv = self._f[nombre](*argumentos) & 0xFFFFFFFF
        if rv != CKR_OK and rv not in permitidos:
            raise ErrorPkcs11(nombre, rv)
        return rv

    def inicializar(self):
        argumentos = CK_C_INITIALIZE_ARGS(flags=CKF_OS_LOCKING_OK)
        self._llamar("C_Initialize", ctypes.byref(argumentos), permitidos=(CKR_CRYPTOKI_ALREADY_INITIALIZED,))

    def finalizar(self):
        self._llamar("C_Finalize", None)

    def slots(self, con_token: bool) -> list[int]:
        presente = CK_BBOOL(1 if con_token else 0)
        cuantos = CK_ULONG()
        self._llamar("C_GetSlotList", presente, None, ctypes.byref(cuantos))
        if not cuantos.value:
            return []
        lista = (CK_ULONG * cuantos.value)()
        self._llamar("C_GetSlotList", presente, lista, ctypes.byref(cuantos))
        return list(lista[: cuantos.value])

    def info_slot(self, slot: int) -> InfoSlot:
        info = CK_SLOT_INFO()
        self._llamar("C_GetSlotInfo", slot, ctypes.byref(info))
        return InfoSlot(_texto(info.slotDescription), _texto(info.manufacturerID), info.flags)

    def info_token(self, slot: int) -> InfoToken:
        info = CK_TOKEN_INFO()
        self._llamar("C_GetTokenInfo", slot, ctypes.byref(info))
        return InfoToken(
            _texto(info.label),
            _texto(info.manufacturerID),
            _texto(info.model),
            _texto(info.serialNumber),
            info.flags,
            info.ulMinPinLen,
            info.ulMaxPinLen,
        )

    def mecanismos(self, slot: int) -> list[int]:
        cuantos = CK_ULONG()
        self._llamar("C_GetMechanismList", slot, None, ctypes.byref(cuantos))
        if not cuantos.value:
            return []
        lista = (CK_ULONG * cuantos.value)()
        self._llamar("C_GetMechanismList", slot, lista, ctypes.byref(cuantos))
        return list(lista[: cuantos.value])

    def abrir_sesion(self, slot: int) -> int:
        sesion = CK_ULONG()
        self._llamar("C_OpenSession", slot, CKF_SERIAL_SESSION, None, None, ctypes.byref(sesion))
        return sesion.value

    def cerrar_sesion(self, sesion: int):
        self._llamar("C_CloseSession", sesion)

    def login(self, sesion: int, tipo: int, pin, largo: int):
        """`pin`: búfer de ctypes con el PIN en UTF-8; `largo`: bytes del PIN, sin terminador."""
        self._llamar("C_Login", sesion, tipo, ctypes.addressof(pin), largo)

    def logout(self, sesion: int):
        self._llamar("C_Logout", sesion)

    def buscar_objetos(self, sesion: int, plantilla: list[tuple[int, object]]) -> list[int]:
        """Objetos que cumplen `plantilla` [(CKA_*, int o bytes)]."""
        atributos = (CK_ATTRIBUTE * len(plantilla))()
        valores = []  # mantiene vivos los búferes mientras dura la búsqueda
        for i, (tipo, valor) in enumerate(plantilla):
            dato = valor if isinstance(valor, bytes) else bytes(CK_ULONG(valor))
            buf = (ctypes.c_ubyte * max(len(dato), 1)).from_buffer_copy(dato.ljust(1, b"\x00"))
            valores.append(buf)
            atributos[i] = CK_ATTRIBUTE(tipo, ctypes.cast(buf, ctypes.c_void_p), len(dato))
        self._llamar("C_FindObjectsInit", sesion, atributos, len(plantilla))
        hallados = []
        try:
            lote, cuantos = (CK_ULONG * 16)(), CK_ULONG()
            while len(hallados) < 256:
                self._llamar("C_FindObjects", sesion, lote, 16, ctypes.byref(cuantos))
                if not cuantos.value:
                    break
                hallados.extend(lote[: cuantos.value])
        finally:
            try:
                self._llamar("C_FindObjectsFinal", sesion)
            except ErrorPkcs11 as e:
                logger.info("PKCS#11: %s", e)
        return hallados

    def atributo(self, sesion: int, objeto: int, tipo: int) -> bytes | None:
        """Valor de un atributo, o None si el objeto no lo tiene o es sensible."""
        attr = CK_ATTRIBUTE(tipo, None, 0)
        no_disponible = (CKR_ATTRIBUTE_SENSITIVE, CKR_ATTRIBUTE_TYPE_INVALID)
        if self._llamar("C_GetAttributeValue", sesion, objeto, ctypes.byref(attr), 1, permitidos=no_disponible):
            return None
        if attr.ulValueLen == CK_UNAVAILABLE_INFORMATION:
            return None
        buf = (ctypes.c_ubyte * max(attr.ulValueLen, 1))()
        attr.pValue = ctypes.cast(buf, ctypes.c_void_p)
        self._llamar("C_GetAttributeValue", sesion, objeto, ctypes.byref(attr), 1)
        return bytes(buf[: attr.ulValueLen])

    def iniciar_firma(self, sesion: int, mecanismo: int, llave: int):
        self._llamar("C_SignInit", sesion, ctypes.byref(CK_MECHANISM(mecanismo, None, 0)), llave)

    def firmar(self, sesion: int, datos: bytes) -> bytes:
        entrada = (ctypes.c_ubyte * len(datos)).from_buffer_copy(datos)
        salida, tam = (ctypes.c_ubyte * _TAM_FIRMA_MAXIMO)(), CK_ULONG(_TAM_FIRMA_MAXIMO)
        self._llamar("C_Sign", sesion, entrada, len(datos), salida, ctypes.byref(tam))
        return bytes(salida[: tam.value])


def ruta_modulo() -> str | None:
    """Ruta del módulo PKCS#11 de IDEMIA, o None si no está instalado."""
    propia = os.environ.get(VARIABLE_MODULO)
    if propia is not None:
        candidata = Path(propia)
    else:
        candidata = Path(os.environ.get("ProgramFiles") or r"C:\Program Files") / _RUTA_IDEMIA
    return str(candidata) if candidata.is_file() else None


def digest_info(resumen: bytes, algoritmo: str) -> bytes:
    """DigestInfo(algoritmo) || resumen: lo que firma CKM_RSA_PKCS para dar PKCS#1 v1.5."""
    prefijo = _PREFIJOS_DIGEST_INFO.get(algoritmo)
    if prefijo is None:
        raise ErrorCNG(f"Hash no soportado por PKCS#11: {algoritmo}")
    if len(resumen) != _LARGO_HASH[algoritmo]:
        raise ErrorCNG(f"Resumen {algoritmo} de largo inválido: {len(resumen)} bytes")
    return prefijo + resumen


def _describir_flags_token(flags: int) -> str:
    avisos = [
        texto
        for flag, texto in (
            (CKF_USER_PIN_LOCKED, "PIN bloqueado"),
            (CKF_USER_PIN_FINAL_TRY, "último intento del PIN"),
            (CKF_USER_PIN_COUNT_LOW, "PIN con intentos fallidos"),
            (CKF_PROTECTED_AUTHENTICATION_PATH, "PIN en el teclado del lector"),
        )
        if flags & flag
    ]
    return f"flags 0x{flags:X}" + (f" ({', '.join(avisos)})" if avisos else "")


def _finalizar(ck):
    try:
        ck.finalizar()
    except Exception as e:  # noqa: BLE001 - cerrar nunca oculta el resultado
        logger.info("PKCS#11: C_Finalize: %s", e)


def _cerrar_sesion(ck, sesion):
    try:
        ck.cerrar_sesion(sesion)
    except Exception as e:  # noqa: BLE001
        logger.info("PKCS#11: C_CloseSession: %s", e)


@dataclass
class Ubicacion:
    """Dónde está la llave en el módulo PKCS#11: slot y CKA_ID del certificado elegido."""

    ruta: str
    slot: int
    id_llave: bytes
    token: str


def _buscar_en_slot(ck, slot: int, cert_der: bytes) -> bytes | None:
    """CKA_ID del certificado `cert_der` en el token del slot, o None. Sin PIN."""
    sesion = ck.abrir_sesion(slot)
    try:
        for objeto in ck.buscar_objetos(sesion, [(CKA_CLASS, CKO_CERTIFICATE)]):
            if ck.atributo(sesion, objeto, CKA_VALUE) == cert_der:
                return ck.atributo(sesion, objeto, CKA_ID) or b""
        return None
    finally:
        _cerrar_sesion(ck, sesion)


def buscar_llave(cert_der: bytes) -> Ubicacion | None:
    """
    Busca, sin PIN, el token del módulo de IDEMIA que tiene el certificado que
    eligió CNG. Devuelve su ubicación o None (sin módulo, sin token, sin
    coincidencia o ante cualquier fallo, que se registra). Nunca lanza.
    """
    ruta = ruta_modulo()
    if ruta is None:
        logger.info("PKCS#11: no está el módulo de IDEMIA (%s); se usa CNG.", _RUTA_IDEMIA.name)
        return None
    inicio = time.monotonic()
    ck = None
    inicializado = False
    try:
        ck = _Cryptoki(ruta)
        ck.inicializar()
        inicializado = True
        todos, con_token = ck.slots(False), ck.slots(True)
        logger.info(
            "PKCS#11: módulo %s (Cryptoki %s, lista de funciones %s); %d slot(s), %d con token.",
            ruta,
            ck.version,
            "coherente" if ck.lista_coherente else "SIN VERIFICAR",
            len(todos),
            len(con_token),
        )
        for slot in con_token:
            try:
                info = ck.info_token(slot)
                logger.info(
                    "PKCS#11: slot %d: token «%s» (%s %s), %s.",
                    slot, info.etiqueta, info.fabricante, info.modelo, _describir_flags_token(info.flags),
                )
                id_llave = _buscar_en_slot(ck, slot, cert_der)
            except Exception as e:  # noqa: BLE001 - un token que falla no impide revisar los demás
                logger.info("PKCS#11: slot %d omitido (%s: %s).", slot, type(e).__name__, e)
                continue
            if id_llave is None:
                logger.info("PKCS#11: slot %d: el token no tiene el certificado elegido.", slot)
                continue
            if not id_llave:
                logger.info("PKCS#11: slot %d: el certificado no tiene CKA_ID; se omite.", slot)
                continue
            logger.info("PKCS#11: certificado hallado en el slot %d en %d ms.", slot, _ms(inicio))
            return Ubicacion(ruta, slot, id_llave, info.etiqueta)
        logger.info("PKCS#11: ningún token tiene el certificado elegido (%d ms); se usa CNG.", _ms(inicio))
        return None
    except Exception as e:  # noqa: BLE001 - sin PKCS#11 se sigue con CNG
        logger.warning("PKCS#11: no se pudo buscar el certificado (%s: %s); se usa CNG.", type(e).__name__, e)
        return None
    finally:
        if inicializado:
            _finalizar(ck)


def _ms(inicio: float) -> int:
    return int((time.monotonic() - inicio) * 1000)


class SesionPkcs11:
    """
    Firma muchas veces con UN login (CKU_USER) en el módulo PKCS#11:

        sesion = SesionPkcs11(ubicacion, pin, llave)
        sesion.abrir()                 # C_Initialize, sesión y C_Login una vez
        with sesion as firmante:       # FirmanteDeSesion para pyHanko
            firmar_pdf(..., firmante, ...)

    Si la llave tiene CKA_ALWAYS_AUTHENTICATE, antes de cada C_Sign se hace un
    login de contexto (CKU_CONTEXT_SPECIFIC) con el mismo PIN en memoria. Se
    firma con CKM_RSA_PKCS sobre DigestInfo || hash: el mismo PKCS#1 v1.5 que
    NCryptSignHash con BCRYPT_PAD_PKCS1.

    El PIN (UTF-8) vive solo en un búfer de ctypes que se llena de ceros al
    cerrar o en cuanto se rechaza. Un PIN rechazado detiene la sesión: no se
    reintenta. Al cerrar siempre se hace C_Logout, C_CloseSession y C_Finalize.
    """

    def __init__(self, ubicacion: Ubicacion, pin: str, llave: LlaveTarjeta):
        if not pin:
            raise ValueError("La sesión PKCS#11 necesita el PIN.")
        self._ubicacion = ubicacion
        self._llave = llave
        dato = pin.encode("utf-8")
        self._largo_pin = len(dato)
        self._pin = ctypes.create_string_buffer(dato, self._largo_pin + 1)
        self._ck = None
        self._inicializado = False
        self._sesion = None
        self._objeto_llave = None
        self.login_intentado = False  # True en cuanto se llamó a C_Login(CKU_USER)
        self.login_aceptado = False  # True si ese C_Login fue aceptado (sigue así tras cerrar)
        self.logueado = False  # sesión con login vigente (se cierra con C_Logout)
        self.siempre_autenticar = False
        self.firmante: FirmanteDeSesion | None = None
        self.firmas = 0
        self.fallidas = 0
        self._error_pin: int | None = None

    def _borrar_pin(self):
        pin, self._pin = self._pin, None
        if pin is not None:
            ctypes.memset(ctypes.addressof(pin), 0, ctypes.sizeof(pin))

    def _login(self, tipo: int):
        try:
            self._ck.login(self._sesion, tipo, self._pin, self._largo_pin)
        except ErrorPkcs11 as e:
            if e.rv != CKR_USER_ALREADY_LOGGED_IN:
                raise

    def _detener_por_pin(self, error: ErrorCNG):
        self._error_pin = error.codigo
        self._borrar_pin()
        logger.warning("Sesión PKCS#11 detenida: el DNIe rechazó el PIN provisto (%s).", error)

    def abrir(self):
        """
        Abre la sesión y hace el login UNA vez. Lanza ante cualquier fallo, tras
        cerrar todo; `login_intentado` dice si el PIN llegó a usarse.
        """
        u = self._ubicacion
        try:
            self._ck = _Cryptoki(u.ruta)
            self._ck.inicializar()
            self._inicializado = True
            # After C_Initialize the IDEMIA module only accepts slot IDs once
            # C_GetSlotList ran (otherwise CKR_SLOT_ID_INVALID, field log 2026-11-20).
            if u.slot not in self._ck.slots(True):
                raise ErrorCNG(f"El slot {u.slot} del DNIe ya no tiene token.")
            info = self._ck.info_token(u.slot)
            if info.flags & CKF_USER_PIN_LOCKED:
                raise ErrorPkcs11("C_GetTokenInfo (PIN bloqueado)", CKR_PIN_LOCKED)
            if info.flags & CKF_PROTECTED_AUTHENTICATION_PATH:
                raise ErrorCNG("El lector pide el PIN en su propio teclado (CKF_PROTECTED_AUTHENTICATION_PATH).")
            try:
                mecanismos = self._ck.mecanismos(u.slot)
            except ErrorPkcs11 as e:
                logger.info("PKCS#11: no se pudo leer la lista de mecanismos (%s); se prueba CKM_RSA_PKCS.", e)
            else:
                if CKM_RSA_PKCS not in mecanismos:
                    raise ErrorCNG(
                        "El token no ofrece CKM_RSA_PKCS (mecanismos: "
                        + ", ".join(f"0x{m:X}" for m in mecanismos[:40])
                        + ")."
                    )
            self._sesion = self._ck.abrir_sesion(u.slot)
            self.login_intentado = True
            self._login(CKU_USER)
            self.login_aceptado = self.logueado = True
            llaves = self._ck.buscar_objetos(self._sesion, [(CKA_CLASS, CKO_PRIVATE_KEY), (CKA_ID, u.id_llave)])
            if not llaves:
                raise ErrorCNG("No se halló la llave privada del certificado en el token.")
            self._objeto_llave = llaves[0]
            valor = self._ck.atributo(self._sesion, self._objeto_llave, CKA_ALWAYS_AUTHENTICATE)
            self.siempre_autenticar = bool(valor and valor[0])  # CK_BBOOL
        except BaseException as e:
            if es_error_de_pin(e):
                self._detener_por_pin(e)
            self.cerrar()
            raise
        logger.info(
            "Sesión PKCS#11 abierta: token «%s» (slot %d), %s, llave con CKA_ALWAYS_AUTHENTICATE: %s.",
            u.token,
            u.slot,
            _describir_flags_token(info.flags),
            "sí (login de contexto antes de cada firma)" if self.siempre_autenticar else "no",
        )

    def __enter__(self) -> FirmanteDeSesion:
        try:
            if self._sesion is None:
                self.abrir()
            # PKCS#11 no usa la ventana: el PIN ya lo pidió la aplicación.
            self.firmante = FirmanteDeSesion(self._llave, None, self)
        except BaseException:
            self.cerrar()
            raise
        return self.firmante

    def __exit__(self, *excepcion):
        self.cerrar()
        return False

    def cerrar(self):
        """Borra el PIN y cierra todo: C_Logout, C_CloseSession y C_Finalize. Nunca lanza."""
        self._borrar_pin()
        ck, sesion, inicializado = self._ck, self._sesion, self._inicializado
        self._sesion = None
        self._inicializado = False
        if ck is not None and sesion is not None:
            if self.logueado:
                try:
                    ck.logout(sesion)
                except Exception as e:  # noqa: BLE001
                    logger.info("PKCS#11: C_Logout: %s", e)
                self.logueado = False
            _cerrar_sesion(ck, sesion)
        if ck is not None and inicializado:
            _finalizar(ck)
            logger.info(
                "Sesión PKCS#11 cerrada: %d firma(s) realizada(s) y %d intento(s) fallido(s) con un solo login.",
                self.firmas,
                self.fallidas,
            )

    def _firmar(self, firmante, resumen: bytes, digest_algorithm: str) -> bytes:
        """Respaldo de `FirmanteDeSesion`: una firma con la sesión abierta."""
        if self._sesion is None:
            raise ErrorCNG("La sesión de firma PKCS#11 está cerrada.")
        if self._error_pin is not None:
            raise ErrorCNG(
                f"La sesión de firma se detuvo porque el DNIe rechazó el PIN (0x{self._error_pin:08X}).",
                codigo=self._error_pin,
            )
        inicio = time.monotonic()
        try:
            datos = digest_info(resumen, digest_algorithm)
            self._ck.iniciar_firma(self._sesion, CKM_RSA_PKCS, self._objeto_llave)
            if self.siempre_autenticar:
                self._login(CKU_CONTEXT_SPECIFIC)
            firma = self._ck.firmar(self._sesion, datos)
        except ErrorCNG as e:
            self.fallidas += 1
            if es_error_de_pin(e):
                self._detener_por_pin(e)
            raise
        self.firmas += 1
        logger.info(
            "Sesión PKCS#11: firma n.º %d (resumen %s) en %d ms.", self.firmas, digest_algorithm, _ms(inicio)
        )
        return firma


def abrir_sesion(llave: LlaveTarjeta, pin: str) -> SesionPkcs11 | None:
    """
    La sesión PKCS#11 ya autenticada para la llave que eligió CNG, o None si
    hay que seguir con CNG: sin módulo, sin coincidencia, llave que no es RSA,
    un fallo antes del login, o un fallo tras un login aceptado.

    Lanza (y no hay que probar CNG) si el PIN fue rechazado o si C_Login falló
    por otra causa: el PIN pudo llegar a la tarjeta y probarlo otra vez por CNG
    sería un segundo intento, que podría bloquear el DNIe.
    """
    try:
        algoritmo = llave.cert.public_key.algorithm
    except Exception:  # noqa: BLE001
        algoritmo = None
    if algoritmo != "rsa":
        logger.info("PKCS#11: la llave no es RSA (%s); se usa CNG.", algoritmo)
        return None
    ubicacion = buscar_llave(llave.cert.dump())
    if ubicacion is None:
        return None
    sesion = SesionPkcs11(ubicacion, pin, llave)
    try:
        sesion.abrir()
    except Exception as e:  # noqa: BLE001 - cada fallo se clasifica
        if es_error_de_pin(e):
            raise
        if sesion.login_intentado and not sesion.login_aceptado:
            logger.warning("PKCS#11: C_Login falló sin rechazar el PIN (%s); no se prueba CNG con el mismo PIN.", e)
            raise
        logger.warning(
            "PKCS#11: no se pudo abrir la sesión %s (%s: %s); se sigue con CNG.",
            "tras un login aceptado" if sesion.login_aceptado else "antes del login",
            type(e).__name__,
            e,
        )
        return None
    return sesion
