param([string]$Python = 'python', [switch]$UseExistingDependencies)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$buildPython = $Python
if (!$UseExistingDependencies) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    $buildPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    & $buildPython -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
$packagePath = Join-Path $PSScriptRoot 'dist\2box-cache-helper-v0.2.0'
if (Test-Path -LiteralPath $packagePath) { throw 'Package destination already exists; inspect before rebuilding.' }
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot 'build\pyinstaller-cache'
& $buildPython -m PyInstaller --noconfirm --clean --onedir --noupx --console --name SharedCache --distpath build\shared-dist --workpath build\shared-pyinstaller --specpath build shared_resources.py
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
Copy-Item -LiteralPath 'build\shared-dist\SharedCache' -Destination $packagePath -Recurse
Copy-Item -LiteralPath 'README.md','RELEASE_NOTES.md','LICENSE','THIRD_PARTY_NOTICES.md','repair_2box_assets.py','shared_resources.py','shared_update.py','requirements-build.txt','build.ps1' -Destination $packagePath
$launcherFiles = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.cmd' -File)
if ($launcherFiles.Count -ne 4) { throw 'Expected four shared-resource launcher scripts.' }
Copy-Item -LiteralPath $launcherFiles.FullName -Destination $packagePath
Copy-Item -LiteralPath 'licenses' -Destination $packagePath -Recurse
Compress-Archive -LiteralPath $packagePath -DestinationPath 'dist\2box-cache-helper-v0.2.0-windows-x64.zip'
Write-Output 'Built dist\2box-cache-helper-v0.2.0-windows-x64.zip'
