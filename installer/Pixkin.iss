#define AppName "Pixkin"
#define AppVersion GetEnv("PIXKIN_INSTALLER_VERSION")
#define ProjectRoot GetEnv("PIXKIN_PROJECT_ROOT")
#define InstallModeSource GetEnv("PIXKIN_INSTALL_MODE_SOURCE")
#define AppIdValue "{{A31F7F5B-CE81-49BA-93B9-5EFCF8695731}"

[Setup]
AppId={#AppIdValue}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Pixkin
DefaultDirName={localappdata}\Programs\Pixkin
DefaultGroupName=Pixkin
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ProjectRoot}\release
OutputBaseFilename=Pixkin-Setup-{#AppVersion}
SetupIconFile={#ProjectRoot}\assets\pixkin.ico
UninstallDisplayIcon={app}\Pixkin.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ChangesAssociations=no
ChangesEnvironment=no
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked
Name: "startup"; Description: "登录 Windows 后自动启动 Pixkin"; GroupDescription: "启动选项："; Flags: unchecked

[Files]
Source: "{#ProjectRoot}\dist\Pixkin\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#InstallModeSource}"; DestDir: "{app}"; DestName: "install-mode.json"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Pixkin"; Filename: "{app}\Pixkin.exe"
Name: "{autodesktop}\Pixkin"; Filename: "{app}\Pixkin.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Pixkin"; ValueData: """{app}\Pixkin.exe"" --minimized"; Tasks: startup; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Pixkin"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\Pixkin.exe"; Description: "启动 Pixkin"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteUserData: Boolean;

function VersionPart(const Version: String; Index: Integer): Integer;
var
  Position, PartIndex, PartStart: Integer;
begin
  Result := 0;
  PartIndex := 0;
  PartStart := 1;
  for Position := 1 to Length(Version) + 1 do
  begin
    if Position > Length(Version) then
    begin
      if PartIndex = Index then
        Result := StrToIntDef(
          Copy(Version, PartStart, Position - PartStart), 0
        );
      Exit;
    end;
    if Version[Position] = '.' then
    begin
      if PartIndex = Index then
      begin
        Result := StrToIntDef(
          Copy(Version, PartStart, Position - PartStart), 0
        );
        Exit;
      end;
      PartIndex := PartIndex + 1;
      PartStart := Position + 1;
    end;
  end;
end;

function CompareVersions(const Left, Right: String): Integer;
var
  Index, LeftPart, RightPart: Integer;
begin
  Result := 0;
  for Index := 0 to 2 do
  begin
    LeftPart := VersionPart(Left, Index);
    RightPart := VersionPart(Right, Index);
    if LeftPart < RightPart then
    begin
      Result := -1;
      Exit;
    end;
    if LeftPart > RightPart then
    begin
      Result := 1;
      Exit;
    end;
  end;
end;

function AllowDowngrade: Boolean;
begin
  Result := Pos('/ALLOWDOWNGRADE', Uppercase(GetCmdTail)) > 0;
end;

function TestMode: Boolean;
begin
  Result := Pos('/TESTMODE', Uppercase(GetCmdTail)) > 0;
end;

function InitializeSetup: Boolean;
var
  InstalledVersion: String;
  UninstallKey: String;
begin
  Result := True;
  UninstallKey :=
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
    '{#AppIdValue}_is1';
  if RegQueryStringValue(
    HKCU, UninstallKey, 'DisplayVersion', InstalledVersion
  ) then
  begin
    if (CompareVersions('{#AppVersion}', InstalledVersion) < 0) and
       (not AllowDowngrade) then
    begin
      MsgBox(
        '检测到已安装版本 ' + InstalledVersion +
        '。安装器默认拒绝降级；如需回滚，请从 Pixkin 的回滚入口启动上一稳定安装器。',
        mbError, MB_OK
      );
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  RollbackDir, RollbackInstaller, RollbackHash: String;
begin
  if (CurStep = ssPostInstall) and (not TestMode) then
  begin
    RollbackDir := ExpandConstant(
      '{localappdata}\Pixkin\updates\rollback'
    );
    ForceDirectories(RollbackDir);
    RollbackInstaller := RollbackDir +
      '\Pixkin-Setup-{#AppVersion}.exe';
    CopyFile(ExpandConstant('{srcexe}'), RollbackInstaller, False);
    RollbackHash := Lowercase(GetSHA256OfFile(RollbackInstaller));
    SaveStringToFile(
      RollbackInstaller + '.sha256', RollbackHash, False
    );
  end;
end;

function InitializeUninstall: Boolean;
begin
  if Pos('/KEEPUSERDATA', Uppercase(GetCmdTail)) > 0 then
    DeleteUserData := False
  else
    DeleteUserData :=
      MsgBox(
        '是否同时彻底删除 Pixkin 的本地用户数据？' + #13#10 + #13#10 +
        '选择“否”将保留配置、聊天、角色、备份与伙伴工坊任务，便于以后重新安装。',
        mbConfirmation, MB_YESNO
      ) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DeleteUserData then
    DelTree(
      ExpandConstant('{localappdata}\Pixkin'),
      True, True, True
    );
end;
