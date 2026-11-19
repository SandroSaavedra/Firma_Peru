"""
Validación de las firmas embebidas en un PDF.

Para cada firma informa: firmante, fecha, integridad (si el contenido firmado
cambió), cobertura, modificaciones posteriores (y si son permitidas), y la
confianza en el certificado usando como raíces el almacén ROOT de Windows.
La revocación se consulta en modo "soft-fail": si no hay red, no se marca la
firma como inválida solo por eso.

Cambios posteriores a una firma (análisis de diferencias de pyHanko):
  - La política predeterminada de pyHanko (DEFAULT_DIFF_POLICY) solo admite,
    en una página, anotaciones nuevas que sean widgets de campos de firma
    nuevos: `SigFieldCreationRule` recorre las /Annots de cada página y
    rechaza cualquier otra («The newly added annotations ... were not
    recognised.»). Por eso, cuando una firma posterior pone su sello con
    «todas» (/Stamp + /Link en las páginas que no son la de firma, ver
    firma_pdf.py), pyHanko clasificaba la firma anterior como OTHER
    (docmdp_ok=False), aunque Acrobat la da por válida: Acrobat permite
    agregar comentarios (anotaciones) y firmas después de una firma de
    aprobación. pyHanko 0.38 no trae una regla para anotaciones (es un TODO
    en su código), así que aquí se agrega una mínima.
  - En documentos SIN certificación (sin DocMDP) se usa POLITICA_SIN_CERTIFICACION:
    la de pyHanko con `SigFieldCreationRule` cambiada por
    `_FirmasYAnotacionesNuevas`, que acepta además anotaciones NUEVAS que no
    sean widgets (objetos creados en esa revisión) y las clasifica como
    ANNOTATIONS. Sigue siendo una modificación permitida solo porque no hay
    DocMDP; el resto de reglas no cambia.
  - Se sigue reportando como no permitido: cambiar el contenido o cualquier
    otra clave de una página, quitar anotaciones, modificar anotaciones ya
    existentes (por ejemplo, mover un sello firmado) o la apariencia de una
    firma anterior, y agregar widgets que no sean de campos de firma nuevos.
  - Documentos certificados (DocMDP): POLITICA_CERTIFICADO, la de pyHanko
    con un solo cambio: acepta campos de firma VISIBLES nuevos
    (`allow_new_visible_after_certify`). pyHanko los rechaza por omisión
    después de una certificación («más estricto que Acrobat», dice su propia
    documentación); Acrobat los admite con DocMDP 2 o 3 como llenado de
    formulario. Las firmas que se agregan a un documento certificado (una sola
    o una por página con «cada_pagina») son campos de firma visibles. Las
    anotaciones que no son widgets siguen sin admitirse.
"""
from __future__ import annotations

import functools
import logging

from asn1crypto import x509
from pyhanko.pdf_utils import generic, misc
from pyhanko.pdf_utils.reader import PdfFileReader, RawPdfPath
from pyhanko.sign.diff_analysis import (
    DEFAULT_DIFF_POLICY,
    FormUpdate,
    FormUpdatingRule,
    GenericFieldModificationRule,
    ModificationLevel,
    SigFieldCreationRule,
    SigFieldModificationRule,
    StandardDiffPolicy,
    SuspiciousModification,
)
from pyhanko.sign.diff_analysis.commons import compare_dicts
from pyhanko.sign.diff_analysis.rules_api import RelativeContext
from pyhanko.sign.validation import validate_pdf_signature, validate_pdf_timestamp
from pyhanko.sign.validation.pdf_embedded import read_certification_data
from pyhanko.sign.validation.settings import KeyUsageConstraints
from pyhanko.sign.validation.status import SignatureCoverageLevel
from pyhanko_certvalidator import ValidationContext

logger = logging.getLogger(__name__)

_USOS_DE_FIRMA = KeyUsageConstraints(key_usage={"non_repudiation", "digital_signature"})


def _referencias(arreglo) -> list:
    arreglo = arreglo.get_object()
    if not isinstance(arreglo, generic.ArrayObject) or not all(
        isinstance(v, generic.IndirectObject) for v in arreglo
    ):
        raise SuspiciousModification("/Annots o /Kids no es un arreglo de referencias indirectas.")
    return [v.reference for v in arreglo]


def _anotaciones_de(pagina):
    """(refs de /Annots, ref del arreglo si es indirecto) de una página."""
    if "/Annots" not in pagina:
        return set(), None
    valor = pagina.raw_get("/Annots")
    ref = valor.reference if isinstance(valor, generic.IndirectObject) else None
    return set(_referencias(valor)), ref


class _FirmasYAnotacionesNuevas(SigFieldCreationRule):
    """
    `SigFieldCreationRule` de pyHanko (campos de firma nuevos) más anotaciones
    nuevas en las páginas. pyHanko recorre las /Annots solo para los widgets de
    los campos de firma nuevos (`approve_widget_bindings`) y rechaza cualquier
    otra anotación; aquí ese recorrido se reemplaza por uno que además acepta,
    en el nivel ANNOTATIONS, anotaciones que no son widgets y que se crearon en
    la revisión analizada (con sus objetos nuevos, p. ej. su apariencia).
    """

    def __init__(self):
        super().__init__(approve_widget_bindings=False)

    def apply(self, context):
        yield from super().apply(context)
        widgets = self._widgets_de_firmas_nuevas(context)
        yield from self._recorrer(context, context.old.root["/Pages"], context.new.root["/Pages"], widgets, set())

    @staticmethod
    def _widgets_de_firmas_nuevas(context) -> dict:
        widgets = {}
        for nombre, spec in context.field_specs.items():
            if spec.field_type != "/Sig" or spec.old_field_ref or spec.new_field_ref is None:
                continue
            campo = spec.new_field_ref.get_object()
            if "/Kids" in campo:
                for hijo in _referencias(campo.raw_get("/Kids")):
                    if "/T" not in hijo.get_object():
                        widgets[hijo] = nombre
            else:
                widgets[spec.new_field_ref] = nombre
        return widgets

    def _recorrer(self, context, viejo, nuevo, widgets, vistos):
        hijos_viejos = _referencias(viejo["/Kids"])
        hijos_nuevos = _referencias(nuevo["/Kids"])
        if hijos_viejos != hijos_nuevos:
            raise SuspiciousModification("Cambió la estructura del árbol de páginas.")
        for ref in hijos_viejos:
            if ref in vistos:
                raise misc.PdfReadError("Referencia circular en el árbol de páginas.")
            pagina_vieja = context.old(ref)
            pagina_nueva = context.new(ref)
            if pagina_vieja.get("/Type") == "/Pages":
                yield from self._recorrer(context, pagina_vieja, pagina_nueva, widgets, vistos | {ref})
            else:
                yield from self._revisar_pagina(context, ref, pagina_vieja, pagina_nueva, widgets)

    @staticmethod
    def _revisar_pagina(context, ref, pagina_vieja, pagina_nueva, widgets):
        viejas, ref_viejas = _anotaciones_de(pagina_vieja)
        nuevas, ref_nuevas = _anotaciones_de(pagina_nueva)
        if viejas == nuevas:
            return
        if viejas - nuevas:
            raise SuspiciousModification(f"Se quitaron las anotaciones {viejas - nuevas}.")
        nivel = ModificationLevel.LTA_UPDATES
        for anot in nuevas - viejas:
            if anot in widgets:
                continue  # widget de un campo de firma nuevo (como en pyHanko)
            objeto = anot.get_object()
            if not context.old.is_ref_unassignable(anot) or objeto.get("/Subtype") == "/Widget":
                raise SuspiciousModification(f"La anotación agregada {anot} no está permitida.")
            nivel = ModificationLevel.ANNOTATIONS
            yield nivel, FormUpdate(updated_ref=anot)
            for dep in context.new.collect_dependencies(objeto, since_revision=context.old.revision + 1):
                yield nivel, FormUpdate(updated_ref=dep)
        # Fuera de /Annots, la página debe quedar igual.
        compare_dicts(pagina_vieja, pagina_nueva, frozenset(["/Annots"]))
        yield nivel, FormUpdate(updated_ref=ref, context_checked=None)
        if ref_nuevas and (ref_nuevas == ref_viejas or context.old.is_ref_unassignable(ref_nuevas)):
            yield nivel, FormUpdate(
                updated_ref=ref_nuevas, context_checked=RelativeContext(ref, RawPdfPath("/Annots"))
            )


POLITICA_SIN_CERTIFICACION = StandardDiffPolicy(
    global_rules=DEFAULT_DIFF_POLICY.global_rules,
    form_rule=FormUpdatingRule(
        field_rules=[_FirmasYAnotacionesNuevas(), SigFieldModificationRule(), GenericFieldModificationRule()],
    ),
)


POLITICA_CERTIFICADO = StandardDiffPolicy(
    global_rules=DEFAULT_DIFF_POLICY.global_rules,
    form_rule=FormUpdatingRule(
        field_rules=[
            SigFieldCreationRule(allow_new_visible_after_certify=True),
            SigFieldModificationRule(),
            GenericFieldModificationRule(),
        ],
    ),
)


def politica_de_cambios(reader) -> StandardDiffPolicy:
    """Política de diferencias: POLITICA_CERTIFICADO si el documento está certificado (DocMDP)."""
    return POLITICA_CERTIFICADO if read_certification_data(reader) is not None else POLITICA_SIN_CERTIFICACION


@functools.lru_cache(maxsize=1)
def _certs_windows() -> tuple[tuple[x509.Certificate, ...], tuple[x509.Certificate, ...]]:
    """(raíces, intermedios) de los almacenes ROOT y CA de Windows."""
    try:
        import dnie_cng

        return tuple(dnie_cng.certificados_de_almacen("ROOT")), tuple(dnie_cng.certificados_de_almacen("CA"))
    except Exception as e:  # noqa: BLE001 - sin almacén solo se pierde la confianza
        logger.warning("No se pudieron leer los almacenes de Windows: %s", e)
        return (), ()


def _contexto(permitir_red: bool, raices_extra) -> ValidationContext:
    raices, intermedios = _certs_windows()
    return ValidationContext(
        trust_roots=list(raices) + list(raices_extra or ()),
        other_certs=list(intermedios),
        allow_fetching=permitir_red,
        revocation_mode="soft-fail",
    )


def _nombre(nombre: x509.Name | None) -> str:
    if nombre is None:
        return ""
    datos = nombre.native
    return datos.get("common_name") or datos.get("organization_name") or nombre.human_friendly


def _fecha(dt) -> str | None:
    return dt.isoformat() if dt else None


def _cobertura(estado) -> tuple[str, bool]:
    if estado.coverage == SignatureCoverageLevel.ENTIRE_FILE:
        return "documento_completo", True
    if estado.coverage == SignatureCoverageLevel.ENTIRE_REVISION:
        return "revision_completa", False
    return "parcial", False


def _modificaciones(estado, cubre_todo: bool) -> str:
    if cubre_todo:
        return "ninguna"
    nivel = getattr(estado, "modification_level", None)
    if nivel is None:
        return "desconocidas"
    if nivel == ModificationLevel.NONE:
        return "ninguna"
    return "permitidas" if estado.docmdp_ok else "no_permitidas"


def _resumen(d: dict) -> str:
    quien = d["firmante"] or "firmante desconocido"
    if d["integridad"] != "intacta":
        return f"Firma de {quien}: el contenido firmado fue alterado. La firma NO es válida."
    if not d["criptografia_valida"]:
        return f"Firma de {quien}: la firma criptográfica no es válida."
    if d["modificaciones"] == "no_permitidas":
        return f"Firma de {quien}: el documento tiene cambios posteriores no permitidos por esta firma."
    partes = [f"Firma de {quien} íntegra."]
    if d["modificaciones"] == "permitidas":
        if d["nivel_modificacion"] == ModificationLevel.ANNOTATIONS.name:
            partes.append("Después se agregaron cambios permitidos: otras firmas o anotaciones (como sus sellos).")
        else:
            partes.append("Después se agregaron cambios permitidos (por ejemplo, otras firmas).")
    elif d["modificaciones"] == "ninguna":
        partes.append("El documento no cambió después de firmar.")
    if d["confianza"] == "confiable":
        partes.append("El certificado es de confianza.")
    elif d["confianza"] == "revocado":
        partes.append("El certificado está REVOCADO.")
    else:
        partes.append("No se pudo confirmar la confianza del certificado en este equipo.")
    return " ".join(partes)


def _estado_general(d: dict) -> str:
    if (
        d["integridad"] != "intacta"
        or not d["criptografia_valida"]
        or d["modificaciones"] == "no_permitidas"
        or d["confianza"] == "revocado"
    ):
        return "invalida"
    if d["confianza"] != "confiable" or d["modificaciones"] == "desconocidas":
        return "advertencia"
    return "valida"


def _describir(emb, contexto, politica) -> dict:
    es_sello_tiempo = emb.sig_object_type == "/DocTimeStamp"
    if es_sello_tiempo:
        estado = validate_pdf_timestamp(emb, validation_context=contexto, diff_policy=politica)
    else:
        estado = validate_pdf_signature(emb, contexto, diff_policy=politica, key_usage_settings=_USOS_DE_FIRMA)

    cobertura, cubre_todo = _cobertura(estado)
    cert = estado.signing_cert
    sello = getattr(estado, "timestamp_validity", None)
    if es_sello_tiempo:
        fecha, origen = estado.timestamp, "sello_de_tiempo"
    elif sello is not None:
        fecha, origen = sello.timestamp, "sello_de_tiempo"
    else:
        fecha, origen = getattr(estado, "signer_reported_dt", None), "reloj_del_firmante"

    if getattr(estado, "revoked", False):
        confianza = "revocado"
    elif estado.trusted:
        confianza = "confiable"
    else:
        confianza = "no_confiable"

    d = {
        "campo": emb.field_name,
        "tipo": "sello_de_tiempo" if es_sello_tiempo else "firma",
        "firmante": _nombre(cert.subject) if cert else "",
        "emisor": _nombre(cert.issuer) if cert else "",
        "fecha_firma": _fecha(fecha),
        "origen_fecha": origen,
        "integridad": "intacta" if estado.intact else "modificada",
        "criptografia_valida": bool(estado.valid),
        "cobertura": cobertura,
        "cubre_todo": cubre_todo,
        "nivel_modificacion": getattr(getattr(estado, "modification_level", None), "name", None),
        "docmdp_ok": getattr(estado, "docmdp_ok", None),
        "confianza": confianza,
        "subfilter": str(emb.sig_object.get("/SubFilter", "")),
        "error": None,
    }
    d["modificaciones"] = _modificaciones(estado, cubre_todo)
    d["estado"] = _estado_general(d)
    d["resumen"] = _resumen(d)
    return d


def _con_error(emb, error: Exception) -> dict:
    return {
        "campo": getattr(emb, "field_name", ""),
        "tipo": "firma",
        "firmante": "",
        "emisor": "",
        "fecha_firma": None,
        "origen_fecha": None,
        "integridad": "modificada",
        "criptografia_valida": False,
        "cobertura": "parcial",
        "cubre_todo": False,
        "nivel_modificacion": None,
        "docmdp_ok": None,
        "confianza": "no_confiable",
        "subfilter": "",
        "modificaciones": "desconocidas",
        "estado": "invalida",
        "resumen": f"No se pudo validar esta firma: {error}",
        "error": str(error),
    }


def validar_pdf(ruta, permitir_red: bool = True, raices_extra=None) -> list[dict]:
    """
    Valida cada firma embebida. Devuelve [] si el PDF no tiene firmas.
    `permitir_red=False` evita consultas OCSP/CRL (útil sin conexión y en pruebas).
    """
    with open(ruta, "rb") as f:
        reader = PdfFileReader(f, strict=False)
        if reader.encrypted:
            reader.decrypt("")
        firmas = reader.embedded_signatures
        if not firmas:
            return []
        contexto = _contexto(permitir_red, raices_extra)
        politica = politica_de_cambios(reader)
        resultado = []
        for emb in firmas:
            try:
                resultado.append(_describir(emb, contexto, politica))
            except Exception as e:  # noqa: BLE001 - una firma rota no debe ocultar las demás
                logger.exception("Error al validar la firma %s", getattr(emb, "field_name", "?"))
                resultado.append(_con_error(emb, e))
        return resultado
