"""
Validación de las firmas embebidas en un PDF.

Para cada firma informa: firmante, fecha, integridad (si el contenido firmado
cambió), cobertura, modificaciones posteriores (y si son permitidas), y la
confianza en el certificado usando como raíces el almacén ROOT de Windows.
La revocación se consulta en modo "soft-fail": si no hay red, no se marca la
firma como inválida solo por eso.
"""
from __future__ import annotations

import functools
import logging

from asn1crypto import x509
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.validation import validate_pdf_signature, validate_pdf_timestamp
from pyhanko.sign.validation.settings import KeyUsageConstraints
from pyhanko.sign.validation.status import SignatureCoverageLevel
from pyhanko_certvalidator import ValidationContext

logger = logging.getLogger(__name__)

_USOS_DE_FIRMA = KeyUsageConstraints(key_usage={"non_repudiation", "digital_signature"})


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


def _describir(emb, contexto) -> dict:
    es_sello_tiempo = emb.sig_object_type == "/DocTimeStamp"
    if es_sello_tiempo:
        estado = validate_pdf_timestamp(emb, validation_context=contexto)
    else:
        estado = validate_pdf_signature(emb, contexto, key_usage_settings=_USOS_DE_FIRMA)

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
        resultado = []
        for emb in firmas:
            try:
                resultado.append(_describir(emb, contexto))
            except Exception as e:  # noqa: BLE001 - una firma rota no debe ocultar las demás
                logger.exception("Error al validar la firma %s", getattr(emb, "field_name", "?"))
                resultado.append(_con_error(emb, e))
        return resultado
