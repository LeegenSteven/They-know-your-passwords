@echo off
setlocal
echo Configuring browser integration and opening the software for remote viewing.
call "%~dp0Setup.cmd" -AllowScreenCapture %*
exit /b %ERRORLEVEL%
