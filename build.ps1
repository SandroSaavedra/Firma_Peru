# Construye dist\FirmaPeru\FirmaPeru.exe (Firma Peru)
# Uso (PowerShell, en la carpeta del proyecto):  .\build.ps1
#   -SinPruebas  omite pytest antes de construir
param([switch]$SinPruebas)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $SinPruebas) {
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Las pruebas fallaron; no se construye el ejecutable." }
}

python -m PyInstaller --noconfirm --clean firmador.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló." }

Write-Host ""
Write-Host "Listo: dist\FirmaPeru\FirmaPeru.exe"
Write-Host "Para distribuir, copie la carpeta completa dist\FirmaPeru."
