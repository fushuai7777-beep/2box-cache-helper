@echo off
setlocal
if not exist "%~dp02Box.exe" (
    echo 2Box.exe is missing. Download 2Box from Wei1030/2Box on GitHub.
    echo Put 2Box.exe in the same folder as this launcher.
    pause
    exit /b 1
)
if not exist "%~dp0RefreshCache.exe" (
    echo RefreshCache.exe is missing. Extract the complete helper package.
    pause
    exit /b 1
)
echo Checking game update cache. Please wait...
"%~dp0RefreshCache.exe" --apply --quick --timeout 120 --report "%~dp0repair-last-report.json"
if errorlevel 1 (
    echo Cache refresh failed. Close 2Box before retrying.
    pause
    exit /b 1
)
echo Starting 2Box...
pushd "%~dp0"
start "" /D "%~dp0." "%~dp02Box.exe"
popd
endlocal
