@echo off
setlocal
echo Confirm only after the updated game has launched successfully.
choice /C YN /N /M "Release this update's rollback files? [Y/N]: "
if errorlevel 2 exit /b 0
"%~dp0SharedCache.exe" confirm-update --root "%~dp0."
pause
endlocal
