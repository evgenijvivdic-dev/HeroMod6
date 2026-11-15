@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "LOG=%~dp0build_log.txt"
set "OUT=%~dp0Release"
set "SRC=%~dp0Source\HeroMod.cpp"
echo === Сборка HeroMod.asi ===

rem 1. Plugin-SDK: переменная PLUGIN_SDK_DIR или файл sdk_path.txt рядом с build.bat
if not defined PLUGIN_SDK_DIR if exist "%~dp0sdk_path.txt" set /p PLUGIN_SDK_DIR=<"%~dp0sdk_path.txt"
if not defined PLUGIN_SDK_DIR goto :nosdk
if "%PLUGIN_SDK_DIR:~-1%"=="\" set "PLUGIN_SDK_DIR=%PLUGIN_SDK_DIR:~0,-1%"
if not exist "%PLUGIN_SDK_DIR%\plugin_vc" goto :nosdk

rem 2. Visual Studio: 32-битные инструменты C++
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" goto :novs
set "VSPATH="
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSPATH=%%i"
if not defined VSPATH goto :novs
call "%VSPATH%\VC\Auxiliary\Build\vcvars32.bat" >nul
if errorlevel 1 goto :novs

rem 3. Библиотека plugin_vc.lib (ищем внутри папки SDK)
set "SDKLIB="
for /f "delims=" %%f in ('dir /s /b "%PLUGIN_SDK_DIR%\plugin_vc.lib" 2^>nul') do if not defined SDKLIB set "SDKLIB=%%f"
if not defined SDKLIB goto :nolib

rem 4. Пути к заголовкам и библиотекам (через INCLUDE и LIB, чтобы не путаться в кавычках)
set "INCLUDE=%PLUGIN_SDK_DIR%\plugin_vc;%PLUGIN_SDK_DIR%\plugin_vc\game_vc;%PLUGIN_SDK_DIR%\shared;%PLUGIN_SDK_DIR%\shared\game;%INCLUDE%"
if defined DIRECTX9_SDK_DIR set "INCLUDE=%INCLUDE%;%DIRECTX9_SDK_DIR%\Include"
if defined DIRECTX9_SDK_DIR set "LIB=%LIB%;%DIRECTX9_SDK_DIR%\Lib\x86"

rem 5. Сборка
if not exist "%OUT%" mkdir "%OUT%"
if exist "%OUT%\HeroMod.asi" del /q "%OUT%\HeroMod.asi"
echo Компилирую...
cl /nologo /LD /MT /O2 /EHsc /std:c++17 /D_CRT_SECURE_NO_WARNINGS /D_CRT_NON_CONFORMING_SWPRINTFS /DGTAVC /DPLUGIN_SGV_10EN /DNDEBUG "%SRC%" /Fo"%OUT%\\" /Fe"%OUT%\HeroMod.asi" /link "%SDKLIB%" user32.lib > "%LOG%" 2>&1
if not exist "%OUT%\HeroMod.asi" goto :fail

rem 6. Если есть game_path.txt (путь к папке игры), копируем плагин сразу туда
set "GAMEDIR="
if exist "%~dp0game_path.txt" set /p GAMEDIR=<"%~dp0game_path.txt"
if not defined GAMEDIR goto :done
if not exist "%GAMEDIR%\gta-vc.exe" goto :done
copy /y "%OUT%\HeroMod.asi" "%GAMEDIR%\HeroMod.asi" >nul
echo Скопировано в папку игры: %GAMEDIR%

:done
echo.
echo ГОТОВО: %OUT%\HeroMod.asi
echo Положите его в папку игры рядом с gta-vc.exe и dinput8.dll.
if not defined CI pause
exit /b 0

:nosdk
echo.
echo Plugin-SDK не найден.
echo Скачайте Plugin-SDK с GitHub: DK22Pac/plugin-sdk
echo Затем создайте рядом с build.bat файл sdk_path.txt и впишите в него одну строку:
echo путь к папке SDK, например C:\dev\plugin-sdk
if not defined CI pause
exit /b 1

:novs
echo.
echo Не найдены инструменты C++ от Visual Studio.
echo Установите Visual Studio 2022 Community или Build Tools и отметьте
echo "Разработка классических приложений на C++".
if not defined CI pause
exit /b 1

:nolib
echo.
echo Не найдена библиотека plugin_vc.lib в папке SDK.
echo Откройте plugin_sdk.sln в Visual Studio и соберите проект plugin_vc
echo в конфигурации Release и платформе Win32, либо возьмите готовые библиотеки из релизов SDK.
if not defined CI pause
exit /b 1

:fail
echo.
echo ОШИБКА СБОРКИ. Первые найденные ошибки:
findstr /I /C:"error" "%LOG%"
echo.
echo Полный лог: %LOG%
echo Чаще всего виноваты строки с "// VERIFY" в HeroMod.cpp: сверьте имена с заголовками SDK.
echo Пришлите содержимое build_log.txt, и код можно будет поправить.
if not defined CI pause
exit /b 1
