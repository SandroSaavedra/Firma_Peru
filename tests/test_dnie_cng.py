"""Pruebas de `dnie_cng` que no necesitan la tarjeta."""
import asyncio
import ctypes
import hashlib
import logging

import pytest

import dnie_cng


def test_hwnd_se_asigna_a_la_llave_antes_de_firmar(monkeypatch, material_prueba):
    llamadas = []

    def _set_property(handle, nombre, valor, tam, flags):
        puntero = ctypes.cast(valor, ctypes.POINTER(ctypes.c_void_p))
        llamadas.append((handle, nombre, puntero.contents.value, tam, flags))
        return 0

    monkeypatch.setattr(dnie_cng, "_SetProperty", _set_property)
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    llave = dnie_cng.LlaveTarjeta("llave", 0, material_prueba[1])

    dnie_cng.FirmanteCNG(llave, hwnd=0x1234)._asignar_ventana(7)
    dnie_cng.FirmanteCNG(llave)._asignar_ventana(7)

    assert llamadas == [(7, "HWND Handle", 0x1234, ctypes.sizeof(ctypes.c_void_p), 0)]


def test_error_al_asignar_hwnd_no_impide_firmar(monkeypatch, material_prueba):
    monkeypatch.setattr(dnie_cng, "_SetProperty", lambda *a: 0x80090029)  # NTE_NOT_SUPPORTED
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    llave = dnie_cng.LlaveTarjeta("llave", 0, material_prueba[1])
    dnie_cng.FirmanteCNG(llave, hwnd=0x1234)._asignar_ventana(7)  # no lanza


# --- sesión de firma: proveedor y llave abiertos una sola vez ---------------------


class _CngFalso:
    """Registra las llamadas a los envoltorios de ncrypt sin tocar la tarjeta."""

    def __init__(self, monkeypatch):
        self.proveedores = 0
        self.llaves = 0
        self.liberados = []
        self.ventanas = []
        self.firmas = []
        monkeypatch.setattr(dnie_cng, "_abrir_proveedor", self._abrir_proveedor)
        monkeypatch.setattr(dnie_cng, "_abrir_llave", self._abrir_llave)
        monkeypatch.setattr(dnie_cng, "_FreeObject", lambda h: self.liberados.append(int(h)) or 0)
        monkeypatch.setattr(dnie_cng, "_SetProperty", self._set_property)
        monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
        # Función (no método ligado) para que reciba el firmante como `self`.
        monkeypatch.setattr(
            dnie_cng.FirmanteCNG, "_firmar_con_handle", lambda firmante, *a: self._firmar(firmante, *a)
        )

    def _abrir_proveedor(self):
        self.proveedores += 1
        return 100 + self.proveedores

    def _abrir_llave(self, proveedor, llave, flags=0):
        self.llaves += 1
        return 200 + self.llaves

    def _set_property(self, handle, nombre, valor, tam, flags):
        self.ventanas.append((int(handle), nombre))
        return 0

    def _firmar(self, firmante, handle, resumen, algoritmo):
        self.firmas.append(int(handle))
        return b"firma"


def test_sesion_abre_proveedor_y_llave_una_vez(monkeypatch, material_prueba):
    cng = _CngFalso(monkeypatch)
    llave = dnie_cng.LlaveTarjeta("llave", 0, material_prueba[1])
    with dnie_cng.SesionFirma(llave, hwnd=0x1234) as firmante:
        assert isinstance(firmante, dnie_cng.FirmanteCNG)
        for _ in range(3):
            assert firmante._firmar_resumen(b"\x00" * 32, "sha256") == b"firma"
        assert firmante.sesion.firmas == 3
    assert (cng.proveedores, cng.llaves) == (1, 1)
    assert cng.firmas == [201, 201, 201]  # siempre la misma llave abierta
    assert cng.ventanas == [(201, "HWND Handle")]  # la ventana se asigna una sola vez
    assert sorted(cng.liberados) == [101, 201]  # llave y proveedor liberados al salir
    try:
        firmante._firmar_resumen(b"\x00" * 32, "sha256")
    except dnie_cng.ErrorCNG:
        pass
    else:
        raise AssertionError("una sesión cerrada no debe firmar")


def test_sesion_libera_los_handles_aunque_falle_la_firma(monkeypatch, material_prueba):
    cng = _CngFalso(monkeypatch)

    def _falla(firmante, handle, resumen, algoritmo):
        raise dnie_cng.ErrorCNG("NCryptSignHash falló (0x8010006E) cancelado")

    monkeypatch.setattr(dnie_cng.FirmanteCNG, "_firmar_con_handle", _falla)
    llave = dnie_cng.LlaveTarjeta("llave", 0, material_prueba[1])
    sesion = dnie_cng.SesionFirma(llave)
    try:
        with sesion as firmante:
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
    except dnie_cng.ErrorCNG:
        pass
    assert sorted(cng.liberados) == [101, 201]
    assert (sesion.firmas, sesion.fallidas) == (0, 1)
    assert cng.ventanas == []  # sin HWND no se asigna ventana


def test_firmante_de_un_uso_abre_y_libera_en_cada_firma(monkeypatch, material_prueba):
    cng = _CngFalso(monkeypatch)
    firmante = dnie_cng.FirmanteCNG(dnie_cng.LlaveTarjeta("llave", 0, material_prueba[1]))
    firmante._firmar_resumen(b"\x00" * 32, "sha256")
    firmante._firmar_resumen(b"\x00" * 32, "sha256")
    assert (cng.proveedores, cng.llaves) == (2, 2)
    assert len(cng.liberados) == 4


# --- PIN provisto por la aplicación (un solo PIN para muchas firmas) --------------

PIN = "975310"


class _NcryptFalso:
    """
    Simula NCryptSetProperty y NCryptSignHash (con el `_firmar_con_handle` real)
    y registra el orden de las llamadas. `fallo_firma` / `fallo_pin`: código que
    devuelve NCryptSignHash o la asignación del PIN. `respuestas`: códigos que
    devuelven, en orden, los primeros NCryptSignHash (después, `fallo_firma`).
    """

    def __init__(self, monkeypatch, fallo_firma=0, fallo_pin=0, respuestas=()):
        self.llamadas = []
        self.pines = []  # bytes que recibió NCryptSetProperty("SmartCardPin")
        self.resumenes = []  # resumen que recibió cada NCryptSignHash (la firma, no el tamaño)
        self.fallo_firma = fallo_firma
        self.fallo_pin = fallo_pin
        self.respuestas = list(respuestas)
        monkeypatch.setattr(dnie_cng, "_abrir_proveedor", lambda: 101)
        monkeypatch.setattr(dnie_cng, "_abrir_llave", lambda proveedor, llave, flags=0: 201)
        monkeypatch.setattr(dnie_cng, "_FreeObject", lambda h: 0)
        monkeypatch.setattr(dnie_cng, "_SetProperty", self._set_property)
        monkeypatch.setattr(dnie_cng, "_SignHash", self._sign_hash)
        monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])

    def _set_property(self, handle, nombre, valor, tam, flags):
        if nombre == "SmartCardPin":
            self.pines.append(ctypes.string_at(ctypes.addressof(valor._obj), tam))
            self.llamadas.append(("pin", int(handle), flags))
            return self.fallo_pin
        self.llamadas.append((nombre, int(handle), flags))
        return 0

    def _sign_hash(self, handle, relleno, resumen, largo, salida, tam, p_tam, flags):
        if salida is None:
            p_tam._obj.value = 4
            self.llamadas.append(("tamaño", int(handle), flags))
            return 0
        self.llamadas.append(("firma", int(handle), flags))
        self.resumenes.append(bytes(resumen))
        codigo = self.respuestas.pop(0) if self.respuestas else self.fallo_firma
        if codigo:
            return codigo
        ctypes.memmove(salida, b"FIRM", 4)
        return 0


def _sesion(material_prueba, pin):
    return dnie_cng.SesionFirma(dnie_cng.LlaveTarjeta("llave", 0, material_prueba[1]), pin=pin)


def test_con_pin_se_asigna_antes_de_cada_firma_y_firma_en_silencio(monkeypatch, material_prueba):
    cng = _NcryptFalso(monkeypatch)
    with _sesion(material_prueba, PIN) as firmante:
        assert firmante._firmar_resumen(b"\x00" * 32, "sha256") == b"FIRM"
        assert firmante._firmar_resumen(b"\x00" * 32, "sha256") == b"FIRM"

    silencioso = dnie_cng._BCRYPT_PAD_PKCS1 | dnie_cng._NCRYPT_SILENT_FLAG
    una_firma = [("pin", 201, 0), ("tamaño", 201, silencioso), ("firma", 201, silencioso)]
    assert cng.llamadas == una_firma * 2
    # UTF-16LE con el terminador nulo incluido
    assert cng.pines == [PIN.encode("utf-16-le") + b"\x00\x00"] * 2


def test_sin_pin_no_cambia_nada(monkeypatch, material_prueba):
    cng = _NcryptFalso(monkeypatch)
    with _sesion(material_prueba, None) as firmante:
        firmante._firmar_resumen(b"\x00" * 32, "sha256")
    # sin «SmartCardPin» y sin NCRYPT_SILENT_FLAG: Windows muestra su cuadro del PIN
    assert cng.llamadas == [("tamaño", 201, dnie_cng._BCRYPT_PAD_PKCS1), ("firma", 201, dnie_cng._BCRYPT_PAD_PKCS1)]
    assert cng.pines == []


def test_el_pin_se_borra_de_la_memoria_al_cerrar(monkeypatch, material_prueba):
    _NcryptFalso(monkeypatch)
    sesion = _sesion(material_prueba, PIN)
    buffer = sesion._pin
    with sesion as firmante:
        firmante._firmar_resumen(b"\x00" * 32, "sha256")
    assert sesion._pin is None
    assert ctypes.string_at(ctypes.addressof(buffer), ctypes.sizeof(buffer)) == bytes(ctypes.sizeof(buffer))


def test_el_pin_se_borra_si_no_se_puede_abrir_la_llave(monkeypatch, material_prueba):
    _NcryptFalso(monkeypatch)

    def _sin_llave(proveedor, llave, flags=0):
        raise dnie_cng.ErrorCNG("NCryptOpenKey falló (0x80090016)", codigo=0x80090016)

    monkeypatch.setattr(dnie_cng, "_abrir_llave", _sin_llave)
    sesion = _sesion(material_prueba, PIN)
    buffer = sesion._pin
    with pytest.raises(dnie_cng.ErrorCNG):
        with sesion:
            pass
    assert sesion._pin is None
    assert ctypes.string_at(ctypes.addressof(buffer), ctypes.sizeof(buffer)) == bytes(ctypes.sizeof(buffer))


@pytest.mark.parametrize("codigo", [0x8010006B, 0x8010006C, 0x80090033])
def test_pin_incorrecto_al_firmar_detiene_sin_reintentar(monkeypatch, material_prueba, caplog, codigo):
    caplog.set_level(logging.DEBUG)
    cng = _NcryptFalso(monkeypatch, fallo_firma=codigo)
    sesion = _sesion(material_prueba, PIN)
    with sesion as firmante:
        with pytest.raises(dnie_cng.ErrorCNG) as error:
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
        assert error.value.codigo == codigo
        # la sesión queda detenida: ni otra firma ni otro intento del PIN
        with pytest.raises(dnie_cng.ErrorCNG) as otra:
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
        assert otra.value.codigo == codigo
    assert [c[0] for c in cng.llamadas].count("firma") == 1
    assert [c[0] for c in cng.llamadas].count("pin") == 1
    assert sesion._pin is None  # se borra en cuanto el PIN falla
    assert PIN not in str(error.value) and PIN not in str(otra.value)
    assert PIN not in caplog.text


def test_pin_rechazado_al_asignarlo_no_llega_a_firmar(monkeypatch, material_prueba):
    cng = _NcryptFalso(monkeypatch, fallo_pin=0x8010006B)
    with _sesion(material_prueba, PIN) as firmante:
        with pytest.raises(dnie_cng.ErrorCNG) as error:
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
        with pytest.raises(dnie_cng.ErrorCNG):
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
    assert error.value.codigo == 0x8010006B
    assert cng.llamadas == [("pin", 201, 0)]  # una sola vez y sin NCryptSignHash


def test_otro_error_de_firma_no_detiene_la_sesion(monkeypatch, material_prueba):
    cng = _NcryptFalso(monkeypatch, fallo_firma=0x80100069)  # se retiró la tarjeta
    with _sesion(material_prueba, PIN) as firmante:
        for _ in range(2):
            with pytest.raises(dnie_cng.ErrorCNG):
                firmante._firmar_resumen(b"\x00" * 32, "sha256")
    # no es un error del PIN: quien llama decide; la sesión no lo reintenta por su cuenta
    assert [c[0] for c in cng.llamadas].count("firma") == 2


def test_la_sesion_no_guarda_el_pin_como_texto(monkeypatch, material_prueba):
    _NcryptFalso(monkeypatch)
    sesion = _sesion(material_prueba, PIN)
    assert all(valor != PIN for valor in vars(sesion).values())
    assert PIN not in repr(vars(sesion))


# --- DNIe que no acepta el PIN en silencio (DNIe 3.0): el cuadro de Windows en cada firma

NTE_SILENT_CONTEXT = 0x80090022
SILENCIOSO = dnie_cng._BCRYPT_PAD_PKCS1 | dnie_cng._NCRYPT_SILENT_FLAG
INTERACTIVO = dnie_cng._BCRYPT_PAD_PKCS1


def _sesion_con_aviso(material_prueba, pin, avisos):
    return dnie_cng.SesionFirma(
        dnie_cng.LlaveTarjeta("llave", 0, material_prueba[1]),
        hwnd=0x1234,
        pin=pin,
        al_pedir_pin_por_firma=lambda: avisos.append(True),
    )


def test_rechazo_silencioso_reintenta_la_misma_firma_con_el_cuadro_de_windows(monkeypatch, material_prueba, caplog):
    caplog.set_level(logging.DEBUG)
    cng = _NcryptFalso(monkeypatch, respuestas=[NTE_SILENT_CONTEXT])
    avisos = []
    sesion = _sesion_con_aviso(material_prueba, PIN, avisos)
    buffer = sesion._pin
    with sesion as firmante:
        assert not sesion.pin_por_firma
        assert firmante._firmar_resumen(b"\x01" * 32, "sha256") == b"FIRM"
        # la sesión pasa a pedir el PIN a Windows y borra el PIN de inmediato
        assert sesion.pin_por_firma
        assert sesion._pin is None
        assert ctypes.string_at(ctypes.addressof(buffer), ctypes.sizeof(buffer)) == bytes(ctypes.sizeof(buffer))
        # las firmas siguientes ya son con el cuadro de Windows, sin «SmartCardPin»
        assert firmante._firmar_resumen(b"\x02" * 32, "sha256") == b"FIRM"

    assert cng.llamadas == [
        ("HWND Handle", 201, 0),
        ("pin", 201, 0),
        ("tamaño", 201, SILENCIOSO),
        ("firma", 201, SILENCIOSO),
        ("HWND Handle", 201, 0),  # llave reabierta limpia, con la misma ventana
        ("tamaño", 201, INTERACTIVO),
        ("firma", 201, INTERACTIVO),
        ("tamaño", 201, INTERACTIVO),
        ("firma", 201, INTERACTIVO),
    ]
    # el reintento firma el mismo resumen: la tarjeta no llegó a tocarse
    assert cng.resumenes == [b"\x01" * 32, b"\x01" * 32, b"\x02" * 32]
    assert avisos == [True]  # se avisa una sola vez
    assert (sesion.firmas, sesion.fallidas) == (2, 0)
    assert "el DNIe exige su propio cuadro del PIN en cada firma" in caplog.text
    assert PIN not in caplog.text


def test_pin_por_firma_es_de_solo_lectura(monkeypatch, material_prueba):
    _NcryptFalso(monkeypatch)
    sesion = _sesion(material_prueba, PIN)
    with pytest.raises(AttributeError):
        sesion.pin_por_firma = True


def test_sin_rechazo_silencioso_todo_sigue_igual(monkeypatch, material_prueba):
    cng = _NcryptFalso(monkeypatch)
    avisos = []
    sesion = _sesion_con_aviso(material_prueba, PIN, avisos)
    with sesion as firmante:
        for _ in range(3):
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
    assert not sesion.pin_por_firma and avisos == []
    assert [c for c in cng.llamadas if c[0] == "firma"] == [("firma", 201, SILENCIOSO)] * 3
    assert [c[0] for c in cng.llamadas].count("pin") == 3


def test_cancelar_el_cuadro_de_windows_tras_el_rechazo_silencioso(monkeypatch, material_prueba):
    cng = _NcryptFalso(monkeypatch, respuestas=[NTE_SILENT_CONTEXT, 0x8010006E])  # SCARD_W_CANCELLED_BY_USER
    sesion = _sesion_con_aviso(material_prueba, PIN, [])
    with sesion as firmante:
        with pytest.raises(dnie_cng.ErrorCNG) as error:
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
    assert error.value.codigo == 0x8010006E
    assert [c for c in cng.llamadas if c[0] == "firma"] == [("firma", 201, SILENCIOSO), ("firma", 201, INTERACTIVO)]
    assert sesion._pin is None


@pytest.mark.parametrize("codigo", [0x8010006B, 0x8010006C, 0x80090033])
def test_pin_incorrecto_en_el_cuadro_de_windows_detiene_la_sesion(monkeypatch, material_prueba, codigo):
    cng = _NcryptFalso(monkeypatch, respuestas=[NTE_SILENT_CONTEXT, codigo])
    sesion = _sesion_con_aviso(material_prueba, PIN, [])
    with sesion as firmante:
        with pytest.raises(dnie_cng.ErrorCNG) as error:
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
        assert error.value.codigo == codigo
        # detenida: la firma siguiente no toca la tarjeta
        with pytest.raises(dnie_cng.ErrorCNG) as otra:
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
        assert otra.value.codigo == codigo
    assert [c[0] for c in cng.llamadas].count("firma") == 2
    assert sesion.fallidas == 1


def test_un_segundo_rechazo_silencioso_no_se_reintenta(monkeypatch, material_prueba):
    cng = _NcryptFalso(monkeypatch, respuestas=[NTE_SILENT_CONTEXT, NTE_SILENT_CONTEXT])
    with _sesion_con_aviso(material_prueba, PIN, []) as firmante:
        with pytest.raises(dnie_cng.ErrorCNG) as error:
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
    assert error.value.codigo == NTE_SILENT_CONTEXT
    assert [c[0] for c in cng.llamadas].count("firma") == 2


def test_sin_pin_un_rechazo_silencioso_no_se_reintenta(monkeypatch, material_prueba):
    cng = _NcryptFalso(monkeypatch, respuestas=[NTE_SILENT_CONTEXT])
    avisos = []
    sesion = _sesion_con_aviso(material_prueba, None, avisos)
    with sesion as firmante:
        with pytest.raises(dnie_cng.ErrorCNG):
            firmante._firmar_resumen(b"\x00" * 32, "sha256")
    assert [c[0] for c in cng.llamadas].count("firma") == 1
    assert not sesion.pin_por_firma and avisos == []


def test_un_aviso_que_falla_no_impide_firmar(monkeypatch, material_prueba):
    _NcryptFalso(monkeypatch, respuestas=[NTE_SILENT_CONTEXT])

    def _aviso_roto():
        raise RuntimeError("sin interfaz")

    sesion = dnie_cng.SesionFirma(
        dnie_cng.LlaveTarjeta("llave", 0, material_prueba[1]), pin=PIN, al_pedir_pin_por_firma=_aviso_roto
    )
    with sesion as firmante:
        assert firmante._firmar_resumen(b"\x00" * 32, "sha256") == b"FIRM"
    assert sesion.pin_por_firma


def test_rechazo_silencioso_reabre_la_llave_sin_el_pin_antes_del_cuadro_de_windows(monkeypatch, material_prueba):
    cng = _NcryptFalso(monkeypatch, respuestas=[NTE_SILENT_CONTEXT])
    abiertas, liberadas = [], []
    monkeypatch.setattr(dnie_cng, "_abrir_llave", lambda proveedor, llave, flags=0: abiertas.append(1) or 200 + len(abiertas))
    monkeypatch.setattr(dnie_cng, "_FreeObject", lambda h: liberadas.append(int(h)) or 0)
    sesion = _sesion_con_aviso(material_prueba, PIN, [])
    with sesion as firmante:
        firmante._firmar_resumen(b"" * 32, "sha256")
        firmante._firmar_resumen(b"" * 32, "sha256")
        assert 201 in liberadas  # la llave que recibió el PIN se suelta antes del reintento
    # el reintento y las firmas siguientes usan la llave nueva (202), con su ventana asignada
    assert [h for (_, h, f) in cng.llamadas if f == INTERACTIVO] == [202, 202, 202, 202]
    assert ("HWND Handle", 202, 0) in cng.llamadas
    assert ("pin", 202, 0) not in cng.llamadas
    assert len(abiertas) == 2


# --- respaldo de firma enchufable (CNG o PKCS#11) --------------------------------


def test_el_firmante_de_sesion_delega_la_firma_en_su_respaldo(monkeypatch, material_prueba):
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    recibido = []

    class _Respaldo:
        def _firmar(self, firmante, resumen, algoritmo):
            recibido.append((firmante, resumen, algoritmo))
            return b"FIRMA"

    respaldo = _Respaldo()
    firmante = dnie_cng.FirmanteDeSesion(dnie_cng.LlaveTarjeta("llave", 0, material_prueba[1]), None, respaldo)
    assert asyncio.run(firmante.async_sign_raw(b"datos", "sha256")) == b"FIRMA"
    assert recibido == [(firmante, hashlib.sha256(b"datos").digest(), "sha256")]
    assert firmante.sesion is respaldo
