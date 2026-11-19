"""
Prueba de humo de la API que usa la interfaz (`app.Api`), sin abrir ventana
ni tocar el DNIe: la llave se simula y la firma CNG también.
"""
import base64
import io
import json
from pathlib import Path

import pytest
from pyhanko.pdf_utils.reader import PdfFileReader

import app

# Valores de app.py antes de que el fixture `appdata` (conftest) los reemplace en cada prueba.
LOGO_INICIAL_ORIGINAL = app.LOGO_INICIAL


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

    # Segunda firma sobre el PDF ya firmado: no sobrescribe y sella cada página como la primera.
    info2 = api.abrir_ruta(r["salida"])
    assert info2["firmas"] == ["FirmaDNIe"]
    r2 = api.firmar({"paginas": "todas"})
    assert r2["ok"] and r2["modo"] == "contenido" and r2["paginas_selladas"] == [0, 1, 2]
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
    # Lista cerrada de motivos por tipo; el primero es el predeterminado.
    assert c["motivos"] == {
        "firma": ["Soy el autor del documento", "En señal de conformidad"],
        "visto_bueno": ["Doy V° B°"],
    }


AZUL = (30, 60, 200)  # logo inicial (el que acompaña al programa)
VERDE = (10, 150, 10)  # logo elegido con «Cambiar logo…» en versiones anteriores


@pytest.fixture
def logo_inicial(tmp_path, monkeypatch):
    """Logo inicial junto al programa (azul, más grande que LADO_MAXIMO_LOGO)."""
    carpeta = tmp_path / "src"
    carpeta.mkdir(exist_ok=True)
    ruta = _imagen(carpeta / "Emblema peruano con firma digital.png", "PNG", (1000, 1000), AZUL)
    monkeypatch.setattr(app, "LOGO_INICIAL", ruta)
    return ruta


def _logo_propio_de_version_anterior():
    """Configuración que dejaba «Cambiar logo…» (opción que ya no existe): logo.png verde."""
    carpeta = app.carpeta_config()
    carpeta.mkdir(parents=True, exist_ok=True)
    _imagen(carpeta / "logo.png", "PNG", (437, 259), VERDE)
    datos = {"tipo": "firma", "logo": "logo.png", "logo_inicial_aplicado": True}
    (carpeta / "config.json").write_text(json.dumps(datos), encoding="utf-8")


def _logo_quitado_en_version_anterior():
    """Configuración que dejaba «Quitar logo» (opción que ya no existe)."""
    carpeta = app.carpeta_config()
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "logo.png").unlink(missing_ok=True)
    datos = {"tipo": "firma", "logo": None, "logo_inicial_aplicado": True}
    (carpeta / "config.json").write_text(json.dumps(datos), encoding="utf-8")


def _vista_previa(imagen: dict):
    """(tamaño, color de la esquina) de una imagen enviada a la interfaz como data URL."""
    from PIL import Image

    datos = base64.b64decode(imagen["datos"].split(",", 1)[1])
    with Image.open(io.BytesIO(datos)) as vista:
        return vista.size, vista.convert("RGB").getpixel((0, 0))


def _espiar_logo(monkeypatch) -> list:
    """
    Registra, en cada llamada a `firmar_pdf`, (tamaño, color de la esquina) del
    logo que recibe, o None si firma sin logo.
    """
    from PIL import Image

    vistos = []
    firmar_pdf = app.firmar_pdf

    def _firmar_pdf(*args, **kwargs):
        opciones = args[4] if len(args) > 4 else kwargs["opciones"]
        if opciones.logo:
            with Image.open(opciones.logo) as logo:
                vistos.append((logo.size, logo.convert("RGB").getpixel((0, 0))))
        else:
            vistos.append(None)
        return firmar_pdf(*args, **kwargs)

    monkeypatch.setattr(app, "firmar_pdf", _firmar_pdf)
    return vistos


def _tiene_imagen(ruta, indice=2) -> bool:
    """True si la apariencia del sello de la página `indice` incluye una imagen."""
    with open(ruta, "rb") as f:
        reader = PdfFileReader(f, strict=False)
        pagina = reader.find_page_for_modification(indice)[0].get_object()
        anot = next(a.get_object() for a in pagina["/Annots"])
        recursos = anot["/AP"]["/N"].get_object()["/Resources"]
        return any(x.get_object().get("/Subtype") == "/Image" for x in recursos.get("/XObject", {}).values())


def test_el_logo_no_se_puede_cambiar_ni_quitar():
    assert not hasattr(app.Api, "elegir_logo") and not hasattr(app.Api, "quitar_logo")
    # La imagen de visto bueno sí se elige y se quita.
    assert hasattr(app.Api, "elegir_imagen_vb") and hasattr(app.Api, "quitar_imagen_vb")


def test_tipo_persiste_y_el_logo_es_el_inicial(api, appdata, logo_inicial):
    assert api.guardar_tipo("visto_bueno")["ok"]
    c = app.Api().obtener_config()  # simula reiniciar la aplicación
    assert c["tipo"] == "visto_bueno"
    assert c["logo"]["datos"].startswith("data:image/png;base64,")
    assert _vista_previa(c["logo"])[1] == AZUL


def test_config_muestra_el_logo_inicial_reducido(api, logo_inicial):
    original = logo_inicial.read_bytes()
    tamano, color = _vista_previa(api.obtener_config()["logo"])
    assert color == AZUL and max(tamano) <= app.LADO_MAXIMO_LOGO
    assert logo_inicial.read_bytes() == original  # el archivo junto al programa no se toca


def test_logo_elegido_en_version_anterior_se_ignora(api, pdf_multipagina, logo_inicial, monkeypatch):
    _logo_propio_de_version_anterior()
    assert _vista_previa(app.Api().obtener_config()["logo"])[1] == AZUL

    vistos = _espiar_logo(monkeypatch)
    api.abrir_ruta(str(pdf_multipagina))
    r = api.firmar({"paginas": "ultima", "tipo": "firma"})
    assert r["ok"], r
    assert [color for _, color in vistos] == [AZUL]


def test_logo_quitado_en_version_anterior_vuelve_a_ser_el_inicial(api, pdf_multipagina, logo_inicial, monkeypatch):
    _logo_quitado_en_version_anterior()
    assert _vista_previa(app.Api().obtener_config()["logo"])[1] == AZUL

    vistos = _espiar_logo(monkeypatch)
    api.abrir_ruta(str(pdf_multipagina))
    assert api.firmar({"paginas": "ultima", "tipo": "firma"})["ok"]
    assert [color for _, color in vistos] == [AZUL]


def test_logo_inicial_grande_se_reduce_al_firmar(api, pdf_multipagina, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "LOGO_INICIAL", _imagen(tmp_path / "grande.png", "PNG", (4000, 1000), AZUL))
    vistos = _espiar_logo(monkeypatch)
    api.abrir_ruta(str(pdf_multipagina))
    assert api.firmar({"paginas": "ultima", "tipo": "firma"})["ok"]
    (tamano, color), = vistos
    assert color == AZUL and max(tamano) <= app.LADO_MAXIMO_LOGO


def test_sin_logo_inicial_se_firma_sin_logo_aunque_haya_uno_guardado(api, pdf_multipagina, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(app, "LOGO_INICIAL", tmp_path / "no-existe.png")
    _logo_propio_de_version_anterior()
    assert api.obtener_config()["logo"] is None

    vistos = _espiar_logo(monkeypatch)
    api.abrir_ruta(str(pdf_multipagina))
    with caplog.at_level("WARNING", logger="firmador"):
        r = api.firmar({"paginas": "ultima", "tipo": "firma"})
    assert r["ok"], r
    assert vistos == [None]
    assert not _tiene_imagen(r["salida"])
    assert "logo" in caplog.text


def test_logo_inicial_que_no_es_imagen_se_firma_sin_logo(api, pdf_multipagina, tmp_path, monkeypatch, caplog):
    falso = tmp_path / "falso.png"
    falso.write_text("no soy una imagen")
    monkeypatch.setattr(app, "LOGO_INICIAL", falso)
    assert api.obtener_config()["logo"] is None

    api.abrir_ruta(str(pdf_multipagina))
    with caplog.at_level("WARNING", logger="firmador"):
        r = api.firmar({"paginas": "ultima", "tipo": "firma"})
    assert r["ok"], r
    assert not _tiene_imagen(r["salida"])
    assert "logo" in caplog.text


def test_tipo_invalido_y_config_danada(api, appdata):
    assert api.guardar_tipo("otro")["ok"] is False
    carpeta = appdata / "FirmadorDNIe"
    carpeta.mkdir(exist_ok=True)
    (carpeta / "config.json").write_text("{ no es json", encoding="utf-8")
    c = app.Api().obtener_config()
    assert c["ok"] and c["tipo"] == "firma" and c["logo"] is None


def test_sin_logo_inicial_no_hay_logo(api, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "LOGO_INICIAL", tmp_path / "no-existe.png")
    assert api.obtener_config()["logo"] is None


def test_logo_inicial_se_busca_en_los_recursos_empaquetados(monkeypatch, tmp_path):
    # Empaquetado con PyInstaller, el logo va dentro del ejecutable (_MEIPASS),
    # no junto al .exe: el instalador no copia la carpeta src/.
    recursos = tmp_path / "_internal"
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app.sys, "executable", str(tmp_path / "FirmaPeru.exe"))
    monkeypatch.setattr(app.sys, "_MEIPASS", str(recursos), raising=False)
    monkeypatch.setattr(app, "LOGO_INICIAL", LOGO_INICIAL_ORIGINAL)
    assert app.ruta_logo_inicial() == recursos / "src" / "Emblema peruano con firma digital.png"


def test_el_ejecutable_empaqueta_el_logo_inicial():
    spec = (Path(app.__file__).parent / "firmador.spec").read_text(encoding="utf-8")
    assert '"src/Emblema peruano con firma digital.png", "src"' in spec


def test_logo_inicial_predeterminado_es_el_emblema_peruano():
    assert LOGO_INICIAL_ORIGINAL == Path("src") / "Emblema peruano con firma digital.png"


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


def test_firmar_usa_tipo_motivo_y_logo_inicial(api, pdf_multipagina, logo_inicial, tmp_path):
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
    monkeypatch.setattr(app, "configurar_registro", lambda *a, **k: None)  # no escribe el registro real
    monkeypatch.setattr(app, "crear_ventana", lambda api: object())
    monkeypatch.setattr(app.webview, "start", lambda *a, **kw: recibido.update(kw))
    monkeypatch.setattr(app.sys, "argv", ["app.py"])
    app.main()
    assert recibido["icon"] == str(app.RUTA_ICONO)


def test_al_iniciar_se_configura_el_registro(monkeypatch):
    llamadas = []
    monkeypatch.setattr(app, "configurar_registro", lambda *a, **k: llamadas.append(a))
    monkeypatch.setattr(app, "crear_ventana", lambda api: object())
    monkeypatch.setattr(app.webview, "start", lambda *a, **kw: None)
    monkeypatch.setattr(app.sys, "argv", ["app.py"])
    app.main()
    assert llamadas == [()]


def _rect_y_motivo(ruta, indice=2):
    with open(ruta, "rb") as f:
        reader = PdfFileReader(f, strict=False)
        pagina = reader.find_page_for_modification(indice)[0].get_object()
        anot = next(a.get_object() for a in pagina["/Annots"])
        return tuple(float(v) for v in anot["/Rect"]), reader.embedded_signatures[0].sig_object["/Reason"]


def test_firmar_ignora_el_tamano_que_envia_la_interfaz(api, pdf_multipagina):
    api.abrir_ruta(str(pdf_multipagina))
    caja = {"x": 0.1, "y": 0.1, "ancho": 0.5, "alto": 0.3}  # tamaño antiguo o manipulado
    r = api.firmar({"paginas": "ultima", "tipo": "firma", "caja": caja})
    assert r["ok"], r
    rect, _ = _rect_y_motivo(r["salida"])
    assert rect == pytest.approx((59.5, 84.2, 219.5, 124.2), abs=0.01)  # 160 x 40 pt en A4


def _rects_por_pagina(ruta) -> list[tuple[float, ...]]:
    """
    /Rect del sello de cada página (una sola firma): el widget del campo en la
    página de firma y la anotación /Stamp en las demás (con su /Link encima).
    """
    with open(ruta, "rb") as f:
        reader = PdfFileReader(f, strict=False)
        rects = []
        for indice in range(int(reader.root["/Pages"]["/Count"])):
            pagina = reader.find_page_for_modification(indice)[0].get_object()
            anots = [a.get_object() for a in pagina["/Annots"]]
            (anot,) = [a for a in anots if a["/Subtype"] in ("/Widget", "/Stamp")]
            rects.append(tuple(round(float(v), 2) for v in anot["/Rect"]))
        return rects


def test_firmar_usa_la_posicion_propia_de_cada_pagina(api, pdf_multipagina):
    # pdf_multipagina: A4, carta horizontal (792 x 612) y A4.
    api.abrir_ruta(str(pdf_multipagina))
    r = api.firmar(
        {
            "paginas": "todas",
            "tipo": "firma",
            "caja": {"x": 0.1, "y": 0.1},
            "posiciones_paginas": {"1": {"x": 0.5, "y": 0.5}, "2": {"x": 0.2, "y": 0.8}},
        }
    )
    assert r["ok"], r
    assert _rects_por_pagina(r["salida"]) == [
        (59.5, 84.2, 219.5, 124.2),
        (396.0, 306.0, 556.0, 346.0),
        (119.0, 673.6, 279.0, 713.6),
    ]
    assert len(api.validar(r["salida"])["firmas"]) == 1


def test_posiciones_por_pagina_mal_formadas_se_ignoran(api, pdf_multipagina):
    api.abrir_ruta(str(pdf_multipagina))
    malas = {
        "x": {"x": 0.5, "y": 0.5},  # índice no numérico
        "-1": {"x": 0.5, "y": 0.5},
        "3": {"x": 0.5, "y": 0.5},  # el PDF tiene 3 páginas (0..2)
        "0": {"x": "nan", "y": 0.5},
        "1": {"x": 1.5, "y": 0.5},
        "2": [0.5, 0.5],
    }
    for posiciones in (malas, ["no", "es", "un", "objeto"], "texto"):
        r = api.firmar({"paginas": "todas", "tipo": "firma", "caja": {"x": 0.1, "y": 0.1}, "posiciones_paginas": posiciones})
        assert r["ok"], r
        a4 = (59.5, 84.2, 219.5, 124.2)
        assert _rects_por_pagina(r["salida"]) == [a4, (79.2, 61.2, 239.2, 101.2), a4]
        Path(r["salida"]).unlink()


def test_opciones_firma_convierte_las_posiciones_por_pagina():
    opts = app.Api._opciones_firma(
        {"tipo": "firma"}, {"x": 0.1, "y": 0.2}, -1, {"0": {"x": 0.3, "y": 0.4}, "9": {"x": 0.3, "y": 0.4}}, 5
    )
    assert opts.posicion == app.PosicionRelativa(x=0.1, y=0.2)
    assert opts.posiciones_paginas == {0: app.PosicionRelativa(x=0.3, y=0.4)}
    assert app.Api._opciones_firma({"tipo": "firma"}, None, -1).posiciones_paginas == {}


@pytest.mark.parametrize(
    "tipo, motivo, esperado",
    [
        ("firma", "En señal de conformidad", "En señal de conformidad"),
        ("firma", "Soy el autor del documento", "Soy el autor del documento"),
        ("firma", "Texto libre", "Soy el autor del documento"),
        ("firma", "Doy V° B°", "Soy el autor del documento"),
        ("visto_bueno", "En señal de conformidad", "Doy V° B°"),
        ("visto_bueno", None, "Doy V° B°"),
    ],
)
def test_firmar_solo_acepta_motivos_de_la_lista(api, pdf_multipagina, tipo, motivo, esperado):
    api.abrir_ruta(str(pdf_multipagina))
    r = api.firmar({"paginas": "ultima", "tipo": tipo, "motivo": motivo})
    assert r["ok"], r
    assert _rect_y_motivo(r["salida"])[1] == esperado
