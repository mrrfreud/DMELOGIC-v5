; ============================================================================
; DMELogic with Nova — Inno Setup installer
;
; Design goals (v5):
;   * Application code installs to Program Files (per-machine, read-only).
;   * ALL runtime data lives separately under C:\ProgramData\DMELogic, created
;     here with write permission for all users — the install folder never grows
;     with patient data, scans, or backups.
;   * The installer provisions every data subfolder the app expects.
;
; Build prerequisites:
;   1. Build the app bundle:   pyinstaller installer\DMELogic.spec
;      (produces dist\DMELogic\DMELogic.exe + _internal\)
;   2. (Optional) Place a bundled Tesseract under vendor\tesseract\.
;   3. Compile this script with Inno Setup 6:  iscc installer\DMELogic.iss
; ============================================================================

#define MyAppName "DMELogic"
#define MyAppNameFull "DMELogic with Nova"
#define MyAppVersion "5.0.0"
#define MyAppPublisher "DMELogic"
#define MyAppExeName "DMELogic.exe"
#define MyAppURL "https://github.com/mrrfreud/DMELOGIC-v5"
; Canonical shared data root (matches dmelogic.config.data_root()).
#define DataRoot "{commonappdata}\DMELogic"

[Setup]
AppId={{B2F7C9D1-4A6E-4C2F-9E3A-7D8B1F0A5C42}
AppName={#MyAppNameFull}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; Per-machine install into Program Files. Requires admin (for the shared
; data root and Program Files write); this matches a multi-user pharmacy PC.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=Output
OutputBaseFilename=DMELogic_Setup_{#MyAppVersion}
SetupIconFile=..\assets\DMELogic Icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
DisableProgramGroupPage=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; PyInstaller bundle (exe + _internal). Build with installer\DMELogic.spec.
Source: "..\dist\DMELogic\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Bundled Tesseract OCR runtime (optional — omit the folder to use a system install).
Source: "..\vendor\tesseract\*"; DestDir: "{app}\tesseract"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Dirs]
; Shared data root + every subfolder the app uses, writable by all users.
Name: "{#DataRoot}";                  Permissions: users-modify
Name: "{#DataRoot}\Databases";        Permissions: users-modify
Name: "{#DataRoot}\Backups";          Permissions: users-modify
Name: "{#DataRoot}\Scans";            Permissions: users-modify
Name: "{#DataRoot}\DeliveryTickets";  Permissions: users-modify
Name: "{#DataRoot}\FaxPackets";       Permissions: users-modify
Name: "{#DataRoot}\PatientDocuments"; Permissions: users-modify
Name: "{#DataRoot}\Tickets";          Permissions: users-modify
Name: "{#DataRoot}\POD";              Permissions: users-modify
Name: "{#DataRoot}\CMN";              Permissions: users-modify
Name: "{#DataRoot}\Exports";          Permissions: users-modify
Name: "{#DataRoot}\Logs";             Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppNameFull}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\assets\DMELogic Icon.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\assets\DMELogic Icon.ico"; Tasks: desktopicon

[Registry]
; Per-machine install metadata. The app reads data_root from config/ProgramData,
; so we record the data root here for support/diagnostics only.
Root: HKLM; Subkey: "Software\DMELogic"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\DMELogic"; ValueType: string; ValueName: "DataRoot"; ValueData: "{#DataRoot}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\DMELogic"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove install tree only. Patient data under ProgramData is intentionally
; preserved on uninstall so an upgrade/reinstall keeps the dataset.
Type: filesandordirs; Name: "{app}\_internal"
