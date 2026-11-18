# Feature: Firma Perú per-user installer

## Objective
Ship the desktop signer as an installable Windows program named "Firma Perú" that installs and uninstalls without administrator rights (no UAC prompt).

## Problem
`build.ps1` produces only a PyInstaller `dist\FirmadorDNIe` folder that users must copy by hand. There is no installer, Start menu entry, or uninstaller.

## Why
Users on locked-down institutional PCs have no admin rights, so they need a per-user install.

## Scope
- Rename the product to "Firma Perú": window title, HTML `<title>`, exe `FirmaPeru.exe`, and output folder `dist\FirmaPeru`.
- Keep the settings folder `%APPDATA%\FirmadorDNIe` unchanged so existing settings and logo survive.
- Add an Inno Setup 6 script `installer/FirmaPeru.iss`:
  - `PrivilegesRequired=lowest`.
  - Installs to `{autopf}` (resolves to `%LOCALAPPDATA%\Programs`).
  - Registers the uninstaller under HKCU.
  - Adds a Start menu shortcut and an optional desktop shortcut.
  - UI in Spanish, x64 only.
- `build.ps1` compiles the installer after PyInstaller. It finds `ISCC.exe` per user or system-wide.
- Document in the README how to build and install.

## Out of scope
- Code signing, which would remove the SmartScreen warning.
- Auto-update.
- Bundling the institutional logo from `src/`.

## Constraints
- No admin rights at any step: Inno Setup is installed with `winget --scope user`.
- Unsigned binaries are acceptable for now.

## Tasks
- [x] T1 Rename the product to "Firma Perú" (`TITULO`, `ui/index.html`, `firmador.spec` name, `build.ps1` messages, README). Route: delegated direct (writer trigger: 2+ non-trivial files with T2/T3).
- [x] T2 Write the Inno Setup script plus the `build.ps1` installer step and `.gitignore` entries. Route: delegated direct.
- [x] T3 Build, then run a silent per-user install and uninstall from a non-elevated shell. Check the log for non-admin mode. Route: delegated direct (build is a per-action worker).

## Acceptance criteria
- `.\build.ps1` produces `dist\FirmaPeru\FirmaPeru.exe` and `dist\installer\FirmaPeru-Setup-<version>.exe`.
- The installer runs non-elevated, installs under `%LOCALAPPDATA%\Programs\Firma Peru`, creates a Start menu shortcut, and appears in "Apps" for the current user only.
- A silent uninstall removes the program files.
- `python -m pytest -q` passes.

## Checks
- `python -m pytest -q`
- `.\build.ps1 -SinPruebas`
- Silent install and uninstall with `/LOG`, reviewing the log.

## Progress
- 2026-11-18: Inno Setup 6.7.3 installed per user via winget (`C:\Users\<user>\AppData\Local\Programs\Inno Setup 6\ISCC.exe`). Branch `feat/instalador-sin-admin`.
- 2026-11-18: T1 done in `e3c2683` (`feat: rename product to Firma Perú`). `TITULO`, HTML `<title>` and header brand, spec name `FirmaPeru`, build messages, README. `%APPDATA%\FirmadorDNIe` unchanged. `python -m pytest -q`: 118 passed.
- 2026-11-18: T2 done in `e950630` (`build: add per-user Inno Setup installer`). `installer/FirmaPeru.iss` (UTF-8 BOM, AppId `{CD030D66-750A-4C77-B44E-518479224DF8}`), `build.ps1` `-Version`/`-SinInstalador` + ISCC lookup, README installer section. `.gitignore` already ignored `build/` and `dist/`; no change. `.\build.ps1 -SinPruebas`: exit 0, produced `dist\FirmaPeru\FirmaPeru.exe` and `dist\installer\FirmaPeru-Setup-1.0.0.exe` (only PyInstaller's pre-existing pycparser hidden-import warnings).
- 2026-11-18: T3 verified from a non-elevated shell (IsInRole Administrator = False). Silent install `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /DIR=<scratch>\fp_install /LOG=...`: exit 0, `FirmaPeru.exe` present. Log: `User privileges: None`, `Administrative install mode: No`, `Install mode root key: HKEY_CURRENT_USER`, `64-bit install mode: Yes`. Uninstall key present under HKCU only (HKLM and WOW6432Node absent), DisplayName `Firma Perú`, version 1.0.0. Start menu shortcut `Firma Perú.lnk` under `%APPDATA%\Microsoft\Windows\Start Menu\Programs`; no desktop shortcut (task unchecked). Silent uninstall via `unins000.exe`: exit 0; install dir, HKCU key and shortcut removed; `%APPDATA%\FirmadorDNIe` kept. GUI app not launched.

## Next step
Native review of the work-unit commits if RDD applies, then the user decides on push/PR. Optional follow-ups: an `.ico` for `SetupIconFile`, code signing (out of scope).
