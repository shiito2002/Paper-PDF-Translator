@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
rem PDFファイルをこのファイルにドラッグ＆ドロップすると翻訳します（複数可）
if "%~1"=="" (
  echo PDFファイルをこのアイコンにドラッグ＆ドロップしてください。
  pause
  exit /b
)
:loop
if "%~1"=="" goto end
python "%~dp0translate_pdf.py" "%~1"
shift
goto loop
:end
pause
