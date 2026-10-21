; Minimal installer for the guincalo/zapretgui fork (upstream's own .iss is private).
#ifndef AppVersion
  #define AppVersion "0.0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\out\Zapret2"
#endif

[Setup]
AppId={{8C1E6C2E-5F44-4C55-9D57-2D2C2B6F0A11}
AppName=Zapret 2 (clean fork)
AppVersion={#AppVersion}
AppPublisher=guincalo/zapretgui
AppPublisherURL=https://github.com/guincalo/zapretgui
DefaultDirName={sd}\Zapret2Clean
AppendDefaultDirName=no
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\out
OutputBaseFilename=Zapret2Setup_{#AppVersion}_clean
Compression=lzma2/max
SolidCompression=yes
SetupIconFile={#SourceDir}\system\ico\Zapret2.ico
UninstallDisplayIcon={app}\_internal\Zapret.exe

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "user\*"

[Dirs]
Name: "{app}\user"

[Icons]
Name: "{autoprograms}\Zapret 2"; Filename: "{app}\_internal\Zapret.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Zapret 2"; Filename: "{app}\_internal\Zapret.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; Flags: unchecked

[Run]
Filename: "{app}\_internal\Zapret.exe"; WorkingDir: "{app}"; Description: "Запустить Zapret 2"; Flags: nowait postinstall skipifsilent shellexec

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM winws2.exe /T"; Flags: runhidden; RunOnceId: "killws2"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM winws.exe /T"; Flags: runhidden; RunOnceId: "killws"
Filename: "{sys}\sc.exe"; Parameters: "stop Monkey"; Flags: runhidden; RunOnceId: "stopdrv"
Filename: "{sys}\sc.exe"; Parameters: "delete Monkey"; Flags: runhidden; RunOnceId: "deldrv"
