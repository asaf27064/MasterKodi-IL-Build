[Setup]
AppId={{A7B2D9C1-2F4E-4C11-9B6E-7D91A1FBC001}
AppName=MasterKodi IL
AppVersion=2.3.9
AppPublisher=MasterKodi IL
DefaultDirName=C:\MasterKodi IL
DisableDirPage=no
DisableProgramGroupPage=yes
OutputBaseFilename=MasterKodiIL_Setup
Compression=none
SolidCompression=no
DiskSpanning=no
WizardStyle=modern
PrivilegesRequired=admin
SetupIconFile=MasterKodiIL.ico
WizardImageFile=WizardImage.bmp
WizardSmallImageFile=WizardSmallImage.bmp
UninstallDisplayIcon={app}\kodi.exe
DirExistsWarning=no
LanguageDetectionMethod=none
ShowLanguageDialog=no

[Languages]
Name: "hebrew"; MessagesFile: "compiler:Languages\Hebrew.isl"

[Files]
Source: "package.7z"; DestDir: "{tmp}"; Flags: ignoreversion
Source: "7za.exe"; DestDir: "{tmp}"; Flags: ignoreversion deleteafterinstall
Source: "MasterKodiIL.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\MasterKodi IL"; Filename: "{app}\kodi.exe"; Parameters: "-p"; WorkingDir: "{app}"; IconFilename: "{app}\MasterKodiIL.ico"
Name: "{autoprograms}\MasterKodi IL"; Filename: "{app}\kodi.exe"; Parameters: "-p"; WorkingDir: "{app}"; IconFilename: "{app}\MasterKodiIL.ico"

[Run]
Filename: "{tmp}\7za.exe"; Parameters: "x ""{tmp}\package.7z"" -o""{app}"" -y"; Flags: waituntilterminated runhidden
Filename: "{app}\kodi.exe"; Parameters: "-p"; Flags: nowait postinstall skipifsilent

[Code]
function IsKodiRunning(): Boolean;
var
  ResultCode: Integer;
  OutputFile: string;
  S: AnsiString;
begin
  OutputFile := ExpandConstant('{tmp}\mk_tasklist.txt');
  Exec(ExpandConstant('{cmd}'),
    '/C tasklist /FI "IMAGENAME eq kodi.exe" > "' + OutputFile + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if LoadStringFromFile(OutputFile, S) then
    Result := (Pos('kodi.exe', Lowercase(string(S))) > 0)
  else
    Result := False;
end;

var
  DoCleanInstall: Boolean;

function InitializeSetup(): Boolean;
begin
  if IsKodiRunning() then
  begin
    MsgBox('Kodi פתוח כרגע. נא לסגור אותו לפני ההתקנה.', mbError, MB_OK);
    Result := False;
    exit;
  end;

  DoCleanInstall := True;
  Result := True;
end;

// Only a folder that is actually a Kodi/MasterKodi install may be recursively
// deleted -- otherwise a user who points the installer at an ordinary folder
// (Documents, a drive root) would have its contents destroyed.
function LooksLikeKodiInstall(Dir: String): Boolean;
begin
  Result := FileExists(Dir + '\kodi.exe')
         or DirExists(Dir + '\portable_data')
         or DirExists(Dir + '\addons');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if CurPageID = wpSelectDir then
  begin
    if DirExists(WizardDirValue) then
    begin
      if not LooksLikeKodiInstall(WizardDirValue) then
      begin
        // Existing, non-empty, and NOT a Kodi install -> refuse to wipe it.
        MsgBox(
          'התיקייה שנבחרה קיימת ואינה נראית כמו התקנת Kodi/MasterKodi:' + #13#10 +
          WizardDirValue + #13#10#13#10 +
          'כדי למנוע מחיקת קבצים לא קשורים, בחרו תיקייה חדשה או ריקה.',
          mbError, MB_OK);
        Result := False;
        exit;
      end;
      if MsgBox(
        'נמצאה התקנה קיימת של MasterKodi IL / Kodi בתיקייה:' + #13#10 +
        WizardDirValue + #13#10#13#10 +
        'האם למחוק אותה לחלוטין ולהתקין מחדש?',
        mbConfirmation, MB_YESNO
      ) <> IDYES then
      begin
        Result := False;
        exit;
      end;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    // Guard the destructive delete with the same marker check.
    if DoCleanInstall and DirExists(WizardDirValue)
       and LooksLikeKodiInstall(WizardDirValue) then
      DelTree(WizardDirValue, True, True, True);
  end;
end;

function InitializeUninstall(): Boolean;
begin
  if IsKodiRunning() then
  begin
    MsgBox('Kodi פתוח כרגע. נא לסגור אותו לפני הסרה.', mbError, MB_OK);
    Result := False;
    exit;
  end;
  Result := True;
end;

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: files; Name: "{userappdata}\Microsoft\Windows\Recent\MasterKodi IL*.lnk"