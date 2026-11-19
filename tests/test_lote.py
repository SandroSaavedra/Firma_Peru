"""
Cola de documentos y firma por lotes (`app.Api`), sin ventana ni DNIe.

La tarjeta se simula al nivel de los envoltorios de ncrypt (`_abrir_proveedor`,
`_abrir_llave`, `_FreeObject`, `_SetProperty`) y de `_firmar_con_handle`, que
firma con la llave de prueba. Así se comprueba que el lote abre el proveedor y
la llave UNA sola vez y que las firmas resultantes son válidas.
"""
import base64
import json
import re
import shutil
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import padding, utils
from pyhanko.pdf_utils.reader import PdfFileReader

import app
import dnie_cng
import dnie_pkcs11
from conftest import _HASHES, A4, construir_pdf
from test_dnie_pkcs11 import ModuloFalso, Token
from validacion import validar_pdf

CAJA = {"x": 0.5, "y": 0.05, "ancho": 0.4, "alto": 0.1}


class Cng:
    """Estado de la tarjeta simulada."""

    def __init__(self):
        self.proveedores = 0
        self.llaves = 0
        self.liberados = []
        self.ventanas = []
        self.firmas = []  # handle usado en cada intento de firma
        self.silenciosas = []  # si cada intento fue sin el cuadro de Windows (PIN provisto)
        self.fallos = {}  # n.º de intento (1, 2, ...) -> excepción a lanzar
        self.despues = {}  # n.º de intento -> función a ejecutar tras firmar


@pytest.fixture(autouse=True)
def sin_pkcs11(monkeypatch):
    """Sin el módulo PKCS#11 de IDEMIA: las pruebas nunca cargan la DLL real."""
    monkeypatch.setattr(dnie_pkcs11, "ruta_modulo", lambda: None)


@pytest.fixture
def cng(monkeypatch, material_prueba):
    llave_privada, cert = material_prueba
    estado = Cng()

    def _abrir_proveedor():
        estado.proveedores += 1
        return 100 + estado.proveedores

    def _abrir_llave(proveedor, llave, flags=0):
        estado.llaves += 1
        return 200 + estado.llaves

    def _set_property(handle, nombre, valor, tam, flags):
        estado.ventanas.append((int(handle), nombre))
        return 0

    def _firmar(firmante, handle, resumen, algoritmo, silencioso=False):
        estado.firmas.append(int(handle))
        estado.silenciosas.append(silencioso)
        intento = len(estado.firmas)
        if intento in estado.fallos:
            raise estado.fallos[intento]
        firma = llave_privada.sign(resumen, padding.PKCS1v15(), utils.Prehashed(_HASHES[algoritmo]()))
        if intento in estado.despues:
            estado.despues[intento]()
        return firma

    monkeypatch.setattr(dnie_cng, "_abrir_proveedor", _abrir_proveedor)
    monkeypatch.setattr(dnie_cng, "_abrir_llave", _abrir_llave)
    monkeypatch.setattr(dnie_cng, "_FreeObject", lambda h: estado.liberados.append(int(h)) or 0)
    monkeypatch.setattr(dnie_cng, "_SetProperty", _set_property)
    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    monkeypatch.setattr(dnie_cng.FirmanteCNG, "_firmar_con_handle", _firmar)
    llave = dnie_cng.LlaveTarjeta(nombre="llave-prueba", tipo_legado=0, cert=cert)
    monkeypatch.setattr(app, "elegir_llave_de_firma", lambda filtro=None: llave)
    return estado


class Ventana:
    """Ventana falsa: responde los diálogos y guarda lo que se envía a la interfaz."""

    native = None

    def __init__(self, respuesta=None):
        self.respuesta = respuesta
        self.dialogos = []
        self.js = []

    def create_file_dialog(self, tipo, **opciones):
        self.dialogos.append((tipo, opciones))
        return self.respuesta

    def evaluate_js(self, codigo):
        self.js.append(codigo)

    def eventos(self):
        datos = []
        for codigo in self.js:
            m = re.search(r"progresoLote\((.*)\)\s*$", codigo, re.S)
            if m:
                datos.append(json.loads(m.group(1)))
        return datos


@pytest.fixture
def api(cng, monkeypatch):
    a = app.Api()
    a._asignar_ventana(Ventana())
    monkeypatch.setattr(a, "_hwnd", lambda: 0x1234)
    return a


def _pdfs(carpeta: Path, *nombres, paginas=3) -> list[Path]:
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas = []
    for nombre in nombres:
        ruta = carpeta / nombre
        ruta.write_bytes(construir_pdf([{"media_box": A4}] * paginas))
        rutas.append(ruta)
    return rutas


def _subir(api, nombre, datos, abrir=False):
    inicio = api.abrir_bytes_inicio(nombre, len(datos))
    assert inicio["ok"], inicio
    assert api.abrir_bytes_fragmento(inicio["token"], base64.b64encode(datos).decode("ascii"))["ok"]
    return api.abrir_bytes_fin(inicio["token"], abrir)


def _estados(api):
    return [(i["nombre"], i["estado"]) for i in api.obtener_cola()["cola"]]


def _campo_de_firma(ruta) -> tuple[int, tuple[float, ...]]:
    """(página, /Rect) del campo de firma más reciente del PDF."""
    with open(ruta, "rb") as f:
        reader = PdfFileReader(f, strict=False)
        nombre = reader.embedded_signatures[-1].field_name
        total = int(reader.root["/Pages"]["/Count"])
        for indice in range(total):
            pagina = reader.find_page_for_modification(indice)[0].get_object()
            for anot in pagina.get("/Annots", []):
                anot = anot.get_object()
                if anot.get("/T") == nombre:
                    return indice, tuple(round(float(v), 2) for v in anot["/Rect"])
    raise AssertionError("sin campo de firma")


# --- cola -------------------------------------------------------------------


def test_cola_agrega_ignora_duplicados_y_marca_invalidos(api, tmp_path):
    a, b = _pdfs(tmp_path / "docs", "a.pdf", "b.pdf")
    falso = tmp_path / "docs" / "falso.pdf"
    falso.write_text("no soy un PDF")

    r = api.agregar_rutas([str(a), str(b), str(a), str(falso)])
    assert r["ok"]
    assert [i["nombre"] for i in r["agregados"]] == ["a.pdf", "b.pdf", "falso.pdf"]
    assert r["duplicados"] == ["a.pdf"]
    assert _estados(api) == [("a.pdf", "pendiente"), ("b.pdf", "pendiente"), ("falso.pdf", "error")]
    invalido = r["cola"][2]
    assert invalido["valido"] is False and "PDF" in invalido["mensaje"]
    assert r["cola"][0]["paginas"] == 3 and r["cola"][0]["firmas"] == 0

    # La misma ruta escrita distinto (mayúsculas) también es un duplicado.
    r2 = api.agregar_rutas([str(a).upper()])
    assert r2["agregados"] == [] and r2["duplicados"] == ["a.pdf"]

    assert api.quitar_item(r["cola"][1]["id"])["ok"]
    assert _estados(api) == [("a.pdf", "pendiente"), ("falso.pdf", "error")]
    assert api.vaciar_cola()["cola"] == []


def test_agregar_pdfs_usa_dialogo_de_seleccion_multiple(api, tmp_path):
    a, b = _pdfs(tmp_path, "a.pdf", "b.pdf")
    api._ventana.respuesta = (str(a), str(b))
    r = api.agregar_pdfs()
    assert r["ok"] and len(r["cola"]) == 2
    tipo, opciones = api._ventana.dialogos[0]
    assert tipo == app.webview.FileDialog.OPEN and opciones["allow_multiple"] is True
    api._ventana.respuesta = None
    assert api.agregar_pdfs() == {"ok": False, "cancelado": True}


def test_abrir_un_pdf_es_una_cola_de_uno_y_abrir_item_carga_otro(api, tmp_path):
    a, b = _pdfs(tmp_path, "a.pdf", "b.pdf", paginas=2)
    info = api.abrir_ruta(str(a))
    assert info["ok"] and info["id"] and len(api.obtener_cola()["cola"]) == 1
    otra = api.abrir_ruta(str(a))
    assert otra["ok"] and otra["duplicado"] and otra["id"] == info["id"]

    agregado = api.agregar_rutas([str(b)])["agregados"][0]
    r = api.abrir_item(agregado["id"])
    assert r["ok"] and r["nombre"] == "b.pdf" and r["paginas"] == 2
    assert api.leer_fragmento(info["token"], 0)["ok"] is False  # solo un documento cargado
    assert api.leer_fragmento(r["token"], 0)["ok"]


def test_soltar_varios_envia_todas_las_rutas_pdf(cng):
    a = app.Api()
    ventana = Ventana()
    a._asignar_ventana(ventana)
    archivos = [{"pywebviewFullPath": r"C:\x\uno.pdf"}, {"pywebviewFullPath": r"C:\x\nota.txt"}, {"pywebviewFullPath": r"C:\x\dos.PDF"}]
    a._al_soltar({"dataTransfer": {"files": archivos}})
    assert ventana.js and json.loads(re.search(r"alSoltar\((.*)\)", ventana.js[0]).group(1)) == [
        r"C:\x\uno.pdf",
        r"C:\x\dos.PDF",
    ]


# --- cerrar documento ---------------------------------------------------------------


def test_cerrar_documento_libera_el_temporal_y_lo_quita_de_la_cola(api, tmp_path):
    datos = construir_pdf([{"media_box": A4}])
    info = _subir(api, "Arrastrado.pdf", datos, abrir=True)
    assert info["ok"] and info["ruta"] is None
    temporal = Path(api._pdf["archivo"])
    assert temporal.is_file()

    r = api.cerrar_documento()
    assert r["ok"] and r["cola"] == []
    assert not temporal.exists()
    assert api._pdf is None
    assert api.leer_fragmento(info["token"], 0)["ok"] is False


def test_cerrar_o_quitar_durante_la_firma_no_se_permite(api, tmp_path):
    (a,) = _pdfs(tmp_path, "a.pdf")
    info = api.abrir_ruta(str(a))
    api._firmando.acquire()
    try:
        assert api.cerrar_documento()["ok"] is False
        assert api.quitar_item(info["id"])["ok"] is False
        assert api.vaciar_cola()["ok"] is False
    finally:
        api._firmando.release()
    assert len(api.obtener_cola()["cola"]) == 1


# --- firma por lotes ----------------------------------------------------------------


def test_lote_de_3_con_una_sola_sesion_cng(api, cng, tmp_path):
    rutas = _pdfs(tmp_path / "docs", "uno.pdf", "dos.pdf", "tres.pdf")
    (tmp_path / "docs" / "uno_firmado.pdf").write_bytes(b"existente")  # no se sobrescribe
    api.agregar_rutas([str(r) for r in rutas])

    r = api.firmar_lote({"caja": CAJA, "paginas": "todas", "tipo": "firma"})
    assert r["ok"], r
    assert [Path(f["salida"]).name for f in r["firmados"]] == ["uno_firmado (2).pdf", "dos_firmado.pdf", "tres_firmado.pdf"]
    assert all(Path(f["salida"]).parent == tmp_path / "docs" for f in r["firmados"])
    assert (tmp_path / "docs" / "uno_firmado.pdf").read_bytes() == b"existente"
    assert r["errores"] == [] and r["omitidos"] == [] and r["pendientes"] == 0 and r["detenido"] is None
    assert _estados(api) == [("uno.pdf", "firmado"), ("dos.pdf", "firmado"), ("tres.pdf", "firmado")]

    # UNA apertura de proveedor y de llave para todo el lote, la misma llave en las 3 firmas.
    assert (cng.proveedores, cng.llaves) == (1, 1)
    assert cng.firmas == [201, 201, 201]
    assert cng.ventanas == [(201, "HWND Handle")]
    assert sorted(cng.liberados) == [101, 201]
    assert r["firmas_en_sesion"] == 3
    assert api._ventana.dialogos == []  # junto al original: ningún diálogo

    for f in r["firmados"]:
        v = validar_pdf(f["salida"], permitir_red=False)
        assert len(v) == 1 and v[0]["integridad"] == "intacta"
        assert api._permitida(f["salida"]) is not None  # «Abrir» y «Mostrar en carpeta» funcionan


def test_un_archivo_con_error_se_marca_y_el_lote_sigue(api, cng, tmp_path):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", "tres.pdf")
    api.agregar_rutas([str(r) for r in rutas])
    rutas[1].unlink()  # el original desapareció después de agregarlo

    r = api.firmar_lote({"caja": CAJA})
    assert r["ok"]
    assert _estados(api) == [("uno.pdf", "firmado"), ("dos.pdf", "error"), ("tres.pdf", "firmado")]
    assert [e["nombre"] for e in r["errores"]] == ["dos.pdf"] and r["errores"][0]["mensaje"]
    assert len(r["firmados"]) == 2 and r["detenido"] is None


def test_error_cng_no_relacionado_con_el_pin_sigue_con_el_siguiente(api, cng, tmp_path):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", "tres.pdf")
    api.agregar_rutas([str(r) for r in rutas])
    cng.fallos[2] = dnie_cng.ErrorCNG("NCryptSignHash falló (0x80090020) Error interno")

    r = api.firmar_lote({"caja": CAJA})
    assert _estados(api) == [("uno.pdf", "firmado"), ("dos.pdf", "error"), ("tres.pdf", "firmado")]
    assert r["errores"][0]["mensaje"].startswith("Error con el DNIe")
    assert len(cng.firmas) == 3


def test_pin_cancelado_detiene_el_lote_y_deja_pendientes(api, cng, tmp_path):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", "tres.pdf")
    api.agregar_rutas([str(r) for r in rutas])
    cng.fallos[2] = dnie_cng.ErrorCNG("NCryptSignHash falló (0x8010006E) cancelado por el usuario")

    r = api.firmar_lote({"caja": CAJA})
    assert r["ok"] and r["detenido"] == "pin_cancelado"
    assert _estados(api) == [("uno.pdf", "firmado"), ("dos.pdf", "pendiente"), ("tres.pdf", "pendiente")]
    assert r["pendientes"] == 2 and r["errores"] == []
    assert r["mensaje"] == app._MENSAJES_CNG[0x8010006E]
    assert len(cng.firmas) == 2  # no se intenta el tercero
    assert not (tmp_path / "dos_firmado.pdf").exists()
    assert sorted(cng.liberados) == [101, 201]


@pytest.mark.parametrize("codigo", [0x8010006B, 0x8010006C])
def test_pin_incorrecto_o_bloqueado_detiene_el_lote(api, cng, tmp_path, codigo):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", "tres.pdf")
    api.agregar_rutas([str(r) for r in rutas])
    cng.fallos[2] = dnie_cng.ErrorCNG(f"NCryptSignHash falló (0x{codigo:08X}) PIN")

    r = api.firmar_lote({"caja": CAJA})
    assert r["detenido"] == "tarjeta"
    assert r["mensaje"] == app._MENSAJES_CNG[codigo]
    assert _estados(api) == [("uno.pdf", "firmado"), ("dos.pdf", "error"), ("tres.pdf", "pendiente")]
    assert len(cng.firmas) == 2  # no se repite el intento: la tarjeta podría bloquearse


def test_modo_carpeta_pide_la_carpeta_una_vez(api, cng, tmp_path):
    rutas = _pdfs(tmp_path / "docs", "uno.pdf", "dos.pdf", "tres.pdf")
    destino = tmp_path / "salidas"
    destino.mkdir()
    api.agregar_rutas([str(r) for r in rutas])
    api._ventana.respuesta = (str(destino),)

    r = api.firmar_lote({"caja": CAJA, "en_carpeta": True})
    assert r["ok"] and r["carpeta"] == str(destino)
    assert len(api._ventana.dialogos) == 1
    assert api._ventana.dialogos[0][0] == app.webview.FileDialog.FOLDER
    assert sorted(p.name for p in destino.iterdir()) == ["dos_firmado.pdf", "tres_firmado.pdf", "uno_firmado.pdf"]


def test_documentos_por_bytes_piden_una_sola_carpeta(api, cng, tmp_path):
    (con_ruta,) = _pdfs(tmp_path / "docs", "con_ruta.pdf")
    datos = construir_pdf([{"media_box": A4}])
    destino = tmp_path / "salidas"
    destino.mkdir()
    api.agregar_rutas([str(con_ruta)])
    assert _subir(api, "Arrastrado 1.pdf", datos)["ok"]
    assert _subir(api, "Arrastrado 2.pdf", construir_pdf([{"media_box": A4}] * 2))["ok"]

    api._ventana.respuesta = None  # cancelar la carpeta no firma nada
    assert api.firmar_lote({"caja": CAJA}) == {"ok": False, "cancelado": True}
    assert cng.firmas == []

    api._ventana.respuesta = (str(destino),)
    r = api.firmar_lote({"caja": CAJA})
    assert r["ok"] and len(r["firmados"]) == 3
    assert len(api._ventana.dialogos) == 2  # uno cancelado + uno aceptado: nunca uno por archivo
    assert sorted(p.name for p in destino.iterdir()) == ["Arrastrado 1_firmado.pdf", "Arrastrado 2_firmado.pdf"]
    assert (tmp_path / "docs" / "con_ruta_firmado.pdf").is_file()  # el de ruta, junto a su original


def test_eventos_de_progreso_en_orden(api, cng, tmp_path):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf")
    api.agregar_rutas([str(r) for r in rutas])
    cng.fallos[2] = dnie_cng.ErrorCNG("NCryptSignHash falló (0x80090020) Error interno")
    api.firmar_lote({"caja": CAJA})

    eventos = api._ventana.eventos()
    resumen = [(e["evento"], e.get("indice"), e.get("nombre"), e.get("estado")) for e in eventos]
    assert resumen == [
        ("inicio", None, None, None),
        ("archivo", 1, "uno.pdf", "firmando"),
        ("archivo", 1, "uno.pdf", "firmado"),
        ("archivo", 2, "dos.pdf", "firmando"),
        ("archivo", 2, "dos.pdf", "error"),
        ("fin", None, None, None),
    ]
    assert all(e.get("total") == 2 for e in eventos[:-1])
    assert eventos[4]["mensaje"] and eventos[-1]["resumen"]["firmados"][0]["nombre"] == "uno.pdf"


def test_cancelar_entre_archivos_termina_el_actual(api, cng, tmp_path):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", "tres.pdf")
    api.agregar_rutas([str(r) for r in rutas])
    cng.despues[1] = api.cancelar_lote  # el usuario pulsa «Cancelar» mientras se firma el primero

    r = api.firmar_lote({"caja": CAJA})
    assert r["ok"] and r["detenido"] == "cancelado"
    assert _estados(api) == [("uno.pdf", "firmado"), ("dos.pdf", "pendiente"), ("tres.pdf", "pendiente")]
    v = validar_pdf(r["firmados"][0]["salida"], permitir_red=False)
    assert v[0]["integridad"] == "intacta"  # el archivo en curso se terminó de escribir
    assert len(cng.firmas) == 1

    # Un lote nuevo vuelve a empezar (la cancelación no queda pegada).
    r2 = api.firmar_lote({"caja": CAJA})
    assert len(r2["firmados"]) == 2 and r2["detenido"] is None


def test_lote_con_documento_ya_firmado_conserva_la_primera_firma(api, cng, tmp_path):
    uno, dos = _pdfs(tmp_path, "uno.pdf", "dos.pdf")
    api.abrir_ruta(str(uno))
    primera = api.firmar({"caja": CAJA, "paginas": "todas"})
    assert primera["ok"], primera
    assert _estados(api) == [("uno.pdf", "firmado")]
    previo = tmp_path / "previo.pdf"
    shutil.copy(primera["salida"], previo)

    r = api.agregar_rutas([str(previo), str(dos)])
    assert r["agregados"][0]["firmas"] == 1
    lote = api.firmar_lote({"caja": CAJA, "paginas": "todas"})
    assert [Path(f["salida"]).name for f in lote["firmados"]] == ["previo_firmado.pdf", "dos_firmado.pdf"]
    assert lote["firmados"][0]["modo"] == "contenido"  # sellado normal: la primera firma sigue válida

    v = validar_pdf(lote["firmados"][0]["salida"], permitir_red=False)
    assert len(v) == 2
    assert v[0]["integridad"] == "intacta" and v[0]["modificaciones"] == "permitidas"
    assert v[1]["integridad"] == "intacta"


def test_ajuste_por_documento_solo_se_aplica_a_ese(api, cng, tmp_path):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", "tres.pdf")
    cola = api.agregar_rutas([str(r) for r in rutas])["cola"]
    propia = {"x": 0.05, "y": 0.8, "ancho": 0.3, "alto": 0.1}
    ajustes = {
        cola[1]["id"]: {"caja": propia, "pagina_firma": 0},
        cola[2]["id"]: {"pagina_firma": 99},  # se limita a la última página del archivo
    }
    r = api.firmar_lote({"caja": CAJA, "paginas": "ultima", "ajustes": ajustes})
    assert r["ok"] and len(r["firmados"]) == 3

    def rect(caja):
        # Solo se usa la posición: el sello «Firma» mide siempre 160 x 40 pt.
        x, y = round(caja["x"] * 595, 2), round(caja["y"] * 842, 2)
        return x, y, round(x + 160, 2), round(y + 40, 2)

    salidas = [f["salida"] for f in r["firmados"]]
    assert _campo_de_firma(salidas[0]) == (2, rect(CAJA))
    assert _campo_de_firma(salidas[1]) == (0, rect(propia))
    assert _campo_de_firma(salidas[2]) == (2, rect(CAJA))


def _rects_por_pagina(ruta) -> list[tuple[float, ...]]:
    """/Rect del sello de cada página: widget del campo en la página de firma, /Stamp en las demás."""
    with open(ruta, "rb") as f:
        reader = PdfFileReader(f, strict=False)
        rects = []
        for indice in range(int(reader.root["/Pages"]["/Count"])):
            pagina = reader.find_page_for_modification(indice)[0].get_object()
            anots = [a.get_object() for a in pagina["/Annots"]]
            (anot,) = [a for a in anots if a["/Subtype"] in ("/Widget", "/Stamp")]
            rects.append(tuple(round(float(v), 2) for v in anot["/Rect"]))
        return rects


def test_posicion_por_pagina_solo_se_aplica_a_esa_pagina_de_ese_documento(api, cng, tmp_path):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf")
    cola = api.agregar_rutas([str(r) for r in rutas])["cola"]
    propia = {"x": 0.05, "y": 0.8}
    ajustes = {
        cola[1]["id"]: {
            "caja": propia,
            "posiciones_paginas": {"1": {"x": 0.3, "y": 0.4}, "7": {"x": 0.3, "y": 0.4}, "z": None},
        },
    }
    r = api.firmar_lote({"caja": CAJA, "paginas": "todas", "ajustes": ajustes})
    assert r["ok"] and len(r["firmados"]) == 2, r

    def rect(caja):
        x, y = round(caja["x"] * 595, 2), round(caja["y"] * 842, 2)
        return x, y, round(x + 160, 2), round(y + 40, 2)

    salidas = [f["salida"] for f in r["firmados"]]
    assert _rects_por_pagina(salidas[0]) == [rect(CAJA)] * 3
    assert _rects_por_pagina(salidas[1]) == [rect(propia), rect({"x": 0.3, "y": 0.4}), rect(propia)]
    for salida in salidas:
        assert len(validar_pdf(salida, permitir_red=False)) == 1


def test_firmar_mas_documentos_quita_solo_los_firmados(api, cng, tmp_path):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf")
    falso = tmp_path / "falso.pdf"
    falso.write_text("x")
    api.agregar_rutas([str(r) for r in rutas] + [str(falso)])
    api.firmar_lote({"caja": CAJA})
    r = api.vaciar_cola(True)
    assert r["ok"] and [i["nombre"] for i in r["cola"]] == ["falso.pdf"]


def test_lote_sin_pendientes_o_ya_en_curso(api, cng, tmp_path):
    assert api.firmar_lote({})["ok"] is False
    (a,) = _pdfs(tmp_path, "a.pdf")
    api.agregar_rutas([str(a)])
    api._firmando.acquire()
    try:
        assert api.firmar_lote({})["mensaje"] == "Ya hay una firma en curso."
    finally:
        api._firmando.release()


def test_lote_usa_el_logo_inicial_aunque_haya_uno_guardado(api, cng, tmp_path, monkeypatch):
    """El logo no se puede cambiar: un logo.png elegido con versiones anteriores se ignora."""
    from PIL import Image

    azul, verde = (30, 60, 200), (10, 150, 10)
    Image.new("RGB", (300, 100), azul).save(tmp_path / "inicial.png")
    monkeypatch.setattr(app, "LOGO_INICIAL", tmp_path / "inicial.png")
    carpeta = app.carpeta_config()
    carpeta.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (300, 100), verde).save(carpeta / "logo.png")
    (carpeta / "config.json").write_text(json.dumps({"tipo": "firma", "logo": "logo.png"}), encoding="utf-8")

    colores = []
    firmar_pdf = app.firmar_pdf

    def _firmar_pdf(entrada, salida, firmante, titular, opciones):
        with Image.open(opciones.logo) as logo:
            colores.append(logo.convert("RGB").getpixel((0, 0)))
        return firmar_pdf(entrada, salida, firmante, titular, opciones)

    monkeypatch.setattr(app, "firmar_pdf", _firmar_pdf)
    api.agregar_rutas([str(r) for r in _pdfs(tmp_path / "docs", "uno.pdf", "dos.pdf")])
    r = api.firmar_lote({"caja": CAJA, "tipo": "firma"})
    assert r["ok"] and len(r["firmados"]) == 2, r
    assert colores == [azul, azul]


# --- firma real en cada página: un solo PIN para todo el lote -------------------

PIN = "975310"


def test_lote_cada_pagina_usa_un_solo_pin_y_una_sesion(api, cng, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "INTERVALO_AVANCE_PAGINAS", 0)
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", paginas=3)
    api.agregar_rutas([str(r) for r in rutas])
    opciones = {"caja": CAJA, "paginas": "cada_pagina", "pin": PIN}

    r = api.firmar_lote(opciones)
    assert r["ok"] and len(r["firmados"]) == 2 and r["detenido"] is None, r
    assert "pin" not in opciones
    assert (cng.proveedores, cng.llaves) == (1, 1)
    assert len(cng.firmas) == 6  # 3 páginas x 2 documentos, con la misma llave abierta
    assert cng.silenciosas == [True] * 6  # sin el cuadro de Windows
    assert [n for _, n in cng.ventanas].count("SmartCardPin") == 6  # el PIN se entrega antes de cada firma
    assert PIN not in json.dumps(r)
    for f in r["firmados"]:
        v = validar_pdf(f["salida"], permitir_red=False)
        assert len(v) == 3 and all(x["integridad"] == "intacta" for x in v)

    eventos = api._ventana.eventos()
    assert eventos[0] == {"evento": "inicio", "total": 2, "pin_provisto": True}
    paginas = [(e["indice"], e["hechas"], e["total"]) for e in eventos if e["evento"] == "paginas"]
    assert paginas == [(1, 0, 3), (1, 1, 3), (1, 2, 3), (1, 3, 3), (2, 0, 3), (2, 1, 3), (2, 2, 3), (2, 3, 3)]
    assert all(PIN not in codigo for codigo in api._ventana.js)


def test_lote_cada_pagina_cancelar_a_mitad_de_un_documento(api, cng, tmp_path):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", paginas=3)
    api.agregar_rutas([str(r) for r in rutas])
    cng.despues[2] = api.cancelar_lote  # «Cancelar» en la 2.ª página del primer documento

    r = api.firmar_lote({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["ok"] and r["detenido"] == "cancelado" and r["mensaje"] == app.MENSAJE_CANCELADO_LOTE
    assert _estados(api) == [("uno.pdf", "pendiente"), ("dos.pdf", "pendiente")]
    assert r["firmados"] == [] and r["errores"] == []
    assert len(cng.firmas) == 2
    assert not (tmp_path / "uno_firmado.pdf").exists()  # sin archivo a medias


@pytest.mark.parametrize("codigo", [0x8010006B, 0x8010006C])
def test_lote_cada_pagina_pin_incorrecto_no_reintenta(api, cng, tmp_path, codigo):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", paginas=3)
    api.agregar_rutas([str(r) for r in rutas])
    cng.fallos[1] = dnie_cng.ErrorCNG(f"NCryptSignHash falló (0x{codigo:08X}) PIN", codigo=codigo)

    r = api.firmar_lote({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["detenido"] == "tarjeta" and r["mensaje"] == app._MENSAJES_CNG[codigo]
    assert len(cng.firmas) == 1  # un solo intento con ese PIN
    assert _estados(api) == [("uno.pdf", "error"), ("dos.pdf", "pendiente")]
    assert not (tmp_path / "uno_firmado.pdf").exists()


def test_lote_sin_cada_pagina_ignora_el_pin(api, cng, tmp_path):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf")
    api.agregar_rutas([str(r) for r in rutas])
    r = api.firmar_lote({"caja": CAJA, "paginas": "todas", "pin": PIN})
    assert r["ok"] and len(r["firmados"]) == 2
    assert cng.silenciosas == [False, False]
    assert "SmartCardPin" not in [n for _, n in cng.ventanas]
    assert api._ventana.eventos()[0]["pin_provisto"] is False


# --- DNIe que no acepta el PIN en silencio (DNIe 3.0): el PIN de Windows en cada página


def _rechazo_silencioso():
    return dnie_cng.ErrorCNG("NCryptSignHash falló (0x80090022)", codigo=0x80090022)


def test_lote_cada_pagina_rechazo_silencioso_sigue_con_el_pin_de_windows(api, cng, tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(app, "INTERVALO_AVANCE_PAGINAS", 0)
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", paginas=3)
    api.agregar_rutas([str(r) for r in rutas])
    cng.fallos[1] = _rechazo_silencioso()  # la 1.ª página: la tarjeta no acepta el PIN en silencio

    r = api.firmar_lote({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["ok"] and len(r["firmados"]) == 2 and r["detenido"] is None, r
    assert r["errores"] == []
    # la 1.ª página se reintenta una vez con el cuadro de Windows; el resto ya es así
    assert len(cng.firmas) == 7
    assert cng.silenciosas == [True] + [False] * 6
    assert [n for _, n in cng.ventanas].count("SmartCardPin") == 1
    for f in r["firmados"]:
        v = validar_pdf(f["salida"], permitir_red=False)
        assert len(v) == 3 and all(x["integridad"] == "intacta" for x in v)

    eventos = api._ventana.eventos()
    avisos = [i for i, e in enumerate(eventos) if e["evento"] == "pin_por_pagina"]
    assert len(avisos) == 1  # un solo aviso para todo el lote
    paginas = [i for i, e in enumerate(eventos) if e["evento"] == "paginas"]
    assert paginas[0] < avisos[0] < paginas[1]  # antes de que Windows pida el PIN de la 1.ª página
    assert "se continúa con el PIN de Windows por página" in caplog.text
    assert PIN not in caplog.text and all(PIN not in codigo for codigo in api._ventana.js)


def test_lote_cada_pagina_cancelar_el_pin_de_windows_deja_todo_pendiente(api, cng, tmp_path):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", paginas=3)
    api.agregar_rutas([str(r) for r in rutas])
    cng.fallos[1] = _rechazo_silencioso()
    cng.fallos[2] = dnie_cng.ErrorCNG("NCryptSignHash falló (0x8010006E) cancelado", codigo=0x8010006E)

    r = api.firmar_lote({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["detenido"] == "pin_cancelado" and r["mensaje"] == app._MENSAJES_CNG[0x8010006E]
    assert r["firmados"] == [] and r["errores"] == []
    assert _estados(api) == [("uno.pdf", "pendiente"), ("dos.pdf", "pendiente")]
    assert len(cng.firmas) == 2
    assert not (tmp_path / "uno_firmado.pdf").exists()


@pytest.mark.parametrize("codigo", [0x8010006B, 0x8010006C])
def test_lote_cada_pagina_pin_incorrecto_en_windows_detiene_el_lote(api, cng, tmp_path, codigo):
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", paginas=3)
    api.agregar_rutas([str(r) for r in rutas])
    cng.fallos[1] = _rechazo_silencioso()
    cng.fallos[2] = dnie_cng.ErrorCNG(f"NCryptSignHash falló (0x{codigo:08X}) PIN", codigo=codigo)

    r = api.firmar_lote({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["detenido"] == "tarjeta" and r["mensaje"] == app._MENSAJES_CNG[codigo]
    assert len(cng.firmas) == 2  # el PIN incorrecto no se reintenta
    assert _estados(api) == [("uno.pdf", "error"), ("dos.pdf", "pendiente")]
    assert not (tmp_path / "uno_firmado.pdf").exists()


def test_firmar_un_documento_rechazo_silencioso_sigue_con_el_pin_de_windows(api, cng, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "INTERVALO_AVANCE_PAGINAS", 0)
    (ruta,) = _pdfs(tmp_path, "uno.pdf", paginas=3)
    api.abrir_ruta(str(ruta))
    cng.fallos[1] = _rechazo_silencioso()

    r = api.firmar({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["ok"], r
    assert r["paginas_selladas"] == [0, 1, 2]
    assert cng.silenciosas == [True, False, False, False]
    eventos = api._ventana.eventos()
    assert [e for e in eventos if e["evento"] == "pin_por_pagina"] == [{"evento": "pin_por_pagina"}]
    v = validar_pdf(r["salida"], permitir_red=False)
    assert len(v) == 3 and all(x["integridad"] == "intacta" for x in v)


def test_firmar_un_documento_cancelar_el_pin_de_windows_no_es_un_error(api, cng, tmp_path):
    (ruta,) = _pdfs(tmp_path, "uno.pdf", paginas=3)
    api.abrir_ruta(str(ruta))
    cng.fallos[1] = _rechazo_silencioso()
    cng.fallos[2] = dnie_cng.ErrorCNG("NCryptSignHash falló (0x8010006E) cancelado", codigo=0x8010006E)

    r = api.firmar({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r == {"ok": False, "cancelado": True, "mensaje": app._MENSAJES_CNG[0x8010006E]}
    assert not (tmp_path / "uno_firmado.pdf").exists()
    assert _estados(api) == [("uno.pdf", "pendiente")]


def test_firmar_un_documento_pin_incorrecto_en_windows_se_detiene(api, cng, tmp_path):
    (ruta,) = _pdfs(tmp_path, "uno.pdf", paginas=3)
    api.abrir_ruta(str(ruta))
    cng.fallos[1] = _rechazo_silencioso()
    cng.fallos[2] = dnie_cng.ErrorCNG("NCryptSignHash falló (0x8010006B) PIN", codigo=0x8010006B)

    r = api.firmar({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r == {"ok": False, "mensaje": app._MENSAJES_CNG[0x8010006B]}
    assert len(cng.firmas) == 2
    assert not (tmp_path / "uno_firmado.pdf").exists()


def test_firmar_un_documento_sin_rechazo_silencioso_no_avisa(api, cng, tmp_path):
    (ruta,) = _pdfs(tmp_path, "uno.pdf", paginas=3)
    api.abrir_ruta(str(ruta))
    r = api.firmar({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["ok"], r
    assert cng.silenciosas == [True] * 3
    assert not [e for e in api._ventana.eventos() if e["evento"] == "pin_por_pagina"]


# --- DNIe 3.0 por PKCS#11 (módulo de IDEMIA): un solo PIN para todo


@pytest.fixture
def dnie_v3(monkeypatch, material_prueba):
    """Módulo de IDEMIA simulado con un DNIe 3.0 (CKA_ALWAYS_AUTHENTICATE) que tiene el certificado de prueba."""
    llave_privada, cert = material_prueba
    return ModuloFalso({1: Token(cert.dump(), llave_privada)}).instalar(monkeypatch)


USUARIO, CONTEXTO = dnie_pkcs11.CKU_USER, dnie_pkcs11.CKU_CONTEXT_SPECIFIC


def test_lote_cada_pagina_dnie_v3_por_pkcs11_con_un_solo_pin(api, cng, dnie_v3, tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(app, "INTERVALO_AVANCE_PAGINAS", 0)
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", paginas=3)
    api.agregar_rutas([str(r) for r in rutas])

    r = api.firmar_lote({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["ok"] and len(r["firmados"]) == 2 and r["detenido"] is None, r
    assert r["firmas_en_sesion"] == 6
    # Un solo login con el PIN y el de contexto (mismo PIN, en memoria) antes de cada firma.
    assert dnie_v3.logins == [USUARIO] + [CONTEXTO] * 6
    assert (cng.proveedores, cng.firmas) == (0, [])  # CNG no se usa
    for f in r["firmados"]:
        v = validar_pdf(f["salida"], permitir_red=False)
        assert len(v) == 3 and all(x["integridad"] == "intacta" for x in v)
    assert "Firma por PKCS#11 (IDEMIA): un solo PIN para todas las páginas" in caplog.text
    assert not [e for e in api._ventana.eventos() if e["evento"] == "pin_por_pagina"]
    assert PIN not in caplog.text and all(PIN not in codigo for codigo in api._ventana.js)
    assert dnie_v3.sesiones == {} and dnie_v3.inicializado == 0  # sesión y biblioteca cerradas


def test_firmar_un_documento_dnie_v3_por_pkcs11_con_un_solo_pin(api, cng, dnie_v3, tmp_path):
    (ruta,) = _pdfs(tmp_path, "uno.pdf", paginas=4)
    api.abrir_ruta(str(ruta))

    r = api.firmar({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["ok"], r
    assert r["paginas_selladas"] == [0, 1, 2, 3]
    assert dnie_v3.logins == [USUARIO] + [CONTEXTO] * 4
    assert cng.proveedores == 0
    v = validar_pdf(r["salida"], permitir_red=False)
    assert len(v) == 4 and all(x["integridad"] == "intacta" for x in v)
    assert dnie_v3.sesiones == {} and dnie_v3.inicializado == 0


def test_lote_pin_incorrecto_por_pkcs11_se_detiene_sin_probar_cng(api, cng, dnie_v3, tmp_path):
    dnie_v3.pin = "000000"  # el PIN que escribió el usuario no es el del DNIe
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", paginas=3)
    api.agregar_rutas([str(r) for r in rutas])

    r = api.firmar_lote({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["detenido"] == "tarjeta" and r["mensaje"] == app._MENSAJES_CNG[0x8010006B]
    assert dnie_v3.logins == [USUARIO]  # un solo intento del PIN
    assert (cng.proveedores, cng.firmas) == (0, [])  # ni CNG con el mismo PIN
    assert _estados(api) == [("uno.pdf", "pendiente"), ("dos.pdf", "pendiente")]
    assert dnie_v3.sesiones == {} and dnie_v3.inicializado == 0


def test_firmar_un_documento_pin_incorrecto_por_pkcs11_se_detiene_sin_probar_cng(api, cng, dnie_v3, tmp_path):
    dnie_v3.pin = "000000"
    (ruta,) = _pdfs(tmp_path, "uno.pdf", paginas=3)
    api.abrir_ruta(str(ruta))

    r = api.firmar({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r == {"ok": False, "mensaje": app._MENSAJES_CNG[0x8010006B]}
    assert dnie_v3.logins == [USUARIO] and cng.proveedores == 0
    assert not (tmp_path / "uno_firmado.pdf").exists()


def test_dnie_que_el_modulo_no_reconoce_sigue_por_cng(api, cng, monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    modulo = ModuloFalso({1: Token(b"certificado de otro DNIe", None)}).instalar(monkeypatch)
    rutas = _pdfs(tmp_path, "uno.pdf", "dos.pdf", paginas=3)
    api.agregar_rutas([str(r) for r in rutas])

    r = api.firmar_lote({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["ok"] and len(r["firmados"]) == 2, r
    assert modulo.logins == []
    assert cng.silenciosas == [True] * 6  # la vía CNG de siempre, con el PIN de la aplicación
    assert "Firma por PKCS#11" not in caplog.text


def test_fallo_de_pkcs11_antes_del_login_sigue_por_cng(api, cng, dnie_v3, tmp_path):
    dnie_v3.mecanismos_ = [dnie_pkcs11.CKM_SHA256_RSA_PKCS]  # sin CKM_RSA_PKCS
    (ruta,) = _pdfs(tmp_path, "uno.pdf", paginas=3)
    api.abrir_ruta(str(ruta))

    r = api.firmar({"caja": CAJA, "paginas": "cada_pagina", "pin": PIN})
    assert r["ok"], r
    assert dnie_v3.logins == []
    assert cng.silenciosas == [True] * 3


def test_sin_pin_de_la_aplicacion_no_se_prueba_pkcs11(api, cng, dnie_v3, tmp_path):
    (ruta,) = _pdfs(tmp_path, "uno.pdf", paginas=2)
    api.abrir_ruta(str(ruta))
    assert api.firmar({"caja": CAJA, "paginas": "cada_pagina"})["ok"]
    assert dnie_v3.cargas == 0 and cng.silenciosas == [False, False]
