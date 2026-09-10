Unicode true
RequestExecutionLevel user
ManifestDPIAware true
SetCompressor /SOLID lzma
SetCompressorDictSize 32
Name "Agent4Market ${VERSION}"
OutFile "${OUTPUT_FILE}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "Agent4Market Sales Director"
VIAddVersionKey "FileDescription" "Agent4Market per-user installer"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "WorkFlow_Market contributors"

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "安装 Agent4Market ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "本安装包包含 Claude Code / Codex CLI 模型后端。$\r$\n$\r$\n仅安装到当前用户的新版本目录，不覆盖或迁移旧版数据。现有同名目录会被拒绝。$\r$\n$\r$\n已包含 Node.js、Python 和 Pi。需要本机已安装 Git、ripgrep、fd 和 Microsoft Edge WebView2；生成演示文稿另需 LibreOffice。CLI 程序及其登录由你自行安装管理。$\r$\n$\r$\n安装后不会自动启动，请关闭旧版再启动新版。"
!define MUI_FINISHPAGE_TEXT "安装完成。$\r$\n$\r$\n旧版本和数据未改动。请先关闭旧版本，再通过 Agent4Market ${VERSION} 快捷方式启动。$\r$\n$\r$\n首次使用请在设置中选择 API 或 CLI 后端。详情见安装目录 INSTALL-NOTES.md。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${SOURCE_ROOT}\LICENSE"
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!define MUI_UNCONFIRMPAGE_TEXT_TOP "卸载只删除清单中未修改的程序文件。数据、模型设置、输出文件及改动过的文件会保留，版本目录不会递归删除。$\r$\n$\r$\n请先自行关闭本版本；卸载程序不会终止运行中的应用。"
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Var VerificationId
Var RootSuffix

!macro ExtractBootstrap
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\python"
  File "${PAYLOAD_ROOT}\.venv\Scripts\python.exe"
  File "${PAYLOAD_ROOT}\.venv\Scripts\*.dll"
  File "${PAYLOAD_ROOT}\.venv\Scripts\*.pyd"
  File "${PAYLOAD_ROOT}\.venv\Scripts\python311.zip"
  File "${SOURCE_ROOT}\desktop\installer-python311._pth"
  Rename "$PLUGINSDIR\python\installer-python311._pth" "$PLUGINSDIR\python\python311._pth"
  SetOutPath "$PLUGINSDIR"
  File /oname=bootstrap.py "${SOURCE_ROOT}\scripts\windows-installer-bootstrap.py"
  File /oname=privacy.py "${SOURCE_ROOT}\agent_platform\wechat_privacy.py"
!macroend

Function .onInit
  SetShellVarContext current
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "此安装包仅支持 Windows x64。"
    SetErrorLevel 2
    Abort
  ${EndIf}
  ${GetParameters} $0
  ${GetOptions} $0 "/VERIFY=" $VerificationId
  StrCpy $RootSuffix ""
  ${If} $VerificationId != ""
    StrCpy $RootSuffix "-test-$VerificationId"
  ${EndIf}
  StrCpy $INSTDIR "$PROFILE\Agent4Market-${VERSION}$RootSuffix"
FunctionEnd

Section "Agent4Market" SEC_MAIN
  !insertmacro ExtractBootstrap
  nsExec::ExecToLog '"$PLUGINSDIR\python\python.exe" -I -S "$PLUGINSDIR\bootstrap.py" prepare "$INSTDIR" "${VERSION}" "$VerificationId"'
  Pop $0
  ${If} $0 != 0
    SetErrorLevel 2
    Abort "安装前检查失败；请查看详情中的缺少依赖或目录问题。未覆盖现有目录。"
  ${EndIf}
  SetOverwrite off
  !include "${BUILD_ROOT}\payload.nsh"
  SetOutPath "$INSTDIR\runtime"
  File "${BUILD_ROOT}\install-manifest.json"
  nsExec::ExecToLog '"$PLUGINSDIR\python\python.exe" -I -S "$PLUGINSDIR\bootstrap.py" verify-staging "$INSTDIR" "${VERSION}" "$VerificationId"'
  Pop $0
  ${If} $0 != 0
    SetErrorLevel 2
    Abort "安装文件校验失败；程序尚未启用。"
  ${EndIf}
  nsExec::ExecToLog '"$INSTDIR\.venv\Scripts\python.exe" -I -B "$INSTDIR\plugin\market-director-copilot\scripts\init_local_data.py" --project "$INSTDIR"'
  Pop $0
  ${If} $0 != 0
    SetErrorLevel 2
    Abort "空白业务表初始化失败；程序尚未启用，现有数据未被覆盖。"
  ${EndIf}
  SetOutPath "$INSTDIR\runtime"
  File "${PAYLOAD_ROOT}\runtime\private-runtime.marker"
  SetOutPath "$INSTDIR"
  File "${PAYLOAD_ROOT}\Agent4Market.exe"
  nsExec::ExecToLog '"$PLUGINSDIR\python\python.exe" -I -S "$PLUGINSDIR\bootstrap.py" verify "$INSTDIR" "${VERSION}" "$VerificationId"'
  Pop $0
  ${If} $0 != 0
    Delete "$INSTDIR\Agent4Market.exe"
    Delete "$INSTDIR\runtime\private-runtime.marker"
    SetErrorLevel 2
    Abort "完整文件校验失败；未注册或启用此版本。"
  ${EndIf}
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ${If} $VerificationId == ""
    CreateShortcut "$DESKTOP\Agent4Market ${VERSION}.lnk" "$INSTDIR\Agent4Market.exe" "" "$INSTDIR\Agent4Market.exe"
    CreateShortcut "$SMPROGRAMS\Agent4Market ${VERSION}.lnk" "$INSTDIR\Agent4Market.exe" "" "$INSTDIR\Agent4Market.exe"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Agent4Market-${VERSION}" "DisplayName" "Agent4Market ${VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Agent4Market-${VERSION}" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Agent4Market-${VERSION}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Agent4Market-${VERSION}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Agent4Market-${VERSION}" "DisplayIcon" "$INSTDIR\Agent4Market.exe"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Agent4Market-${VERSION}" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Agent4Market-${VERSION}" "NoRepair" 1
  ${EndIf}
  SetErrorLevel 0
SectionEnd

Function un.onInit
  SetShellVarContext current
  StrCpy $VerificationId ""
  ${un.GetParameters} $0
  ${un.GetOptions} $0 "/VERIFY=" $VerificationId
FunctionEnd

Section "Uninstall"
  !insertmacro ExtractBootstrap
  nsExec::ExecToLog '"$PLUGINSDIR\python\python.exe" -I -S "$PLUGINSDIR\bootstrap.py" uninstall "$INSTDIR" "${VERSION}" "$VerificationId"'
  Pop $0
  ${If} $0 != 0
    SetErrorLevel 2
    Abort "安全校验未通过；保留安装目录，请勿手动批量删除数据。"
  ${EndIf}
  ${If} $VerificationId == ""
    Delete "$DESKTOP\Agent4Market ${VERSION}.lnk"
    Delete "$SMPROGRAMS\Agent4Market ${VERSION}.lnk"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Agent4Market-${VERSION}"
  ${EndIf}
  Delete "$INSTDIR\Uninstall.exe"
  SetErrorLevel 0
SectionEnd
