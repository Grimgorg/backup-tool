# Builds "GoG Sync.exe" into this folder (next to gog-token.dat and gog-manifest.dat, which it uses).
# Needs the venv with the build tools: .venv\Scripts\python.exe -m pip install pyinstaller sv-ttk
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'

# PyInstaller logs to stderr, which must not become a terminating error under 'Stop'.
$ErrorActionPreference = 'Continue'
& $Python -m PyInstaller --noconfirm --onefile --windowed --name 'GoG Sync' `
    --distpath $Root --workpath (Join-Path $Root 'build') --specpath (Join-Path $Root 'build') `
    --paths (Join-Path $Root 'gogrepoc') --hidden-import gogrepoc --collect-data sv_ttk `
    --exclude-module PyQt5 (Join-Path $Root 'gog_sync.py')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }
