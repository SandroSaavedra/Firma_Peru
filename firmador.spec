# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: Firmador DNIe (ventana, sin consola, carpeta "onedir").
# Construir con:  python -m PyInstaller --noconfirm --clean firmador.spec
from PyInstaller.utils.hooks import collect_submodules

datas = [("ui", "ui")]

# pyHanko y su validador cargan algunos módulos de forma diferida. fontTools
# importa cada tabla de la fuente por nombre (ttLib.tables.*) y uharfbuzz es
# una extensión compilada: ambos se usan para incrustar Arial en el sello.
# La fuente se lee de C:\Windows\Fonts en tiempo de ejecución. El logo del
# usuario (src/) NO se empaqueta: se busca junto al .exe.
hiddenimports = (
    collect_submodules("pyhanko.sign")
    + collect_submodules("pyhanko.stamp")
    + collect_submodules("pyhanko.pdf_utils.font")
    + collect_submodules("pyhanko_certvalidator")
    + collect_submodules("fontTools.ttLib")
    + ["fontTools.subset", "fontTools.ttLib.scaleUpem", "uharfbuzz"]
)

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "pypdfium2", "matplotlib", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FirmadorDNIe",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FirmadorDNIe",
)
