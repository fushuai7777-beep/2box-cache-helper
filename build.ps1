param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& $Python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
$buildPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $buildPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot 'build\pyinstaller-cache'
& $buildPython -m PyInstaller --noconfirm --clean --onefile --console --name RefreshCache --distpath dist\2box-cache-helper --workpath build\pyinstaller --specpath build repair_2box_assets.py
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
$packagePath = Join-Path $PSScriptRoot 'dist\2box-cache-helper'
Copy-Item -LiteralPath 'README.md','LICENSE','THIRD_PARTY_NOTICES.md','repair_2box_assets.py' -Destination $packagePath -Force
$launcherFiles = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.cmd' -File)
if ($launcherFiles.Count -ne 1) { throw 'Expected exactly one launcher script.' }
Copy-Item -LiteralPath $launcherFiles[0].FullName -Destination $packagePath -Force
Copy-Item -LiteralPath 'licenses' -Destination $packagePath -Recurse -Force
Compress-Archive -Path (Join-Path $packagePath '*') -DestinationPath 'dist\2box-cache-helper.zip' -Force
Write-Output 'Built dist\2box-cache-helper.zip'
