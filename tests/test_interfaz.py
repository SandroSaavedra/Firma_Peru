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


def _boton(html: str, id_: str) -> str:
    boton = re.search(rf'<button[^>]*\bid="{id_}"[^>]*>(.*?)</button>', html, re.S)
    assert boton, f"falta el botón {id_}"
    return boton.group(1).strip()


def test_boton_aplicar_a_todos_los_documentos_existe_y_esta_conectado():
    html, js = _html(), _js()
    assert _boton(html, "btn-posicion-todos") == "Aplicar a todos los documentos"
    assert '$("btn-posicion-todos")' in js
    assert re.search(r"el\.btnPosicionTodos\.addEventListener\(\s*\"click\"", js)


def test_boton_aplicar_a_todas_las_paginas_existe_y_esta_conectado():
    html, js = _html(), _js()
    assert _boton(html, "btn-posicion-paginas") == "Aplicar a todas las páginas"
    assert '$("btn-posicion-paginas")' in js
    assert re.search(r"el\.btnPosicionPaginas\.addEventListener\(\s*\"click\"", js)
    # Ambos botones se desactivan mientras se firma o sin documento abierto.
    cuerpo = re.search(r"function actualizarBotonFirmar\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert "el.btnPosicionPaginas.disabled" in cuerpo and "el.btnPosicionTodos.disabled" in cuerpo


def test_ya_no_existe_el_texto_aplicar_esta_posicion_a_todos():
    html, js = _html(), _js()
    assert "Aplicar esta posición a todos" not in html and "Aplicar esta posición a todos" not in js


def test_posiciones_por_pagina_se_envian_a_python_en_ambas_firmas():
    js = _js()
    lote = re.search(r"function ajustesParaPython\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert "posiciones_paginas" in lote
    uno = re.search(r"async function firmar\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert "posiciones_paginas" in uno


def test_mover_el_sello_solo_cambia_la_posicion_del_documento_abierto():
    js = _js()
    cuerpo = re.search(r"function confirmarCaja\(\) \{(.*?)\n\}", js, re.S)
    assert cuerpo, "falta confirmarCaja"
    assert "estado.caja" not in cuerpo.group(1)
    assert "guardarPreferencia" not in cuerpo.group(1)
    assert "ajusteEditable()" in cuerpo.group(1)


def test_mover_el_sello_solo_cambia_la_posicion_de_la_pagina_a_la_vista():
    js = _js()
    cuerpo = re.search(r"function confirmarCaja\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert "cajasPaginas" in cuerpo and "estado.pagina" in cuerpo
    assert ".caja =" not in cuerpo  # la posición del documento no cambia


# --- registro de diagnóstico ----------------------------------------------------


def test_enlace_abrir_registro_existe_y_esta_conectado():
    html, js = _html(), _js()
    assert _boton(html, "btn-abrir-registro") == "Abrir registro de diagnóstico"
    assert '$("btn-abrir-registro")' in js
    assert re.search(r"el\.btnAbrirRegistro\.addEventListener\(\s*\"click\",\s*abrirRegistro", js)
    cuerpo = re.search(r"async function abrirRegistro\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert 'llamar("abrir_carpeta_registros")' in cuerpo


def test_los_dialogos_de_error_ofrecen_abrir_registro():
    js = _js()
    assert re.search(r'const ACCION_ABRIR_REGISTRO = \{ texto: "Abrir registro", icono: "\w+", accion: abrirRegistro \}', js)
    uno = re.search(r"async function firmar\(\) \{(.*?)\n\}", js, re.S).group(1)
    lote = re.search(r"async function firmarLote\(\) \{(.*?)\n\}", js, re.S).group(1)
    resumen = re.search(r"function mostrarResumenLote\(r\) \{(.*?)\n\}", js, re.S).group(1)
    for cuerpo in (uno, lote, resumen):
        assert "ACCION_ABRIR_REGISTRO" in cuerpo


def test_la_interfaz_envia_eventos_al_registro_sin_romperse():
    js = _js()
    cuerpo = re.search(r"function registrar\(nivel, mensaje\) \{(.*?)\n\}", js, re.S).group(1)
    assert "registrar_evento" in cuerpo and "try" in cuerpo and "catch" in cuerpo
    # Clic en Firmar con el modo del plan.
    uno = re.search(r"async function firmar\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert re.search(r'registrar\("info", `Clic en Firmar', uno)
    # Botón desactivado: solo cuando cambia el motivo.
    boton = re.search(r"function actualizarBotonFirmar\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert "motivoBotonDesactivado(plan)" in boton and "ultimoMotivoBloqueo" in boton
    motivos = re.search(r"function motivoBotonDesactivado\(plan\) \{(.*?)\n\}", js, re.S).group(1)
    for texto in ("estado.firmando", "!estado.info", "docmdp === 1", "actualYaFirmado()"):
        assert texto in motivos
    # Cada diálogo de error queda registrado con su mensaje.
    dialogo = re.search(r"function mostrarDialogo\(.*?\) \{(.*?)\n\}", js, re.S).group(1)
    assert re.search(r'tipo === "error"\) registrar\("error"', dialogo)


# --- firma real en cada página (un solo PIN) ---------------------------------


def _funcion(js: str, firma: str) -> str:
    cuerpo = re.search(rf"{re.escape(firma)} \{{(.*?)\n\}}", js, re.S)
    assert cuerpo, f"falta {firma}"
    return cuerpo.group(1)


def test_tercera_opcion_firma_real_en_cada_pagina():
    html = _html()
    assert re.search(
        r'<input type="radio" name="paginas" value="cada_pagina"[^>]*><span>Firma real en cada página</span>', html
    )
    assert "Una firma digital por página; pide el PIN una sola vez." in html
    js = _js()
    assert '"cada_pagina"' in js
    # el aviso corto se muestra solo con esa opción
    assert '$("txt-paginas-ayuda")' in js and 'id="txt-paginas-ayuda"' in html


def test_dialogo_del_pin_es_un_campo_de_contrasena():
    html, js = _html(), _js()
    campo = re.search(r'<input[^>]*\bid="inp-pin"[^>]*>', html)
    assert campo, "falta el campo del PIN"
    assert 'type="password"' in campo.group(0) and 'autocomplete="off"' in campo.group(0)
    assert {"dialogo-pin", "inp-pin"} <= _ids_html()
    # Enter confirma; Cancelar / Escape cierran; el campo se vacía siempre.
    assert re.search(r'el\.inpPin\.addEventListener\(\s*"keydown".{0,200}?"Enter".{0,200}?aceptarPin\(\)', js, re.S)
    assert 'el.inpPin.value = ""' in _funcion(js, "function cerrarDialogo()")
    assert 'el.inpPin.value = ""' in _funcion(js, "function aceptarPin()")


def test_el_pin_no_se_guarda_en_la_interfaz():
    js = _js()
    assert not re.search(r"estado\.pin\b", js)
    assert not re.search(r"guardarPreferencia\([^)]*pin", js, re.I)
    assert not re.search(r"registrar\([^)]*\bpin\b[^)]*\)", _funcion(js, "async function firmar()"))
    # el PIN solo viaja dentro de la llamada a Python, con «cada_pagina»
    assert re.search(r'llamar\("firmar", pin === null \? opciones : \{ \.\.\.opciones, pin \}\)', js)
    assert re.search(r'llamar\("firmar_lote", pin === null \? opciones : \{ \.\.\.opciones, pin \}\)', js)


def test_el_pin_se_pide_una_vez_por_documento_o_por_lote():
    js = _js()
    for firma in ("async function firmar()", "async function firmarLote()"):
        cuerpo = _funcion(js, firma)
        assert cuerpo.count("pedirPin(") == 1, firma
        assert '"cada_pagina"' in cuerpo, firma


def test_avance_por_pagina_con_cancelar():
    js = _js()
    assert 'evento.evento === "paginas"' in _funcion(js, "function progresoLote(evento)")
    assert "Firmando página ${" in js
    dialogo = _funcion(js, "function mostrarDialogoLote()")
    assert "Cancelar" in dialogo and "cancelarLote" in dialogo
    assert 'llamar("cancelar_lote")' in js
