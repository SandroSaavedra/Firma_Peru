"""
Búsqueda de la llave del DNIe por tres vías, sin la tarjeta:

1. NCryptEnumKeys sin alcance (como siempre);
2. NCryptEnumKeys con el alcance de cada lector con tarjeta (\\\\.\\<lector>\\);
3. certificados del almacén «MY» del usuario cuya llave está en una tarjeta.

Se simulan los envoltorios de ncrypt (`_abrir_proveedor`, `_EnumKeys`,
`_abrir_llave`, `_leer_propiedad`, `_FreeObject`, `_FreeBuffer`), la consulta a
WinSCard (`_recolectar_hechos`) y la lectura del almacén
(`_certificados_de_tarjeta_en_almacen`).
"""
import ctypes
import datetime

import pytest
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

import diagnostico_dnie as dd
import dnie_cng

NTE_NOT_FOUND = 0x80090011
NTE_BAD_KEYSET = 0x80090016
PIN = "975310"


def _lector(nombre, estado=dd.SCARD_STATE_PRESENT):
    return dd.Lector(nombre=nombre, estado=estado, atr=b"\x3b\x7f", tarjeta="DNIe")


def _alcance(nombre):
    return "\\\\.\\" + nombre + "\\"


class _TarjetaFalsa:
    """
    `enumeraciones`: {alcance (None = sin alcance): [nombres de llave] o código de error}.
    `abribles`: nombres que NCryptOpenKey abre. `almacen`: candidatos del almacén MY.
    """

    def __init__(self, monkeypatch, cert, enumeraciones, lectores=None, almacen=None, abribles=None):
        self.cert = cert
        self.enumeraciones = enumeraciones
        self.pendientes = {}
        self.alcances = []  # alcances con los que se llamó a NCryptEnumKeys (una vez cada uno)
        self.aperturas = []  # (nombre, flags) de NCryptOpenKey
        self.liberados = []
        self.vivos = []  # estructuras que deben sobrevivir a la llamada
        self.lectores = lectores
        self.almacen = almacen
        self.consultas_lectores = 0
        self.consultas_almacen = 0
        self.abribles = set(abribles or ())
        monkeypatch.setattr(dnie_cng, "_abrir_proveedor", lambda: dnie_cng._HANDLE(7))
        monkeypatch.setattr(dnie_cng, "_EnumKeys", self._enum_keys)
        monkeypatch.setattr(dnie_cng, "_abrir_llave", self._abrir_llave)
        monkeypatch.setattr(dnie_cng, "_leer_propiedad", lambda handle, nombre: self.cert.dump())
        monkeypatch.setattr(dnie_cng, "_FreeObject", lambda h: self.liberados.append(int(getattr(h, "value", h))) or 0)
        monkeypatch.setattr(dnie_cng, "_FreeBuffer", lambda b: 0)
        monkeypatch.setattr(dnie_cng, "_recolectar_hechos", self._recolectar_hechos)
        monkeypatch.setattr(dnie_cng, "_certificados_de_tarjeta_en_almacen", self._almacen)
        monkeypatch.setattr(dnie_cng, "_dormir", lambda s: None)

    def _enum_keys(self, proveedor, alcance, p_item, p_estado, flags):
        if alcance not in self.pendientes:
            self.alcances.append(alcance)
            resultado = self.enumeraciones.get(alcance, NTE_NOT_FOUND)
            self.pendientes[alcance] = list(resultado) if isinstance(resultado, list) else resultado
        pendiente = self.pendientes[alcance]
        if isinstance(pendiente, int):
            del self.pendientes[alcance]
            return pendiente
        if not pendiente:
            del self.pendientes[alcance]
            return dnie_cng._NTE_NO_MORE_ITEMS
        info = dnie_cng._NCryptKeyName(pszName=pendiente.pop(0), dwLegacyKeySpec=2)
        self.vivos.append(info)
        p_item._obj.contents = info
        return 0

    def _abrir_llave(self, proveedor, llave, flags=0):
        self.aperturas.append((llave.nombre, flags))
        if llave.nombre not in self.abribles:
            raise dnie_cng.ErrorCNG(f"NCryptOpenKey falló (0x{NTE_BAD_KEYSET:08X})", codigo=NTE_BAD_KEYSET)
        return dnie_cng._HANDLE(200 + len(self.aperturas))

    def _recolectar_hechos(self):
        self.consultas_lectores += 1
        if self.lectores is None:
            raise AssertionError("no se debía consultar WinSCard")
        return {"error_lectores": None, "lectores": self.lectores}

    def _almacen(self):
        self.consultas_almacen += 1
        if self.almacen is None:
            raise AssertionError("no se debía leer el almacén MY")
        return self.almacen


def _cert_sin_uso_de_firma(material_prueba):
    """Certificado con uso de cifrado solamente (no sirve para firmar)."""
    llave = material_prueba[0]
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PRUEBA CIFRADO 12345678")])
    ahora = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(days=1))
        .not_valid_after(ahora + datetime.timedelta(days=365))
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(llave, hashes.SHA256())
    )
    return asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))


def _texto(caplog):
    return "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("firmador"))


# --- intento 1: sin alcance -------------------------------------------------------


def test_sin_alcance_encuentra_la_llave_y_no_usa_las_otras_vias(monkeypatch, material_prueba, caplog):
    caplog.set_level("INFO")
    tarjeta = _TarjetaFalsa(monkeypatch, material_prueba[1], {None: ["cont-1"]}, abribles={"cont-1"})
    llaves = dnie_cng.listar_llaves()
    assert [l.nombre for l in llaves] == ["cont-1"]
    assert tarjeta.alcances == [None]
    assert (tarjeta.consultas_lectores, tarjeta.consultas_almacen) == (0, 0)
    assert "halladas por: sin alcance" in _texto(caplog)
    assert tarjeta.liberados == [201, 7]  # la llave tras leer su certificado y el proveedor


# --- vía 2: por lector --------------------------------------------------------------


def test_por_lector_encuentra_la_llave_con_nombre_completo(monkeypatch, material_prueba, caplog):
    caplog.set_level("INFO")
    lectores = [_lector("Lector A", dd.SCARD_STATE_EMPTY), _lector("Lector B")]
    nombre = _alcance("Lector B") + "cont-b"
    tarjeta = _TarjetaFalsa(
        monkeypatch,
        material_prueba[1],
        {None: NTE_NOT_FOUND, _alcance("Lector B"): ["cont-b"]},
        lectores=lectores,
        abribles={nombre},
    )
    llaves = dnie_cng.listar_llaves()
    assert [l.nombre for l in llaves] == [nombre]
    assert llaves[0].es_de_firma
    # el lector sin tarjeta no se enumera; el almacén no se consulta
    assert tarjeta.alcances == [None, _alcance("Lector B")]
    assert tarjeta.consultas_almacen == 0
    texto = _texto(caplog)
    assert "halladas por: lector «Lector B»" in texto
    assert "Lector A" in texto and "0x10" in texto  # lectores vistos y su estado
    assert "0x20" in texto


def test_por_lector_respeta_un_nombre_ya_completo(monkeypatch, material_prueba):
    nombre = _alcance("Lector B") + "cont-b"
    _TarjetaFalsa(
        monkeypatch,
        material_prueba[1],
        {None: NTE_NOT_FOUND, _alcance("Lector B"): [nombre]},
        lectores=[_lector("Lector B")],
        abribles={nombre},
    )
    assert [l.nombre for l in dnie_cng.listar_llaves()] == [nombre]


# --- vía 3: almacén MY --------------------------------------------------------------


def test_almacen_my_encuentra_la_llave_si_fallan_las_otras_vias(monkeypatch, material_prueba, caplog):
    caplog.set_level("INFO")
    cert = material_prueba[1]
    nombre = _alcance("Lector B") + "cont-s"
    almacen = [
        dnie_cng._CertDeTarjeta(_cert_sin_uso_de_firma(material_prueba), dnie_cng.PROVEEDOR_TARJETA, "cont-x", 1),
        dnie_cng._CertDeTarjeta(cert, "Microsoft Software Key Storage Provider", "cont-sw", 0),
        dnie_cng._CertDeTarjeta(cert, "Microsoft Base Smart Card Crypto Provider", "cont-s", 2),
    ]
    tarjeta = _TarjetaFalsa(
        monkeypatch,
        cert,
        # el lector falla al enumerar con otro código: se registra y se sigue
        {None: NTE_NOT_FOUND, _alcance("Lector B"): 0x80100069},
        lectores=[_lector("Lector B")],
        almacen=almacen,
        abribles={nombre},
    )
    llaves = dnie_cng.listar_llaves()
    assert [(l.nombre, l.tipo_legado) for l in llaves] == [(nombre, 2)]
    assert llaves[0].cert.dump() == cert.dump()
    # solo el certificado de firma con proveedor de tarjeta se intenta abrir, en silencio
    assert [a for a in tarjeta.aperturas if "cont-" in a[0]] == [(nombre, dnie_cng._NCRYPT_SILENT_FLAG)]
    assert tarjeta.liberados == [201, 7]
    texto = _texto(caplog)
    assert "halladas por: almacén MY" in texto
    assert "PRUEBA FIRMANTE FIR 12345678" in texto and "Microsoft Base Smart Card Crypto Provider" in texto
    assert "cont-s" in texto and "0x80100069" in texto
    assert "Microsoft Software Key Storage Provider" in texto  # visto y omitido


def test_almacen_my_prueba_el_contenedor_sin_lector(monkeypatch, material_prueba):
    """Sin lectores con tarjeta, o si el nombre completo falla, se prueba el contenedor tal cual."""
    cert = material_prueba[1]
    tarjeta = _TarjetaFalsa(
        monkeypatch,
        cert,
        {None: NTE_NOT_FOUND},
        lectores=[_lector("Lector B")],
        almacen=[dnie_cng._CertDeTarjeta(cert, dnie_cng.PROVEEDOR_TARJETA, "cont-s", 0xFFFFFFFF)],
        abribles={"cont-s"},
    )
    llaves = dnie_cng.listar_llaves()
    assert [(l.nombre, l.tipo_legado) for l in llaves] == [("cont-s", 0)]  # CERT_NCRYPT_KEY_SPEC -> 0
    assert [a[0] for a in tarjeta.aperturas] == [_alcance("Lector B") + "cont-s", "cont-s"]


# --- todas las vías fallan ----------------------------------------------------------


def test_si_todo_falla_el_error_conserva_nte_not_found(monkeypatch, material_prueba, caplog):
    caplog.set_level("INFO")
    tarjeta = _TarjetaFalsa(
        monkeypatch,
        material_prueba[1],
        {None: NTE_NOT_FOUND},
        lectores=[_lector("Lector B")],
        almacen=[dnie_cng._CertDeTarjeta(material_prueba[1], dnie_cng.PROVEEDOR_TARJETA, "cont-s", 2)],
    )
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        dnie_cng.listar_llaves()
    assert error.value.codigo == NTE_NOT_FOUND
    assert str(error.value) == "No se encontró ninguna llave con certificado. ¿Está el DNIe insertado?"
    assert tarjeta.alcances == [None, _alcance("Lector B")]
    assert tarjeta.consultas_almacen == 1
    assert tarjeta.liberados == [7]
    assert "ninguna vía" in _texto(caplog)


def test_si_todo_falla_se_reintenta_y_se_diagnostica(monkeypatch, material_prueba):
    tarjeta = _TarjetaFalsa(
        monkeypatch, material_prueba[1], {None: NTE_NOT_FOUND}, lectores=[_lector("Lector B")], almacen=[]
    )
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        dnie_cng.elegir_llave_de_firma()
    assert tarjeta.consultas_almacen == 4  # 1 intento + 3 reintentos, todas las vías en cada uno
    assert error.value.codigo == NTE_NOT_FOUND and error.value.diagnostico == "sin_certificados"
    assert str(error.value).startswith("El DNIe se lee correctamente pero no tiene certificados")


def test_si_winscard_y_el_almacen_fallan_se_informa_el_error_de_siempre(monkeypatch, material_prueba):
    tarjeta = _TarjetaFalsa(monkeypatch, material_prueba[1], {None: NTE_NOT_FOUND})

    def _falla():
        raise OSError("sin acceso")

    monkeypatch.setattr(dnie_cng, "_recolectar_hechos", _falla)
    monkeypatch.setattr(dnie_cng, "_certificados_de_tarjeta_en_almacen", _falla)
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        dnie_cng.listar_llaves()
    assert error.value.codigo == NTE_NOT_FOUND
    assert tarjeta.liberados == [7]


# --- firma con una llave hallada en el almacén -----------------------------------


def test_sesion_con_llave_del_almacen_asigna_el_pin_y_firma_en_silencio(monkeypatch, material_prueba, caplog):
    cert = material_prueba[1]
    nombre = _alcance("Lector B") + "cont-s"
    tarjeta = _TarjetaFalsa(
        monkeypatch,
        cert,
        {None: NTE_NOT_FOUND},
        lectores=[_lector("Lector B")],
        almacen=[dnie_cng._CertDeTarjeta(cert, "Microsoft Base Smart Card Crypto Provider", "cont-s", 2)],
        abribles={nombre},
    )
    llave = dnie_cng.elegir_llave_de_firma()
    assert llave.nombre == nombre

    llamadas = []

    def _set_property(handle, propiedad, valor, tam, flags):
        if propiedad == "SmartCardPin":
            llamadas.append(("pin", int(handle.value), ctypes.string_at(ctypes.addressof(valor._obj), tam)))
        return 0

    def _sign_hash(handle, relleno, resumen, largo, salida, tam, p_tam, flags):
        if salida is None:
            p_tam._obj.value = 4
            return 0
        llamadas.append(("firma", int(handle.value), flags))
        ctypes.memmove(salida, b"FIRM", 4)
        return 0

    monkeypatch.setattr(dnie_cng, "_SetProperty", _set_property)
    monkeypatch.setattr(dnie_cng, "_SignHash", _sign_hash)
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    tarjeta.aperturas.clear()
    tarjeta.liberados.clear()
    with dnie_cng.SesionFirma(llave, pin=PIN) as firmante:
        assert firmante._firmar_resumen(b"\x00" * 32, "sha256") == b"FIRM"

    # se abre por su nombre completo, sin el flag silencioso (Windows puede pedir lo que falte)
    assert tarjeta.aperturas == [(nombre, 0)]
    handle = 201
    silencioso = dnie_cng._BCRYPT_PAD_PKCS1 | dnie_cng._NCRYPT_SILENT_FLAG
    assert llamadas == [("pin", handle, PIN.encode("utf-16-le") + b"\x00\x00"), ("firma", handle, silencioso)]
    assert sorted(tarjeta.liberados) == [7, handle]
    assert PIN not in caplog.text


def test_lectura_real_del_almacen_nunca_lanza():
    """Solo lectura del almacén MY real: devuelve una lista (quizá vacía)."""
    assert isinstance(dnie_cng._certificados_de_tarjeta_en_almacen(), list)
