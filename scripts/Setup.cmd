@echo off
setlocal
echo Registering Chrome and Edge browser integration...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0register-browsers.ps1"
if errorlevel 1 goto setup_failed
call "%~dp0Launch.cmd" %*
if errorlevel 1 exit /b 1
start "" "%SystemRoot%\System32\notepad.exe" "%~dp0first-use.md" >nul 2>nul
exit /b 0
:setup_failed
echo.
echo Browser setup failed. The error above explains the problem.
pause
exit /b 1
