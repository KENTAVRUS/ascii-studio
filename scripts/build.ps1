$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run setup.bat first.' }
$releaseVersion = & $pythonExe -c 'from ascii_studio import __version__; print(__version__)'
$releaseRoot = Join-Path $projectRoot "dist\$releaseVersion"
$releaseDirectory = Join-Path $releaseRoot 'ASCII Studio'
$runningRelease = Get-Process -Name 'ASCII Studio' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $releaseDirectory 'ASCII Studio.exe') }
if ($runningRelease) { throw 'Close this release of ASCII Studio before rebuilding it.' }
& $pythonExe -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $pythonExe scripts\make_assets.py
if ($LASTEXITCODE -ne 0) { throw 'Asset generation failed.' }
$originalBuildPath = $env:PATH
try {
    # Do not collect unrelated DLLs from developer tools (Poppler, Git, etc.).
    $pythonBase = & $pythonExe -c 'import sys; print(sys.base_prefix)'
    $env:PATH = "$projectRoot\.venv\Scripts;$pythonBase;$env:SystemRoot\System32;$env:SystemRoot"
    & $pythonExe -m PyInstaller --noconfirm --windowed --onedir --distpath $releaseRoot --workpath build\windows --name 'ASCII Studio' --icon assets\icon.ico --add-data 'assets;assets' --collect-all imageio_ffmpeg --exclude-module tkinter main.py
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
} finally {
    $env:PATH = $originalBuildPath
}
Copy-Item -LiteralPath README.md -Destination (Join-Path $releaseDirectory 'README.md')
Copy-Item -LiteralPath THIRD_PARTY.md -Destination (Join-Path $releaseDirectory 'THIRD_PARTY.md')
& $pythonExe scripts\collect_licenses.py $releaseDirectory
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
if (Test-Path -LiteralPath 'docs') {
    New-Item -ItemType Directory -Path (Join-Path $releaseDirectory 'docs') -Force | Out-Null
    Get-ChildItem -LiteralPath 'docs' -File | Copy-Item -Destination (Join-Path $releaseDirectory 'docs')
}
New-Item -ItemType Directory -Path (Join-Path $releaseDirectory 'assets') -Force | Out-Null
Copy-Item -LiteralPath 'assets\icon.png' -Destination (Join-Path $releaseDirectory 'assets\icon.png')
Write-Output "Ready: $releaseDirectory\ASCII Studio.exe"
