"""La CLI `firma1.py` sigue funcionando sobre `firma_pdf.py`."""
from pyhanko.pdf_utils.reader import PdfFileReader

import firma1


def test_cli_firma_con_caja_predeterminada(monkeypatch, tmp_path, pdf_multipagina, firmante, capsys):
    monkeypatch.setattr(firma1, "elegir_llave_de_firma", lambda filtro: firmante._llave)
    salida = tmp_path / "salida.pdf"
    monkeypatch.setattr("sys.argv", ["firma1.py", str(pdf_multipagina), str(salida)])

    firma1.main()

    assert "3 de 3 páginas selladas" in capsys.readouterr().out
    with open(salida, "rb") as f:
        firmas = PdfFileReader(f, strict=False).embedded_signatures
        assert len(firmas) == 1
        assert firmas[0].sig_object["/Reason"] == "Soy el autor del documento"


def test_cli_visto_bueno_con_motivo(monkeypatch, tmp_path, pdf_multipagina, firmante):
    monkeypatch.setattr(firma1, "elegir_llave_de_firma", lambda filtro: firmante._llave)
    salida = tmp_path / "vb.pdf"
    monkeypatch.setattr(
        "sys.argv", ["firma1.py", str(pdf_multipagina), str(salida), "--tipo", "vb", "--motivo", "Conforme"]
    )

    firma1.main()

    with open(salida, "rb") as f:
        assert PdfFileReader(f, strict=False).embedded_signatures[0].sig_object["/Reason"] == "Conforme"


def test_cli_visto_bueno_con_imagen(monkeypatch, tmp_path, pdf_multipagina, firmante):
    from PIL import Image

    imagen = tmp_path / "check.png"
    Image.new("RGB", (80, 80), (200, 0, 0)).save(imagen)
    monkeypatch.setattr(firma1, "elegir_llave_de_firma", lambda filtro: firmante._llave)
    salida = tmp_path / "vb_img.pdf"
    monkeypatch.setattr(
        "sys.argv", ["firma1.py", str(pdf_multipagina), str(salida), "--tipo", "vb", "--imagen-vb", str(imagen)]
    )

    firma1.main()

    with open(salida, "rb") as f:
        reader = PdfFileReader(f, strict=False)
        pagina = reader.find_page_for_modification(2)[0].get_object()
        anot = next(a.get_object() for a in pagina["/Annots"])
        xobjects = anot["/AP"]["/N"].get_object()["/Resources"]["/XObject"]
        assert any(x.get_object().get("/Subtype") == "/Image" for x in xobjects.values())


def test_cli_firma_con_logo(monkeypatch, tmp_path, pdf_multipagina, firmante):
    from PIL import Image

    logo = tmp_path / "logo.png"
    Image.new("RGB", (60, 40), (0, 80, 160)).save(logo)
    monkeypatch.setattr(firma1, "elegir_llave_de_firma", lambda filtro: firmante._llave)
    salida = tmp_path / "f.pdf"
    monkeypatch.setattr("sys.argv", ["firma1.py", str(pdf_multipagina), str(salida), "--tipo", "firma", "--logo", str(logo)])

    firma1.main()

    with open(salida, "rb") as f:
        reader = PdfFileReader(f, strict=False)
        pagina = reader.find_page_for_modification(2)[0].get_object()
        anot = next(a.get_object() for a in pagina["/Annots"])
        xobjects = anot["/AP"]["/N"].get_object()["/Resources"]["/XObject"]
        assert any(x.get_object().get("/Subtype") == "/Image" for x in xobjects.values())
