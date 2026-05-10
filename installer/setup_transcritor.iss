; =============================================================
; setup_transcritor.iss
; Inno Setup 6 — Transcritor v7.0
; =============================================================

#define AppName      "Transcritor"
#define AppVersion   "7.0"
#define AppPublisher "Joao Arias"
#define AppExeName   "transcritor_v7.exe"
#define SourceDist   "..\build\transcritor_v7.dist"
#define SourceModel  "..\modelos\medium"

[Setup]
AppId={{F3A2B1C0-9D4E-4F8A-B2C3-1A2B3C4D5E6F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=
DefaultDirName={localappdata}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=Output
OutputBaseFilename=Setup_Transcritor_v7
SetupIconFile=..\transcritor.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}

; Compressao maxima (LZMA2 comprime bem as DLLs do app)
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; Visual moderno
WizardStyle=modern
WizardResizable=no

; Exige Windows 10 ou superior (64-bit)
MinVersion=10.0
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

; Nao requer admin se instalar em AppData do usuario
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

DisableProgramGroupPage=yes
DisableWelcomePage=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Messages]
; Textos da tela de boas-vindas
WelcomeLabel1=Bem-vindo ao instalador do{br}{#AppName}
WelcomeLabel2=Este assistente vai instalar o {#AppName} versao {#AppVersion} no seu computador.{br}{br}O programa transcreve reunioes gravadas (modo Arquivo) ou em tempo real via microfone e audio do headset (modo Ao Vivo).{br}{br}Clique em Avancar para continuar.

[Tasks]
Name: "desktopicon"; Description: "Criar icone na area de trabalho"; GroupDescription: "Opcoes adicionais:"; Flags: unchecked

[Files]
; ----- App compilado (Nuitka --standalone) -----
Source: "{#SourceDist}\*"; \
    DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; ----- Modelo Whisper Medium -----
Source: "{#SourceModel}\*"; \
    DestDir: "{app}\modelos\medium"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; ----- replace.txt (template em branco se nao existir) -----
Source: "..\replace.txt"; \
    DestDir: "{app}"; \
    Flags: ignoreversion onlyifdoesntexist; \
    DestName: replace.txt

[Dirs]
; Pastas de dados do usuario em Documentos (gravadas pelo app em execucao)
Name: "{userdocs}\Transcritor"
Name: "{userdocs}\Transcritor\output"
Name: "{userdocs}\Transcritor\logs"
Name: "{userdocs}\Transcritor\temp"

[Icons]
; Menu Iniciar
Name: "{group}\{#AppName}"; \
    Filename: "{app}\{#AppExeName}"; \
    IconFilename: "{app}\{#AppExeName}"

Name: "{group}\Desinstalar {#AppName}"; \
    Filename: "{uninstallexe}"

; Area de trabalho (opcional, marcada pelo usuario)
Name: "{autodesktop}\{#AppName}"; \
    Filename: "{app}\{#AppExeName}"; \
    IconFilename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon

[Run]
; Oferta de abrir o app ao final da instalacao
Filename: "{app}\{#AppExeName}"; \
    Description: "Abrir o {#AppName} agora"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove apenas arquivos temporarios; preserva output e logs do usuario
Type: filesandordirs; Name: "{userdocs}\Transcritor\temp"
