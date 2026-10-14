@echo off
setlocal
if not exist "%~dp0SharedCache.exe" (
  echo SharedCache.exe is missing.
  pause
  exit /b 1
)
pushd "%~dp0"
"%~dp0SharedCache.exe" launch --root "%~dp0."
if errorlevel 1 (
  echo Resource check failed. 2Box was not started.
  echo Close all games and 2Box. Complete the official game update first.
  echo If the previous update worked, run the confirmation shortcut.
  popd
  pause
  exit /b 1
)
popd
endlocal
