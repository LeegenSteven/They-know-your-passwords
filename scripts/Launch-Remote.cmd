@echo off
setlocal
echo Remote viewing allows the software window to appear in screen sharing.
call "%~dp0Launch.cmd" -AllowScreenCapture %*
exit /b %ERRORLEVEL%
