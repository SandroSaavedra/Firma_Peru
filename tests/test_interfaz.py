"""
Comprobaciones estáticas de la interfaz (ui/index.html y ui/app.js): no se
ejecuta JavaScript, solo se revisan los identificadores y los controles.
"""
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"


def _html() -> str:
    return (UI / "index.html").read_text(encoding="utf-8")


def _js() -> str:
    return (UI / "app.js").read_text(encoding="utf-8")


def _ids_html() -> set[str]:
    return set(re.findall(r'\bid="([^"]+)"', _html()))


def test_cada_id_usado_en_app_js_existe_en_index_html():
    usados = set(re.findall(r'\$\("([^"]+)"\)', _js()))
    assert usados and usados <= _ids_html(), usados - _ids_html()


def test_el_logo_se_muestra_pero_no_se_puede_cambiar():
    ids = _ids_html()
    assert {"campo-logo", "logo-vista", "logo-img", "logo-vacio", "sello-logo"} <= ids
    assert "btn-logo" not in ids and "btn-logo-texto" not in ids and "btn-quitar-logo" not in ids
    html, js = _html(), _js()
    for texto in ("Elegir logo", "Cambiar logo", "Quitar logo"):
        assert texto not in html and texto not in js
    assert "elegir_logo" not in js and "quitar_logo" not in js


def test_la_imagen_de_visto_bueno_se_puede_elegir_y_quitar():
    assert {"btn-imagen-vb", "btn-imagen-vb-texto", "btn-quitar-imagen-vb", "imagen-vb-img"} <= _ids_html()


def test_ya_no_existe_el_interruptor_de_posicion_propia():
    html, js = _html(), _js()
    assert "chk-posicion-propia" not in html and "campo-posicion-propia" not in html
    assert "Posición propia para este documento" not in html
    assert "chkPosicionPropia" not in js and "campoPosicionPropia" not in js


def test_boton_aplicar_posicion_a_todos_existe_y_esta_conectado():
    html, js = _html(), _js()
    boton = re.search(r'<button[^>]*\bid="btn-posicion-todos"[^>]*>(.*?)</button>', html, re.S)
    assert boton, "falta el botón btn-posicion-todos"
    assert "Aplicar esta posición a todos" in boton.group(1)
    assert '$("btn-posicion-todos")' in js
    assert re.search(r"el\.btnPosicionTodos\.addEventListener\(\s*\"click\"", js)


def test_mover_el_sello_solo_cambia_la_posicion_del_documento_abierto():
    js = _js()
    cuerpo = re.search(r"function confirmarCaja\(\) \{(.*?)\n\}", js, re.S)
    assert cuerpo, "falta confirmarCaja"
    assert "estado.caja" not in cuerpo.group(1)
    assert "guardarPreferencia" not in cuerpo.group(1)
    assert "ajusteEditable()" in cuerpo.group(1)
