# Construye dist\FirmaPeru\FirmaPeru.exe (Firma Peru) y el instalador por usuario
# dist\installer\FirmaPeru-Setup-<version>.exe (Inno Setup 6, sin permisos de administrador).
# Uso (PowerShell, en la carpeta del proyecto):  .\build.ps1
#   -SinPruebas     omite pytest antes de construir
#   -SinInstalador  solo genera la carpeta dist\FirmaPeru, sin compilar el instalador
#   -Version x.y.z  version del instalador (predeterminada: 1.1.0)
param(
    [switch]$SinPruebas,
    [switch]$SinInstalador,
    [string]$Version = "1.1.0"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-Iscc {
    $candidatas = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    if (${env:ProgramFiles(x86)}) { $candidatas += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe") }
    if ($env:ProgramFiles) { $candidatas += (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe") }
    foreach ($ruta in $candidatas) {
        if (Test-Path -LiteralPath $ruta) { return $ruta }
    }
    $enPath = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($enPath) { return $enPath.Source }
    return $null
}

if (-not $SinPruebas) {
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Las pruebas fallaron; no se construye el ejecutable." }
}

python -m PyInstaller --noconfirm --clean firmador.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló." }

Write-Host ""
Write-Host "Listo: dist\FirmaPeru\FirmaPeru.exe"

if ($SinInstalador) {
    Write-Host "Para distribuir, copie la carpeta completa dist\FirmaPeru."
    return
}

$iscc = Find-Iscc
if (-not $iscc) {
    throw ("No se encontro Inno Setup 6 (ISCC.exe). Instalelo sin permisos de administrador con: " +
        "winget install JRSoftware.InnoSetup --scope user  (o use -SinInstalador).")
}

& $iscc "/DMyAppVersion=$Version" "installer\FirmaPeru.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup no pudo compilar el instalador (codigo $LASTEXITCODE)." }

$instalador = Join-Path $PSScriptRoot "dist\installer\FirmaPeru-Setup-$Version.exe"
if (-not (Test-Path -LiteralPath $instalador)) { throw "No se encontro el instalador esperado: $instalador" }

Write-Host ""
Write-Host "Instalador: $instalador"
