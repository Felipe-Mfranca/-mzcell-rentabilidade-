; installer.nsh — Script NSIS personalizado
; MZCell Rentabilidade
; Verifica Python, instala se necessário, recria venv no PC destino

!macro customInstall
  ; ── 1. VERIFICA SE PYTHON 3.x ESTÁ INSTALADO ─────────────────────────────
  nsExec::ExecToStack 'python --version'
  Pop $0
  Pop $1

  ${If} $0 != 0
    ; Python não encontrado — baixa e instala silenciosamente
    DetailPrint "Python não encontrado. Instalando Python 3.12..."
    inetc::get /CAPTION "Instalando Python 3.12..." \
      "https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe" \
      "$TEMP\python-installer.exe" /END
    ExecWait '"$TEMP\python-installer.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0' $0
    Delete "$TEMP\python-installer.exe"

    ${If} $0 != 0
      MessageBox MB_OK|MB_ICONEXCLAMATION "Falha ao instalar Python. Instale manualmente em https://python.org/downloads e marque 'Add Python to PATH'."
    ${Else}
      DetailPrint "Python 3.12 instalado com sucesso!"
    ${EndIf}
  ${Else}
    DetailPrint "Python encontrado: $1"
  ${EndIf}

  ; ── 2. RECRIA O VENV NO PC DESTINO ───────────────────────────────────────
  ; Garante que o venv aponta para o Python local (não o do PC de origem)
  DetailPrint "Recriando ambiente virtual Python..."
  SetDetailsPrint both

  nsExec::ExecToLog 'cmd /C "python -m venv "$INSTDIR\resources\venv" --clear"'

  ; ── 3. INSTALA DEPENDÊNCIAS ───────────────────────────────────────────────
  DetailPrint "Instalando dependências Python..."
  nsExec::ExecToLog 'cmd /C ""$INSTDIR\resources\venv\Scripts\python.exe" -m pip install --quiet fastapi uvicorn httpx"'

  ; ── 4. PERMISSÃO DE PASTA ─────────────────────────────────────────────────
  DetailPrint "Configurando permissões..."
  nsExec::ExecToLog 'icacls "$INSTDIR\resources" /grant *S-1-1-0:F /T /Q'

  DetailPrint "MZCell Rentabilidade instalado com sucesso!"
!macroend

!macro customUnInstall
  ; Limpa venv na desinstalação
  RMDir /r "$INSTDIR\resources\venv"
!macroend
