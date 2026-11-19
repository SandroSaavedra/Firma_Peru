"""
Acceso al DNIe a través de Windows CNG (Microsoft Smart Card Key Storage
Provider + minidriver instalado por RENIEC).

No usa PKCS#11: Windows maneja la tarjeta y muestra su propio cuadro de
diálogo para pedir el PIN al momento de firmar.

Excepción: una `SesionFirma` creada con `pin=` (firma real en cada página, un
solo PIN para muchas firmas) entrega el PIN a la llave con NCryptSetProperty
("SmartCardPin", comportamiento documentado del Microsoft Smart Card KSP)
antes de cada NCryptSignHash y firma con NCRYPT_SILENT_FLAG, sin el cuadro de
Windows. El PIN vive solo en un búfer de ctypes que se borra al cerrar la
sesión o en cuanto el PIN falla; nunca se registra, ni va en un error, ni se
devuelve. Un PIN incorrecto o bloqueado detiene la sesión: no se reintenta.
Si la tarjeta rechaza el PIN en silencio (NTE_SILENT_CONTEXT, p. ej. el DNIe
3.0), la sesión borra el PIN y sigue con el cuadro del PIN de Windows en cada
firma (`SesionFirma.pin_por_firma`). Con el PIN de la aplicación, el DNIe 3.0
se firma antes por PKCS#11 si está el módulo de IDEMIA (`dnie_pkcs11`), con el
mismo `FirmanteDeSesion`.

Búsqueda de la llave (`listar_llaves`), en este orden y solo si la vía
anterior no halló ninguna llave con certificado:

1. NCryptEnumKeys sin alcance (la tarjeta que el proveedor toma por omisión).
2. NCryptEnumKeys con el alcance de cada lector con tarjeta (`\\\\.\\<lector>\\`,
   lectores de WinSCard vía `diagnostico_dnie`). La llave queda con su nombre
   completo `\\\\.\\<lector>\\<contenedor>`, que el proveedor abre directamente.
3. Certificados del almacén «MY» del usuario cuya llave está en una tarjeta
   inteligente (CERT_KEY_PROV_INFO_PROP_ID): se abre el contenedor indicado,
   primero con el nombre completo de cada lector con tarjeta y luego tal cual.

Las tres vías dejan una `LlaveTarjeta` que se abre igual (NCryptOpenKey en el
Smart Card KSP por su nombre), así que `FirmanteCNG` y `SesionFirma` firman sin
cambios con cualquiera de ellas.
"""
import ctypes
import hashlib
import logging
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

from asn1crypto import algos, x509
from pyhanko.sign import signers
from pyhanko_certvalidator.registry import SimpleCertificateStore

import diagnostico_dnie

if sys.platform != "win32":
    raise ImportError("dnie_cng solo funciona en Windows")

logger = logging.getLogger("firmador.cng")

_ncrypt = ctypes.WinDLL("ncrypt.dll")
_crypt32 = ctypes.WinDLL("crypt32.dll")

PROVEEDOR_TARJETA = "Microsoft Smart Card Key Storage Provider"
_PROP_CERTIFICADO = "SmartCardKeyCertificate"
_PROP_VENTANA = "HWND Handle"  # NCRYPT_WINDOW_HANDLE_PROPERTY
_PROP_PIN = "SmartCardPin"  # NCRYPT_PIN_PROPERTY
_NCRYPT_SILENT_FLAG = 0x40
_BCRYPT_PAD_PKCS1 = 0x2
_NTE_NO_MORE_ITEMS = 0x8009002A
_NTE_NOT_FOUND = 0x80090011
# La tarjeta quiso mostrar su cuadro del PIN y el flag silencioso lo impidió (no se tocó la tarjeta).
NTE_SILENT_CONTEXT = 0x80090022
_CERT_KEY_PROV_INFO_PROP_ID = 2
_CERT_NCRYPT_KEY_SPEC = 0xFFFFFFFF  # dwKeySpec de una llave CNG (no CAPI)

# PIN incorrecto o bloqueado: otro intento con el mismo PIN podría bloquear el DNIe.
CODIGOS_PIN = {
    0x8010006B,  # SCARD_W_WRONG_CHV
    0x8010006C,  # SCARD_W_CHV_BLOCKED
    0x80090033,  # NTE_INCORRECT_PASSWORD
}

_HASH_CNG = {"sha1": "SHA1", "sha256": "SHA256", "sha384": "SHA384", "sha512": "SHA512"}

_HANDLE = ctypes.c_size_t  # NCRYPT_HANDLE es un ULONG_PTR


class _NCryptKeyName(ctypes.Structure):
    _fields_ = [
        ("pszName", wintypes.LPWSTR),
        ("pszAlgid", wintypes.LPWSTR),
        ("dwLegacyKeySpec", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
    ]


class _Pkcs1PaddingInfo(ctypes.Structure):
    _fields_ = [("pszAlgId", wintypes.LPCWSTR)]


class _CertContext(ctypes.Structure):
    _fields_ = [
        ("dwCertEncodingType", wintypes.DWORD),
        ("pbCertEncoded", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbCertEncoded", wintypes.DWORD),
        ("pCertInfo", ctypes.c_void_p),
        ("hCertStore", ctypes.c_void_p),
    ]


class _ProvInfo(ctypes.Structure):  # CRYPT_KEY_PROV_INFO
    _fields_ = [
        ("pwszContainerName", wintypes.LPWSTR),
        ("pwszProvName", wintypes.LPWSTR),
        ("dwProvType", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("cProvParam", wintypes.DWORD),
        ("rgProvParam", ctypes.c_void_p),
        ("dwKeySpec", wintypes.DWORD),
    ]


def _prototipo(fn, *argtypes):
    fn.argtypes = argtypes
    fn.restype = ctypes.c_long  # SECURITY_STATUS
    return fn


_OpenStorageProvider = _prototipo(
    _ncrypt.NCryptOpenStorageProvider, ctypes.POINTER(_HANDLE), wintypes.LPCWSTR, wintypes.DWORD
)
_EnumKeys = _prototipo(
    _ncrypt.NCryptEnumKeys,
    _HANDLE,
    wintypes.LPCWSTR,
    ctypes.POINTER(ctypes.POINTER(_NCryptKeyName)),
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.DWORD,
)
_OpenKey = _prototipo(
    _ncrypt.NCryptOpenKey, _HANDLE, ctypes.POINTER(_HANDLE), wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD
)
_GetProperty = _prototipo(
    _ncrypt.NCryptGetProperty,
    _HANDLE,
    wintypes.LPCWSTR,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.DWORD,
)
_SetProperty = _prototipo(
    _ncrypt.NCryptSetProperty, _HANDLE, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD
)
_SignHash = _prototipo(
    _ncrypt.NCryptSignHash,
    _HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.DWORD,
)
_FreeBuffer = _prototipo(_ncrypt.NCryptFreeBuffer, ctypes.c_void_p)
_FreeObject = _prototipo(_ncrypt.NCryptFreeObject, _HANDLE)

_crypt32.CertOpenSystemStoreW.argtypes = [ctypes.c_size_t, wintypes.LPCWSTR]
_crypt32.CertOpenSystemStoreW.restype = ctypes.c_void_p
_crypt32.CertEnumCertificatesInStore.argtypes = [ctypes.c_void_p, ctypes.POINTER(_CertContext)]
_crypt32.CertEnumCertificatesInStore.restype = ctypes.POINTER(_CertContext)
_crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_crypt32.CertCloseStore.restype = wintypes.BOOL
_crypt32.CertGetCertificateContextProperty.argtypes = [
    ctypes.POINTER(_CertContext),
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD),
]
_crypt32.CertGetCertificateContextProperty.restype = wintypes.BOOL


class ErrorCNG(RuntimeError):
    """
    Error de CNG / tarjeta. `codigo`: SECURITY_STATUS de Windows, si se conoce.
    `diagnostico`: código de `diagnostico_dnie` cuando el mensaje ya es el del
    diagnóstico (se muestra tal cual al usuario).
    """

    def __init__(self, mensaje: str = "", codigo: int | None = None, diagnostico: str | None = None):
        super().__init__(mensaje)
        self.codigo = codigo
        self.diagnostico = diagnostico


def _verificar(status, operacion):
    codigo = status & 0xFFFFFFFF
    if codigo != 0:
        try:
            detalle = ctypes.FormatError(codigo).strip()
        except Exception:  # noqa: BLE001 - el mensaje es solo informativo
            detalle = ""
        raise ErrorCNG(f"{operacion} falló (0x{codigo:08X}) {detalle}".strip(), codigo=codigo)


@dataclass
class LlaveTarjeta:
    """Una llave privada del DNIe junto con su certificado."""

    nombre: str
    tipo_legado: int
    cert: x509.Certificate

    @property
    def titular(self) -> str:
        return self.cert.subject.native.get("common_name", "")

    @property
    def usos(self) -> set:
        return set(self.cert.key_usage_value.native) if self.cert.key_usage_value else set()

    @property
    def es_de_firma(self) -> bool:
        return "non_repudiation" in self.usos or "content_commitment" in self.usos


def describir_llave(llave: LlaveTarjeta) -> str:
    """Resumen de una llave para el registro (nombre, certificado, uso y vigencia). Nunca lanza."""
    try:
        texto = f"llave={llave.nombre!r}, tipo_legado={llave.tipo_legado}"
        if llave.cert is None:
            return texto + ", sin certificado"
        validez = llave.cert["tbs_certificate"]["validity"]
        return (
            f"{texto}, titular={llave.titular!r}, es_de_firma={llave.es_de_firma}, "
            f"vigencia={validez['not_before'].native:%Y-%m-%d} a {validez['not_after'].native:%Y-%m-%d}"
        )
    except Exception as e:  # noqa: BLE001 - el registro nunca debe impedir firmar
        return f"llave={getattr(llave, 'nombre', '?')!r} (no se pudo describir: {type(e).__name__})"


def _abrir_proveedor():
    proveedor = _HANDLE()
    try:
        _verificar(_OpenStorageProvider(ctypes.byref(proveedor), PROVEEDOR_TARJETA, 0), "NCryptOpenStorageProvider")
    except ErrorCNG as e:
        logger.warning("No se pudo abrir el proveedor «%s»: %s", PROVEEDOR_TARJETA, e)
        raise
    logger.info("Proveedor «%s» abierto.", PROVEEDOR_TARJETA)
    return proveedor


def _abrir_llave(proveedor, llave: LlaveTarjeta, flags=0):
    handle = _HANDLE()
    _verificar(_OpenKey(proveedor, ctypes.byref(handle), llave.nombre, llave.tipo_legado, flags), "NCryptOpenKey")
    return handle


def _leer_propiedad(handle, nombre) -> bytes:
    tam = wintypes.DWORD()
    _verificar(_GetProperty(handle, nombre, None, 0, ctypes.byref(tam), _NCRYPT_SILENT_FLAG), f"Leer {nombre}")
    buf = (ctypes.c_ubyte * tam.value)()
    _verificar(_GetProperty(handle, nombre, buf, tam, ctypes.byref(tam), _NCRYPT_SILENT_FLAG), f"Leer {nombre}")
    return bytes(buf[: tam.value])


def _describir_alcance(alcance: str | None) -> str:
    return f"alcance «{alcance}»" if alcance else "sin alcance"


def _enumerar(proveedor, alcance: str | None = None) -> tuple[list[LlaveTarjeta], int | None, int]:
    """
    Enumera las llaves de `proveedor` en `alcance` (None = sin alcance) y lee
    el certificado de cada una. Devuelve (llaves con certificado, NTE_NOT_FOUND
    si la enumeración terminó así o None, llaves enumeradas). No pide PIN.
    """
    llaves = []
    enumeradas = 0
    estado_fin = None  # NTE_NOT_FOUND si la enumeración terminó así
    estado = ctypes.c_void_p()
    try:
        while True:
            item = ctypes.POINTER(_NCryptKeyName)()
            status = _EnumKeys(proveedor, alcance, ctypes.byref(item), ctypes.byref(estado), _NCRYPT_SILENT_FLAG)
            if status & 0xFFFFFFFF == _NTE_NO_MORE_ITEMS:
                break
            if status & 0xFFFFFFFF == _NTE_NOT_FOUND:
                # El proveedor no encuentra la tarjeta o sus llaves: equivale a «no hay llaves».
                logger.warning(
                    "NCryptEnumKeys devolvió 0x%08X (NTE_NOT_FOUND) en «%s» (%s) tras %d llave(s); "
                    "se trata como fin de la enumeración.",
                    status & 0xFFFFFFFF,
                    PROVEEDOR_TARJETA,
                    _describir_alcance(alcance),
                    enumeradas,
                )
                estado_fin = _NTE_NOT_FOUND
                break
            try:
                _verificar(status, "NCryptEnumKeys")
            except ErrorCNG as e:
                logger.warning(
                    "NCryptEnumKeys falló con 0x%08X en «%s» (%s) tras %d llave(s): %s",
                    status & 0xFFFFFFFF,
                    PROVEEDOR_TARJETA,
                    _describir_alcance(alcance),
                    enumeradas,
                    e,
                )
                raise
            enumeradas += 1
            try:
                info = item.contents
                nombre = info.pszName
                if alcance and not nombre.startswith("\\\\.\\"):
                    # Con alcance, el contenedor se abre con su nombre completo \\.\<lector>\<contenedor>.
                    nombre = alcance + nombre
                candidata = LlaveTarjeta(nombre, info.dwLegacyKeySpec, None)
            finally:
                _FreeBuffer(item)
            try:
                handle = _abrir_llave(proveedor, candidata, _NCRYPT_SILENT_FLAG)
            except ErrorCNG as e:
                logger.warning("No se pudo abrir la %s: %s", describir_llave(candidata), e)
                raise
            try:
                der = _leer_propiedad(handle, _PROP_CERTIFICADO)
            except ErrorCNG as e:
                logger.info("Llave sin certificado (se omite): %s; %s", describir_llave(candidata), e)
                continue  # llave sin certificado asociado
            finally:
                _FreeObject(handle)
            candidata.cert = x509.Certificate.load(der)
            logger.info("Llave con certificado: %s", describir_llave(candidata))
            llaves.append(candidata)
    finally:
        if estado:
            _FreeBuffer(estado)
    return llaves, estado_fin, enumeradas


def _alcance_de_lector(nombre: str) -> str:
    """Alcance del Smart Card KSP para un lector: barra doble, punto, barra, el lector y barra."""
    return "\\\\.\\" + nombre + "\\"


def _lectores_con_tarjeta() -> list[diagnostico_dnie.Lector]:
    """Lectores de WinSCard con una tarjeta presente. Registra todos los vistos. Nunca lanza."""
    try:
        hechos = _recolectar_hechos()
    except Exception as e:  # noqa: BLE001 - sin WinSCard se sigue con el almacén
        logger.warning("Búsqueda por lector: no se pudieron consultar los lectores (%s: %s).", type(e).__name__, e)
        return []
    lectores = hechos.get("lectores") or []
    error = hechos.get("error_lectores")
    logger.info(
        "Búsqueda por lector: %d lector(es) visto(s)%s.",
        len(lectores),
        f" (error de WinSCard 0x{error & 0xFFFFFFFF:08X})" if error else "",
    )
    con_tarjeta = []
    for lector in lectores:
        presente = bool(lector.estado & diagnostico_dnie.SCARD_STATE_PRESENT)
        logger.info(
            "Lector «%s»: estado 0x%X%s.", lector.nombre, lector.estado, "" if presente else ", sin tarjeta (se omite)"
        )
        if presente:
            con_tarjeta.append(lector)
    return con_tarjeta


def _buscar_por_lector(proveedor, lectores) -> tuple[list[LlaveTarjeta], list[str]]:
    """Vía 2: enumera con el alcance de cada lector con tarjeta. Un lector que falla se registra y se omite."""
    halladas, origenes = [], []
    for lector in lectores:
        alcance = _alcance_de_lector(lector.nombre)
        try:
            llaves, estado_fin, enumeradas = _enumerar(proveedor, alcance)
        except ErrorCNG as e:
            logger.warning("Lector «%s» (estado 0x%X): la enumeración falló: %s", lector.nombre, lector.estado, e)
            continue
        logger.info(
            "Lector «%s» (estado 0x%X): %d llave(s) enumerada(s), %d con certificado%s.",
            lector.nombre,
            lector.estado,
            enumeradas,
            len(llaves),
            f"; terminó con 0x{estado_fin:08X}" if estado_fin else "",
        )
        if llaves:
            halladas.extend(llaves)
            origenes.append(f"lector «{lector.nombre}»")
    return halladas, origenes


@dataclass
class _CertDeTarjeta:
    """Certificado del almacén «MY» con el proveedor y el contenedor de su llave (CRYPT_KEY_PROV_INFO)."""

    cert: x509.Certificate
    proveedor: str
    contenedor: str
    tipo_legado: int  # dwKeySpec; _CERT_NCRYPT_KEY_SPEC si es una llave CNG


def _leer_prov_info(ctx) -> tuple[str, str, int] | None:
    """(proveedor, contenedor, dwKeySpec) de CERT_KEY_PROV_INFO_PROP_ID, o None si no tiene."""
    tam = wintypes.DWORD()
    if not _crypt32.CertGetCertificateContextProperty(ctx, _CERT_KEY_PROV_INFO_PROP_ID, None, ctypes.byref(tam)):
        return None
    if tam.value < ctypes.sizeof(_ProvInfo):
        return None
    buf = (ctypes.c_ubyte * tam.value)()
    if not _crypt32.CertGetCertificateContextProperty(ctx, _CERT_KEY_PROV_INFO_PROP_ID, buf, ctypes.byref(tam)):
        return None
    info = _ProvInfo.from_buffer(buf)  # sus cadenas apuntan dentro de `buf`: se copian antes de soltarlo
    return info.pwszProvName or "", info.pwszContainerName or "", info.dwKeySpec


def _certificados_de_tarjeta_en_almacen() -> list[_CertDeTarjeta]:
    """
    Certificados del almacén «MY» del usuario que indican dónde está su llave
    (CERT_KEY_PROV_INFO_PROP_ID). Solo lectura: no abre llaves ni toca la tarjeta.
    """
    almacen = _crypt32.CertOpenSystemStoreW(0, "MY")
    if not almacen:
        logger.warning("Almacén MY: no se pudo abrir el almacén del usuario.")
        return []
    hallados = []
    try:
        ctx = _crypt32.CertEnumCertificatesInStore(almacen, None)
        while ctx:
            try:
                prov_info = _leer_prov_info(ctx)
                if prov_info is not None:
                    c = ctx.contents
                    cert = x509.Certificate.load(ctypes.string_at(c.pbCertEncoded, c.cbCertEncoded))
                    cert.subject.native  # asn1crypto analiza en diferido: un certificado dañado falla aquí
                    hallados.append(_CertDeTarjeta(cert, *prov_info))
            except Exception as e:  # noqa: BLE001 - un certificado ilegible no impide revisar los demás
                logger.info("Almacén MY: certificado omitido (%s: %s).", type(e).__name__, e)
            ctx = _crypt32.CertEnumCertificatesInStore(almacen, ctx)
    finally:
        _crypt32.CertCloseStore(almacen, 0)
    return hallados


_USOS_DE_FIRMA = {"digital_signature", "non_repudiation", "content_commitment"}


def _buscar_en_almacen(proveedor, lectores) -> list[LlaveTarjeta]:
    """
    Vía 3: certificados del almacén «MY» con la llave en una tarjeta inteligente
    y uso de firma. Su contenedor se abre en silencio, primero con el nombre
    completo de cada lector con tarjeta y luego tal cual. Nunca lanza.
    """
    try:
        candidatos = _certificados_de_tarjeta_en_almacen()
    except Exception as e:  # noqa: BLE001 - el almacén es la última vía; su fallo no oculta el error original
        logger.warning("Almacén MY: no se pudo leer (%s: %s).", type(e).__name__, e)
        return []
    logger.info("Almacén MY: %d certificado(s) con llave asociada.", len(candidatos))
    halladas = []
    for c in candidatos:
        try:
            titular = c.cert.subject.native.get("common_name", "")
            usos = set(c.cert.key_usage_value.native) if c.cert.key_usage_value else set()
        except Exception as e:  # noqa: BLE001
            logger.info("Almacén MY: certificado omitido (%s: %s).", type(e).__name__, e)
            continue
        de_tarjeta = "smart card" in c.proveedor.lower()
        de_firma = bool(usos & _USOS_DE_FIRMA)
        logger.info(
            "Almacén MY: titular %r, proveedor «%s», contenedor %r, tipo_legado 0x%X, tarjeta: %s, uso de firma: %s.",
            titular,
            c.proveedor,
            c.contenedor,
            c.tipo_legado,
            "sí" if de_tarjeta else "no",
            "sí" if de_firma else "no",
        )
        if not (de_tarjeta and de_firma and c.contenedor):
            continue
        tipo_legado = 0 if c.tipo_legado == _CERT_NCRYPT_KEY_SPEC else c.tipo_legado
        if c.contenedor.startswith("\\\\.\\"):
            nombres = [c.contenedor]
        else:
            nombres = [_alcance_de_lector(l.nombre) + c.contenedor for l in lectores] + [c.contenedor]
        for nombre in nombres:
            candidata = LlaveTarjeta(nombre, tipo_legado, None)
            try:
                handle = _abrir_llave(proveedor, candidata, _NCRYPT_SILENT_FLAG)
            except Exception as e:  # noqa: BLE001 - se prueba el siguiente nombre
                logger.info("Almacén MY: no se pudo abrir el contenedor %r: %s", nombre, e)
                continue
            try:
                der = _leer_propiedad(handle, _PROP_CERTIFICADO)
            except ErrorCNG:
                der = None  # sin esa propiedad se usa el certificado del almacén
            finally:
                _FreeObject(handle)
            if der is not None and der != c.cert.dump():
                logger.info("Almacén MY: el contenedor %r tiene otro certificado; se omite.", nombre)
                continue
            candidata.cert = c.cert
            logger.info("Llave con certificado (almacén MY): %s", describir_llave(candidata))
            halladas.append(candidata)
            break
    return halladas


def listar_llaves() -> list[LlaveTarjeta]:
    """
    Las llaves del DNIe que tienen certificado. No pide PIN. Prueba, en orden y
    solo si la anterior no halló ninguna: sin alcance, por lector y almacén «MY».
    """
    proveedor = _abrir_proveedor()
    try:
        llaves, estado_fin, enumeradas = _enumerar(proveedor, None)
        logger.info("Llaves enumeradas: %d; con certificado: %d.", enumeradas, len(llaves))
        origen = "sin alcance"
        if not llaves:
            lectores = _lectores_con_tarjeta()
            llaves, origenes = _buscar_por_lector(proveedor, lectores)
            origen = ", ".join(origenes)
            if not llaves:
                llaves = _buscar_en_almacen(proveedor, lectores)
                origen = "almacén MY"
    finally:
        _FreeObject(proveedor)
    if not llaves:
        logger.warning(
            "No se halló ninguna llave con certificado por ninguna vía (sin alcance, por lector ni almacén MY)."
        )
        # Con NTE_NOT_FOUND se conserva el código: es reintentable y el diagnóstico lo usa.
        raise ErrorCNG("No se encontró ninguna llave con certificado. ¿Está el DNIe insertado?", codigo=estado_fin)
    logger.info("Llave(s) del DNIe halladas por: %s (%d).", origen, len(llaves))
    return llaves


# Puntos de prueba: las pruebas reemplazan la espera y la consulta a WinSCard.
_dormir = time.sleep
_recolectar_hechos = diagnostico_dnie.recolectar_hechos


def _con_diagnostico(error: ErrorCNG) -> ErrorCNG:
    """
    El error con el mensaje del diagnóstico (WinSCard + código de la
    enumeración), o el mismo error si el diagnóstico no concluye o falla.
    """
    try:
        hechos = _recolectar_hechos()
        codigo, mensaje = diagnostico_dnie.diagnosticar(
            error_lectores=hechos.get("error_lectores"),
            lectores=hechos.get("lectores"),
            estado_llaves=error.codigo,
        )
        lectores = "; ".join(str(l) for l in hechos.get("lectores") or []) or "ninguno"
        error_lectores = hechos.get("error_lectores")
        logger.warning(
            "Diagnóstico del DNIe: %s. Error original: %s (código %s). Lectores: %s. Error de WinSCard: %s.",
            codigo,
            error,
            f"0x{error.codigo:08X}" if error.codigo is not None else "-",
            lectores,
            f"0x{error_lectores & 0xFFFFFFFF:08X}" if error_lectores else "-",
        )
    except Exception as e:  # noqa: BLE001 - el diagnóstico nunca oculta el error original
        logger.warning("No se pudo diagnosticar el DNIe (%s: %s); se informa el error original.", type(e).__name__, e)
        return error
    if mensaje is None or codigo == "ok":
        return error
    return ErrorCNG(mensaje, codigo=error.codigo, diagnostico=codigo)


def elegir_llave_de_firma(filtro: str | None = None) -> LlaveTarjeta:
    """
    Devuelve la llave de FIRMA (non_repudiation). Si se pasa `filtro`, se
    elige la llave cuyo titular contenga ese texto (sin distinguir mayúsculas).

    Los errores pasajeros de la tarjeta al listar las llaves se reintentan
    (`diagnostico_dnie.con_reintentos`). Si aun así falla, el error lleva el
    mensaje del diagnóstico (falta el lector, la tarjeta, el software, los
    certificados...). Nunca pide el PIN.
    """
    try:
        llaves = diagnostico_dnie.con_reintentos(
            lambda: listar_llaves(), dormir=_dormir, descripcion="Listar las llaves del DNIe"
        )
    except ErrorCNG as e:
        diagnosticado = _con_diagnostico(e)
        if diagnosticado is e:
            raise
        raise diagnosticado from e
    if filtro:
        candidatas = [l for l in llaves if filtro.lower() in l.titular.lower()]
    else:
        candidatas = [l for l in llaves if l.es_de_firma]
    if len(candidatas) != 1:
        logger.warning(
            "Llave de firma: %d candidata(s) entre %d llave(s) (%s); se esperaba 1.",
            len(candidatas),
            len(llaves),
            f"filtro {filtro!r}" if filtro else "uso de no repudio",
        )
        titulares = "\n  ".join(l.titular for l in (candidatas or llaves))
        raise ErrorCNG(
            f"Se esperaba 1 certificado de firma y hay {len(candidatas)}. "
            f"Usa --cert con parte del nombre. Disponibles:\n  {titulares}"
        )
    logger.info("Llave de firma elegida: %s", describir_llave(candidatas[0]))
    return candidatas[0]


def _certs_de_almacen(nombre: str) -> list[x509.Certificate]:
    almacen = _crypt32.CertOpenSystemStoreW(0, nombre)
    if not almacen:
        return []
    certs = []
    try:
        ctx = _crypt32.CertEnumCertificatesInStore(almacen, None)
        while ctx:
            c = ctx.contents
            der = ctypes.string_at(c.pbCertEncoded, c.cbCertEncoded)
            try:
                certs.append(x509.Certificate.load(der))
            except ValueError:
                pass
            ctx = _crypt32.CertEnumCertificatesInStore(almacen, ctx)
    finally:
        _crypt32.CertCloseStore(almacen, 0)
    return certs


def certificados_de_almacen(nombre: str) -> list[x509.Certificate]:
    """Certificados de un almacén del sistema de Windows ("ROOT", "CA", "MY"...)."""
    return _certs_de_almacen(nombre)


def cadena_intermedia(cert: x509.Certificate) -> list[x509.Certificate]:
    """Busca en los almacenes CA y ROOT de Windows los emisores del certificado."""
    disponibles = _certs_de_almacen("CA") + _certs_de_almacen("ROOT")
    cadena, vistos, actual = [], {cert.sha256}, cert
    while actual.issuer != actual.subject:
        emisor = next(
            (c for c in disponibles if c.subject == actual.issuer and c.sha256 not in vistos), None
        )
        if emisor is None:
            break
        cadena.append(emisor)
        vistos.add(emisor.sha256)
        actual = emisor
    return cadena


class FirmanteCNG(signers.Signer):
    """Signer de pyHanko que delega la operación de firma en Windows CNG."""

    def __init__(self, llave: LlaveTarjeta, hwnd: int | None = None):
        """`hwnd`: ventana a la que Windows asocia su cuadro de diálogo del PIN."""
        self._llave = llave
        self._hwnd = hwnd
        self._algoritmo = llave.cert.public_key.algorithm
        if self._algoritmo not in ("rsa", "ec"):
            raise ErrorCNG(f"Algoritmo de llave no soportado: {self._algoritmo}")
        super().__init__(
            signing_cert=llave.cert,
            cert_registry=SimpleCertificateStore.from_certs(cadena_intermedia(llave.cert)),
            prefer_pss=False,
        )

    def _tam_firma(self) -> int:
        bits = self._llave.cert.public_key.bit_size
        if self._algoritmo == "rsa":
            return (bits + 7) // 8
        return 2 * ((bits + 7) // 8) + 9  # DER de ECDSA: dos enteros + cabeceras

    async def async_sign_raw(self, data: bytes, digest_algorithm: str, dry_run=False) -> bytes:
        if dry_run:
            return bytes(self._tam_firma())
        if digest_algorithm not in _HASH_CNG:
            raise ErrorCNG(f"Hash no soportado: {digest_algorithm}")
        resumen = hashlib.new(digest_algorithm, data).digest()
        firma = self._firmar_resumen(resumen, digest_algorithm)
        if self._algoritmo == "ec":
            firma = algos.DSASignature.from_p1363(firma).dump()
        return firma

    def _firmar_resumen(self, resumen: bytes, digest_algorithm: str) -> bytes:
        """Firma de un solo uso: abre el proveedor y la llave, firma y los libera."""
        proveedor = _abrir_proveedor()
        try:
            llave = _abrir_llave(proveedor, self._llave)
            try:
                self._asignar_ventana(llave)
                return self._firmar_con_handle(llave, resumen, digest_algorithm)
            finally:
                _FreeObject(llave)
        finally:
            _FreeObject(proveedor)

    def _firmar_con_handle(self, llave, resumen: bytes, digest_algorithm: str, silencioso: bool = False) -> bytes:
        """
        NCryptSignHash sobre una llave ya abierta. `silencioso`: el PIN ya se
        asignó a la llave y se firma con NCRYPT_SILENT_FLAG (sin cuadro de Windows).
        """
        if self._algoritmo == "rsa":
            relleno = _Pkcs1PaddingInfo(_HASH_CNG[digest_algorithm])
            p_relleno, flags = ctypes.byref(relleno), _BCRYPT_PAD_PKCS1
        else:
            p_relleno, flags = None, 0
        if silencioso:
            flags |= _NCRYPT_SILENT_FLAG
        hash_buf = (ctypes.c_ubyte * len(resumen)).from_buffer_copy(resumen)
        tam = wintypes.DWORD()
        _verificar(
            _SignHash(llave, p_relleno, hash_buf, len(resumen), None, 0, ctypes.byref(tam), flags),
            "NCryptSignHash (tamaño)",
        )
        salida = (ctypes.c_ubyte * tam.value)()
        logger.info(
            "NCryptSignHash: solicitando firma %s (resumen %s de %d bytes, ventana asociada: %s).",
            "en silencio con el PIN provisto por la aplicación" if silencioso else "/ PIN a Windows",
            digest_algorithm,
            len(resumen),
            "sí" if self._hwnd else "no",
        )
        inicio = time.monotonic()
        # Aquí Windows muestra su cuadro de diálogo para el PIN (si la tarjeta lo pide).
        status = _SignHash(llave, p_relleno, hash_buf, len(resumen), salida, tam, ctypes.byref(tam), flags)
        logger.info(
            "NCryptSignHash terminó: estado 0x%08X en %d ms.",
            status & 0xFFFFFFFF,
            int((time.monotonic() - inicio) * 1000),
        )
        _verificar(status, "NCryptSignHash")
        return bytes(salida[: tam.value])

    def _asignar_ventana(self, llave):
        """Hace que el cuadro del PIN aparezca delante de la ventana de la aplicación."""
        if not self._hwnd:
            return
        valor = ctypes.c_void_p(self._hwnd)
        try:
            _verificar(
                _SetProperty(llave, _PROP_VENTANA, ctypes.byref(valor), ctypes.sizeof(valor), 0),
                "NCryptSetProperty (HWND)",
            )
        except ErrorCNG:
            pass  # sin ventana padre Windows igual muestra el cuadro del PIN


class FirmanteDeSesion(FirmanteCNG):
    """
    FirmanteCNG cuya firma en bruto la hace un respaldo enchufable: cualquier
    objeto con `_firmar(firmante, resumen, algoritmo) -> bytes`. Hoy son la
    `SesionFirma` (CNG, con la llave que mantiene abierta) y la
    `dnie_pkcs11.SesionPkcs11` (módulo PKCS#11 de IDEMIA). pyHanko, la cadena
    de certificados y el tamaño de la firma son los mismos con los dos.
    """

    def __init__(self, llave: LlaveTarjeta, hwnd: int | None, sesion):
        super().__init__(llave, hwnd=hwnd)
        self.sesion = sesion

    def _firmar_resumen(self, resumen: bytes, digest_algorithm: str) -> bytes:
        return self.sesion._firmar(self, resumen, digest_algorithm)


class SesionFirma:
    """
    Firma varios documentos con UNA sola apertura del proveedor y de la llave:

        with SesionFirma(llave, hwnd) as firmante:
            firmar_pdf(..., firmante, ...)   # una o muchas veces

    Al reutilizar el mismo handle de la llave, el minidriver de la tarjeta puede
    aprovechar su caché del PIN y Windows pedirlo una sola vez. Eso depende de la
    política de la tarjeta: si exige el PIN en cada firma, Windows lo pedirá en
    cada una.

    Sin `pin` este código nunca lee, guarda ni asigna el PIN. Con `pin` (texto
    que la aplicación pidió UNA vez) se copia de inmediato a un búfer de ctypes
    (UTF-16LE con su terminador), se asigna a la llave antes de cada firma y se
    firma en silencio. El búfer se llena de ceros al cerrar la sesión, si no se
    puede abrir la llave o en cuanto el PIN falla. Un PIN incorrecto o
    bloqueado (CODIGOS_PIN) detiene la sesión: las firmas siguientes fallan sin
    tocar la tarjeta, para no gastar otro intento.

    Única excepción al «nunca se reintenta»: si la tarjeta rechaza el PIN en
    silencio (NTE_SILENT_CONTEXT, la llave exige su propio cuadro del PIN en
    cada firma, como el DNIe 3.0), la tarjeta no llegó a tocarse. La sesión
    borra el PIN, deja de asignarlo y pasa a pedir el PIN a Windows (sin
    NCRYPT_SILENT_FLAG, con la ventana ya asociada) hasta cerrarse:
    `pin_por_firma` queda en True, se llama UNA vez a `al_pedir_pin_por_firma`
    (si se dio) y se repite esa misma firma una sola vez. Un PIN incorrecto en
    el cuadro de Windows también detiene la sesión.
    """

    def __init__(
        self,
        llave: LlaveTarjeta,
        hwnd: int | None = None,
        pin: str | None = None,
        al_pedir_pin_por_firma=None,
    ):
        self._llave = llave
        self._hwnd = hwnd
        self._proveedor = None
        self._handle = None
        self.firmante: FirmanteDeSesion | None = None
        self.firmas = 0  # firmas completadas en la sesión
        self.fallidas = 0  # intentos de firma que terminaron en error
        # Búfer mutable con el PIN (se borra con `_borrar_pin`); None = Windows pide el PIN.
        self._pin = ctypes.create_unicode_buffer(pin) if pin else None
        self.pin_provisto = self._pin is not None
        self._error_pin: int | None = None  # código del PIN rechazado: la sesión queda detenida
        self._pin_por_firma = False  # True: la tarjeta exige el cuadro de Windows en cada firma
        self._al_pedir_pin_por_firma = al_pedir_pin_por_firma

    @property
    def pin_por_firma(self) -> bool:
        """True si la tarjeta rechazó el PIN en silencio y Windows lo pide en cada firma."""
        return self._pin_por_firma

    def __enter__(self) -> FirmanteCNG:
        try:
            # El firmante (cadena de certificados) se arma antes de abrir los handles.
            self.firmante = FirmanteDeSesion(self._llave, self._hwnd, self)
            proveedor = _abrir_proveedor()
            try:
                handle = _abrir_llave(proveedor, self._llave)
            except BaseException:
                _FreeObject(proveedor)
                raise
        except BaseException:
            self._borrar_pin()
            raise
        self._proveedor, self._handle = proveedor, handle
        self.firmante._asignar_ventana(handle)  # una sola vez para toda la sesión
        logger.info(
            "Sesión de firma abierta: proveedor y llave abiertos 1 vez (PIN provisto: %s).",
            "sí" if self.pin_provisto else "no",
        )
        return self.firmante

    def __exit__(self, *excepcion):
        self.cerrar()
        return False

    def _borrar_pin(self):
        """Llena de ceros el búfer del PIN y lo suelta."""
        pin, self._pin = self._pin, None
        if pin is not None:
            ctypes.memset(ctypes.addressof(pin), 0, ctypes.sizeof(pin))

    def _asignar_pin(self):
        """NCryptSetProperty(SmartCardPin) con el PIN del búfer (UTF-16LE con terminador)."""
        _verificar(
            _SetProperty(self._handle, _PROP_PIN, ctypes.byref(self._pin), ctypes.sizeof(self._pin), 0),
            "NCryptSetProperty (PIN)",
        )

    def cerrar(self):
        self._borrar_pin()
        handle, proveedor = self._handle, self._proveedor
        self._handle = self._proveedor = None
        if handle is not None:
            _FreeObject(handle)
        if proveedor is not None:
            _FreeObject(proveedor)
            logger.info(
                "Sesión de firma cerrada: %d firma(s) realizada(s) y %d intento(s) fallido(s) "
                "con una sola apertura de la llave.",
                self.firmas,
                self.fallidas,
            )

    def _pasar_a_pin_por_firma(self):
        """La tarjeta no acepta el PIN en silencio: Windows lo pedirá en cada firma."""
        self._borrar_pin()
        self._pin_por_firma = True
        # La llave que recibió «SmartCardPin» se suelta y se abre otra limpia, con la
        # misma ventana, para que el cuadro de Windows no herede ese estado.
        viejo, self._handle = self._handle, None
        if viejo is not None:
            _FreeObject(viejo)
        self._handle = _abrir_llave(self._proveedor, self._llave)
        self.firmante._asignar_ventana(self._handle)
        logger.warning(
            "Sesión de firma: el DNIe exige su propio cuadro del PIN en cada firma (0x%08X); "
            "se continúa con el PIN de Windows por página.",
            NTE_SILENT_CONTEXT,
        )
        if self._al_pedir_pin_por_firma is not None:
            try:
                self._al_pedir_pin_por_firma()
            except Exception:  # noqa: BLE001 - el aviso es informativo
                logger.debug("No se pudo avisar que el PIN se pedirá en cada firma", exc_info=True)

    def _firmar(self, firmante: FirmanteCNG, resumen: bytes, digest_algorithm: str) -> bytes:
        if self._handle is None:
            raise ErrorCNG("La sesión de firma está cerrada.")
        if self._error_pin is not None:
            raise ErrorCNG(
                f"La sesión de firma se detuvo porque el DNIe rechazó el PIN (0x{self._error_pin:08X}).",
                codigo=self._error_pin,
            )
        try:
            if self._pin is not None:
                try:
                    self._asignar_pin()
                    firma = firmante._firmar_con_handle(self._handle, resumen, digest_algorithm, True)
                except ErrorCNG as e:
                    if e.codigo != NTE_SILENT_CONTEXT:
                        raise
                    # La tarjeta no se tocó: misma firma, una sola vez, con el cuadro de Windows.
                    self._pasar_a_pin_por_firma()
                    firma = firmante._firmar_con_handle(self._handle, resumen, digest_algorithm)
            else:
                firma = firmante._firmar_con_handle(self._handle, resumen, digest_algorithm)
        except ErrorCNG as e:
            self.fallidas += 1
            if self.pin_provisto and e.codigo in CODIGOS_PIN:
                # Ni otro intento ni otra firma con este PIN: podría bloquear el DNIe.
                self._error_pin = e.codigo
                self._borrar_pin()
                logger.warning("Sesión de firma detenida: el DNIe rechazó el PIN provisto (0x%08X).", e.codigo)
            raise
        self.firmas += 1
        logger.info("Sesión de firma: firma n.º %d completada con la misma llave abierta.", self.firmas)
        return firma
