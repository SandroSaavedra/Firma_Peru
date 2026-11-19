"""
Núcleo de firma de PDF, independiente de la interfaz (CLI o escritorio).

Produce UNA sola firma criptográfica PAdES (subfilter ETSI.CAdES.detached)
con un campo de firma visible de UN solo widget, en la página de firma
(clic = estado de la firma en el lector). Con `paginas="todas"` cada una de
las demás páginas recibe, en la misma revisión (cubiertas por la firma):
  - una anotación /Stamp bloqueada (imprimible, solo lectura, bloqueada) que
    muestra el mismo XObject de apariencia del campo;
  - encima, una anotación /Link con el mismo /Rect y una acción JavaScript
    que en Acrobat muestra una ventana con el estado de ESA firma (otros
    lectores pueden ignorar el clic; Acrobat puede pedir permitir JavaScript).
Por qué no un widget por página: un campo de firma con varios widgets se
valida bien solo, pero en cuanto se agrega otra firma Acrobat marca la
anterior como «documento modificado» (comprobado en Acrobat). Con /Stamp +
/Link las firmas anteriores siguen válidas («se han efectuado cambios
posteriores»).

Convención de coordenadas (la interfaz debe usar la misma):
  - La posición del sello (su esquina inferior izquierda) se expresa en
    FRACCIONES (0..1) de la "vista" de la página: la intersección de /CropBox
    y /MediaBox (lo mismo que `page.view` en pdf.js). Origen abajo a la
    izquierda, espacio PDF SIN ROTAR.
  - Cada página puede tener su propia posición (`posiciones_paginas`); las
    demás usan la del documento. Cambia solo el /Rect del sello (widget del
    campo en la página de firma, /Stamp + /Link en las demás).
  - El tamaño del sello es fijo por tipo y en puntos PDF (TAMANOS_SELLO): mide
    lo mismo en cualquier tamaño de página. Si en la posición pedida no cabe,
    se desplaza hasta quedar dentro de la página.
  - En páginas con /Rotate distinto de 0 el sello se ubica en el lugar correcto
    (si la interfaz convierte con `viewport.convertToPdfPoint`), pero el texto
    del sello se dibuja con la orientación del espacio sin rotar, es decir,
    girado junto con la página.

PDF que ya tienen firmas: la nueva firma se agrega en una revisión
incremental con su propio campo y, con `paginas="todas"`, sus propios
/Stamp + /Link en las demás páginas. Un campo de firma nuevo y anotaciones
nuevas son cambios permitidos después de una firma de aprobación: Acrobat da
las firmas anteriores por válidas y `validacion.py` las reporta con cambios
permitidos (nivel ANNOTATIONS). Por eso se sella igual que en un PDF sin
firmas.
Documentos certificados (DocMDP): con nivel 1 (sin cambios) se rechaza la
operación. Con nivel 2 o 3 solo se coloca el campo de firma en su página
(modo "solo_campo"): el nivel 2 no permite anotaciones y el 3 se trata igual
por simplicidad.

Firma real en cada página (`paginas="cada_pagina"`): una firma PAdES por
página, cada una en su propia revisión incremental y con su PROPIO campo de
firma de UN solo widget en esa página (nombres FirmaDNIe, FirmaDNIe_2...,
sin chocar con los campos de firmantes anteriores). Sin /Stamp ni /Link. Un
campo de firma nuevo por revisión es un cambio permitido (también en
documentos certificados con DocMDP 2 o 3, como llenado de formulario), así
que cada firma deja válidas a las anteriores. Las revisiones se encadenan en
memoria y el archivo de salida se escribe (temporal + reemplazo) solo cuando
todas las páginas quedaron firmadas; si se cancela o falla, no queda archivo.

Tipos de sello (sin borde, letra sin serifa: Arial incrustada si está
disponible, si no Helvetica estándar):
  - "firma": logo del usuario a la izquierda (≈38 % del ancho) y texto a la
    derecha. Sin logo, el texto ocupa toda la caja.
  - "visto_bueno": imagen arriba (la del usuario o, si no hay, una marca de
    verificación vectorial propia), opaca, y el texto debajo sin superponerse.
El motivo se escribe en el sello y también como /Reason de la firma.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable, Literal

from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.content import RawContent
from pyhanko.pdf_utils.font import FontEngineFactory, SimpleFontEngineFactory
from pyhanko.pdf_utils.generic import pdf_name
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.layout import AxisAlignment, BoxConstraints, InnerScaling, Margins, SimpleBoxLayoutRule
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.pdf_utils.rw_common import find_inherited_value_in_tree
from pyhanko.pdf_utils.text import TextBoxStyle
from pyhanko.pdf_utils.writer import PdfFileWriter
from pyhanko.sign import fields, signers
from pyhanko.sign.signers.pdf_cms import PdfCMSSignedAttributes
from pyhanko.sign.validation.pdf_embedded import read_certification_data
from pyhanko.stamp import TextStampStyle

logger = logging.getLogger("firmador")
# fontTools avisa de cada tabla que no sabe reducir al incrustar la fuente; no aporta nada al usuario.
logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

TipoSello = Literal["firma", "visto_bueno"]
TIPOS_SELLO: tuple[str, ...] = ("firma", "visto_bueno")
# Motivos que ofrece la interfaz para cada tipo (lista cerrada); el primero es
# el predeterminado. `firmar_pdf` y la CLI aceptan cualquier motivo.
MOTIVOS_POR_TIPO = {
    "firma": ("Soy el autor del documento", "En señal de conformidad"),
    "visto_bueno": ("Doy V° B°",),
}
MOTIVOS_PREDETERMINADOS = {tipo: motivos[0] for tipo, motivos in MOTIVOS_POR_TIPO.items()}
# Tamaño fijo de cada sello en puntos PDF, igual en cualquier tamaño de página
# (la interfaz usa los mismos): «Firma» 160 x 40 y «Visto bueno» 140 x 110
# (imagen arriba y 4-5 líneas de texto debajo, legibles a unos 8 pt).
TAMANOS_SELLO = {"firma": (160, 40), "visto_bueno": (140, 110)}
PLANTILLAS_SELLO = {
    "firma": "Firmado digitalmente por:\n%(firmante)s\nMotivo: %(motivo)s\nFecha: %(ts)s",
    "visto_bueno": "Firmado digitalmente por\n%(firmante)s\nMotivo: %(motivo)s\nFecha: %(ts)s",
}
FORMATOS_FECHA = {"firma": "%Y/%m/%d %H:%M:%S%z", "visto_bueno": "%d.%m.%Y %H:%M:%S %:z"}
TEXTO_SELLO = PLANTILLAS_SELLO["firma"]
NOMBRE_CAMPO = "FirmaDNIe"
PAGINAS_VALIDAS = ("todas", "ultima", "cada_pagina")
_TOLERANCIA = 1e-6

# Geometría de los sellos (fracciones de la caja) y tamaños de letra en pt.
PROPORCION_LOGO = 0.38
_TAM_MAXIMO = {"firma": 9, "visto_bueno": 8}
_TAM_MINIMO = 3.0
_INTERLINEADO = 1.2
# Visto bueno: la imagen ocupa el 55 % superior y se alinea a la izquierda,
# como el bloque de texto que va debajo.
PROPORCION_IMAGEN_VB = 0.55
ALINEACION_IMAGEN_VB = AxisAlignment.ALIGN_MIN
_FUENTE_ARIAL = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"

# Marca de visto bueno (diseño propio): círculo gris y check rojo apagado, en
# una caja de 100 x 100. El círculo se traza con 4 curvas de Bézier.
_K = 0.5523 * 43


def _marca_visto_bueno() -> RawContent:
    c, r = 50, 43
    circulo = (
        b"%g %g m " % (c + r, c)
        + b"%g %g %g %g %g %g c " % (c + r, c + _K, c + _K, c + r, c, c + r)
        + b"%g %g %g %g %g %g c " % (c - _K, c + r, c - r, c + _K, c - r, c)
        + b"%g %g %g %g %g %g c " % (c - r, c - _K, c - _K, c - r, c, c - r)
        + b"%g %g %g %g %g %g c " % (c + _K, c - r, c + r, c - _K, c + r, c)
    )
    datos = (
        b"q 1 J 1 j 0.4 0.4 0.42 RG 8 w " + circulo + b"S "
        b"0.78 0.12 0.12 RG 12 w 27 51 m 44 34 l 75 70 l S Q"
    )
    return RawContent(datos, box=BoxConstraints(width=100, height=100))


class ErrorFirma(ValueError):
    """Error de datos o de documento, con mensaje apto para el usuario."""


class FirmaCancelada(ErrorFirma):
    """El usuario canceló la firma página por página; no se escribió ningún archivo."""

    def __init__(self, mensaje: str = "Se canceló la firma. El documento no se firmó."):
        super().__init__(mensaje)


# Avance de la firma página por página: (páginas firmadas, total). Si devuelve
# False, la firma se cancela (FirmaCancelada).
Progreso = Callable[[int, int], "bool | None"]


@dataclass
class PosicionRelativa:
    """Esquina inferior izquierda del sello en fracciones de la vista de la página (origen abajo-izq.)."""

    x: float
    y: float


# Posición predeterminada de cada tipo, abajo a la derecha: en A4, borde derecho
# a 560 pt y borde inferior a 30 pt (la interfaz usa las mismas).
POSICIONES_PREDETERMINADAS = {
    tipo: PosicionRelativa(x=(560 - ancho) / 595, y=30 / 842) for tipo, (ancho, _) in TAMANOS_SELLO.items()
}


def posicion_predeterminada(tipo: str) -> PosicionRelativa:
    return PosicionRelativa(**vars(POSICIONES_PREDETERMINADAS.get(tipo, POSICIONES_PREDETERMINADAS["firma"])))


@dataclass
class OpcionesFirma:
    posicion: PosicionRelativa | None = None  # None = posición predeterminada del tipo
    # Posición propia de algunas páginas (índice desde 0); las demás usan `posicion`.
    posiciones_paginas: dict[int, PosicionRelativa] | None = None
    paginas: str = "todas"  # "todas" | "ultima" | "cada_pagina"
    pagina_firma: int = -1  # página con el campo de firma real (negativo = desde el final)
    tipo: TipoSello = "firma"
    texto_sello: str | None = None  # None = texto del tipo de sello
    motivo: str | None = None  # None o vacío = motivo predeterminado del tipo
    lugar: str | None = None
    logo: str | os.PathLike | None = None  # imagen del logo (solo tipo "firma")
    imagen_vb: str | os.PathLike | None = None  # imagen de arriba (solo "visto_bueno"; si falta, marca vectorial)
    nombre_campo: str = NOMBRE_CAMPO


@dataclass
class ResultadoFirma:
    salida: str
    total_paginas: int
    paginas_selladas: list[int]
    pagina_firma: int
    # "contenido": sello normal (con "todas", el campo en su página y un /Stamp en cada
    # una de las demás), haya o no firmas previas.
    # "solo_campo": documento certificado (DocMDP 2 o 3): solo el campo de firma en su página.
    modo: str
    firmas_previas: int
    campo: str
    # Campos firmados en esta operación (uno; con "cada_pagina", uno por página en orden).
    campos: list[str] = field(default_factory=list)


@dataclass
class InfoPdf:
    total_paginas: int
    firmas: list[str]  # nombres de los campos con firmas ya embebidas
    docmdp: int | None  # nivel de permisos de certificación (1, 2, 3) o None
    vistas: list[tuple[float, float, float, float]]
    rotaciones: list[int]


def rect_sello(posicion: PosicionRelativa, vista, tipo: str) -> tuple[float, float, float, float]:
    """
    (x1, y1, x2, y2) en puntos PDF del sello del tipo en la vista dada: la
    esquina inferior izquierda sale de la posición relativa y el tamaño es el
    fijo del tipo (TAMANOS_SELLO). Si en esa posición no cabe, se desplaza
    hasta quedar dentro de la vista (en una página más chica que el sello
    queda pegado a su esquina inferior izquierda).
    """
    if not all(-_TOLERANCIA <= v <= 1 + _TOLERANCIA for v in (posicion.x, posicion.y)):
        raise ErrorFirma("La posición del sello está fuera de la página.")
    ancho, alto = TAMANOS_SELLO[tipo]
    x0, y0, x1, y1 = vista
    x = max(x0, min(x0 + posicion.x * (x1 - x0), x1 - ancho))
    y = max(y0, min(y0 + posicion.y * (y1 - y0), y1 - alto))
    return x, y, x + ancho, y + alto


def _validar_posiciones_paginas(posiciones, total: int) -> dict[int, PosicionRelativa]:
    """Comprueba las posiciones propias por página: índices del documento y x, y en 0..1."""
    if not posiciones:
        return {}
    resultado = {}
    for indice, posicion in posiciones.items():
        if isinstance(indice, bool) or not isinstance(indice, int) or not 0 <= indice < total:
            raise ErrorFirma(
                f"La posición del sello indica una página que no existe ({indice!r}; el PDF tiene {total})."
            )
        valores = (getattr(posicion, "x", None), getattr(posicion, "y", None))
        if not isinstance(posicion, PosicionRelativa) or not all(
            isinstance(v, (int, float)) and math.isfinite(v) and -_TOLERANCIA <= v <= 1 + _TOLERANCIA for v in valores
        ):
            raise ErrorFirma(f"La posición del sello en la página {indice + 1} está fuera de la página.")
        resultado[indice] = posicion
    return resultado


def _rectangulo(valor):
    a, b, c, d = (float(v) for v in valor)
    return min(a, c), min(b, d), max(a, c), max(b, d)


def _pagina(handler, indice):
    return handler.find_page_for_modification(indice)[0].get_object()


def vista_de_pagina(handler, indice) -> tuple[float, float, float, float]:
    """Intersección de /CropBox y /MediaBox de la página (como `page.view` de pdf.js)."""
    pagina = _pagina(handler, indice)
    media = find_inherited_value_in_tree(pagina, "/MediaBox", "/Parent")
    if media is None:
        raise ErrorFirma(f"La página {indice + 1} no tiene tamaño definido (/MediaBox).")
    media = _rectangulo(media)
    recorte = find_inherited_value_in_tree(pagina, "/CropBox", "/Parent")
    if recorte is None:
        return media
    recorte = _rectangulo(recorte)
    vista = (
        max(media[0], recorte[0]),
        max(media[1], recorte[1]),
        min(media[2], recorte[2]),
        min(media[3], recorte[3]),
    )
    if vista[2] <= vista[0] or vista[3] <= vista[1]:
        return media
    return vista


def _rotacion(handler, indice) -> int:
    valor = find_inherited_value_in_tree(_pagina(handler, indice), "/Rotate", "/Parent")
    return int(valor or 0) % 360


def _total_paginas(handler) -> int:
    return int(handler.root["/Pages"]["/Count"])


def _nivel_docmdp(reader) -> int | None:
    info = read_certification_data(reader)
    if info is None or info.permission is None:
        return None
    return int(info.permission.value)


def analizar_pdf(ruta) -> InfoPdf:
    """Datos del PDF útiles para la interfaz. No modifica nada."""
    with open(ruta, "rb") as f:
        reader = PdfFileReader(f, strict=False)
        _desbloquear(reader)
        total = _total_paginas(reader)
        return InfoPdf(
            total_paginas=total,
            firmas=[s.field_name for s in reader.embedded_signatures],
            docmdp=_nivel_docmdp(reader),
            vistas=[vista_de_pagina(reader, i) for i in range(total)],
            rotaciones=[_rotacion(reader, i) for i in range(total)],
        )


def _desbloquear(reader):
    """Abre PDF cifrados sin contraseña de usuario; rechaza los que la exigen."""
    if not reader.encrypted:
        return
    try:
        reader.decrypt("")
    except Exception as e:  # noqa: BLE001 - cualquier fallo significa "pide contraseña"
        raise ErrorFirma("El PDF está protegido con contraseña y no se puede firmar.") from e


def _validar_plantilla(plantilla: str, parametros: dict):
    try:
        plantilla % {**parametros, "ts": "2000-01-01 00:00:00"}
    except (KeyError, ValueError, TypeError) as e:
        raise ErrorFirma(
            "El texto del sello no es válido. Usa solo %(firmante)s, %(ts)s, "
            "%(motivo)s y %(lugar)s como campos variables."
        ) from e


def _nombre_libre(existentes, base: str) -> str:
    if base not in existentes:
        return base
    n = 2
    while f"{base}_{n}" in existentes:
        n += 1
    return f"{base}_{n}"


def _nombre_campo_libre(handler, base: str) -> str:
    return _nombre_libre({nombre for nombre, _, _ in fields.enumerate_sig_fields(handler)}, base)


# --- estilos de sello -----------------------------------------------------------


def fecha_sello(tipo: str, momento: datetime) -> str:
    """Fecha y hora con el formato del tipo de sello."""
    return momento.strftime(FORMATOS_FECHA[tipo])


class _FabricaFuenteEnMemoria(FontEngineFactory):
    """Como `GlyphAccumulatorFactory`, pero con la fuente ya cargada en memoria."""

    def __init__(self, datos: bytes):
        self.datos = datos

    def create_font_engine(self, writer, obj_stream=None):
        from pyhanko.pdf_utils.font.opentype import GlyphAccumulator

        if obj_stream is None and writer.stream_xrefs:
            obj_stream = writer.prepare_object_stream()
        return GlyphAccumulator(writer=writer, font_handle=io.BytesIO(self.datos), font_size=10, obj_stream=obj_stream)


def _arial_a_1000_unidades(ruta: Path) -> bytes:
    """
    Arial reducida a latín (incluye á, ñ, °) y reescalada a 1000 unidades por
    em. pyHanko 0.38 escribe los anchos (/W) en unidades de la fuente, pero PDF
    los interpreta en milésimas de em: con las 2048 unidades de Arial las
    letras quedarían separadas. Con 1000 unidades coinciden.
    """
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.scaleUpem import scale_upem

    fuente = TTFont(str(ruta))
    opciones = subset.Options()
    opciones.hinting = False
    opciones.layout_features = ["kern"]
    opciones.name_IDs = ["*"]
    opciones.notdef_outline = True
    subconjunto = subset.Subsetter(opciones)
    subconjunto.populate(unicodes=[*range(0x20, 0x7F), *range(0xA0, 0x180), *range(0x2010, 0x2027), 0x20AC])
    subconjunto.subset(fuente)
    scale_upem(fuente, 1000)
    salida = io.BytesIO()
    fuente.save(salida)
    return salida.getvalue()


@lru_cache(maxsize=1)
def _fuente():
    """
    (fábrica de fuente, motor para medir textos, nombre). Arial se incrusta
    (subconjunto) con el soporte OpenType de pyHanko (fontTools + uharfbuzz);
    si no está disponible se usa Helvetica, fuente estándar no incrustada.
    """
    try:
        if not _FUENTE_ARIAL.is_file():
            raise FileNotFoundError(str(_FUENTE_ARIAL))
        fabrica = _FabricaFuenteEnMemoria(_arial_a_1000_unidades(_FUENTE_ARIAL))
        return fabrica, fabrica.create_font_engine(PdfFileWriter()), "Arial"
    except Exception:  # noqa: BLE001 - cualquier fallo: fuente estándar
        logger.warning("No se pudo usar Arial; el sello usará Helvetica estándar.", exc_info=True)
        fabrica = SimpleFontEngineFactory("Helvetica", 0.56)
        return fabrica, fabrica.create_font_engine(PdfFileWriter()), "Helvetica"


def nombre_fuente() -> str:
    """Fuente con la que se escribe el sello: "Arial" (incrustada) o "Helvetica"."""
    return _fuente()[2]


def _medir_em(texto: str) -> float:
    """Ancho del texto en unidades de em (multiplicar por el tamaño de letra)."""
    return _fuente()[1].shape(texto).x_advance if texto else 0.0


def _ajustar_lineas(texto: str, ancho_max: float, medir) -> list[str]:
    """Reparte el texto en líneas que no superen `ancho_max` según `medir(línea)`."""
    lineas: list[str] = []
    for parrafo in texto.split("\n"):
        actual = ""
        for palabra in parrafo.split(" "):
            candidata = f"{actual} {palabra}" if actual else palabra
            if not actual or medir(candidata) <= ancho_max:
                actual = candidata
            else:
                lineas.append(actual)
                actual = palabra
            # Una palabra sola más ancha que la caja se corta por caracteres.
            while len(actual) > 1 and medir(actual) > ancho_max:
                corte = len(actual) - 1
                while corte > 1 and medir(actual[:corte]) > ancho_max:
                    corte -= 1
                lineas.append(actual[:corte])
                actual = actual[corte:]
        lineas.append(actual)
    return lineas


def _componer_texto(texto: str, ancho: float, alto: float, tam_maximo: int):
    """
    Elige el mayor tamaño de letra (hasta `tam_maximo`) con el que el texto,
    repartido en líneas, cabe en ancho x alto. Devuelve (líneas, tamaño
    nominal, interlineado, margen inferior); pyHanko luego reduce el bloque
    (SHRINK_TO_FIT) hasta el tamaño elegido o algo mayor si sobra espacio.
    """
    nominal = tam_maximo
    interlineado = max(nominal + 1, round(nominal * _INTERLINEADO))
    descendente = max(1, round(nominal * 0.25))  # espacio para letras como «g» o «p»
    efectivo = float(nominal)
    while True:
        escala = efectivo / nominal
        lineas = _ajustar_lineas(texto, max(ancho, 1) / escala, lambda s: _medir_em(s) * nominal)
        alto_bloque = (len(lineas) * interlineado + descendente) * escala
        if alto_bloque <= alto or efectivo <= _TAM_MINIMO:
            return lineas, nominal, interlineado, descendente
        efectivo -= 0.25


def fondo_blanco_a_transparente(imagen, umbral: int = 245):
    """Vuelve transparentes los píxeles casi blancos (fondo de iconos sin canal alfa)."""
    from PIL import ImageChops

    rgba = imagen.convert("RGBA")
    r, g, b, a = rgba.split()
    minimo = ImageChops.darker(ImageChops.darker(r, g), b)
    rgba.putalpha(ImageChops.darker(a, minimo.point(lambda v: 0 if v >= umbral else 255)))
    return rgba


def recortar_margenes(imagen, umbral: int = 245):
    """
    Recorta el borde casi blanco o transparente que rodea al contenido, para que
    el logo quede pegado al texto. Una imagen en blanco se devuelve sin cambios.
    """
    rgba = imagen.convert("RGBA")
    r, g, b, a = rgba.split()
    from PIL import ImageChops

    minimo = ImageChops.darker(ImageChops.darker(r, g), b)
    visible = ImageChops.darker(a, minimo.point(lambda v: 0 if v >= umbral else 255))
    caja = visible.getbbox()
    return imagen.crop(caja) if caja else imagen


def _imagen_reutilizable(imagen):
    """
    PdfImage que se incrusta UNA sola vez por documento. pyHanko vuelve a
    incrustar la imagen con cada writer nuevo; con "cada_pagina" hay un writer
    (una revisión) por página y el logo se repetiría en cada firma (cientos de
    KB por página). Aquí, después de la primera vez, el sello de las firmas
    siguientes apunta al MISMO XObject de imagen, ya escrito en una revisión
    anterior del mismo documento (las revisiones se encadenan sobre un solo
    flujo, así que el número de objeto sigue siendo válido).
    """
    from pyhanko.pdf_utils.images import PdfImage

    class _ImagenReutilizable(PdfImage):
        _incrustada: tuple[int, int] | None = None

        @property
        def image_ref(self) -> generic.IndirectObject:
            if self._incrustada is not None:
                return generic.IndirectObject(*self._incrustada, self.writer)
            ref = super().image_ref
            self._incrustada = (ref.idnum, ref.generation)
            return ref

    return _ImagenReutilizable(imagen)


def _cargar_imagen(ruta, descripcion: str, fondo_transparente: bool = False):
    """Imagen para pyHanko (logo o imagen de visto bueno), o ErrorFirma si no se puede leer."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(ruta) as original:
            original.load()
            imagen = original.copy()
    except (OSError, UnidentifiedImageError) as e:
        raise ErrorFirma(f"No se pudo leer la imagen del {descripcion}: {Path(ruta).name}.") from e
    if fondo_transparente:
        imagen = fondo_blanco_a_transparente(imagen)
    else:
        imagen = recortar_margenes(imagen)
        if imagen.mode not in ("RGB", "RGBA", "L", "LA"):
            imagen = imagen.convert("RGBA")
    return _imagen_reutilizable(imagen)


Rect = tuple[float, float, float, float]  # (x0, y0, x1, y1) en pt, origen abajo a la izquierda


@dataclass
class GeometriaSello:
    """Zonas del sello. La interfaz (app.js) reproduce el mismo cálculo."""

    area_imagen: Rect | None  # zona reservada a la imagen (None = sin imagen)
    imagen: Rect | None  # rectángulo que ocupa la imagen (proporción conservada)
    texto: Rect  # zona del texto; nunca se superpone con la imagen
    alineacion_imagen: AxisAlignment  # horizontal
    alineacion_vertical_imagen: AxisAlignment
    alineacion_texto: AxisAlignment  # vertical: centro (firma) o arriba (visto bueno)


def _alinear(alineacion: AxisAlignment, inicio: float, fin: float, largo: float) -> float:
    # Igual que AxisAlignment.align de pyHanko (incluida la división entera al centrar).
    if alineacion == AxisAlignment.ALIGN_MIN:
        return inicio
    if alineacion == AxisAlignment.ALIGN_MAX:
        return fin - largo
    return inicio + ((fin - inicio) - largo) // 2


def _encajar(dimensiones, area: Rect, horizontal: AxisAlignment, vertical: AxisAlignment) -> Rect:
    """Lo mismo que hace pyHanko con STRETCH_TO_FIT: escala uniforme dentro del área."""
    ancho_img, alto_img = dimensiones
    x0, y0, x1, y1 = area
    escala = max(0.0, min((x1 - x0) / ancho_img, (y1 - y0) / alto_img))
    ancho, alto = ancho_img * escala, alto_img * escala
    izquierda = _alinear(horizontal, x0, x1, ancho)
    abajo = _alinear(vertical, y0, y1, alto)
    return izquierda, abajo, izquierda + ancho, abajo + alto


def geometria_sello(tipo: str, ancho: float, alto: float, dimensiones_imagen=None) -> GeometriaSello:
    """
    Zonas de imagen y texto para una caja de `ancho` x `alto` pt.
      - "firma": logo (si hay) en el 38 % izquierdo y el texto justo a su derecha.
      - "visto_bueno": imagen arriba (55 % de la altura, opacidad 1) y el texto
        debajo, separados: no se superponen. Sin imagen se usa la marca vectorial.
    """
    margen = max(2.0, min(ancho, alto) * 0.06)
    if tipo == "visto_bueno":
        separacion = max(1.0, alto * 0.03)
        corte = alto * (1 - PROPORCION_IMAGEN_VB)
        area = (margen, corte + separacion / 2, ancho - margen, alto - margen)
        texto = (margen, margen, ancho - margen, max(margen + 1, corte - separacion / 2))
        dimensiones = dimensiones_imagen or (100, 100)
        # La imagen se apoya en el borde inferior de su área (justo sobre el
        # texto); ALIGN_MIN además evita el redondeo hacia abajo del centrado.
        return GeometriaSello(
            area_imagen=area,
            imagen=_encajar(dimensiones, area, ALINEACION_IMAGEN_VB, AxisAlignment.ALIGN_MIN),
            texto=texto,
            alineacion_imagen=ALINEACION_IMAGEN_VB,
            alineacion_vertical_imagen=AxisAlignment.ALIGN_MIN,
            alineacion_texto=AxisAlignment.ALIGN_MAX,
        )
    area = imagen = None
    izquierda = margen
    if dimensiones_imagen:
        area = (margen, margen, ancho * PROPORCION_LOGO, alto - margen)
        imagen = _encajar(dimensiones_imagen, area, AxisAlignment.ALIGN_MIN, AxisAlignment.ALIGN_MID)
        izquierda = imagen[2] + margen
    return GeometriaSello(
        area_imagen=area,
        imagen=imagen,
        texto=(izquierda, margen, ancho - margen, alto - margen),
        alineacion_imagen=AxisAlignment.ALIGN_MIN,
        alineacion_vertical_imagen=AxisAlignment.ALIGN_MID,
        alineacion_texto=AxisAlignment.ALIGN_MID,
    )


def _margenes(rect: Rect, ancho: float, alto: float) -> Margins:
    return Margins(left=rect[0], right=ancho - rect[2], bottom=rect[1], top=alto - rect[3])


def estilo_sello(
    tipo: str, ancho: float, alto: float, texto: str, fecha_muestra: str, imagen=None
) -> TextStampStyle:
    """
    Estilo pyHanko del sello para una caja de `ancho` x `alto` pt. `texto` ya
    tiene sustituidos firmante, motivo y lugar, y conserva `%(ts)s` para que
    pyHanko escriba la hora de la firma con el formato del tipo. `fecha_muestra`
    (mismo formato) solo se usa para medir y repartir las líneas. `imagen` es
    el logo ("firma") o la imagen de visto bueno (si falta, marca vectorial).
    """
    fondo = imagen
    if tipo == "visto_bueno" and fondo is None:
        fondo = _marca_visto_bueno()
    dimensiones = (fondo.box.width, fondo.box.height) if fondo is not None else None
    g = geometria_sello(tipo, ancho, alto, dimensiones)

    area_ancho = g.texto[2] - g.texto[0]
    area_alto = g.texto[3] - g.texto[1]
    muestra = texto.replace("%(ts)s", fecha_muestra)
    lineas, nominal, interlineado, descendente = _componer_texto(muestra, area_ancho, area_alto, _TAM_MAXIMO[tipo])
    # Texto literal para pyHanko: se escapan los «%» y se repone la fecha.
    literal = "\n".join(lineas).replace("%", "%%").replace(fecha_muestra.replace("%", "%%"), "%(ts)s")

    opciones = {}
    if fondo is not None:
        opciones = {
            "background": fondo,
            "background_opacity": 1.0,
            "background_layout": SimpleBoxLayoutRule(
                x_align=g.alineacion_imagen,
                y_align=g.alineacion_vertical_imagen,
                margins=_margenes(g.area_imagen, ancho, alto),
                inner_content_scaling=InnerScaling.STRETCH_TO_FIT,
            ),
        }
    return TextStampStyle(
        stamp_text=literal,
        timestamp_format=FORMATOS_FECHA[tipo],
        border_width=0,
        text_box_style=TextBoxStyle(
            font=_fuente()[0],
            font_size=nominal,
            leading=interlineado,
            text_color=(0, 0, 0),
            box_layout_rule=SimpleBoxLayoutRule(
                x_align=AxisAlignment.ALIGN_MIN,
                y_align=AxisAlignment.ALIGN_MIN,
                margins=Margins(bottom=descendente),
                inner_content_scaling=InnerScaling.NO_SCALING,
            ),
        ),
        inner_content_layout=SimpleBoxLayoutRule(
            x_align=AxisAlignment.ALIGN_MIN,
            y_align=g.alineacion_texto,
            margins=_margenes(g.texto, ancho, alto),
            inner_content_scaling=InnerScaling.SHRINK_TO_FIT,
        ),
        **opciones,
    )


def _misma_ruta(a, b) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return Path(a).resolve() == Path(b).resolve()


# --- firma con sello en cada página ----------------------------------------------


async def _firmar_con_paso_previo(pdf_signer, writer, salida, paso_previo):
    """
    Lo mismo que `PdfSigner.async_sign_pdf` (API de firma por etapas de
    pyHanko), pero llama a `paso_previo(sesion)` cuando el campo ya tiene su
    apariencia y su valor (/V) y antes de escribir el documento: lo que agregue
    ahí queda en la misma revisión y, por tanto, cubierto por la firma.
    """
    sesion = pdf_signer.init_signing_session(writer)
    info_validacion = await sesion.perform_presign_validation(writer)
    reservado = await sesion.estimate_signature_container_size(
        info_validacion, tight=pdf_signer.signature_meta.tight_size_estimates
    )
    documento = sesion.prepare_tbs_document(validation_info=info_validacion, bytes_reserved=reservado)
    paso_previo(sesion)
    resumen, salida_real = documento.digest_tbs_document(output=salida)
    firmado = await documento.perform_signature(
        document_digest=resumen.document_digest,
        pdf_cms_signed_attrs=PdfCMSSignedAttributes(
            signing_time=sesion.system_time,
            adobe_revinfo_attr=None if info_validacion is None else info_validacion.adobe_revinfo_attr,
            cades_signed_attrs=pdf_signer.signature_meta.cades_signed_attr_spec,
        ),
    )
    await firmado.post_signature_processing(salida_real)


# /F del sello de las demás páginas: Print | ReadOnly | Locked | LockedContents.
FLAGS_SELLO = 4 | 64 | 128 | 512
# /F del enlace que va encima del sello: Print.
FLAGS_ENLACE = 4

# JavaScript de Acrobat que, al hacer clic en el sello de una página que no es
# la de firma, muestra el estado de ESA firma. Solo ASCII: los acentos van
# como escapes \uXXXX de JavaScript. `signatureValidate()` devuelve -1 (no es
# campo de firma), 0 (sin firmar), 1 (validez desconocida), 2 (no válida),
# 3 (válida, identidad no verificada) o 4 (válida); `signatureInfo()` da el
# nombre del firmante (`name`) y la fecha (`date`, un Date). Los textos imitan
# los de la ventana «Estado de validación de la firma» de Acrobat.
_SCRIPT_ESTADO_FIRMA = (
    r'var p = "\n\nEl campo de esta firma est\u00e1 en la p\u00e1gina __PAGINA__.";'
    r" try {"
    r" var f = this.getField(__CAMPO__);"
    r" var e = f.signatureValidate();"
    r" var i = f.signatureInfo();"
    r' var n = i.name || "";'
    r' var m = "- No ha habido modificaciones en el documento desde que se firm\u00f3.";'
    r" var t;"
    r' if (e == 4) t = "La firma es V\u00c1LIDA, firmada por " + n + ".\n" + m'
    r' + "\n- La identidad del firmante es v\u00e1lida.";'
    r' else if (e == 3) t = "La validez de la firma es DESCONOCIDA, firmada por " + n + ".\n" + m'
    r' + "\n- No se ha podido verificar la identidad del firmante.";'
    r' else if (e == 2) t = "La firma NO ES V\u00c1LIDA, firmada por " + n'
    r' + ".\n- Revise los detalles en el panel Firmas.";'
    r' else if (e == 0) t = "El campo de firma est\u00e1 vac\u00edo.";'
    r' else t = "La validez de la firma es DESCONOCIDA, firmada por " + n + ".";'
    r' var d = i.date ? util.printd("dd/mm/yyyy HH:MM:ss", i.date) : "";'
    r' app.alert(t + (d ? "\n\nFecha: " + d : "") + p, 3);'
    r" } catch (x) {"
    r' app.alert("Sello de la firma digital " + __CAMPO__ + "." + p, 3);'
    r" }"
)


def script_estado_firma(campo: str, pagina_firma: int) -> str:
    """JavaScript (solo ASCII) que muestra el estado de la firma `campo`; `pagina_firma` desde 0."""
    return _SCRIPT_ESTADO_FIRMA.replace("__CAMPO__", json.dumps(campo)).replace("__PAGINA__", str(pagina_firma + 1))


def _agregar_sellos(writer, campo: str, firmante: str, paginas, pagina_firma: int, posicion_de, tipo: str):
    """
    Sella las páginas de `paginas` distintas de `pagina_firma` (donde ya está
    el ÚNICO widget del campo de firma `campo`). En cada una agrega:
      - una anotación /Stamp bloqueada (FLAGS_SELLO) que muestra el mismo
        XObject de apariencia del campo (mismo sello y misma hora; no se
        dibuja de nuevo por página, así 600 páginas siguen siendo rápidas);
      - encima, una anotación /Link con el mismo /Rect cuya acción JavaScript
        muestra en Acrobat el estado de ESTA firma (otros lectores pueden
        ignorar el clic).
    El /Rect sale de `posicion_de(índice)`; el tamaño es el fijo del tipo.
    Un campo de firma con varios widgets hace que Acrobat dé por modificada la
    firma en cuanto se agrega otra firma; estas anotaciones no.
    """
    ((_, _, campo_ref),) = fields.enumerate_sig_fields(writer, with_name=campo)
    apariencia = campo_ref.get_object()["/AP"].raw_get("/N")
    script = script_estado_firma(campo, pagina_firma)
    descripcion = (
        f"Sello de la firma digital «{campo}» de {firmante}. "
        f"El campo de la firma está en la página {pagina_firma + 1}."
    )
    for indice in paginas:
        if indice == pagina_firma:
            continue
        rect = [round(v, 4) for v in rect_sello(posicion_de(indice), vista_de_pagina(writer, indice), tipo)]
        pagina_ref = writer.find_page_for_modification(indice)[0]
        sello = generic.DictionaryObject(
            {
                pdf_name("/Type"): pdf_name("/Annot"),
                pdf_name("/Subtype"): pdf_name("/Stamp"),
                pdf_name("/F"): generic.NumberObject(FLAGS_SELLO),
                pdf_name("/Rect"): generic.ArrayObject(generic.FloatObject(v) for v in rect),
                pdf_name("/P"): pagina_ref,
                pdf_name("/Name"): pdf_name("/FirmaDigital"),
                pdf_name("/Contents"): generic.TextStringObject(descripcion),
                pdf_name("/AP"): generic.DictionaryObject({pdf_name("/N"): apariencia}),
            }
        )
        enlace = generic.DictionaryObject(
            {
                pdf_name("/Type"): pdf_name("/Annot"),
                pdf_name("/Subtype"): pdf_name("/Link"),
                pdf_name("/F"): generic.NumberObject(FLAGS_ENLACE),
                pdf_name("/Rect"): generic.ArrayObject(generic.FloatObject(v) for v in rect),
                pdf_name("/Border"): generic.ArrayObject([generic.NumberObject(0)] * 3),
                pdf_name("/Contents"): generic.TextStringObject("Ver el estado de la firma digital."),
                pdf_name("/A"): generic.DictionaryObject(
                    {
                        pdf_name("/S"): pdf_name("/JavaScript"),
                        pdf_name("/JS"): generic.TextStringObject(script),
                    }
                ),
            }
        )
        writer.register_annotation(pagina_ref, writer.add_object(sello))
        writer.register_annotation(pagina_ref, writer.add_object(enlace))


def _abrir_para_firmar(flujo) -> IncrementalPdfFileWriter:
    """Writer incremental sobre `flujo` (archivo o BytesIO); abre los PDF cifrados sin contraseña de usuario."""
    writer = IncrementalPdfFileWriter(flujo, strict=False)
    _desbloquear(writer.prev)
    if writer.prev.encrypted:
        writer.encrypt("")
    return writer


@dataclass
class _Documento:
    """Datos del documento que valen para todas sus firmas."""

    total: int
    pagina_firma: int
    posiciones_paginas: dict[int, PosicionRelativa]
    firmas_previas: int
    # "contenido" o "solo_campo" (documento certificado con DocMDP 2 o 3; ver ResultadoFirma).
    modo: str


def _examinar_para_firmar(writer, opciones: OpcionesFirma) -> _Documento:
    """Comprueba páginas, posiciones y certificación (DocMDP 1 se rechaza) antes de firmar."""
    total = _total_paginas(writer)
    if total < 1:
        raise ErrorFirma("El PDF no tiene páginas.")
    pagina_firma = opciones.pagina_firma if opciones.pagina_firma >= 0 else total + opciones.pagina_firma
    if not 0 <= pagina_firma < total:
        raise ErrorFirma(f"La página de firma {pagina_firma + 1} no existe (el PDF tiene {total}).")
    posiciones_paginas = _validar_posiciones_paginas(opciones.posiciones_paginas, total)
    firmas_previas = len(writer.prev.embedded_signatures)
    nivel_docmdp = _nivel_docmdp(writer.prev)
    if nivel_docmdp == fields.MDPPerm.NO_CHANGES.value:
        raise ErrorFirma(
            "El documento está certificado y no permite ningún cambio, ni siquiera "
            "nuevas firmas. No se puede firmar sin invalidar la certificación."
        )
    # Con firmas previas de aprobación se sella igual que sin firmas; un
    # documento certificado (DocMDP 2 o 3) solo recibe campos de firma
    # (sin anotaciones /Stamp ni /Link).
    modo = "solo_campo" if nivel_docmdp is not None else "contenido"
    return _Documento(total, pagina_firma, posiciones_paginas, firmas_previas, modo)


def _escribir_atomico(salida, datos):
    """
    Escribe `datos` en `salida` a través de un temporal de la misma carpeta y
    un reemplazo: nunca queda un archivo a medias, y si algo falla el archivo
    que ya existía en `salida` no cambia.
    """
    salida = Path(salida)
    descriptor, temporal = tempfile.mkstemp(dir=salida.parent, prefix=f".{salida.stem}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as f:
            f.write(datos)
        os.replace(temporal, salida)
    except BaseException:
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise


def _avisar(progreso: Progreso | None, hechas: int, total: int):
    """Informa el avance; si `progreso` devuelve False, cancela (FirmaCancelada)."""
    if progreso is not None and progreso(hechas, total) is False:
        logger.info("Firma página por página cancelada con %d de %d página(s) firmadas.", hechas, total)
        raise FirmaCancelada()


def firmar_pdf(
    entrada,
    salida,
    signer,
    firmante: str,
    opciones: OpcionesFirma | None = None,
    progreso: Progreso | None = None,
) -> ResultadoFirma:
    """
    Firma `entrada` y escribe `salida`. `signer` es un `pyhanko` Signer (p. ej.
    `dnie_cng.FirmanteCNG`); con el DNIe, Windows pide el PIN durante este paso.
    El archivo de salida solo se escribe si la firma termina bien.
    `progreso(hechas, total)` (solo con "cada_pagina") recibe el avance antes
    de la primera página y después de cada una; si devuelve False se cancela.
    """
    opciones = opciones or OpcionesFirma()
    if opciones.paginas not in PAGINAS_VALIDAS:
        raise ErrorFirma(f"Opción de páginas desconocida: {opciones.paginas!r}.")
    if _misma_ruta(entrada, salida):
        raise ErrorFirma("El archivo de salida debe ser distinto del original.")

    tipo = opciones.tipo
    if tipo not in TIPOS_SELLO:
        raise ErrorFirma(f"Tipo de sello desconocido: {tipo!r}.")
    motivo = (opciones.motivo or "").strip() or MOTIVOS_PREDETERMINADOS[tipo]
    lugar = (opciones.lugar or "").strip()
    plantilla = opciones.texto_sello or PLANTILLAS_SELLO[tipo]
    parametros = {"firmante": firmante, "motivo": motivo, "lugar": lugar}
    _validar_plantilla(plantilla, parametros)
    # %(ts)s queda pendiente: pyHanko escribe la hora de la firma en el campo.
    texto = plantilla % {**parametros, "ts": "%(ts)s"}
    imagen = None
    if tipo == "firma" and opciones.logo:
        imagen = _cargar_imagen(opciones.logo, "logo")
    elif tipo == "visto_bueno" and opciones.imagen_vb:
        # El icono suele venir con fondo blanco sin alfa: se quita para que no se vea un recuadro.
        imagen = _cargar_imagen(opciones.imagen_vb, "visto bueno", fondo_transparente=True)
    fecha = fecha_sello(tipo, datetime.now().astimezone())
    posicion = opciones.posicion or posicion_predeterminada(tipo)
    estilo = estilo_sello(tipo, *TAMANOS_SELLO[tipo], texto, fecha, imagen)

    def firmador_pdf(campo: str) -> signers.PdfSigner:
        meta = signers.PdfSignatureMetadata(
            field_name=campo,
            md_algorithm="sha256",
            subfilter=fields.SigSeedSubFilter.PADES,
            reason=motivo,
            location=lugar or None,
        )
        return signers.PdfSigner(meta, signer=signer, stamp_style=estilo)

    if opciones.paginas == "cada_pagina":
        return _firmar_cada_pagina(entrada, salida, opciones, posicion, firmador_pdf, progreso)

    with open(entrada, "rb") as inf:
        writer = _abrir_para_firmar(inf)
        doc = _examinar_para_firmar(writer, opciones)
        pagina_firma = doc.pagina_firma

        def posicion_de(indice: int) -> PosicionRelativa:
            return doc.posiciones_paginas.get(indice, posicion)

        paginas = [pagina_firma]
        # "todas": el campo de firma (un solo widget) va en su página y las
        # demás reciben un /Stamp con la misma apariencia más un /Link, en la
        # misma revisión (cubiertos por la firma).
        sellos_extra = doc.modo == "contenido" and opciones.paginas == "todas"
        if sellos_extra:
            paginas = list(range(doc.total))

        campo = _nombre_campo_libre(writer, opciones.nombre_campo)
        caja_campo = rect_sello(posicion_de(pagina_firma), vista_de_pagina(writer, pagina_firma), tipo)
        # Campo y widget en un mismo diccionario (sin /Kids): un solo widget.
        fields.append_signature_field(
            writer, fields.SigFieldSpec(sig_field_name=campo, on_page=pagina_firma, box=caja_campo)
        )

        pdf_signer = firmador_pdf(campo)
        buffer = io.BytesIO()
        if sellos_extra:

            def agregar_sellos(_sesion):
                _agregar_sellos(writer, campo, firmante, paginas, pagina_firma, posicion_de, tipo)

            asyncio.run(_firmar_con_paso_previo(pdf_signer, writer, buffer, agregar_sellos))
        else:
            pdf_signer.sign_pdf(writer, output=buffer)

    with buffer.getbuffer() as datos:
        _escribir_atomico(salida, datos)
    return ResultadoFirma(
        salida=str(salida),
        total_paginas=doc.total,
        paginas_selladas=paginas,
        pagina_firma=pagina_firma,
        modo=doc.modo,
        firmas_previas=doc.firmas_previas,
        campo=campo,
        campos=[campo],
    )


def _firmar_cada_pagina(entrada, salida, opciones, posicion, firmador_pdf, progreso) -> ResultadoFirma:
    """
    Una firma por página, en orden, cada una con su PROPIO campo de firma (un
    solo widget, en esa página) y en su propia revisión incremental.

    Las revisiones se encadenan sobre UN BytesIO: cada firma se agrega al
    final del mismo búfer (`in_place`), sin copiar el documento ni pasar por
    el disco; para la página siguiente solo se vuelven a leer las tablas de
    referencias. La salida se escribe una sola vez, al terminar todas.
    """
    flujo = io.BytesIO(Path(entrada).read_bytes())
    writer = _abrir_para_firmar(flujo)
    doc = _examinar_para_firmar(writer, opciones)
    existentes = {nombre for nombre, _, _ in fields.enumerate_sig_fields(writer)}
    campos: list[str] = []
    _avisar(progreso, 0, doc.total)
    for indice in range(doc.total):
        if indice:
            writer = _abrir_para_firmar(flujo)
        campo = _nombre_libre(existentes, opciones.nombre_campo)
        existentes.add(campo)
        caja = rect_sello(
            doc.posiciones_paginas.get(indice, posicion), vista_de_pagina(writer, indice), opciones.tipo
        )
        fields.append_signature_field(writer, fields.SigFieldSpec(sig_field_name=campo, on_page=indice, box=caja))
        firmador_pdf(campo).sign_pdf(writer, in_place=True)
        campos.append(campo)
        _avisar(progreso, indice + 1, doc.total)

    with flujo.getbuffer() as datos:
        _escribir_atomico(salida, datos)
    return ResultadoFirma(
        salida=str(salida),
        total_paginas=doc.total,
        paginas_selladas=list(range(doc.total)),
        pagina_firma=doc.total - 1,
        modo=doc.modo,
        firmas_previas=doc.firmas_previas,
        campo=campos[-1],
        campos=campos,
    )
