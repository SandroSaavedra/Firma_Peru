"""
Pruebas de la firma real en cada página por partes: un documento de más de
`PAGINAS_POR_PARTE` páginas se divide en partes de páginas consecutivas y se
firma cada página de cada parte, con la misma sesión de firma.

`PAGINAS_POR_PARTE` se reduce a 3 para que las pruebas sean rápidas.
"""
import io
from pathlib import Path

import pytest

import firma_pdf
import validacion
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.generic import pdf_name
from pyhanko.pdf_utils.writer import PageObject, PdfFileWriter

from conftest import construir_pdf
from firma_pdf import ErrorFirma, FirmaCancelada, OpcionesFirma, firmar_pdf
from test_firma_por_pagina import _campos_por_pagina, _leer, _rect, _validas_con_app

PAGINAS_POR_PARTE_ORIGINAL = getattr(firma_pdf, "PAGINAS_POR_PARTE", None)


@pytest.fixture(autouse=True)
def partes_de_3(monkeypatch):
    monkeypatch.setattr(firma_pdf, "PAGINAS_POR_PARTE", 3, raising=False)


def _vista(indice):
    """Tamaño propio de cada página: así se comprueba el orden de las páginas en cada parte."""
    return (0, 0, 500 + 10 * indice, 700 + 5 * indice)


def _pdf(tmp_path, paginas, nombre="expediente.pdf") -> Path:
    ruta = tmp_path / nombre
    ruta.write_bytes(construir_pdf([{"media_box": _vista(i)} for i in range(paginas)]))
    return ruta


def _paginas(ruta):
    reader = _leer(ruta)
    total = int(reader.root["/Pages"]["/Count"])
    return [tuple(float(v) for v in firma_pdf.vista_de_pagina(reader, i)) for i in range(total)]


def _contenido(carpeta):
    return sorted(p.name for p in carpeta.iterdir())


def test_el_limite_es_de_75_paginas():
    assert PAGINAS_POR_PARTE_ORIGINAL == 75


def test_rangos_de_paginas_consecutivas():
    assert firma_pdf.rangos_de_partes(7) == [(0, 3), (3, 6), (6, 7)]
    assert firma_pdf.rangos_de_partes(6) == [(0, 3), (3, 6)]
    assert firma_pdf.rangos_de_partes(3) == [(0, 3)]


@pytest.mark.parametrize(
    "salida, partes, carpeta, primero",
    [
        ("expediente_firmado.pdf", 3, "expediente_firmado", "expediente_parte01de03_firmado.pdf"),
        ("expediente_vb.pdf", 12, "expediente_vb", "expediente_parte01de12_vb.pdf"),
        ("expediente_firmado (2).pdf", 2, "expediente_firmado (2)", "expediente_parte01de02_firmado.pdf"),
        ("elegido.pdf", 120, "elegido_firmado", "elegido_parte001de120_firmado.pdf"),
    ],
)
def test_nombres_de_la_carpeta_y_de_las_partes(tmp_path, salida, partes, carpeta, primero):
    ruta_carpeta, nombres = firma_pdf.nombres_de_partes(tmp_path / salida, partes)
    assert ruta_carpeta == tmp_path / carpeta
    assert len(nombres) == partes and nombres[0] == primero
    assert len(set(nombres)) == partes


def test_7_paginas_en_partes_de_3_firma_cada_pagina(tmp_path, firmante, material_prueba):
    entrada = _pdf(tmp_path, 7)
    salida = tmp_path / "expediente_firmado.pdf"
    resultado = firmar_pdf(entrada, salida, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"))

    carpeta = tmp_path / "expediente_firmado"
    nombres = [f"expediente_parte0{n}de03_firmado.pdf" for n in (1, 2, 3)]
    assert resultado.carpeta == str(carpeta) and resultado.salida == str(carpeta)
    assert resultado.partes == [str(carpeta / n) for n in nombres]
    assert resultado.total_paginas == 7 and resultado.paginas_selladas == list(range(7))
    assert len(resultado.campos) == 7
    assert _contenido(carpeta) == nombres
    # Nada más junto al original: ni un PDF suelto ni temporales.
    assert _contenido(tmp_path) == sorted(["AppData", "expediente.pdf", "expediente_firmado"])

    # Páginas consecutivas: 3 + 3 + 1, en el mismo orden que el original.
    vistas = [_vista(i) for i in range(7)]
    assert [_paginas(carpeta / n) for n in nombres] == [vistas[0:3], vistas[3:6], vistas[6:7]]
    for nombre, cantidad in zip(nombres, (3, 3, 1)):
        ruta = carpeta / nombre
        assert len(_validas_con_app(ruta, material_prueba)) == cantidad
        por_pagina = _campos_por_pagina(_leer(ruta))
        assert sorted(por_pagina) == list(range(cantidad))  # cada página con su propia firma
        assert all(len(campos) == 1 for campos in por_pagina.values())


def test_avance_por_paginas_del_documento_y_por_parte(tmp_path, firmante):
    entrada = _pdf(tmp_path, 7)
    avance = []

    def progreso(hechas, total, parte=None, partes=None):
        avance.append((hechas, total, parte, partes))

    firmar_pdf(entrada, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"), progreso=progreso)
    assert avance == [
        (0, 7, 1, 3), (1, 7, 1, 3), (2, 7, 1, 3), (3, 7, 1, 3),
        (3, 7, 2, 3), (4, 7, 2, 3), (5, 7, 2, 3), (6, 7, 2, 3),
        (6, 7, 3, 3), (7, 7, 3, 3),
    ]


def test_posiciones_por_pagina_se_trasladan_a_cada_parte(tmp_path, firmante):
    entrada = _pdf(tmp_path, 7)
    posicion = firma_pdf.PosicionRelativa(x=0.1, y=0.1)
    propias = {
        1: firma_pdf.PosicionRelativa(x=0.5, y=0.5),
        4: firma_pdf.PosicionRelativa(x=0.2, y=0.7),
        6: firma_pdf.PosicionRelativa(x=0.6, y=0.3),
    }
    resultado = firmar_pdf(
        entrada,
        tmp_path / "s.pdf",
        firmante,
        "PRUEBA",
        OpcionesFirma(posicion=posicion, posiciones_paginas=propias, paginas="cada_pagina", pagina_firma=5),
    )
    for numero, ruta in enumerate(resultado.partes):
        por_pagina = _campos_por_pagina(_leer(ruta))
        for local, (campo,) in por_pagina.items():
            original = numero * 3 + local
            esperado = firma_pdf.rect_sello(propias.get(original, posicion), _vista(original), "firma")
            assert _rect(campo) == pytest.approx(esperado, abs=0.01), (numero, local)


def test_segundo_firmante_en_una_parte_mantiene_validas_todas(tmp_path, firmante, material_prueba):
    entrada = _pdf(tmp_path, 7)
    primera = firmar_pdf(entrada, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"))
    parte = Path(primera.partes[1])
    segunda = tmp_path / "segunda.pdf"
    resultado = firmar_pdf(
        parte, segunda, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina", tipo="visto_bueno")
    )
    assert resultado.firmas_previas == 3 and resultado.partes == [] and resultado.carpeta is None
    assert len(_validas_con_app(segunda, material_prueba)) == 6
    assert all(len(c) == 2 for c in _campos_por_pagina(_leer(segunda)).values())


def test_cancelar_a_mitad_de_una_parte_no_deja_carpeta(tmp_path, firmante):
    entrada = _pdf(tmp_path, 7)

    def progreso(hechas, total, parte=None, partes=None):
        return hechas < 4  # «Cancelar» tras la primera página de la segunda parte

    with pytest.raises(FirmaCancelada):
        firmar_pdf(entrada, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"), progreso=progreso)
    assert _contenido(tmp_path) == sorted(["AppData", "expediente.pdf"])


def test_error_a_mitad_no_deja_carpeta(tmp_path, firmante, monkeypatch):
    entrada = _pdf(tmp_path, 7)
    original = type(firmante)._firmar_resumen
    llamadas = []

    def _falla_en_la_quinta(self, resumen, algoritmo):
        llamadas.append(1)
        if len(llamadas) == 5:
            raise RuntimeError("la tarjeta falló")
        return original(self, resumen, algoritmo)

    monkeypatch.setattr(type(firmante), "_firmar_resumen", _falla_en_la_quinta)
    with pytest.raises(RuntimeError):
        firmar_pdf(entrada, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"))
    assert len(llamadas) == 5  # se detiene en la primera falla: no sigue ni reintenta
    assert _contenido(tmp_path) == sorted(["AppData", "expediente.pdf"])


def test_carpeta_existente_no_se_reemplaza(tmp_path, firmante):
    entrada = _pdf(tmp_path, 4)
    existente = tmp_path / "expediente_firmado"
    existente.mkdir()
    (existente / "otro.pdf").write_bytes(b"anterior")
    resultado = firmar_pdf(
        entrada, tmp_path / "expediente_firmado.pdf", firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina")
    )
    assert resultado.carpeta == str(tmp_path / "expediente_firmado (2)")
    assert _contenido(Path(resultado.carpeta)) == [
        "expediente_parte01de02_firmado.pdf", "expediente_parte02de02_firmado.pdf",
    ]
    assert _contenido(existente) == ["otro.pdf"]


def test_documento_firmado_de_mas_paginas_se_rechaza_sin_firmar(tmp_path, firmante, monkeypatch):
    entrada = _pdf(tmp_path, 4)
    firmado = tmp_path / "firmado.pdf"
    firmar_pdf(entrada, firmado, firmante, "PRUEBA", OpcionesFirma(paginas="ultima"))
    llamadas = []
    original = type(firmante)._firmar_resumen
    monkeypatch.setattr(
        type(firmante), "_firmar_resumen", lambda self, r, a: llamadas.append(1) or original(self, r, a)
    )
    antes = _contenido(tmp_path)

    with pytest.raises(ErrorFirma) as error:
        firmar_pdf(firmado, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"))
    assert str(error.value) == firma_pdf.mensaje_sin_division(4, 1)
    assert llamadas == [] and _contenido(tmp_path) == antes
    # Las otras opciones de páginas no dividen: siguen funcionando.
    firmar_pdf(firmado, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(paginas="ultima"))


def test_mensaje_del_rechazo(monkeypatch):
    monkeypatch.setattr(firma_pdf, "PAGINAS_POR_PARTE", 75)
    assert firma_pdf.mensaje_sin_division(76, 2) == (
        "Este PDF ya tiene firmas y más de 75 hojas: no se puede dividir sin invalidarlas. "
        "Pida al primer firmante el expediente dividido en partes."
    )
    assert firma_pdf.mensaje_sin_division(75, 2) is None  # el segundo firmante de una parte
    assert firma_pdf.mensaje_sin_division(500, 0) is None  # sin firmas: se divide


def test_hasta_el_limite_un_solo_archivo(tmp_path, firmante, material_prueba):
    entrada = _pdf(tmp_path, 3)
    salida = tmp_path / "expediente_firmado.pdf"
    avance = []
    resultado = firmar_pdf(
        entrada, salida, firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina"),
        progreso=lambda hechas, total: avance.append((hechas, total)),
    )
    assert resultado.salida == str(salida) and resultado.carpeta is None and resultado.partes == []
    assert avance == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert len(_validas_con_app(salida, material_prueba)) == 3
    assert _contenido(tmp_path) == sorted(["AppData", "expediente.pdf", "expediente_firmado.pdf"])


def test_las_otras_opciones_de_paginas_no_dividen(tmp_path, firmante):
    entrada = _pdf(tmp_path, 7)
    salida = tmp_path / "s.pdf"
    resultado = firmar_pdf(entrada, salida, firmante, "PRUEBA", OpcionesFirma(paginas="todas"))
    assert resultado.salida == str(salida) and resultado.partes == []
    assert len(validacion.validar_pdf(salida, permitir_red=False)) == 1


# --- T2: regresiones del sello en las partes (expediente real de 597 páginas) -------


A4 = (0, 0, 595.22, 842)
A3_VERTICAL = (0, 0, 842, 1191)
A3_HORIZONTAL = (0, 0, 1191, 842)
A4_GIRADA = (0, 0, 841.86, 595.26)  # se ve vertical con /Rotate 270


def _pdf_a_medida(ruta, paginas, heredado=None) -> Path:
    """
    PDF de prueba con páginas a medida. Cada página: media_box y, opcionales,
    crop_box, rotacion y annots_vacias (un arreglo /Annots VACÍO e indirecto,
    propio de la página, como en el expediente real). `heredado` (media_box,
    crop_box, rotacion) va en el nodo /Pages raíz y se quita de las páginas
    que no lo redefinen: las páginas lo heredan del árbol.
    """
    w = PdfFileWriter()
    for spec in paginas:
        flujo = w.add_object(generic.StreamObject(stream_data=b"0 0 1 rg 20 20 50 50 re f"))
        pagina = PageObject(contents=flujo, media_box=spec.get("media_box") or (0, 0, 1, 1))
        if "rotacion" in spec:
            pagina[pdf_name("/Rotate")] = generic.NumberObject(spec["rotacion"])
        if spec.get("crop_box"):
            pagina[pdf_name("/CropBox")] = generic.ArrayObject(map(generic.FloatObject, spec["crop_box"]))
        if spec.get("annots_vacias"):
            pagina[pdf_name("/Annots")] = w.add_object(generic.ArrayObject())
        if not spec.get("media_box"):
            del pagina["/MediaBox"]
        w.insert_page(pagina)
    if heredado:
        raiz = w.root["/Pages"]
        claves = {"media_box": "/MediaBox", "crop_box": "/CropBox"}
        for clave, nombre in claves.items():
            if clave in heredado:
                raiz[pdf_name(nombre)] = generic.ArrayObject(map(generic.FloatObject, heredado[clave]))
        if "rotacion" in heredado:
            raiz[pdf_name("/Rotate")] = generic.NumberObject(heredado["rotacion"])
    salida = io.BytesIO()
    w.write(salida)
    ruta.write_bytes(salida.getvalue())
    return ruta


def _logo(tmp_path, ancho=300, alto=200):
    from PIL import Image

    ruta = tmp_path / "logo.png"
    Image.new("RGB", (ancho, alto), (200, 30, 30)).save(ruta)
    return ruta


def _recursos_resueltos(objeto, archivo, vistos=None):
    """
    (categoría, nombre, objeto) de cada recurso alcanzable desde la apariencia
    `objeto` (XObject, fuentes y ExtGState, también dentro de los Form
    anidados). Falla si una referencia no existe en `archivo`.
    """
    vistos = vistos if vistos is not None else set()
    encontrados = []
    if "/Resources" not in objeto:
        return encontrados
    recursos = objeto["/Resources"]
    for categoria in ("/XObject", "/Font", "/ExtGState"):
        if categoria not in recursos:
            continue
        diccionario = recursos[categoria]
        for nombre in diccionario:
            crudo = diccionario.raw_get(nombre)
            destino = crudo.get_object()
            assert not isinstance(destino, generic.NullObject), f"{archivo}: {nombre} -> {crudo} no existe"
            encontrados.append((categoria, nombre, destino))
            if categoria == "/XObject" and destino.get("/Subtype") == "/Form":
                clave = getattr(crudo, "idnum", id(destino))
                if clave not in vistos:
                    vistos.add(clave)
                    encontrados += _recursos_resueltos(destino, archivo, vistos)
    return encontrados


def test_cada_parte_incrusta_su_propio_logo(tmp_path, firmante, material_prueba):
    """
    Regresión: el logo «incrustado una vez por documento» se reutilizaba entre
    partes; desde la parte 2 el sello apuntaba a un número de objeto de la
    parte 1 (inexistente o ajeno en la parte nueva) y el logo no se veía.
    """
    entrada = _pdf(tmp_path, 8)
    resultado = firmar_pdf(
        entrada, tmp_path / "s.pdf", firmante, "PRUEBA", OpcionesFirma(paginas="cada_pagina", logo=str(_logo(tmp_path)))
    )
    assert len(resultado.partes) == 3
    for ruta in resultado.partes:
        reader = _leer(ruta)
        logos = set()
        for firma in reader.embedded_signatures:
            recursos = _recursos_resueltos(firma.sig_field["/AP"]["/N"], ruta)
            imagenes = [obj for categoria, _, obj in recursos if obj.get("/Subtype") == "/Image"]
            assert [(int(i["/Width"]), int(i["/Height"])) for i in imagenes] == [(300, 200)], ruta
            for categoria, nombre, obj in recursos:
                if categoria == "/Font":
                    assert obj.get("/Type") == "/Font", (ruta, nombre)
                elif categoria == "/ExtGState":
                    assert obj.get("/Type") in (None, "/ExtGState"), (ruta, nombre)
                else:
                    assert obj.get("/Subtype") in ("/Image", "/Form"), (ruta, nombre)
            xobjetos = firma.sig_field["/AP"]["/N"]["/Resources"]["/XObject"]
            logos.update(xobjetos.raw_get(n).reference for n in xobjetos)
        assert len(logos) == 1, ruta  # dentro de cada parte el logo se incrusta una sola vez
        assert len(_validas_con_app(ruta, material_prueba)) == len(reader.embedded_signatures)


def _anotaciones_por_pagina(reader):
    total = int(reader.root["/Pages"]["/Count"])
    salida = []
    for i in range(total):
        pagina = reader.find_page_for_modification(i)[0].get_object()
        salida.append([a.get_object() for a in pagina["/Annots"]] if "/Annots" in pagina else [])
    return salida


def _dentro(rect, vista, tolerancia=0.01):
    return (
        rect[0] >= vista[0] - tolerancia
        and rect[1] >= vista[1] - tolerancia
        and rect[2] <= vista[2] + tolerancia
        and rect[3] <= vista[3] + tolerancia
    )


TAMANOS_MEZCLADOS = [
    {"media_box": A4, "annots_vacias": True},
    {"media_box": A3_VERTICAL, "rotacion": 270, "annots_vacias": True},
    {"media_box": A4, "annots_vacias": True},
    {"media_box": A3_HORIZONTAL, "annots_vacias": True},
    {"media_box": A4, "annots_vacias": True},
    {"media_box": A3_VERTICAL, "annots_vacias": True},
    {"media_box": A4_GIRADA, "rotacion": 270, "annots_vacias": True},
    {"media_box": A4, "annots_vacias": True},
    {"media_box": A3_VERTICAL, "rotacion": 90, "annots_vacias": True},
    {"media_box": A4, "annots_vacias": True},
]


def test_tamanos_mezclados_cada_pagina_solo_su_sello_y_dentro_de_su_vista(tmp_path, firmante, material_prueba):
    """
    Regresión: el expediente real tiene /Annots VACÍOS e indirectos en muchas
    páginas; al dividir, `compress_identical_objects` los fusionaba en UN solo
    arreglo compartido por varias páginas, y cada firma de esas páginas
    aparecía en todas: en una A4 se veían los sellos de las A3 (calculados
    para 842 pt de ancho), dibujados fuera de la página, a la derecha.
    Cada página de cada parte debe tener SOLO su propio widget, en la posición
    relativa pedida de SU vista y completamente dentro de ella.
    """
    entrada = _pdf_a_medida(tmp_path / "expediente.pdf", TAMANOS_MEZCLADOS)
    posicion = firma_pdf.posicion_predeterminada("firma")
    propias = {7: firma_pdf.PosicionRelativa(x=0.05, y=0.9), 8: firma_pdf.PosicionRelativa(x=0.99, y=0.99)}
    resultado = firmar_pdf(
        entrada,
        tmp_path / "s.pdf",
        firmante,
        "PRUEBA",
        OpcionesFirma(paginas="cada_pagina", logo=str(_logo(tmp_path)), posiciones_paginas=propias),
    )
    assert len(resultado.partes) == 4
    for numero, ruta in enumerate(resultado.partes):
        reader = _leer(ruta)
        anotaciones = _anotaciones_por_pagina(reader)
        por_pagina = _campos_por_pagina(reader)
        for local, anots in enumerate(anotaciones):
            original = numero * 3 + local
            spec = TAMANOS_MEZCLADOS[original]
            vista = tuple(float(v) for v in firma_pdf.vista_de_pagina(reader, local))
            assert vista == pytest.approx(spec["media_box"]), (ruta, local)
            assert len(anots) == 1, (ruta, local, [_rect(a) for a in anots])
            (campo,) = por_pagina[local]
            assert anots[0]["/T"] == campo["/T"]
            esperado = firma_pdf.rect_sello(propias.get(original, posicion), vista, "firma")
            assert _rect(campo) == pytest.approx(esperado, abs=0.01), (ruta, local)
            assert _dentro(_rect(campo), vista), (ruta, local)
        assert len(_validas_con_app(ruta, material_prueba)) == len(anotaciones)


def test_cajas_heredadas_y_origen_distinto_de_cero_se_conservan_en_las_partes(tmp_path, firmante):
    """
    /MediaBox, /CropBox y /Rotate heredados del árbol de páginas, y cajas con
    origen distinto de cero: cada página de cada parte conserva su vista y su
    rotación, y el sello (también con posición propia en una parte posterior)
    se calcula contra esa vista, igual que sin dividir.
    """
    heredado = {"media_box": (100, 200, 795, 1042), "crop_box": (120, 230, 760, 1000), "rotacion": 90}
    paginas = [
        {},  # todo heredado
        {"media_box": (-300, -400, 295, 442)},  # origen negativo, CropBox heredado fuera: vista = intersección
        {"crop_box": (150, 260, 500, 700)},
        {"media_box": (50, 60, 1241, 902), "rotacion": 0},
        {},
        {"media_box": (0, 0, 842, 1191), "crop_box": (10, 20, 832, 1181), "rotacion": 270},
        {},
    ]
    entrada = _pdf_a_medida(tmp_path / "expediente.pdf", paginas, heredado=heredado)
    fuente = _leer(entrada)
    vistas = [tuple(float(v) for v in firma_pdf.vista_de_pagina(fuente, i)) for i in range(len(paginas))]
    rotaciones = [firma_pdf._rotacion(fuente, i) for i in range(len(paginas))]
    assert vistas[0] == (120, 230, 760, 1000) and rotaciones[0] == 90 and rotaciones[3] == 0

    posicion = firma_pdf.PosicionRelativa(x=0.3, y=0.2)
    propias = {4: firma_pdf.PosicionRelativa(x=0.9, y=0.95), 6: firma_pdf.PosicionRelativa(x=0.0, y=0.0)}
    resultado = firmar_pdf(
        entrada,
        tmp_path / "s.pdf",
        firmante,
        "PRUEBA",
        OpcionesFirma(paginas="cada_pagina", posicion=posicion, posiciones_paginas=propias),
    )
    assert len(resultado.partes) == 3
    for numero, ruta in enumerate(resultado.partes):
        reader = _leer(ruta)
        por_pagina = _campos_por_pagina(reader)
        for local, (campo,) in por_pagina.items():
            original = numero * 3 + local
            vista = tuple(float(v) for v in firma_pdf.vista_de_pagina(reader, local))
            assert vista == pytest.approx(vistas[original]), (ruta, local)
            assert firma_pdf._rotacion(reader, local) == rotaciones[original], (ruta, local)
            esperado = firma_pdf.rect_sello(propias.get(original, posicion), vistas[original], "firma")
            assert _rect(campo) == pytest.approx(esperado, abs=0.01), (ruta, local)
            assert _dentro(_rect(campo), vista), (ruta, local)
