@echo off
setlocal
cd /d "%~dp0"
title Gerar Sistema de Senhas

echo =============================================
echo Gerando Sistema de Senhas - GDC + Epson
echo =============================================
echo.

python --version
if errorlevel 1 (
    echo ERRO: Python nao encontrado no PATH.
    pause
    exit /b 1
)

python -m PyInstaller --version
if errorlevel 1 (
    echo PyInstaller nao encontrado. Instalando...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo ERRO ao instalar PyInstaller.
        pause
        exit /b 1
    )
)

if not exist "sistema_senhas.ico" (
    echo ERRO: arquivo sistema_senhas.ico nao encontrado na mesma pasta do BAT.
    pause
    exit /b 1
)

python -m PyInstaller --noconfirm --clean --onefile --windowed --icon "sistema_senhas.ico" --name "Sistema de Senhas" sistema_senhas_gdc_customtkinter_sobre.py
if errorlevel 1 (
    echo.
    echo ERRO: PyInstaller nao conseguiu gerar o executavel.
    pause
    exit /b 1
)

if not exist "dist" mkdir "dist"
if not exist "dist\config.json" (
    > "dist\config.json" echo {
    >> "dist\config.json" echo     "ip_gdc": "10.1.1.199",
    >> "dist\config.json" echo     "porta_gdc": 888,
    >> "dist\config.json" echo     "impressora": "EPSON TM-T20X Receipt6"
    >> "dist\config.json" echo }
)
if not exist "dist\senhas_gravadas.txt" type nul > "dist\senhas_gravadas.txt"
if not exist "dist\impresso.txt" type nul > "dist\impresso.txt"

if not exist "dist\Sistema de Senhas.exe" (
    echo ERRO: o executavel nao foi encontrado na pasta dist.
    pause
    exit /b 1
)

echo.
echo =============================================
echo COMPILACAO CONCLUIDA!
echo =============================================
echo Arquivos iniciais em: %CD%\dist
echo - Sistema de Senhas.exe (com icone personalizado)
echo - config.json
echo - senhas_gravadas.txt
echo - impresso.txt
echo.
echo Ao abrir o programa, confira no registro da interface a linha
 echo "Pasta dos arquivos". Essa e a pasta usada pelo programa.
echo Se a pasta dist nao permitir escrita, os arquivos serao salvos em:
echo %%LOCALAPPDATA%%\SistemaSenhasGDC
echo.
pause
