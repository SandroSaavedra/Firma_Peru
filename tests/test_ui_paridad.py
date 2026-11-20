"""
Parity between the UI stamp geometry (ui/geometria.js) and firma_pdf (feature
"sello-orientacion-y-reintento", T4).

The preview must show each page's stamp where and how it will be signed, and a
dragged stamp must be stored as the frame fraction that `firma_pdf.rect_sello`
turns back into the dropped box. Node runs ui/geometria.js on a table of cases
and the results are compared with firma_pdf within 0.01 pt.

The second half loads a real PDF with the vendored pdf.js (the viewer the app
uses), so the screen <-> PDF conversion of the preview and of a drag is the real
one, including CropBoxes with a nonzero origin.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import firma_pdf
from conftest import construir_pdf
from firma_pdf import PosicionRelativa

RAIZ = Path(__file__).resolve().parent.parent
UI = RAIZ / "ui"
PDFJS = UI / "vendor" / "pdfjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not on PATH; the UI parity test runs ui/geometria.js with node")

TOL = 0.01
ESCALA = 1.5  # px per pt of the pdf.js viewports in the script
# (name, MediaBox, CropBox or None, /Rotate): the page classes of the field
# document plus a CropBox with a nonzero origin in every rotation.
PAGINAS = [
    ("A4-r0", (0, 0, 595, 842), None, 0),
    ("A4-horizontal-r270", (0, 0, 842, 595), None, 270),
    ("A3-r270", (0, 0, 842, 1191), None, 270),
    ("A3-r90", (0, 0, 842, 1191), None, 90),
    ("A3-horizontal-r0", (0, 0, 1191, 842), None, 0),
    ("Carta-horizontal-r0", (0, 0, 792, 612), None, 0),
    ("recorte-r0", (0, 0, 700, 950), (30, 40, 625, 882), 0),
    ("recorte-r90", (0, 0, 700, 950), (30, 40, 625, 882), 90),
    ("recorte-r180", (0, 0, 700, 950), (30, 40, 625, 882), 180),
    ("recorte-r270", (0, 0, 700, 950), (30, 40, 625, 882), 270),
    ("recorte-horizontal-r90", (0, 0, 950, 700), (40, 30, 882, 625), 90),
]
# None = the type's default position.
POSICIONES = [None, (0.2, 0.7), (0.5, 0.25), (0.0, 0.0), (1.0, 1.0), (0.93, 0.02)]
PUNTOS = [(0, 0), (1, 0), (0, 1), (1, 1), (0.25, 0.6), (0.9, 0.1)]
ARRASTRE = (-37.5, -60.0)  # px moved by a simulated drag, kept inside the page


def _vista(media, recorte):
    if recorte is None:
        return media
    return (max(media[0], recorte[0]), max(media[1], recorte[1]), min(media[2], recorte[2]), min(media[3], recorte[3]))


def _posicion(pos, tipo):
    return firma_pdf.posicion_predeterminada(tipo) if pos is None else PosicionRelativa(*pos)


SCRIPT = r"""
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import * as g from "./geometria.mjs";

// pdf.js 5 uses Uint8Array.prototype.toHex, which this Node lacks.
if (!Uint8Array.prototype.toHex) {
  Uint8Array.prototype.toHex = function () {
    return Array.from(this, (b) => b.toString(16).padStart(2, "0")).join("");
  };
}

const entrada = JSON.parse(readFileSync(process.argv[2], "utf8"));
const salida = {
  tamanos: g.TAMANOS_SELLO,
  predeterminadas: g.POSICIONES_PREDETERMINADAS,
  fracciones: [],
  puras: [],
  pdfjs: [],
};

for (const giro of [0, 90, 180, 270]) {
  for (const [x, y] of entrada.puntos) {
    const marco = g.fraccionEnMarco(x, y, giro);
    salida.fracciones.push({ giro, x, y, marco, vuelta: g.fraccionSinRotar(marco[0], marco[1], giro) });
  }
}

const posicionDe = (pos, tipo) => (pos === null ? g.POSICIONES_PREDETERMINADAS[tipo] : { x: pos[0], y: pos[1] });

for (const p of entrada.paginas) {
  const fila = { nombre: p.nombre, giro: g.giroDeSello(p.vista, p.rotacion), pantalla: g.giroEnPantalla(p.vista, p.rotacion), casos: [] };
  for (const tipo of entrada.tipos) {
    for (const pos of entrada.posiciones) {
      const rect = g.rectSello(posicionDe(pos, tipo), p.vista, tipo, p.rotacion);
      fila.casos.push({ tipo, pos, rect, guardada: g.posicionDeRect(rect, p.vista, p.rotacion) });
    }
  }
  salida.puras.push(fila);
}

const pdfjs = await import(pathToFileURL(entrada.pdfjs + "/pdf.min.mjs").href);
pdfjs.GlobalWorkerOptions.workerSrc = pathToFileURL(entrada.pdfjs + "/pdf.worker.min.mjs").href;
const doc = await pdfjs.getDocument({ data: new Uint8Array(readFileSync(entrada.pdf)), isEvalSupported: false }).promise;
for (let n = 1; n <= doc.numPages; n++) {
  const pagina = await doc.getPage(n);
  const vp = pagina.getViewport({ scale: entrada.escala });
  const fila = { view: pagina.view, rotate: pagina.rotate, ancho: vp.width, alto: vp.height, casos: [] };
  for (const tipo of entrada.tipos) {
    for (const pos of entrada.posiciones) {
      const rect = g.rectSello(posicionDe(pos, tipo), pagina.view, tipo, pagina.rotate);
      const px = g.rectEnPixeles(vp, rect);
      const guardada = g.posicionDeRect(g.rectDesdePixeles(vp, px), pagina.view, pagina.rotate);
      // Drag like app.js `mover`: shift in pixels, kept inside the page.
      const movido = {
        ...px,
        left: Math.min(Math.max(px.left + entrada.arrastre[0], 0), vp.width - px.width),
        top: Math.min(Math.max(px.top + entrada.arrastre[1], 0), vp.height - px.height),
      };
      const soltado = g.rectDesdePixeles(vp, movido);
      const arrastrada = g.posicionDeRect(soltado, pagina.view, pagina.rotate);
      fila.casos.push({ tipo, pos, rect, px, guardada, soltado, arrastrada });
    }
  }
  salida.pdfjs.push(fila);
}
process.stdout.write(JSON.stringify(salida));
process.exit(0);
"""


@pytest.fixture(scope="module")
def resultado(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("paridad")
    shutil.copyfile(UI / "geometria.js", carpeta / "geometria.mjs")
    (carpeta / "paridad.mjs").write_text(SCRIPT, encoding="utf-8")
    pdf = carpeta / "paginas.pdf"
    pdf.write_bytes(
        construir_pdf(
            [
                {"media_box": media, "crop_box": recorte, "rotacion": rotacion, "contenido": False}
                for _, media, recorte, rotacion in PAGINAS
            ]
        )
    )
    entrada = {
        "puntos": PUNTOS,
        "tipos": list(firma_pdf.TIPOS_SELLO),
        "posiciones": POSICIONES,
        "paginas": [
            {"nombre": nombre, "vista": _vista(media, recorte), "rotacion": rotacion}
            for nombre, media, recorte, rotacion in PAGINAS
        ],
        "pdf": str(pdf),
        "pdfjs": str(PDFJS),
        "escala": ESCALA,
        "arrastre": ARRASTRE,
    }
    (carpeta / "entrada.json").write_text(json.dumps(entrada), encoding="utf-8")
    proceso = subprocess.run(
        [NODE, str(carpeta / "paridad.mjs"), str(carpeta / "entrada.json")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=carpeta,
    )
    assert proceso.returncode == 0, proceso.stderr
    return json.loads(proceso.stdout)


def _cerca(a, b):
    return all(abs(x - y) <= TOL for x, y in zip(a, b, strict=True))


def test_tamanos_y_posiciones_predeterminadas_son_las_de_firma_pdf(resultado):
    for tipo, (ancho, alto) in firma_pdf.TAMANOS_SELLO.items():
        assert resultado["tamanos"][tipo] == {"ancho": ancho, "alto": alto}
        defecto = firma_pdf.posicion_predeterminada(tipo)
        assert _cerca((resultado["predeterminadas"][tipo]["x"], resultado["predeterminadas"][tipo]["y"]), (defecto.x, defecto.y))


def test_fracciones_del_marco_igual_que_python_y_de_ida_y_vuelta(resultado):
    assert len(resultado["fracciones"]) == 4 * len(PUNTOS)
    for f in resultado["fracciones"]:
        assert _cerca(f["marco"], firma_pdf.fraccion_en_marco(f["x"], f["y"], f["giro"])), f
        assert _cerca(f["vuelta"], (f["x"], f["y"])), f
        assert _cerca(firma_pdf.fraccion_sin_rotar(*f["marco"], f["giro"]), (f["x"], f["y"])), f


def test_giro_igual_que_python_y_cubre_los_cuatro(resultado):
    giros = set()
    for (nombre, media, recorte, rotacion), fila in zip(PAGINAS, resultado["puras"], strict=True):
        vista = _vista(media, recorte)
        assert fila["giro"] == firma_pdf.giro_de_sello(vista, rotacion), nombre
        giros.add(fila["giro"])
    assert giros == {0, 90, 180, 270}


def test_en_pantalla_el_sello_queda_derecho_o_vertical_segun_la_pagina(resultado):
    """Content turn on screen (clockwise): 0 on pages displayed in portrait, 270 (reads upward) in landscape."""
    for (nombre, media, recorte, rotacion), fila in zip(PAGINAS, resultado["puras"], strict=True):
        x0, y0, x1, y1 = _vista(media, recorte)
        ancho, alto = (x1 - x0, y1 - y0) if rotacion in (0, 180) else (y1 - y0, x1 - x0)
        assert fila["pantalla"] == (0 if alto >= ancho else 270), nombre


def test_rect_igual_que_rect_sello_en_cada_clase_tipo_y_posicion(resultado):
    for (nombre, media, recorte, rotacion), fila in zip(PAGINAS, resultado["puras"], strict=True):
        vista = _vista(media, recorte)
        assert len(fila["casos"]) == len(firma_pdf.TIPOS_SELLO) * len(POSICIONES)
        for caso in fila["casos"]:
            esperado = firma_pdf.rect_sello(_posicion(caso["pos"], caso["tipo"]), vista, caso["tipo"], rotacion=rotacion)
            assert _cerca(caso["rect"], esperado), (nombre, caso)


def test_posicion_guardada_reproduce_el_mismo_rect_en_python(resultado):
    for (nombre, media, recorte, rotacion), fila in zip(PAGINAS, resultado["puras"], strict=True):
        vista = _vista(media, recorte)
        for caso in fila["casos"]:
            guardada = PosicionRelativa(caso["guardada"]["x"], caso["guardada"]["y"])
            assert 0 <= guardada.x <= 1 and 0 <= guardada.y <= 1
            assert _cerca(firma_pdf.rect_sello(guardada, vista, caso["tipo"], rotacion=rotacion), caso["rect"]), (nombre, caso)
            pos = caso["pos"]
            if pos is not None and 0 < pos[0] < 0.5 and 0 < pos[1] < 0.5:
                # Unclamped position: it comes back unchanged.
                assert _cerca((guardada.x, guardada.y), pos), (nombre, caso)


def test_pdfjs_ve_la_misma_vista_y_rotacion_que_python(resultado):
    for (nombre, media, recorte, rotacion), fila in zip(PAGINAS, resultado["pdfjs"], strict=True):
        assert _cerca(fila["view"], _vista(media, recorte)), nombre
        assert fila["rotate"] == rotacion, nombre


def test_vista_previa_con_pdfjs_muestra_el_rect_firmado(resultado):
    """The preview box (pixels) comes from the signed rect, and is horizontal or vertical as displayed."""
    for (nombre, media, recorte, rotacion), fila in zip(PAGINAS, resultado["pdfjs"], strict=True):
        vista = _vista(media, recorte)
        vertical = fila["alto"] >= fila["ancho"]  # page displayed in portrait
        for caso in fila["casos"]:
            esperado = firma_pdf.rect_sello(_posicion(caso["pos"], caso["tipo"]), vista, caso["tipo"], rotacion=rotacion)
            assert _cerca(caso["rect"], esperado), (nombre, caso)
            ancho, alto = firma_pdf.TAMANOS_SELLO[caso["tipo"]]
            tamano = (ancho * ESCALA, alto * ESCALA) if vertical else (alto * ESCALA, ancho * ESCALA)
            assert _cerca((caso["px"]["width"], caso["px"]["height"]), tamano), (nombre, caso)
            assert -TOL <= caso["px"]["left"] and caso["px"]["left"] + caso["px"]["width"] <= fila["ancho"] + TOL
            assert -TOL <= caso["px"]["top"] and caso["px"]["top"] + caso["px"]["height"] <= fila["alto"] + TOL


def test_posicion_por_defecto_en_pantalla_abajo_derecha_o_margen_derecho(resultado):
    for (nombre, *_), fila in zip(PAGINAS, resultado["pdfjs"], strict=True):
        vertical = fila["alto"] >= fila["ancho"]
        for caso in fila["casos"]:
            if caso["pos"] is not None:
                continue
            px = caso["px"]
            derecha, abajo = fila["ancho"] - px["left"] - px["width"], fila["alto"] - px["top"] - px["height"]
            # The default gap is 30/842 of the displayed height (portrait: above
            # the bottom edge) or of the displayed width (landscape: from the
            # right edge), as on A4.
            if vertical:
                assert abs(abajo - 30 / 842 * fila["alto"]) <= TOL * ESCALA, (nombre, caso)
            else:
                assert abs(derecha - 30 / 842 * fila["ancho"]) <= TOL * ESCALA, (nombre, caso)


def test_arrastre_con_pdfjs_guarda_la_fraccion_que_python_vuelve_a_poner_alli(resultado):
    for (nombre, media, recorte, rotacion), fila in zip(PAGINAS, resultado["pdfjs"], strict=True):
        vista = _vista(media, recorte)
        for caso in fila["casos"]:
            # Without moving, the stored position lays the same box out again.
            guardada = PosicionRelativa(caso["guardada"]["x"], caso["guardada"]["y"])
            assert _cerca(firma_pdf.rect_sello(guardada, vista, caso["tipo"], rotacion=rotacion), caso["rect"]), (nombre, caso)
            # After a drag, Python signs exactly the dropped box.
            arrastrada = PosicionRelativa(caso["arrastrada"]["x"], caso["arrastrada"]["y"])
            soltado = firma_pdf.rect_sello(arrastrada, vista, caso["tipo"], rotacion=rotacion)
            assert _cerca(soltado, caso["soltado"]), (nombre, caso)


# --- app.js uses geometria.js (static checks; app.js needs a browser) -------------------


def _app_js():
    return (UI / "app.js").read_text(encoding="utf-8")


def test_app_js_importa_la_geometria_y_no_tiene_una_copia_propia():
    import re

    js = _app_js()
    importacion = re.search(r'import \{([^}]*)\} from "\./geometria\.js";', js)
    assert importacion, "app.js must import the stamp geometry from ./geometria.js"
    exportados = set(re.findall(r"export (?:const|function) (\w+)", (UI / "geometria.js").read_text(encoding="utf-8")))
    importados = {n.split()[0] for n in importacion.group(1).split(",") if n.strip()}
    assert importados <= exportados
    assert "function rectPdf" not in js  # the old unrotated copy of rect_sello
    assert "convertToPdfPoint" not in js and "convertToViewportPoint" not in js


def test_app_js_vista_previa_y_arrastre_usan_la_rotacion_de_cada_pagina():
    import re

    js = _app_js()
    texto = re.search(r"function ajustarTextoSello\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert "giroEnPantalla(" in texto
    render = re.search(r"async function renderizar\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert "estado.rotacionPdf = rotacionDePagina(estado.pagina" in render
    marca = re.search(r"function actualizarMarca\(n\) \{(.*?)\n\}", js, re.S).group(1)
    assert "rotacionDePagina(n" in marca
    assert re.search(r"function pixelesACaja\([^)]*rotacion = estado\.rotacionPdf\)", js)
