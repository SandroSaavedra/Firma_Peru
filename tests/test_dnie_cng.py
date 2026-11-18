"""Pruebas de `dnie_cng` que no necesitan la tarjeta."""
import ctypes

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
