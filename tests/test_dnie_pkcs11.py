"""
Vía PKCS#11 (módulo de IDEMIA del DNIe 3.0), sin DLL ni tarjeta.

`ModuloFalso` reemplaza a `dnie_pkcs11._Cryptoki` (la envoltura de la lista de
funciones de Cryptoki): simula los slots, el token, el login y la firma RSA
PKCS#1 v1.5 «en bruto» (CKM_RSA_PKCS) con la llave de prueba, así la firma se
puede verificar con la llave pública.
"""
import asyncio
import ctypes
import hashlib
import logging

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, utils

import dnie_cng
import dnie_pkcs11 as p11

PIN = "975310"
RUTA_FALSA = r"C:\IDEMIA\idplug-pkcs11.dll"
_HASHES = {"sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}


def firma_rsa_en_bruto(llave_privada, datos: bytes) -> bytes:
    """RSA PKCS#1 v1.5 sobre `datos` ya armados (DigestInfo || hash), como CKM_RSA_PKCS."""
    numeros = llave_privada.private_numbers()
    n = numeros.public_numbers.n
    k = (n.bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - len(datos) - 3) + b"\x00" + datos
    return pow(int.from_bytes(em, "big"), numeros.d, n).to_bytes(k, "big")


class Token:
    """Un DNIe en un slot: su certificado, su CKA_ID y su llave privada de prueba."""

    def __init__(self, cert_der, llave_privada=None, id_llave=b"\x01\x02", siempre_autenticar=True, flags=0x40D):
        self.cert_der = cert_der
        self.llave_privada = llave_privada
        self.id_llave = id_llave
        self.siempre_autenticar = siempre_autenticar
        self.flags = flags  # CKF_RNG | CKF_LOGIN_REQUIRED | CKF_USER_PIN_INITIALIZED | CKF_TOKEN_INITIALIZED


class ModuloFalso:
    """
    Estado del módulo PKCS#11 simulado. `tokens`: slot -> Token. `fallos`:
    operación -> CKR que devuelve. `logins`: tipo de cada C_Login (1 usuario,
    2 de contexto). `pines`: bytes que recibió cada C_Login.
    """

    def __init__(self, tokens=None, slots_vacios=(), pin=PIN, mecanismos=(p11.CKM_RSA_PKCS, p11.CKM_SHA256_RSA_PKCS)):
        self.tokens = dict(tokens or {})
        self.slots_vacios = list(slots_vacios)
        self.pin = pin
        self.mecanismos_ = list(mecanismos)
        self.fallos = {}
        self.llamadas = []
        self.logins = []
        self.pines = []
        self.firmados = []  # datos que recibió cada C_Sign
        self.cargas = 0
        self.inicializado = 0  # C_Initialize sin su C_Finalize
        self.sesiones = {}  # handle -> slot (abiertas)
        self.logueado = set()
        self._siguiente = 1000
        self._objetos = {}  # handle de objeto -> (slot, clase)
        self._busqueda = {}
        self._firma = {}  # sesión -> (llave, autenticada por contexto)

    def instalar(self, monkeypatch):
        monkeypatch.setattr(p11, "ruta_modulo", lambda: RUTA_FALSA)
        monkeypatch.setattr(p11, "_Cryptoki", self._crear)
        return self

    def _crear(self, ruta):
        assert ruta == RUTA_FALSA
        self.cargas += 1
        if "cargar" in self.fallos:
            raise OSError("no se pudo cargar el módulo")
        return _CryptokiFalso(self)

    def _fallar(self, operacion):
        self.llamadas.append(operacion)
        if operacion in self.fallos:
            raise p11.ErrorPkcs11(operacion, self.fallos[operacion])


class _CryptokiFalso:
    """La misma interfaz que `dnie_pkcs11._Cryptoki`, sobre un `ModuloFalso`."""

    def __init__(self, m: ModuloFalso):
        self.m = m
        self.version = "2.20"
        self.lista_coherente = True

    def inicializar(self):
        self.m._fallar("C_Initialize")
        self.m.inicializado += 1

    def finalizar(self):
        self.m._fallar("C_Finalize")
        self.m.inicializado -= 1

    def slots(self, con_token):
        self.m._fallar("C_GetSlotList")
        con = sorted(self.m.tokens)
        return con if con_token else sorted(con + self.m.slots_vacios)

    def info_slot(self, slot):
        self.m._fallar("C_GetSlotInfo")
        return p11.InfoSlot("Lector falso", "Fabricante", 0x7 if slot in self.m.tokens else 0x6)

    def info_token(self, slot):
        self.m._fallar("C_GetTokenInfo")
        return p11.InfoToken("DNIe", "IDEMIA", "IDPlug", "0001", self.m.tokens[slot].flags, 4, 8)

    def mecanismos(self, slot):
        self.m._fallar("C_GetMechanismList")
        return list(self.m.mecanismos_)

    def abrir_sesion(self, slot):
        self.m._fallar("C_OpenSession")
        self.m._siguiente += 1
        self.m.sesiones[self.m._siguiente] = slot
        return self.m._siguiente

    def cerrar_sesion(self, sesion):
        self.m.llamadas.append("C_CloseSession")
        self.m.sesiones.pop(sesion)

    def login(self, sesion, tipo, pin, largo):
        self.m.logins.append(tipo)
        self.m.pines.append(ctypes.string_at(pin, largo))
        self.m._fallar("C_Login")
        if ctypes.string_at(pin, largo) != self.m.pin.encode("utf-8"):
            raise p11.ErrorPkcs11("C_Login", p11.CKR_PIN_INCORRECT)
        if tipo == p11.CKU_USER:
            self.m.logueado.add(sesion)
        elif tipo == p11.CKU_CONTEXT_SPECIFIC:
            llave, _ = self.m._firma[sesion]
            self.m._firma[sesion] = (llave, True)

    def logout(self, sesion):
        self.m.llamadas.append("C_Logout")
        self.m.logueado.discard(sesion)

    def buscar_objetos(self, sesion, plantilla):
        self.m._fallar("C_FindObjects")
        token = self.m.tokens[self.m.sesiones[sesion]]
        filtro = dict(plantilla)
        hallados = []
        clase = filtro.get(p11.CKA_CLASS)
        if clase == p11.CKO_CERTIFICATE:
            hallados.append(("cert", token))
        elif clase == p11.CKO_PRIVATE_KEY and sesion in self.m.logueado and token.llave_privada is not None:
            if filtro.get(p11.CKA_ID, token.id_llave) == token.id_llave:
                hallados.append(("llave", token))
        handles = []
        for objeto in hallados:
            self.m._siguiente += 1
            self.m._objetos[self.m._siguiente] = objeto
            handles.append(self.m._siguiente)
        return handles

    def atributo(self, sesion, objeto, tipo):
        self.m._fallar("C_GetAttributeValue")
        clase, token = self.m._objetos[objeto]
        if clase == "cert":
            return {p11.CKA_VALUE: token.cert_der, p11.CKA_ID: token.id_llave}.get(tipo)
        if tipo == p11.CKA_ALWAYS_AUTHENTICATE:
            return b"\x01" if token.siempre_autenticar else b"\x00"
        return None

    def iniciar_firma(self, sesion, mecanismo, llave):
        self.m._fallar("C_SignInit")
        assert mecanismo == p11.CKM_RSA_PKCS
        assert sesion in self.m.logueado
        self.m._firma[sesion] = (llave, False)

    def firmar(self, sesion, datos):
        self.m._fallar("C_Sign")
        llave, autenticada = self.m._firma.pop(sesion)
        _, token = self.m._objetos[llave]
        if token.siempre_autenticar and not autenticada:
            raise p11.ErrorPkcs11("C_Sign", p11.CKR_USER_NOT_LOGGED_IN)
        self.m.firmados.append(datos)
        return firma_rsa_en_bruto(token.llave_privada, datos)


@pytest.fixture
def dnie_v3(monkeypatch, material_prueba):
    """Módulo de IDEMIA con un DNIe 3.0 cuyo certificado es el de prueba (slot 2) y un slot vacío."""
    llave_privada, cert = material_prueba
    otro = Token(b"otro certificado", None, id_llave=b"\x09")
    return ModuloFalso({1: otro, 2: Token(cert.dump(), llave_privada)}, slots_vacios=[0]).instalar(monkeypatch)


def _llave(material_prueba):
    return dnie_cng.LlaveTarjeta("{GUID-DNIe-3}", 2, material_prueba[1])


def _verificar_firma(material_prueba, firma, resumen, algoritmo="sha256"):
    material_prueba[0].public_key().verify(firma, resumen, padding.PKCS1v15(), utils.Prehashed(_HASHES[algoritmo]()))


def _todo_cerrado(m: ModuloFalso):
    return m.sesiones == {} and m.inicializado == 0


# --- ABI de Cryptoki en Windows ---------------------------------------------------


def test_estructuras_con_el_abi_de_windows():
    puntero = ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(p11.CK_ULONG) == 4  # unsigned long de 32 bits, también en 64 bits
    assert ctypes.sizeof(p11.CK_TOKEN_INFO) == 160
    assert ctypes.sizeof(p11.CK_SLOT_INFO) == 104
    assert ctypes.sizeof(p11.CK_ATTRIBUTE) == 8 + puntero  # empaquetado: sin relleno
    assert ctypes.sizeof(p11.CK_MECHANISM) == 8 + puntero
    assert p11.CK_FUNCTION_LIST.C_Initialize.offset == 2  # justo después de CK_VERSION
    assert p11.CK_FUNCTION_LIST.C_Sign.offset == 2 + 43 * puntero


def test_digest_info_sha256():
    resumen = hashlib.sha256(b"pagina").digest()
    prefijo = bytes.fromhex("3031300d060960864801650304020105000420")
    assert p11.digest_info(resumen, "sha256") == prefijo + resumen
    with pytest.raises(dnie_cng.ErrorCNG):
        p11.digest_info(resumen, "md5")
    with pytest.raises(dnie_cng.ErrorCNG):
        p11.digest_info(resumen[:20], "sha256")


# --- ubicación del módulo -----------------------------------------------------------


def test_ruta_del_modulo(monkeypatch, tmp_path):
    programas = tmp_path / "Program Files"
    dll = programas / "IDEMIA" / "IDPlugClassic" / "DLLs" / "idplug-pkcs11.dll"
    monkeypatch.delenv("FIRMADOR_PKCS11", raising=False)
    monkeypatch.setenv("ProgramFiles", str(programas))
    assert p11.ruta_modulo() is None  # no instalado: la vía no está disponible, no es un error
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ")
    assert p11.ruta_modulo() == str(dll)
    propio = tmp_path / "otro.dll"
    propio.write_bytes(b"MZ")
    monkeypatch.setenv("FIRMADOR_PKCS11", str(propio))
    assert p11.ruta_modulo() == str(propio)
    monkeypatch.setenv("FIRMADOR_PKCS11", "no")  # una ruta que no existe desactiva la vía
    assert p11.ruta_modulo() is None


# --- buscar_llave: sin PIN ----------------------------------------------------------


def test_buscar_llave_por_el_certificado(dnie_v3, material_prueba):
    ubicacion = p11.buscar_llave(material_prueba[1].dump())
    assert (ubicacion.slot, ubicacion.id_llave, ubicacion.ruta) == (2, b"\x01\x02", RUTA_FALSA)
    assert dnie_v3.logins == []  # sin PIN
    assert _todo_cerrado(dnie_v3)


def test_buscar_llave_sin_modulo_no_carga_nada(monkeypatch, material_prueba):
    m = ModuloFalso().instalar(monkeypatch)
    monkeypatch.setattr(p11, "ruta_modulo", lambda: None)
    assert p11.buscar_llave(material_prueba[1].dump()) is None
    assert m.cargas == 0


def test_buscar_llave_sin_coincidencia(monkeypatch, material_prueba, caplog):
    caplog.set_level(logging.INFO)
    m = ModuloFalso({1: Token(b"certificado de un DNIe 1.0", None)}).instalar(monkeypatch)
    assert p11.buscar_llave(material_prueba[1].dump()) is None
    assert _todo_cerrado(m)
    assert "ningún token tiene el certificado elegido" in caplog.text


@pytest.mark.parametrize("operacion", ["cargar", "C_Initialize", "C_GetSlotList"])
def test_buscar_llave_registra_el_fallo_y_devuelve_none(dnie_v3, material_prueba, operacion, caplog):
    dnie_v3.fallos[operacion] = 0x6  # CKR_FUNCTION_FAILED
    assert p11.buscar_llave(material_prueba[1].dump()) is None
    assert _todo_cerrado(dnie_v3)
    assert "PKCS#11" in caplog.text


def test_buscar_llave_salta_el_token_que_falla(dnie_v3, material_prueba, monkeypatch):
    original = _CryptokiFalso.atributo
    leidos = []

    def _atributo(self, sesion, objeto, tipo):
        leidos.append(self.m.sesiones[sesion])
        if self.m.sesiones[sesion] == 1:  # el token del slot 1 falla; el del slot 2 se revisa igual
            raise p11.ErrorPkcs11("C_GetAttributeValue", 0x30)
        return original(self, sesion, objeto, tipo)

    monkeypatch.setattr(_CryptokiFalso, "atributo", _atributo)
    assert p11.buscar_llave(material_prueba[1].dump()).slot == 2
    assert 1 in leidos and _todo_cerrado(dnie_v3)


# --- SesionPkcs11: un login y, si la llave lo exige, uno de contexto por firma -------


def _abrir(material_prueba):
    sesion = p11.abrir_sesion(_llave(material_prueba), PIN)
    assert sesion is not None
    return sesion


def test_un_solo_login_y_uno_de_contexto_antes_de_cada_firma(dnie_v3, material_prueba, monkeypatch):
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    sesion = _abrir(material_prueba)
    with sesion as firmante:
        for pagina in range(3):
            resumen = hashlib.sha256(b"pagina %d" % pagina).digest()
            _verificar_firma(material_prueba, firmante._firmar_resumen(resumen, "sha256"), resumen)
    assert dnie_v3.logins == [p11.CKU_USER] + [p11.CKU_CONTEXT_SPECIFIC] * 3
    assert dnie_v3.pines == [PIN.encode()] * 4
    assert sesion.firmas == 3 and sesion.siempre_autenticar
    assert "C_Logout" in dnie_v3.llamadas and _todo_cerrado(dnie_v3)


def test_sin_cka_always_authenticate_no_hay_login_de_contexto(dnie_v3, material_prueba, monkeypatch):
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    dnie_v3.tokens[2].siempre_autenticar = False
    with _abrir(material_prueba) as firmante:
        for _ in range(3):
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
    assert dnie_v3.logins == [p11.CKU_USER]


@pytest.mark.parametrize("algoritmo", ["sha256", "sha384", "sha512"])
def test_la_firma_es_pkcs1_v15_como_la_de_cng(dnie_v3, material_prueba, monkeypatch, algoritmo):
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    datos = b"contenido firmado"
    with _abrir(material_prueba) as firmante:
        firma = asyncio.run(firmante.async_sign_raw(datos, algoritmo))
    # La misma verificación que pasa una firma de NCryptSignHash con BCRYPT_PAD_PKCS1.
    material_prueba[0].public_key().verify(firma, datos, padding.PKCS1v15(), _HASHES[algoritmo]())


def test_ya_logueado_cuenta_como_login_correcto(dnie_v3, material_prueba, monkeypatch):
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    dnie_v3.fallos["C_Login"] = p11.CKR_USER_ALREADY_LOGGED_IN
    dnie_v3.tokens[2].siempre_autenticar = False
    original = _CryptokiFalso.login

    def _login(self, sesion, tipo, pin, largo):
        try:
            original(self, sesion, tipo, pin, largo)
        finally:
            self.m.logueado.add(sesion)

    monkeypatch.setattr(_CryptokiFalso, "login", _login)
    with _abrir(material_prueba) as firmante:
        firmante._firmar_resumen(b"\x00" * 32, "sha256")


@pytest.mark.parametrize(
    "ckr, codigo",
    [(0xA0, 0x8010006B), (0xA1, 0x8010006B), (0xA2, 0x8010006B), (0xA3, 0x8010006C), (0xA4, 0x8010006C)],
)
def test_pin_rechazado_en_el_login_detiene_sin_cng(dnie_v3, material_prueba, caplog, ckr, codigo):
    caplog.set_level(logging.DEBUG)
    dnie_v3.fallos["C_Login"] = ckr
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        p11.abrir_sesion(_llave(material_prueba), PIN)
    assert error.value.codigo == codigo and p11.es_error_de_pin(error.value)
    assert dnie_v3.logins == [p11.CKU_USER]  # un solo intento
    assert _todo_cerrado(dnie_v3)
    assert PIN not in caplog.text and PIN not in str(error.value)


def test_pin_incorrecto_borra_el_pin_y_cierra_todo(dnie_v3, material_prueba):
    dnie_v3.pin = "000000"
    ubicacion = p11.buscar_llave(material_prueba[1].dump())
    sesion = p11.SesionPkcs11(ubicacion, PIN, _llave(material_prueba))
    buffer = sesion._pin
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        sesion.abrir()
    assert error.value.codigo == 0x8010006B
    assert sesion._pin is None
    assert ctypes.string_at(ctypes.addressof(buffer), ctypes.sizeof(buffer)) == bytes(ctypes.sizeof(buffer))
    assert _todo_cerrado(dnie_v3)


def test_pin_rechazado_en_el_login_de_contexto_detiene_la_sesion(dnie_v3, material_prueba, monkeypatch):
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    sesion = _abrir(material_prueba)
    buffer = sesion._pin
    with sesion as firmante:
        dnie_v3.pin = "000000"  # la tarjeta rechaza el PIN en el login de contexto
        with pytest.raises(dnie_cng.ErrorCNG) as error:
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
        assert error.value.codigo == 0x8010006B
        with pytest.raises(dnie_cng.ErrorCNG) as otra:  # ni otra firma ni otro intento del PIN
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
        assert otra.value.codigo == 0x8010006B
    assert dnie_v3.logins == [p11.CKU_USER, p11.CKU_CONTEXT_SPECIFIC]
    assert ctypes.string_at(ctypes.addressof(buffer), ctypes.sizeof(buffer)) == bytes(ctypes.sizeof(buffer))
    assert _todo_cerrado(dnie_v3)


def test_la_sesion_no_guarda_el_pin_como_texto(dnie_v3, material_prueba):
    sesion = p11.SesionPkcs11(p11.buscar_llave(material_prueba[1].dump()), PIN, _llave(material_prueba))
    assert PIN not in repr(vars(sesion))
    sesion.cerrar()


def test_la_sesion_cierra_todo_aunque_falle_la_firma(dnie_v3, material_prueba, monkeypatch):
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    with pytest.raises(dnie_cng.ErrorCNG):
        with _abrir(material_prueba) as firmante:
            dnie_v3.fallos["C_Sign"] = 0x30  # CKR_DEVICE_ERROR
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
    assert "C_Logout" in dnie_v3.llamadas and _todo_cerrado(dnie_v3)


# --- abrir_sesion: cuándo se sigue por CNG -------------------------------------------


def test_sin_modulo_o_sin_coincidencia_se_sigue_por_cng(monkeypatch, material_prueba):
    monkeypatch.setattr(p11, "ruta_modulo", lambda: None)
    assert p11.abrir_sesion(_llave(material_prueba), PIN) is None
    m = ModuloFalso({1: Token(b"DNIe 1.0", None)}).instalar(monkeypatch)
    assert p11.abrir_sesion(_llave(material_prueba), PIN) is None
    assert m.logins == []


@pytest.mark.parametrize("operacion", ["C_Initialize", "C_GetTokenInfo", "C_OpenSession"])
def test_un_fallo_antes_del_login_sigue_por_cng(dnie_v3, material_prueba, monkeypatch, operacion, caplog):
    ubicacion = p11.buscar_llave(material_prueba[1].dump())  # la búsqueda pasa; falla al abrir la sesión
    monkeypatch.setattr(p11, "buscar_llave", lambda cert_der: ubicacion)
    dnie_v3.fallos[operacion] = 0x6
    assert p11.abrir_sesion(_llave(material_prueba), PIN) is None
    assert dnie_v3.logins == [] and _todo_cerrado(dnie_v3)
    assert "se sigue con CNG" in caplog.text


def test_sin_ckm_rsa_pkcs_sigue_por_cng_sin_usar_el_pin(dnie_v3, material_prueba, caplog):
    dnie_v3.mecanismos_ = [p11.CKM_SHA256_RSA_PKCS]
    assert p11.abrir_sesion(_llave(material_prueba), PIN) is None
    assert dnie_v3.logins == [] and _todo_cerrado(dnie_v3)
    assert "CKM_RSA_PKCS" in caplog.text


def test_lector_con_teclado_propio_sigue_por_cng(dnie_v3, material_prueba):
    dnie_v3.tokens[2].flags |= p11.CKF_PROTECTED_AUTHENTICATION_PATH
    assert p11.abrir_sesion(_llave(material_prueba), PIN) is None
    assert dnie_v3.logins == []


def test_pin_bloqueado_segun_el_token_no_intenta_el_login(dnie_v3, material_prueba):
    dnie_v3.tokens[2].flags |= p11.CKF_USER_PIN_LOCKED
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        p11.abrir_sesion(_llave(material_prueba), PIN)
    assert error.value.codigo == 0x8010006C and dnie_v3.logins == []


def test_un_fallo_tras_el_login_sin_pin_rechazado_sigue_por_cng(dnie_v3, material_prueba, caplog):
    dnie_v3.tokens[2].llave_privada = None  # el login pasa, pero no aparece la llave privada
    assert p11.abrir_sesion(_llave(material_prueba), PIN) is None
    assert dnie_v3.logins == [p11.CKU_USER]
    assert "C_Logout" in dnie_v3.llamadas and _todo_cerrado(dnie_v3)
    assert "se sigue con CNG" in caplog.text


def test_un_login_que_falla_por_otra_causa_no_sigue_por_cng(dnie_v3, material_prueba):
    # El PIN pudo llegar a la tarjeta: probar CNG con el mismo PIN sería un segundo intento.
    dnie_v3.fallos["C_Login"] = 0x30  # CKR_DEVICE_ERROR
    with pytest.raises(dnie_cng.ErrorCNG):
        p11.abrir_sesion(_llave(material_prueba), PIN)
    assert dnie_v3.logins == [p11.CKU_USER] and _todo_cerrado(dnie_v3)


def test_llave_que_no_es_rsa_sigue_por_cng(dnie_v3, material_prueba, monkeypatch):
    llave = _llave(material_prueba)

    class _CertEc:
        public_key = type("P", (), {"algorithm": "ec"})()

        def dump(self):
            return material_prueba[1].dump()

    monkeypatch.setattr(llave, "cert", _CertEc())
    assert p11.abrir_sesion(llave, PIN) is None
    assert dnie_v3.cargas == 0
