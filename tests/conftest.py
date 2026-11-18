"""
Fixtures comunes de las pruebas.

Nunca se toca el DNIe real: se genera un certificado de prueba y se simula
`FirmanteCNG._firmar_resumen` con la llave privada de prueba, así la firma
resultante es criptográficamente correcta y no se pide ningún PIN.
"""
import datetime
import io
import sys
from pathlib import Path

import pytest
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.generic import pdf_name
from pyhanko.pdf_utils.writer import PageObject, PdfFileWriter

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import dnie_cng  # noqa: E402

_HASHES = {
    "sha256": hashes.SHA256,
    "sha384": hashes.SHA384,
    "sha512": hashes.SHA512,
    "sha1": hashes.SHA1,
}


@pytest.fixture(scope="session")
def material_prueba():
    """Llave RSA 2048 y certificado autofirmado con uso de no repudio."""
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "PRUEBA FIRMANTE FIR 12345678"),
            x509.NameAttribute(NameOID.COUNTRY_NAME, "PE"),
        ]
    )
    ahora = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(days=1))
        .not_valid_after(ahora + datetime.timedelta(days=365))
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(llave, hashes.SHA256())
    )
    cert_asn1 = asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    return llave, cert_asn1


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch) -> Path:
    """%APPDATA% temporal: las pruebas nunca leen ni escriben la configuración real."""
    carpeta = tmp_path / "AppData"
    carpeta.mkdir()
    monkeypatch.setenv("APPDATA", str(carpeta))
    # Las pruebas no dependen de las imágenes reales del usuario en src/.
    import app

    monkeypatch.setattr(app, "LOGO_INICIAL", tmp_path / "sin-logo-inicial.png", raising=False)
    monkeypatch.setattr(app, "IMAGEN_VB_INICIAL", tmp_path / "sin-imagen-vb-inicial.png", raising=False)
    return carpeta


@pytest.fixture
def firmante(monkeypatch, material_prueba):
    """FirmanteCNG con la operación CNG simulada (sin tarjeta ni PIN)."""
    llave_privada, cert = material_prueba

    def _firmar_simulado(self, resumen, algoritmo):
        return llave_privada.sign(
            resumen, padding.PKCS1v15(), utils.Prehashed(_HASHES[algoritmo]())
        )

    monkeypatch.setattr(dnie_cng, "cadena_intermedia", lambda c: [])
    monkeypatch.setattr(dnie_cng.FirmanteCNG, "_firmar_resumen", _firmar_simulado)
    llave = dnie_cng.LlaveTarjeta(nombre="llave-prueba", tipo_legado=0, cert=cert)
    return dnie_cng.FirmanteCNG(llave)


def construir_pdf(paginas) -> bytes:
    """
    Construye un PDF válido (xref correcta, generada por pyHanko).

    `paginas` es una lista de dicts con: media_box (tupla), contenido (bool,
    False = página sin /Contents), rotacion (int, opcional), crop_box (opcional).
    """
    w = PdfFileWriter()
    for spec in paginas:
        texto = b"BT /F1 18 Tf 72 720 Td (Pagina de prueba) Tj ET"
        flujo = w.add_object(generic.StreamObject(stream_data=texto))
        fuente = generic.DictionaryObject(
            {
                pdf_name("/Type"): pdf_name("/Font"),
                pdf_name("/Subtype"): pdf_name("/Type1"),
                pdf_name("/BaseFont"): pdf_name("/Helvetica"),
            }
        )
        recursos = generic.DictionaryObject(
            {pdf_name("/Font"): generic.DictionaryObject({pdf_name("/F1"): w.add_object(fuente)})}
        )
        pagina = PageObject(contents=flujo, media_box=spec["media_box"], resources=recursos)
        if not spec.get("contenido", True):
            del pagina["/Contents"]
        if spec.get("rotacion"):
            pagina[pdf_name("/Rotate")] = generic.NumberObject(spec["rotacion"])
        if spec.get("crop_box"):
            pagina[pdf_name("/CropBox")] = generic.ArrayObject(
                map(generic.FloatObject, spec["crop_box"])
            )
        w.insert_page(pagina)
    salida = io.BytesIO()
    w.write(salida)
    return salida.getvalue()


A4 = (0, 0, 595, 842)
CARTA_HORIZONTAL = (0, 0, 792, 612)


@pytest.fixture
def pdf_multipagina(tmp_path) -> Path:
    """3 páginas de tamaños distintos; la segunda sin /Contents."""
    ruta = tmp_path / "entrada.pdf"
    ruta.write_bytes(
        construir_pdf(
            [
                {"media_box": A4},
                {"media_box": CARTA_HORIZONTAL, "contenido": False},
                {"media_box": A4},
            ]
        )
    )
    return ruta
