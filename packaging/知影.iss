#define MyAppName "知影"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "Fx"
#define MyAppExeName "知影.exe"

[Setup]
AppId={{706D8849-EDB3-43EC-9BAC-F3D828F67E2D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\知影
DefaultGroupName=知影
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\pack
OutputBaseFilename=知影-安装程序-v{#MyAppVersion}
SetupIconFile=..\icon\知影.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ChangesAssociations=yes
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=知影 Windows 安装程序
VersionInfoProductName=知影 · 视频知识工作台

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "..\pack\知影\*"; DestDir: "{app}"; Excludes: "config.yaml,api.yaml,workspace\*,output\*,Resource\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\pack\知影\api.yaml"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "config.installed.yaml"; DestDir: "{app}"; DestName: "config.yaml"; Flags: onlyifdoesntexist

[Dirs]
Name: "{userdocs}\知影"
Name: "{userdocs}\知影\Resource"
Name: "{userdocs}\知影\workspace"
Name: "{userdocs}\知影\output"

[Icons]
Name: "{group}\知影"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\知影"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\video-study"; ValueType: string; ValueName: ""; ValueData: "URL:知影本地回看"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\video-study"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\video-study\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKCU; Subkey: "Software\Classes\video-study\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" play-url --config ""{app}\config.yaml"" ""%1"""

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动知影"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
