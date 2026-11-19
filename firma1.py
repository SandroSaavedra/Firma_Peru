"""
Firma un PDF con el DNI electrónico y estampa el sello visual en TODAS las
páginas, con una sola firma criptográfica (PAdES).

Cómo funciona:
  - El DNIe se usa a través de Windows CNG (minidriver de RENIEC), no PKCS#11.
    Windows pide el PIN con su propio cuadro de diálogo; el script no lo ve.
  - El campo de firma tiene un widget (el sello) en cada página; el de la
    última página es el principal. Todos comparten el mismo aspecto.
  - Se hace UNA sola firma criptográfica, que cubre el documento completo
    (incluidos los sellos).
  - Si el PDF ya tiene firmas, la nueva firma se sella igual en todas las
    páginas y las firmas anteriores siguen siendo válidas. Solo en un documento
    certificado el sello va únicamente en el campo de firma de la última página.

La lógica de firma está en firma_pdf.py (compartida con la aplicación de
escritorio app.py).

Uso:
    1) Ver qué certificados tiene tu DNIe (no pide PIN):
         python firma1.py --listar
    2) Firmar (elige solo el certificado de FIRMA del DNIe):
         python firma1.py entrada.pdf salida.pdf
       Si hubiera más de uno, indica parte del nombre del titular:
         python firma1.py entrada.pdf salida.pdf --cert "FIR"
    3) Tipo de sello (predeterminado: firma de autor):
         python firma1.py entrada.pdf salida.pdf --tipo firma --logo logo.png
         python firma1.py entrada.pdf salida.pdf --tipo vb --motivo "Doy V° B°"
         python firma1.py entrada.pdf salida.pdf --tipo vb --imagen-vb check.png
"""
import argparse
import sys

from dnie_cng import ErrorCNG, FirmanteCNG, elegir_llave_de_firma, listar_llaves
from firma_pdf import ErrorFirma, OpcionesFirma, firmar_pdf

TIPOS_CLI = {"firma": "firma", "vb": "visto_bueno"}


def listar_certificados():
    """Muestra los certificados del DNIe para saber cuál es el de firma."""
    for llave in listar_llaves():
        print("Titular  :", llave.titular)
        print("Llave CNG:", llave.nombre)
        print("Uso clave:", ", ".join(sorted(llave.usos)) or "(no indicado)")
        print("De firma :", "SI" if llave.es_de_firma else "no")
        print("-" * 60)


def firmar(filtro_cert, entrada, salida, tipo="firma", motivo=None, logo=None, imagen_vb=None):
    llave = elegir_llave_de_firma(filtro_cert)
    print(f"Certificado: {llave.titular}")
    # Posición predeterminada del tipo, abajo a la derecha; tamaño fijo (160 x 40 o 140 x 110 pt).
    opciones = OpcionesFirma(tipo=tipo, motivo=motivo, logo=logo, imagen_vb=imagen_vb)
    resultado = firmar_pdf(entrada, salida, FirmanteCNG(llave), llave.titular, opciones)
    if resultado.modo == "solo_campo":
        print("El documento está certificado: el sello va solo en el campo de firma.")
    elif resultado.firmas_previas:
        print(f"El PDF ya tenía {resultado.firmas_previas} firma(s); siguen siendo válidas.")
    print(
        f"Listo: {len(resultado.paginas_selladas)} de {resultado.total_paginas} páginas selladas, "
        f"1 firma digital -> {salida}"
    )


def main():
    p = argparse.ArgumentParser(description="Firma PDF con DNIe, sello en todas las páginas")
    p.add_argument("entrada", nargs="?")
    p.add_argument("salida", nargs="?")
    p.add_argument("--cert", help="Parte del nombre del titular, si hay más de un certificado de firma")
    p.add_argument("--listar", action="store_true", help="Solo listar certificados del DNIe")
    p.add_argument(
        "--tipo", choices=sorted(TIPOS_CLI), default="firma",
        help="firma: firma de autor (logo y texto); vb: visto bueno (marca de verificación)",
    )
    p.add_argument("--motivo", help="Motivo de la firma (predeterminado según el tipo)")
    p.add_argument("--logo", help="Imagen PNG o JPG del logo (solo para --tipo firma)")
    p.add_argument(
        "--imagen-vb", help="Imagen PNG o JPG que va arriba en el visto bueno (solo para --tipo vb)"
    )
    args = p.parse_args()

    try:
        if args.listar:
            listar_certificados()
            return
        if not (args.entrada and args.salida):
            p.error("Indica entrada.pdf y salida.pdf (usa --listar para ver los certificados)")
        firmar(args.cert, args.entrada, args.salida, TIPOS_CLI[args.tipo], args.motivo, args.logo, args.imagen_vb)
    except ErrorCNG as e:
        sys.exit(f"Error con el DNIe: {e}")
    except ErrorFirma as e:
        sys.exit(f"No se pudo firmar: {e}")


if __name__ == "__main__":
    main()
