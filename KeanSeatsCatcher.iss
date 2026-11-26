; ─────────────────────────────────────────────────────────────
; KeanSeatsCatcher — Inno Setup Installer Script
; ─────────────────────────────────────────────────────────────

#define MyAppName       "KeanSeatsCatcher"
#define VersionFile     FileOpen(SourcePath + "VERSION")
#define MyAppVersion    FileRead(VersionFile)
#call FileClose(VersionFile)
#define MyAppPublisher  "Limitime"
#define MyAppURL        "https://github.com/Daozhu1007/KeanSeatsCatcher"
#define MyAppExeName    "KeanSeatsCatcher.exe"

[Setup]
; ── Identity ────────────────────────────────────────────────
AppId={{DA0ZHU10-07K5-E4N5-5EAT-C4TCHER00001}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
VersionInfoTextVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoProductTextVersion={#MyAppVersion}

; ── Paths ───────────────────────────────────────────────────
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=.\dist
OutputBaseFilename=KeanSeatsCatcher_v{#MyAppVersion}_Setup

; ── Appearance ──────────────────────────────────────────────
SetupIconFile=.\assets\logo.ico
WizardStyle=modern
WizardSizePercent=120,120

; ── Compression ─────────────────────────────────────────────
Compression=lzma2
SolidCompression=yes
LZMAUseSeparateProcess=yes

; ── Requirements ────────────────────────────────────────────
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763

; ── Installer Behaviour ─────────────────────────────────────
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
DirExistsWarning=auto
DisableProgramGroupPage=no
LicenseFile=.\DISCLAIMER.txt

; ── Uninstall ───────────────────────────────────────────────
UninstallDisplayName={#MyAppName} v{#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu shortcut
Name: "{group}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"
; Start Menu uninstall shortcut
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
; Desktop shortcut (optional, controlled by [Tasks])
Name: "{autodesktop}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

