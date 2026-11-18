"""
Prueba de humo de la API que usa la interfaz (`app.Api`), sin abrir ventana
ni tocar el DNIe: la llave se simula y la firma CNG también.
"""
import base64
from pathlib import Path

import pytest
from pyhanko.pdf_utils.reader import PdfFileReader

import app

# Valores de app.py antes de que el fixture `appdata` (conftest) los reemplace en cada prueba.
LOGO_INICIAL_ORIGINAL = app.LOGO_INICIAL
LOGO_INICIAL_ANTERIOR_ORIGINAL = getattr(app, "LOGO_INICIAL_ANTERIOR", None)


@pytest.fixture
def api(monkeypatch, firmante):
    monkeypatch.setattr(app, "elegir_llave_de_firma", lambda filtro=None: firmante._llave)
    return app.Api()


def _bajar(api, info):
    datos, inicio = b"", 0
    while True:
        r = api.leer_fragmento(info["token"], inicio)
        assert r["ok"]
        trozo = base64.b64decode(r["datos"])
        datos += trozo
        inicio += len(trozo)
        if r["fin"]:
            return datos


def test_estado_dnie_simulado(api):
    r = api.estado_dnie()
    assert r["ok"] and r["titular"] == "PRUEBA FIRMANTE FIR 12345678"
    assert r["algoritmo"] == "RSA 2048"


def test_estado_dnie_sin_tarjeta_devuelve_error_como_dato(monkeypatch):
    def _sin_tarjeta(filtro=None):
        raise app.ErrorCNG("NCryptOpenKey falló (0x8010000C) No hay tarjeta")

    monkeypatch.setattr(app, "elegir_llave_de_firma", _sin_tarjeta)
    r = app.Api().estado_dnie()
    assert r == {"ok": False, "mensaje": app._MENSAJES_CNG[0x8010000C]}


def test_abrir_firmar_y_validar(api, pdf_multipagina, monkeypatch):
    monkeypatch.setattr(app, "TAM_FRAGMENTO", 1000)  # fuerza varios fragmentos
    info = api.abrir_ruta(str(pdf_multipagina))
    assert info["ok"] and info["paginas"] == 3 and info["firmas"] == []
    assert _bajar(api, info) == pdf_multipagina.read_bytes()

    r = api.firmar(
        {
            "caja": {"x": 0.1, "y": 0.1, "ancho": 0.3, "alto": 0.08},
            "paginas": "todas",
            "pagina_firma": -1,
            "texto_sello": "Firmado por %(firmante)s\n%(ts)s",
            "motivo": "Conformidad",
            "lugar": "",
        }
    )
    assert r["ok"], r
    assert r["nombre"] == "entrada_firmado.pdf"
    assert r["paginas_selladas"] == [0, 1, 2] and r["modo"] == "contenido"

    v = api.validar(r["salida"])
    assert v["ok"] and len(v["firmas"]) == 1
    assert v["firmas"][0]["integridad"] == "intacta"

    # Segunda firma sobre el PDF ya firmado: no sobrescribe y usa solo el campo.
    info2 = api.abrir_ruta(r["salida"])
    assert info2["firmas"] == ["FirmaDNIe"]
    r2 = api.firmar({"paginas": "todas"})
    assert r2["ok"] and r2["modo"] == "solo_campo"
    assert r2["nombre"] == "entrada_firmado_firmado.pdf"


def test_salida_no_sobrescribe_archivos(tmp_path):
    entrada = tmp_path / "doc.pdf"
    (tmp_path / "doc_firmado.pdf").write_bytes(b"x")
    assert app.ruta_salida_predeterminada(entrada).name == "doc_firmado (2).pdf"


def test_salida_de_visto_bueno_termina_en_vb(tmp_path):
    entrada = tmp_path / "doc.pdf"
    assert app.ruta_salida_predeterminada(entrada, "visto_bueno").name == "doc_vb.pdf"
    (tmp_path / "doc_vb.pdf").write_bytes(b"x")
    assert app.ruta_salida_predeterminada(entrada, "visto_bueno").name == "doc_vb (2).pdf"
    assert app.ruta_salida_predeterminada(entrada, "firma").name == "doc_firmado.pdf"


def test_firmar_visto_bueno_guarda_con_sufijo_vb(api, pdf_multipagina, tmp_path):
    info = api.abrir_ruta(str(pdf_multipagina))
    assert info["salidas_sugeridas"]["firma"].endswith("entrada_firmado.pdf")
    assert info["salidas_sugeridas"]["visto_bueno"].endswith("entrada_vb.pdf")

    ventana = VentanaFalsa(str(tmp_path / "elegido.pdf"))
    api._asignar_ventana(ventana)
    assert api.elegir_destino("visto_bueno")["ok"]
    assert ventana.dialogos[0][1]["save_filename"] == "entrada_vb.pdf"

    r = api.firmar({"tipo": "visto_bueno", "paginas": "todas"})
    assert r["ok"], r
    assert r["nombre"] == "entrada_vb.pdf"


def test_error_de_firma_se_devuelve_como_dato(api, pdf_multipagina, monkeypatch):
    def _cancelado(self, resumen, alg):
        raise app.ErrorCNG("NCryptSignHash falló (0x8010006E) cancelado")

    monkeypatch.setattr(app.FirmanteCNG, "_firmar_resumen", _cancelado)
    api.abrir_ruta(str(pdf_multipagina))
    r = api.firmar({})
    assert r == {"ok": False, "mensaje": app._MENSAJES_CNG[0x8010006E]}
    assert not (pdf_multipagina.parent / "entrada_firmado.pdf").exists()


def test_solo_abre_archivos_generados(api, pdf_multipagina):
    assert api.abrir_archivo(str(pdf_multipagina))["ok"] is False
    assert api.mostrar_en_carpeta(str(pdf_multipagina))["ok"] is False


def test_mostrar_en_carpeta_con_espacios_selecciona_el_archivo(api, tmp_path, monkeypatch):
    # explorer.exe solo entiende /select,"ruta": si todo el argumento va entre
    # comillas (lo que hace subprocess con una lista) abre «Documentos».
    carpeta = tmp_path / "Revision CVS" / "PS-006596 PNVR"
    carpeta.mkdir(parents=True)
    salida = carpeta / "HUANCA MAMANI_firmado (2).pdf"
    salida.write_bytes(b"%PDF-1.7\n")
    api._generados.add(str(salida.resolve()))
    llamadas = []
    monkeypatch.setattr(app.subprocess, "Popen", lambda cmd, *a, **k: llamadas.append(cmd))

    assert api.mostrar_en_carpeta(str(salida)) == {"ok": True}
    assert llamadas == [f'explorer /select,"{salida.resolve()}"']


def test_abrir_archivo_que_no_es_pdf(api, tmp_path):
    falso = tmp_path / "x.pdf"
    falso.write_text("hola")
    assert api.abrir_ruta(str(falso))["ok"] is False


# --- apertura por bytes (archivo arrastrado sin ruta de pywebview) ------------


class VentanaFalsa:
    """Sustituye a la ventana de pywebview: registra los diálogos sin abrirlos."""

    native = None

    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.dialogos = []

    def create_file_dialog(self, tipo, **opciones):
        self.dialogos.append((tipo, opciones))
        return self.respuesta

    def evaluate_js(self, codigo):
        return None


def _subir(api, nombre, datos, trozo=1000):
    inicio = api.abrir_bytes_inicio(nombre, len(datos))
    assert inicio["ok"], inicio
    for i in range(0, len(datos), trozo):
        r = api.abrir_bytes_fragmento(inicio["token"], base64.b64encode(datos[i : i + trozo]).decode("ascii"))
        assert r["ok"], r
    return api.abrir_bytes_fin(inicio["token"])


def test_abrir_por_bytes_devuelve_la_misma_forma_que_por_ruta(api, pdf_multipagina, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "carpeta_descargas", lambda: tmp_path)
    datos = pdf_multipagina.read_bytes()
    por_ruta = api.abrir_ruta(str(pdf_multipagina))

    info = _subir(api, "Contrato.PDF", datos)
    assert info["ok"], info
    assert set(info) == set(por_ruta)
    assert info["ruta"] is None and info["carpeta"] is None
    assert info["nombre"] == "Contrato.PDF"
    assert info["paginas"] == 3 and info["firmas"] == [] and info["tamano"] == len(datos)
    assert info["salida_sugerida"] == str(tmp_path / "Contrato_firmado.pdf")
    assert _bajar(api, info) == datos

    v = api.validar()
    assert v["ok"] and v["firmas"] == [] and v["nombre"] == "Contrato.PDF"


def test_abrir_por_bytes_rechaza_lo_que_no_es_pdf(api):
    r = _subir(api, "falso.pdf", b"hola, esto no es un PDF")
    assert r["ok"] is False and "PDF" in r["mensaje"]


def test_abrir_por_bytes_rechaza_archivos_demasiado_grandes(api, monkeypatch):
    monkeypatch.setattr(app, "TAM_MAXIMO_PDF", 100)
    r = api.abrir_bytes_inicio("grande.pdf", 101)
    assert r["ok"] is False and "tamaño máximo" in r["mensaje"]

    # Declarar poco y enviar más tampoco pasa.
    inicio = api.abrir_bytes_inicio("chico.pdf", 50)
    assert inicio["ok"]
    r = api.abrir_bytes_fragmento(inicio["token"], base64.b64encode(b"%PDF" + b"x" * 196).decode("ascii"))
    assert r["ok"] is False
    assert api.abrir_bytes_fin(inicio["token"])["ok"] is False


def test_abrir_por_bytes_rechaza_token_desconocido_o_vencido(api, pdf_multipagina, monkeypatch):
    assert api.abrir_bytes_fragmento("desconocido", "JVBERg==")["ok"] is False
    assert api.abrir_bytes_fin("desconocido")["ok"] is False

    vida = app.VIDA_SUBIDA
    inicio = api.abrir_bytes_inicio("doc.pdf", 10)
    monkeypatch.setattr(app, "VIDA_SUBIDA", -1)  # todo token queda vencido
    assert api.abrir_bytes_fragmento(inicio["token"], "JVBERg==")["ok"] is False
    monkeypatch.setattr(app, "VIDA_SUBIDA", vida)

    # Un token ya usado no sirve de nuevo.
    datos = pdf_multipagina.read_bytes()
    inicio = api.abrir_bytes_inicio("doc.pdf", len(datos))
    assert api.abrir_bytes_fragmento(inicio["token"], base64.b64encode(datos).decode("ascii"))["ok"]
    assert api.abrir_bytes_fin(inicio["token"])["ok"]
    assert api.abrir_bytes_fin(inicio["token"])["ok"] is False


def test_firmar_pdf_abierto_por_bytes_pide_donde_guardar(api, pdf_multipagina, tmp_path, monkeypatch):
    descargas = tmp_path / "Descargas"
    descargas.mkdir()
    monkeypatch.setattr(app, "carpeta_descargas", lambda: descargas)
    elegido = tmp_path / "elegido" / "salida.pdf"
    elegido.parent.mkdir()
    ventana = VentanaFalsa(str(elegido))
    api._asignar_ventana(ventana)
    monkeypatch.setattr(api, "_hwnd", lambda: None)

    info = _subir(api, "Contrato.pdf", pdf_multipagina.read_bytes())
    assert info["ok"], info
    r = api.firmar({"paginas": "todas"})
    assert r["ok"], r
    assert Path(r["salida"]) == elegido and elegido.exists()
    assert r["nombre"] == "salida.pdf"

    tipo, opciones = ventana.dialogos[0]
    assert tipo == app.webview.FileDialog.SAVE
    assert opciones["directory"] == str(descargas)
    assert opciones["save_filename"] == "Contrato_firmado.pdf"

    # «Abrir PDF firmado» y «Mostrar en carpeta» lo permiten.
    assert api._permitida(r["salida"]) is not None
    v = api.validar(r["salida"])
    assert v["ok"] and v["firmas"][0]["integridad"] == "intacta"


def test_firmar_pdf_abierto_por_bytes_cancelado_no_escribe_nada(api, pdf_multipagina, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "carpeta_descargas", lambda: tmp_path)
    api._asignar_ventana(VentanaFalsa(None))
    monkeypatch.setattr(api, "_hwnd", lambda: None)

    assert _subir(api, "Contrato.pdf", pdf_multipagina.read_bytes())["ok"]
    r = api.firmar({"paginas": "todas"})
    assert r == {"ok": False, "cancelado": True}
    assert list(tmp_path.rglob("*_firmado*.pdf")) == []


# --- tipo de sello, logo y configuración persistente ---------------------------


def _imagen(ruta: Path, formato: str, tamano=(300, 100), color=(200, 40, 40)):
    from PIL import Image

    Image.new("RGB", tamano, color).save(ruta, formato)
    return ruta


def _pixeles(ruta: Path):
    """(modo, tamaño, píxeles) de una imagen, para comparar copias sin depender del codificador PNG."""
    from PIL import Image

    with Image.open(ruta) as imagen:
        imagen.load()
        return imagen.mode, imagen.size, imagen.tobytes()


def _color_de_esquina(ruta: Path):
    from PIL import Image

    with Image.open(ruta) as imagen:
        return imagen.convert("RGB").getpixel((0, 0))


def test_config_predeterminada(api):
    c = api.obtener_config()
    assert c["ok"] and c["tipo"] == "firma" and c["logo"] is None
    assert c["motivos"] == {"firma": "Soy el autor del documento", "visto_bueno": "Doy V° B°"}


def test_logo_y_tipo_persisten_en_appdata(api, appdata, tmp_path):
    original = _imagen(tmp_path / "mi logo.jpg", "JPEG")
    api._asignar_ventana(VentanaFalsa([str(original)]))

    r = api.elegir_logo()
    assert r["ok"], r
    assert r["logo"]["datos"].startswith("data:image/png;base64,")
    copia = appdata / "FirmadorDNIe" / "logo.png"
    assert copia.is_file()
    original.unlink()  # mover o borrar el original no afecta a la copia
    assert api.guardar_tipo("visto_bueno")["ok"]

    otra = app.Api()  # simula reiniciar la aplicación
    c = otra.obtener_config()
    assert c["tipo"] == "visto_bueno"
    assert c["logo"] is not None and c["logo"]["datos"].startswith("data:image/png;base64,")

    assert otra.quitar_logo()["ok"]
    assert not copia.exists()
    assert app.Api().obtener_config()["logo"] is None


def test_logo_grande_se_reduce(api, appdata, tmp_path):
    from PIL import Image

    api._asignar_ventana(VentanaFalsa([str(_imagen(tmp_path / "grande.png", "PNG", (4000, 1000)))]))
    assert api.elegir_logo()["ok"]
    with Image.open(appdata / "FirmadorDNIe" / "logo.png") as copia:
        assert max(copia.size) <= app.LADO_MAXIMO_LOGO


def test_logo_que_no_es_imagen_se_rechaza(api, appdata, tmp_path):
    falso = tmp_path / "falso.png"
    falso.write_text("no soy una imagen")
    api._asignar_ventana(VentanaFalsa([str(falso)]))
    r = api.elegir_logo()
    assert r["ok"] is False and "imagen" in r["mensaje"]
    assert not (appdata / "FirmadorDNIe" / "logo.png").exists()


def test_elegir_logo_cancelado(api):
    api._asignar_ventana(VentanaFalsa(None))
    assert api.elegir_logo() == {"ok": False, "cancelado": True}


def test_tipo_invalido_y_config_danada(api, appdata):
    assert api.guardar_tipo("otro")["ok"] is False
    carpeta = appdata / "FirmadorDNIe"
    carpeta.mkdir(exist_ok=True)
    (carpeta / "config.json").write_text("{ no es json", encoding="utf-8")
    c = app.Api().obtener_config()
    assert c["ok"] and c["tipo"] == "firma" and c["logo"] is None


def test_logo_inicial_se_copia_una_sola_vez(api, appdata, tmp_path, monkeypatch):
    inicial = _imagen(tmp_path / "logo_institucion.png", "PNG", (437, 259))
    original = inicial.read_bytes()
    monkeypatch.setattr(app, "LOGO_INICIAL", inicial)

    c = api.obtener_config()
    assert c["logo"] is not None
    assert (appdata / "FirmadorDNIe" / "logo.png").is_file()
    assert inicial.read_bytes() == original  # el archivo del usuario no se toca

    # Quitar el logo se respeta: no se vuelve a copiar al reiniciar.
    assert api.quitar_logo()["ok"]
    assert app.Api().obtener_config()["logo"] is None

    # Elegir otro logo después sigue funcionando.
    api._asignar_ventana(VentanaFalsa([str(_imagen(tmp_path / "otro.png", "PNG"))]))
    assert api.elegir_logo()["ok"]
    assert app.Api().obtener_config()["logo"] is not None


def test_sin_logo_inicial_no_hay_logo(api, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "LOGO_INICIAL", tmp_path / "no-existe.png")
    assert api.obtener_config()["logo"] is None


def test_logo_inicial_se_busca_junto_al_ejecutable(monkeypatch, tmp_path):
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app.sys, "executable", str(tmp_path / "FirmadorDNIe.exe"))
    monkeypatch.setattr(app, "LOGO_INICIAL", app.Path("src") / "firma_digital_vivienda.png")
    assert app.ruta_logo_inicial() == tmp_path.resolve() / "src" / "firma_digital_vivienda.png"


def test_logo_inicial_predeterminado_es_el_emblema_peruano():
    assert LOGO_INICIAL_ORIGINAL == Path("src") / "Emblema peruano con firma digital.png"
    assert LOGO_INICIAL_ANTERIOR_ORIGINAL == Path("src") / "firma_digital_vivienda.png"


@pytest.fixture
def logos_iniciales(tmp_path):
    """Logo inicial anterior (rojo) y actual (azul), con tamaños distintos."""
    carpeta = tmp_path / "src"
    carpeta.mkdir()
    anterior = _imagen(carpeta / "anterior.png", "PNG", (437, 259), (200, 40, 40))
    nuevo = _imagen(carpeta / "nuevo.png", "PNG", (1000, 1000), (30, 60, 200))
    return anterior, nuevo


def _instalacion_anterior(api, monkeypatch, anterior, nuevo):
    """Simula una instalación previa (que copió el logo anterior) y actualiza el programa."""
    monkeypatch.setattr(app, "LOGO_INICIAL", anterior)
    assert api.obtener_config()["logo"] is not None
    monkeypatch.setattr(app, "LOGO_INICIAL", nuevo)
    monkeypatch.setattr(app, "LOGO_INICIAL_ANTERIOR", anterior, raising=False)


def test_configuracion_nueva_copia_el_logo_inicial_actual(api, appdata, logos_iniciales, monkeypatch):
    anterior, nuevo = logos_iniciales
    monkeypatch.setattr(app, "LOGO_INICIAL", nuevo)
    monkeypatch.setattr(app, "LOGO_INICIAL_ANTERIOR", anterior, raising=False)

    assert api.obtener_config()["logo"] is not None
    _, tamano, _ = _pixeles(appdata / "FirmadorDNIe" / "logo.png")
    assert tamano == (app.LADO_MAXIMO_LOGO, app.LADO_MAXIMO_LOGO)
    assert _color_de_esquina(appdata / "FirmadorDNIe" / "logo.png") == (30, 60, 200)  # el emblema nuevo
    assert app.leer_config()["logo_inicial_aplicado"] is True


def test_logo_inicial_anterior_sin_cambios_se_reemplaza(api, appdata, logos_iniciales, monkeypatch):
    anterior, nuevo = logos_iniciales
    _instalacion_anterior(api, monkeypatch, anterior, nuevo)
    guardado = appdata / "FirmadorDNIe" / "logo.png"
    copia_anterior = _pixeles(guardado)

    assert api.obtener_config()["logo"] is not None
    assert _pixeles(guardado) != copia_anterior
    assert _color_de_esquina(guardado) == (30, 60, 200)  # el emblema nuevo
    config = app.leer_config()
    assert config["logo"] == "logo.png" and config["logo_inicial_aplicado"] is True
    assert anterior.is_file() and nuevo.is_file()  # los originales no se tocan


def test_logo_elegido_por_el_usuario_no_se_reemplaza(api, appdata, logos_iniciales, monkeypatch, tmp_path):
    anterior, nuevo = logos_iniciales
    monkeypatch.setattr(app, "LOGO_INICIAL", anterior)
    assert api.obtener_config()["logo"] is not None
    api._asignar_ventana(VentanaFalsa([str(_imagen(tmp_path / "propio.png", "PNG", (437, 259), (10, 150, 10)))]))
    assert api.elegir_logo()["ok"]
    guardado = appdata / "FirmadorDNIe" / "logo.png"
    propio = guardado.read_bytes()

    monkeypatch.setattr(app, "LOGO_INICIAL", nuevo)
    monkeypatch.setattr(app, "LOGO_INICIAL_ANTERIOR", anterior, raising=False)
    assert api.obtener_config()["logo"] is not None
    assert guardado.read_bytes() == propio


def test_logo_quitado_no_vuelve_tras_actualizar(api, appdata, logos_iniciales, monkeypatch):
    anterior, nuevo = logos_iniciales
    _instalacion_anterior(api, monkeypatch, anterior, nuevo)
    assert api.quitar_logo()["ok"]

    assert api.obtener_config()["logo"] is None
    assert not (appdata / "FirmadorDNIe" / "logo.png").exists()
    config = app.leer_config()
    assert config["logo"] is None and config["logo_inicial_aplicado"] is True


def test_sin_logo_anterior_junto_al_programa_no_se_toca(api, appdata, logos_iniciales, monkeypatch):
    anterior, nuevo = logos_iniciales
    _instalacion_anterior(api, monkeypatch, anterior, nuevo)
    guardado = appdata / "FirmadorDNIe" / "logo.png"
    copia_anterior = guardado.read_bytes()
    anterior.unlink()  # no hay con qué comparar: no se puede saber si es la copia de fábrica

    assert api.obtener_config()["logo"] is not None
    assert guardado.read_bytes() == copia_anterior


def test_actualizar_logo_inicial_es_idempotente(api, appdata, logos_iniciales, monkeypatch):
    anterior, nuevo = logos_iniciales
    _instalacion_anterior(api, monkeypatch, anterior, nuevo)
    app.preparar_imagenes_iniciales()
    guardado = appdata / "FirmadorDNIe" / "logo.png"
    archivo = appdata / "FirmadorDNIe" / "config.json"
    tras_la_primera = (guardado.read_bytes(), guardado.stat().st_mtime_ns, archivo.read_bytes())

    app.preparar_imagenes_iniciales()
    app.preparar_imagenes_iniciales()
    assert (guardado.read_bytes(), guardado.stat().st_mtime_ns, archivo.read_bytes()) == tras_la_primera


def test_imagen_vb_persiste_y_se_quita(api, appdata, tmp_path):
    c = api.obtener_config()
    assert c["imagen_vb"] is None
    api._asignar_ventana(VentanaFalsa([str(_imagen(tmp_path / "check.png", "PNG", (1254, 1254)))]))
    r = api.elegir_imagen_vb()
    assert r["ok"], r
    assert r["imagen_vb"]["datos"].startswith("data:image/png;base64,")
    copia = appdata / "FirmadorDNIe" / "imagen_vb.png"
    assert copia.is_file()
    assert app.Api().obtener_config()["imagen_vb"] is not None
    assert app.Api().obtener_config()["logo"] is None  # el logo es independiente

    assert api.quitar_imagen_vb()["ok"]
    assert not copia.exists()
    assert app.Api().obtener_config()["imagen_vb"] is None


def test_imagen_vb_inicial_se_copia_una_sola_vez(api, appdata, tmp_path, monkeypatch):
    inicial = _imagen(tmp_path / "Icono circular con marca roja.png", "PNG", (1254, 1254))
    original = inicial.read_bytes()
    monkeypatch.setattr(app, "IMAGEN_VB_INICIAL", inicial)

    assert api.obtener_config()["imagen_vb"] is not None
    assert inicial.read_bytes() == original
    assert api.quitar_imagen_vb()["ok"]
    assert app.Api().obtener_config()["imagen_vb"] is None  # «Quitar» se respeta


def test_firmar_visto_bueno_usa_la_imagen_de_la_config(api, pdf_multipagina, tmp_path):
    api._asignar_ventana(VentanaFalsa([str(_imagen(tmp_path / "check.png", "PNG", (400, 400)))]))
    assert api.elegir_imagen_vb()["ok"]
    api.abrir_ruta(str(pdf_multipagina))
    r = api.firmar({"paginas": "ultima", "tipo": "visto_bueno"})
    assert r["ok"], r
    with open(r["salida"], "rb") as f:
        reader = PdfFileReader(f, strict=False)
        pagina = reader.find_page_for_modification(2)[0].get_object()
        anot = next(a.get_object() for a in pagina["/Annots"])
        recursos = anot["/AP"]["/N"].get_object()["/Resources"]
        assert any(x.get_object().get("/Subtype") == "/Image" for x in recursos["/XObject"].values())


def test_firmar_usa_tipo_motivo_y_logo_de_la_config(api, pdf_multipagina, tmp_path):
    api._asignar_ventana(VentanaFalsa([str(_imagen(tmp_path / "logo.png", "PNG"))]))
    assert api.elegir_logo()["ok"]
    api.abrir_ruta(str(pdf_multipagina))

    # Motivo vacío: se usa el predeterminado del tipo.
    r = api.firmar({"paginas": "ultima", "tipo": "firma", "motivo": "  "})
    assert r["ok"], r
    with open(r["salida"], "rb") as f:
        reader = PdfFileReader(f, strict=False)
        assert reader.embedded_signatures[0].sig_object["/Reason"] == "Soy el autor del documento"
        pagina = reader.find_page_for_modification(2)[0].get_object()
        anot = next(a.get_object() for a in pagina["/Annots"])
        recursos = anot["/AP"]["/N"].get_object()["/Resources"]
        assert any(x.get_object().get("/Subtype") == "/Image" for x in recursos["/XObject"].values())

    r2 = api.firmar({"paginas": "ultima", "tipo": "visto_bueno", "motivo": "Doy V° B°", "destino": str(tmp_path / "vb.pdf")})
    assert r2["ok"], r2
    with open(r2["salida"], "rb") as f:
        assert PdfFileReader(f, strict=False).embedded_signatures[0].sig_object["/Reason"] == "Doy V° B°"


def test_icono_de_la_aplicacion_tiene_los_tamanos_de_windows():
    from PIL import Image

    assert app.RUTA_ICONO.is_file()
    with Image.open(app.RUTA_ICONO) as icono:
        assert icono.format == "ICO"
        tamanos = icono.info["sizes"]
    for lado in (16, 32, 48, 256):
        assert (lado, lado) in tamanos


def test_la_ventana_usa_el_icono_de_la_aplicacion(monkeypatch):
    recibido = {}
    monkeypatch.setattr(app, "crear_ventana", lambda api: object())
    monkeypatch.setattr(app.webview, "start", lambda *a, **kw: recibido.update(kw))
    monkeypatch.setattr(app.sys, "argv", ["app.py"])
    app.main()
    assert recibido["icon"] == str(app.RUTA_ICONO)
