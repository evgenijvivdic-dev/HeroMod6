@echo off
chcp 65001 >nul
cd /d "%~dp0Hero"
where python >nul 2>nul || (echo Установите Python 3.10+ с python.org, отметив "Add to PATH", и запустите снова. & pause & exit /b)
if not exist ".deps_ok" (
  echo Первый запуск: устанавливаю зависимости...
  python -m pip install -r requirements.txt && echo ok> .deps_ok
)
if "%ANTHROPIC_API_KEY%"=="" (
  if exist key.txt (
    set /p ANTHROPIC_API_KEY=<key.txt
  ) else (
    set /p ANTHROPIC_API_KEY=Вставьте ключ API Anthropic: 
    echo %ANTHROPIC_API_KEY%> key.txt
  )
)
python bridge_agent_vc.py
pause
