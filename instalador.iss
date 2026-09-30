#define AppName "Lector PDF para IA"
#define AppVersion "0.3.0"
[Setup]
AppId={{9350566C-7B13-497F-969C-7BF0523397A5}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Lector PDF para IA
DefaultDirName={autopf}\LectorPDFIA
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=entregables
OutputBaseFilename=LectorPDFIA_Instalador
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
UninstallDisplayIcon={app}\LectorPDFIA.exe
SetupIconFile=icono.ico
CloseApplications=yes
[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
[Files]
Source: "dist\LectorPDFIA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\LectorPDFIA.exe"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\LectorPDFIA.exe"; Tasks: desktopicon
[Tasks]
Name: desktopicon; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"
[Run]
Filename: "{app}\LectorPDFIA.exe"; Description: "Abrir {#AppName}"; Flags: nowait postinstall skipifsilent
