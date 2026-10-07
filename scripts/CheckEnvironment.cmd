@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0check-environment.ps1"
set "TKYP_EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %TKYP_EXIT_CODE%
