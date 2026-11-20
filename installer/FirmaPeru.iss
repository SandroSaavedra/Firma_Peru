; Inno Setup 6 script for Firma Perú.
; Per-user install: no administrator rights and no UAC prompt.
; Build with build.ps1, or manually from the project folder:
;   ISCC.exe /DMyAppVersion=1.2.9 installer\FirmaPeru.iss
; Expects the PyInstaller output in dist\FirmaPeru (python -m PyInstaller firmador.spec).

#ifndef MyAppVersion
  #define MyAppVersion "1.2.9"
#endif
#define MyAppName "Firma Perú"
#define MyAppExeName "FirmaPeru.exe"

[Setup]
; Fixed AppId: upgrades and uninstall rely on it. Never change it.
AppId={{CD030D66-750A-4C77-B44E-518479224DF8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Firma Perú
; Per-user only: never offer the administrative install mode.
PrivilegesRequired=lowest
; {autopf} resolves to %LOCALAPPDATA%\Programs in per-user mode.
DefaultDirName={autopf}\Firma Peru
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\assets\firma-peru.ico
UninstallDisplayName={#MyAppName}
OutputDir=..\dist\installer
OutputBaseFilename=FirmaPeru-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\FirmaPeru\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
