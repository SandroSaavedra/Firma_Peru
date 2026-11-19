"""
Diagnóstico del DNIe (`diagnostico_dnie.py`): de hechos simples (lectores,
estado de la tarjeta, minidriver, llaves, certificados) a un mensaje claro, y
reintentos de los errores pasajeros de la tarjeta. Sin tarjeta ni PIN reales.
"""
import datetime as dt

import pytest

import app
import diagnostico_dnie as dd
import dnie_cng

AHORA = dt.datetime(2026, 11, 19, 12, 0, tzinfo=dt.timezone.utc)
PRESENTE = dd.SCARD_STATE_PRESENT
VIGENTE = dd.Certificado(es_de_firma=True, no_despues=AHORA + dt.timedelta(days=30))
VENCIDO = dd.Certificado(es_de_firma=True, no_despues=dt.datetime(2025, 3, 7, tzinfo=dt.timezone.utc))


def _lector(estado=PRESENTE, tarjeta="Tarjeta DNIe", nombre="Lector 0"):
    return dd.Lector(nombre=nombre, estado=estado, atr=b"\x3b\x7f", tarjeta=tarjeta)


def _codigo(**hechos):
    return dd.diagnosticar(ahora=AHORA, **hechos)[0]


# --- diagnosticar: cada caso --------------------------------------------------


@pytest.mark.parametrize("error", [0x8010001D, 0x8010001E])
def test_sin_servicio(error):
    codigo, mensaje = dd.diagnosticar(error_lectores=error, estado_llaves=0x80090011, ahora=AHORA)
    assert codigo == "sin_servicio"
    assert mensaje == "El servicio de tarjetas inteligentes de Windows no está activo."


def test_sin_lector_por_codigo_o_lista_vacia():
    assert _codigo(error_lectores=0x8010002E) == "sin_lector"
    codigo, mensaje = dd.diagnosticar(lectores=[], ahora=AHORA)
    assert codigo == "sin_lector"
    assert mensaje == "No se detecta ningún lector de tarjetas. Conéctelo e intente de nuevo."


def test_sin_tarjeta():
    codigo, mensaje = dd.diagnosticar(lectores=[_lector(estado=dd.SCARD_STATE_EMPTY)], ahora=AHORA)
    assert codigo == "sin_tarjeta"
    assert mensaje == "El lector está conectado pero no tiene el DNIe insertado."


def test_tarjeta_muda():
    codigo, mensaje = dd.diagnosticar(lectores=[_lector(estado=PRESENTE | dd.SCARD_STATE_MUTE)], ahora=AHORA)
    assert codigo == "tarjeta_muda"
    assert mensaje == "El DNIe no responde. Retírelo y vuelva a insertarlo."


def test_en_uso_exclusivo_por_estado_o_por_codigo():
    exclusivo = _lector(estado=PRESENTE | dd.SCARD_STATE_EXCLUSIVE | dd.SCARD_STATE_INUSE)
    codigo, mensaje = dd.diagnosticar(lectores=[exclusivo], estado_llaves=0x8010000B, ahora=AHORA)
    assert codigo == "en_uso_exclusivo"
    assert mensaje == "Otro programa está usando el DNIe. Ciérrelo e intente de nuevo."
    assert _codigo(lectores=[_lector()], estado_llaves=0x8010000B) == "en_uso_exclusivo"
    # En uso compartido (INUSE sin EXCLUSIVE) no es un problema.
    assert _codigo(lectores=[_lector(estado=PRESENTE | dd.SCARD_STATE_INUSE)], llaves=2, certificados=[VIGENTE]) == "ok"


def test_sin_minidriver():
    codigo, mensaje = dd.diagnosticar(lectores=[_lector(tarjeta="")], estado_llaves=0x80090011, ahora=AHORA)
    assert codigo == "sin_minidriver"
    assert mensaje == "El DNIe está insertado pero falta el software del DNIe de RENIEC en esta PC."


@pytest.mark.parametrize("hechos", [{"estado_llaves": 0x80090011}, {"llaves": 0}])
def test_sin_certificados(hechos):
    codigo, mensaje = dd.diagnosticar(lectores=[_lector()], ahora=AHORA, **hechos)
    assert codigo == "sin_certificados"
    assert mensaje == (
        "El DNIe se lee correctamente pero no tiene certificados digitales activos. "
        "Debe activarlos o renovarlos en RENIEC."
    )


def test_certificado_vencido_con_fecha():
    codigo, mensaje = dd.diagnosticar(lectores=[_lector()], llaves=2, certificados=[VENCIDO], ahora=AHORA)
    assert codigo == "certificado_vencido"
    assert mensaje == "El certificado de firma del DNIe venció el 07/03/2025. Debe renovarlo en RENIEC."
    # Sin datos del lector (solo el certificado) también se detecta.
    assert _codigo(certificados=[VENCIDO]) == "certificado_vencido"
    # Si hay uno vigente, no está vencido.
    assert _codigo(certificados=[VENCIDO, VIGENTE]) == "ok"


def test_ok():
    assert dd.diagnosticar(lectores=[_lector()], llaves=2, certificados=[VIGENTE], ahora=AHORA)[0] == "ok"


# --- diagnosticar: precedencia y hechos incompletos -------------------------------


def test_precedencia_lector_antes_que_tarjeta_y_minidriver_antes_que_llaves():
    assert _codigo(error_lectores=0x8010001D, lectores=[_lector()], estado_llaves=0x80090011) == "sin_servicio"
    muda_sin_driver = _lector(estado=PRESENTE | dd.SCARD_STATE_MUTE, tarjeta="")
    assert _codigo(lectores=[muda_sin_driver]) == "tarjeta_muda"
    assert _codigo(lectores=[_lector(estado=PRESENTE | dd.SCARD_STATE_EXCLUSIVE, tarjeta="")]) == "sin_minidriver"
    assert _codigo(lectores=[_lector()], estado_llaves=0x80090011, certificados=[VENCIDO]) == "sin_certificados"


def test_con_varios_lectores_se_usa_el_que_tiene_la_tarjeta():
    lectores = [_lector(estado=dd.SCARD_STATE_EMPTY, nombre="Vacío"), _lector(nombre="Con DNIe")]
    assert _codigo(lectores=lectores, estado_llaves=0x80090011) == "sin_certificados"
    assert _codigo(lectores=lectores, llaves=2, certificados=[VIGENTE]) == "ok"


def test_hechos_desconocidos_no_inventan_un_diagnostico():
    # WinSCard falló de forma inesperada: no se afirma nada sobre la tarjeta.
    assert dd.diagnosticar(error_lectores=0x80100001, estado_llaves=0x80090011, ahora=AHORA) == ("desconocido", None)
    # Lector y minidriver bien, pero un error de llaves que no se reconoce.
    assert dd.diagnosticar(lectores=[_lector()], estado_llaves=0x80100069, ahora=AHORA) == ("desconocido", None)
    # Nombre de tarjeta desconocido (la consulta falló): no se afirma que falte el minidriver.
    assert _codigo(lectores=[_lector(tarjeta=None)], estado_llaves=0x80090011) == "sin_certificados"


def test_multicadena_de_winscard():
    assert dd._multicadena("Lector A\0Lector B\0\0") == ["Lector A", "Lector B"]
    assert dd._multicadena("\0") == [] and dd._multicadena("") == []


def test_recolectar_hechos_nunca_lanza():
    # Solo lectura: lista lectores y su estado, no se conecta a la tarjeta ni pide PIN.
    hechos = dd.recolectar_hechos()
    assert set(hechos) == {"error_lectores", "lectores"}
    assert hechos["lectores"] is None or all(isinstance(l, dd.Lector) for l in hechos["lectores"])


# --- reintentos ---------------------------------------------------------------------


class _Lister:
    """Operación falsa: lanza los códigos indicados en orden y luego devuelve 'ok'."""

    def __init__(self, *codigos):
        self.codigos = list(codigos)
        self.llamadas = 0

    def __call__(self):
        self.llamadas += 1
        if self.codigos:
            raise dnie_cng.ErrorCNG(f"falla 0x{self.codigos[0]:08X}", codigo=self.codigos.pop(0))
        return "ok"


@pytest.mark.parametrize("codigo", [0x8010000B, 0x80100068, 0x80100069, 0x80090011])
def test_reintenta_errores_pasajeros_y_registra_cada_intento(codigo, caplog):
    esperas = []
    lister = _Lister(codigo, codigo)
    assert dd.con_reintentos(lister, dormir=esperas.append, descripcion="Listar llaves") == "ok"
    assert lister.llamadas == 3 and esperas == [1.0, 1.0]
    avisos = [r.getMessage() for r in caplog.records if r.name == "firmador.dnie"]
    assert sum("Listar llaves: intento" in m for m in avisos) == 2 and f"0x{codigo:08X}" in avisos[0]


def test_reintenta_como_maximo_3_veces():
    esperas = []
    lister = _Lister(*[0x80090011] * 10)
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        dd.con_reintentos(lister, dormir=esperas.append)
    assert error.value.codigo == 0x80090011
    assert lister.llamadas == 4 and len(esperas) == 3


@pytest.mark.parametrize("codigo", [0x8010006B, 0x8010006C, 0x8010006E, 0x800704C7, 0x8010000C, None])
def test_nunca_reintenta_pin_cancelacion_ni_otros(codigo):
    esperas = []
    lister = _Lister(codigo) if codigo else _Lister()
    if codigo is None:
        lister = lambda: (_ for _ in ()).throw(dnie_cng.ErrorCNG("sin código"))  # noqa: E731
    with pytest.raises(dnie_cng.ErrorCNG):
        dd.con_reintentos(lister, dormir=esperas.append)
    assert esperas == []
    assert not (dd.REINTENTABLES & dd.NUNCA_REINTENTAR)


# --- integración en dnie_cng.elegir_llave_de_firma ---------------------------------


@pytest.fixture
def tarjeta_sin_certificados(monkeypatch):
    llamadas = []

    def _listar():
        llamadas.append(1)
        raise dnie_cng.ErrorCNG(
            "No se encontró ninguna llave con certificado. ¿Está el DNIe insertado?", codigo=0x80090011
        )

    monkeypatch.setattr(dnie_cng, "listar_llaves", _listar)
    monkeypatch.setattr(dnie_cng, "_dormir", lambda s: None)
    monkeypatch.setattr(dnie_cng, "_recolectar_hechos", lambda: {"error_lectores": None, "lectores": [_lector()]})
    return llamadas


def test_elegir_llave_reintenta_y_da_el_diagnostico(tarjeta_sin_certificados, caplog):
    caplog.set_level("INFO")
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        dnie_cng.elegir_llave_de_firma()
    assert len(tarjeta_sin_certificados) == 4
    assert error.value.diagnostico == "sin_certificados" and error.value.codigo == 0x80090011
    assert str(error.value).startswith("El DNIe se lee correctamente pero no tiene certificados")
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "Diagnóstico del DNIe: sin_certificados" in texto and "Lector 0" in texto and "0x80090011" in texto


def test_si_el_diagnostico_falla_se_conserva_el_error_original(tarjeta_sin_certificados, monkeypatch, caplog):
    def _falla():
        raise OSError("winscard no disponible")

    monkeypatch.setattr(dnie_cng, "_recolectar_hechos", _falla)
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        dnie_cng.elegir_llave_de_firma()
    assert str(error.value) == "No se encontró ninguna llave con certificado. ¿Está el DNIe insertado?"
    assert getattr(error.value, "diagnostico", None) is None
    assert "No se pudo diagnosticar el DNIe" in caplog.text


def test_diagnostico_desconocido_conserva_el_error_original(tarjeta_sin_certificados, monkeypatch):
    monkeypatch.setattr(dnie_cng, "_recolectar_hechos", lambda: {"error_lectores": 0x80100001, "lectores": None})
    with pytest.raises(dnie_cng.ErrorCNG) as error:
        dnie_cng.elegir_llave_de_firma()
    assert str(error.value).startswith("No se encontró ninguna llave")


def test_la_tarjeta_de_estado_y_los_dialogos_muestran_el_diagnostico(tarjeta_sin_certificados, monkeypatch):
    monkeypatch.setattr(app, "elegir_llave_de_firma", dnie_cng.elegir_llave_de_firma)
    r = app.Api().estado_dnie()
    assert r["ok"] is False
    assert r["mensaje"].startswith("El DNIe se lee correctamente pero no tiene certificados digitales activos.")
    assert r["codigo"] == "0x80090011"
    error = dnie_cng.ErrorCNG("El DNIe no responde.", codigo=0x80100069, diagnostico="tarjeta_muda")
    assert app.mensaje_de_error(error) == "El DNIe no responde."


def test_estado_dnie_avisa_del_certificado_vencido(monkeypatch, material_prueba):
    llave = dnie_cng.LlaveTarjeta(nombre="llave-prueba", tipo_legado=0, cert=material_prueba[1])
    monkeypatch.setattr(app, "elegir_llave_de_firma", lambda filtro=None: llave)
    monkeypatch.setattr(app, "_ahora", lambda: AHORA + dt.timedelta(days=4000))
    r = app.Api().estado_dnie()
    assert r["ok"] is False and r["mensaje"].startswith("El certificado de firma del DNIe venció el ")
