#define MyAppName "Reclaim"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Yash Raj Sondhi"
#define MyAppExeName "Reclaim.exe"

[Setup]
AppId={{9B9A9D4B-3E21-4C7E-9A61-4A7D6A1C9F10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments=Extract. Verify. Reclaim.
AppCopyright=Copyright © 2026 Yash Raj Sondhi

DefaultDirName={autopf}\Reclaim
DefaultGroupName=Reclaim

OutputDir=installer_output
OutputBaseFilename=Reclaim-1.0.0-Setup

SetupIconFile=reclaim.ico
UninstallDisplayIcon={app}\Reclaim.exe

Compression=lzma
SolidCompression=yes

WizardStyle=modern

PrivilegesRequired=lowest

ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "dist\Reclaim\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Reclaim"; Filename: "{app}\Reclaim.exe"
Name: "{autodesktop}\Reclaim"; Filename: "{app}\Reclaim.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\Reclaim.exe"; Description: "Launch Reclaim"; Flags: nowait postinstall skipifsilent