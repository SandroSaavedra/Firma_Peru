"""
Acceso al DNIe a través de Windows CNG (Microsoft Smart Card Key Storage
Provider + minidriver instalado por RENIEC).

No usa PKCS#11: Windows maneja la tarjeta y muestra su propio cuadro de
diálogo para pedir el PIN al momento de firmar. Este módulo nunca ve el PIN.
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

if sys.platform != "win32":
    raise ImportError("dnie_cng solo funciona en Windows")

logger = logging.getLogger("firmador.cng")

_ncrypt = ctypes.WinDLL("ncrypt.dll")
_crypt32 = ctypes.WinDLL("crypt32.dll")

PROVEEDOR_TARJETA = "Microsoft Smart Card Key Storage Provider"
_PROP_CERTIFICADO = "SmartCardKeyCertificate"
_PROP_VENTANA = "HWND Handle"  # NCRYPT_WINDOW_HANDLE_PROPERTY
_NCRYPT_SILENT_FLAG = 0x40
_BCRYPT_PAD_PKCS1 = 0x2
_NTE_NO_MORE_ITEMS = 0x8009002A
_NTE_NOT_FOUND = 0x80090011

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


class ErrorCNG(RuntimeError):
    """Error de CNG / tarjeta. `codigo`: SECURITY_STATUS de Windows, si se conoce."""

    def __init__(self, mensaje: str = "", codigo: int | None = None):
        super().__init__(mensaje)
        self.codigo = codigo


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


def listar_llaves() -> list[LlaveTarjeta]:
    """Enumera las llaves del DNIe que tienen certificado. No pide PIN."""
    proveedor = _abrir_proveedor()
    llaves = []
    enumeradas = 0
    estado = ctypes.c_void_p()
    try:
        while True:
            item = ctypes.POINTER(_NCryptKeyName)()
            status = _EnumKeys(proveedor, None, ctypes.byref(item), ctypes.byref(estado), _NCRYPT_SILENT_FLAG)
            if status & 0xFFFFFFFF == _NTE_NO_MORE_ITEMS:
                break
            if status & 0xFFFFFFFF == _NTE_NOT_FOUND:
                # El proveedor no encuentra la tarjeta o sus llaves: equivale a «no hay llaves».
                logger.warning(
                    "NCryptEnumKeys devolvió 0x%08X (NTE_NOT_FOUND) en «%s» tras %d llave(s); "
                    "se trata como fin de la enumeración.",
                    status & 0xFFFFFFFF,
                    PROVEEDOR_TARJETA,
                    enumeradas,
                )
                break
            try:
                _verificar(status, "NCryptEnumKeys")
            except ErrorCNG as e:
                logger.warning(
                    "NCryptEnumKeys falló con 0x%08X en «%s» tras %d llave(s): %s",
                    status & 0xFFFFFFFF,
                    PROVEEDOR_TARJETA,
                    enumeradas,
                    e,
                )
                raise
            enumeradas += 1
            try:
                info = item.contents
                candidata = LlaveTarjeta(info.pszName, info.dwLegacyKeySpec, None)
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
        _FreeObject(proveedor)
    logger.info("Llaves enumeradas: %d; con certificado: %d.", enumeradas, len(llaves))
    if not llaves:
        raise ErrorCNG("No se encontró ninguna llave con certificado. ¿Está el DNIe insertado?")
    return llaves


def elegir_llave_de_firma(filtro: str | None = None) -> LlaveTarjeta:
    """
    Devuelve la llave de FIRMA (non_repudiation). Si se pasa `filtro`, se
    elige la llave cuyo titular contenga ese texto (sin distinguir mayúsculas).
    """
    llaves = listar_llaves()
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

    def _firmar_con_handle(self, llave, resumen: bytes, digest_algorithm: str) -> bytes:
        """NCryptSignHash sobre una llave ya abierta."""
        if self._algoritmo == "rsa":
            relleno = _Pkcs1PaddingInfo(_HASH_CNG[digest_algorithm])
            p_relleno, flags = ctypes.byref(relleno), _BCRYPT_PAD_PKCS1
        else:
            p_relleno, flags = None, 0
        hash_buf = (ctypes.c_ubyte * len(resumen)).from_buffer_copy(resumen)
        tam = wintypes.DWORD()
        _verificar(
            _SignHash(llave, p_relleno, hash_buf, len(resumen), None, 0, ctypes.byref(tam), flags),
            "NCryptSignHash (tamaño)",
        )
        salida = (ctypes.c_ubyte * tam.value)()
        logger.info(
            "NCryptSignHash: solicitando firma / PIN a Windows (resumen %s de %d bytes, ventana asociada: %s).",
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


class _FirmanteDeSesion(FirmanteCNG):
    """FirmanteCNG que firma con la llave que mantiene abierta una `SesionFirma`."""

    def __init__(self, llave: LlaveTarjeta, hwnd: int | None, sesion: "SesionFirma"):
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
    cada una. Este código nunca lee, guarda ni asigna el PIN.
    """

    def __init__(self, llave: LlaveTarjeta, hwnd: int | None = None):
        self._llave = llave
        self._hwnd = hwnd
        self._proveedor = None
        self._handle = None
        self.firmante: _FirmanteDeSesion | None = None
        self.firmas = 0  # firmas completadas en la sesión
        self.fallidas = 0  # intentos de firma que terminaron en error

    def __enter__(self) -> FirmanteCNG:
        # El firmante (cadena de certificados) se arma antes de abrir los handles.
        self.firmante = _FirmanteDeSesion(self._llave, self._hwnd, self)
        proveedor = _abrir_proveedor()
        try:
            handle = _abrir_llave(proveedor, self._llave)
        except BaseException:
            _FreeObject(proveedor)
            raise
        self._proveedor, self._handle = proveedor, handle
        self.firmante._asignar_ventana(handle)  # una sola vez para toda la sesión
        logger.info("Sesión de firma abierta: proveedor y llave abiertos 1 vez.")
        return self.firmante

    def __exit__(self, *excepcion):
        self.cerrar()
        return False

    def cerrar(self):
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

    def _firmar(self, firmante: FirmanteCNG, resumen: bytes, digest_algorithm: str) -> bytes:
        if self._handle is None:
            raise ErrorCNG("La sesión de firma está cerrada.")
        try:
            firma = firmante._firmar_con_handle(self._handle, resumen, digest_algorithm)
        except ErrorCNG:
            self.fallidas += 1
            raise
        self.firmas += 1
        logger.info("Sesión de firma: firma n.º %d completada con la misma llave abierta.", self.firmas)
        return firma
