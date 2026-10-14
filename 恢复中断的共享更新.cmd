@echo off
setlocal
"%~dp0SharedCache.exe" recover-update --root "%~dp0."
pause
endlocal
