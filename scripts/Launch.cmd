@echo off
setlocal
echo Starting They know your passwords...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" %*
set "TKYP_EXIT_CODE=%ERRORLEVEL%"
if "%TKYP_EXIT_CODE%"=="0" exit /b 0
echo.
echo Launch failed. The error above explains the problem.
echo Run CheckEnvironment.cmd for a file and runtime check.
pause
exit /b %TKYP_EXIT_CODE%
