# Syncs GOG offline installers + extras (Windows, English + German) to D:\GOG Export using gogrepoc.
# By default re-scans the whole library, shows what would be downloaded and deleted, and asks before
# changing anything. Downloads installers plus all extras (manuals, soundtracks, artwork, wallpapers, ...).
# Usage:
#   .\sync.ps1              full manifest update, confirm, then download installers and extras
#   .\sync.ps1 -NoExtras    installers only
#   .\sync.ps1 -Quick       only scan new/updated games instead of the whole library
#   .\sync.ps1 -DryRun      only show what would be downloaded and deleted
#   .\sync.ps1 -Verify      also verify MD5/size/zip integrity after downloading
#   .\sync.ps1 -NoSort      leave the files flat in each game folder instead of sorting them
# Files are sorted into Installer\ and Extras\<category>\ subfolders (organize.py). gogrepoc only
# finds files directly in the game folder, so they are flattened before and sorted again after each run.
# The folder is kept an exact mirror of the library: everything the manifest no longer lists (old installer
# versions, removed extras, games no longer owned) is moved to !orphaned before downloading, deleted once
# all downloads are complete, and moved back if anything fails.
param(
    [switch]$NoExtras,
    [switch]$Quick,
    [switch]$DryRun,
    [switch]$Verify,
    [switch]$NoSort
)

$ErrorActionPreference = 'Stop'
$Root    = $PSScriptRoot
$Python  = Join-Path $Root '.venv\Scripts\python.exe'
$Gogrepo = Join-Path $Root 'gogrepoc\gogrepoc.py'
$Organize = Join-Path $Root 'organize.py'
$SaveDir = 'D:\GOG Export'
$OrphanDir = Join-Path $SaveDir '!orphaned'
$Filter  = @('-os', 'windows', '-lang', 'en', 'de')
# Files not needed for a fresh install (glob patterns, matched case-insensitively):
#  - incremental "old version to new version" patches; the full installer is already the latest version
#  - Witcher 3 language-pack patches (GOG lists them as extras, so -lang doesn't filter them); keeps only DE
$SkipFiles = @('-skipfiles', 'patch_*_to_*', 'patch_witcher3_[!d]?_goty_*')

# gogrepoc keeps gog-token.dat / gog-manifest.dat in the working directory.
Set-Location $Root

if (-not (Test-Path (Join-Path $Root 'gog-token.dat'))) {
    Write-Error "Not logged in. Run .\login.cmd first."
}
if (-not (Test-Path $SaveDir)) {
    New-Item -ItemType Directory -Path $SaveDir | Out-Null
}

# Full console output (including Python tracebacks, which gogrepo.log misses) goes here.
$LogDir = Join-Path $Root 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$Log = Join-Path $LogDir ('sync-{0:yyyy-MM-dd_HHmmss}.log' -f (Get-Date))
$env:PYTHONUNBUFFERED = '1'

function Invoke-Gogrepo {
    # Native stderr must not become a terminating error under 'Stop'.
    $ErrorActionPreference = 'Continue'
    & $Python $Gogrepo @args 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "gogrepoc $($args[0]) failed (exit $LASTEXITCODE), see $Log" }
}

function Invoke-GogrepoQuiet {
    # Like Invoke-Gogrepo, but the output only goes to the log and is returned instead of shown.
    $ErrorActionPreference = 'Continue'
    $lines = & $Python $Gogrepo @args 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "gogrepoc $($args[0]) failed (exit $LASTEXITCODE), see $Log" }
    $lines
}

function Invoke-Organize {
    $ErrorActionPreference = 'Continue'
    & $Python $Organize @args $SaveDir 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "organize.py $($args[0]) failed (exit $LASTEXITCODE), see $Log" }
}

function Get-PlannedDownloads($lines) {
    # "download -dryrun" prints "{<game folder>}" before each game, then one line per file that is
    # missing ("download"), half finished ("working") or in an inconsistent state ("error").
    $game = $null
    foreach ($line in $lines) {
        if ($line -match '\| \{(.+)\}$') { $game = $Matches[1] }
        elseif ($line -match '\|\s+(download|working|error)\s+(.+)$') {
            [pscustomobject]@{ Game = $game; File = $Matches[2] }
        }
    }
}

function Get-PlannedDeletions($lines) {
    # "clean -dryrun" prints "orphaning dir  '<game>'" or "orphaning file '<game>\<file>'".
    foreach ($line in $lines) {
        if ($line -match "orphaning dir\s+'(.+)'$") {
            [pscustomobject]@{ Game = $Matches[1]; File = '(whole folder)' }
        }
        elseif ($line -match "orphaning file '(.+?)\\(.+)'( as it has been marked for change\.)?$") {
            [pscustomobject]@{ Game = $Matches[1]; File = $Matches[2] }
        }
    }
}

function Get-Total($lines, $pattern) {
    foreach ($line in $lines) {
        if ($line -match $pattern) { return $Matches[1] }
    }
    return '?'
}

function Show-Files($title, $files, $color) {
    Write-Host ''
    Write-Host $title -ForegroundColor $color
    if (-not $files) { Write-Host '  (nothing)'; return }
    foreach ($group in $files | Group-Object Game) {
        Write-Host "  $($group.Name)"
        foreach ($f in $group.Group) { Write-Host "    $($f.File)" }
    }
}

function Restore-Orphans {
    # Moves everything in !orphaned back to where clean took it from: !orphaned\<game> is either a whole
    # game folder or holds single files of <game>.
    if (-not (Test-Path -LiteralPath $OrphanDir)) { return }
    if (-not (Get-ChildItem -LiteralPath $OrphanDir -Recurse -File -Force)) {
        # gogrepoc creates the folder even for "clean -dryrun".
        Remove-Item -LiteralPath $OrphanDir -Recurse -Force
        return
    }
    "restoring files from $OrphanDir" | Tee-Object -FilePath $Log -Append
    foreach ($gameDir in Get-ChildItem -LiteralPath $OrphanDir -Directory) {
        $target = Join-Path $SaveDir $gameDir.Name
        if (-not (Test-Path -LiteralPath $target)) {
            Move-Item -LiteralPath $gameDir.FullName -Destination $target
            continue
        }
        foreach ($item in Get-ChildItem -LiteralPath $gameDir.FullName -Force) {
            $dst = Join-Path $target $item.Name
            if (Test-Path -LiteralPath $dst) {
                $msg = "$dst already exists, leaving $($item.FullName) in place"
                Write-Warning $msg
                "WARNING: $msg" | Out-File -FilePath $Log -Append -Encoding utf8
                continue
            }
            Move-Item -LiteralPath $item.FullName -Destination $dst
        }
    }
    if (-not (Get-ChildItem -LiteralPath $OrphanDir -Recurse -File -Force)) {
        Remove-Item -LiteralPath $OrphanDir -Recurse -Force
    }
}

# A previous run that was killed before it could clean up leaves its moved-away files in !orphaned.
Restore-Orphans

Invoke-Organize flatten
$cleaned = $false
$success = $false
try {
    $updateArgs = @('update') + $Filter + @('-installers', 'standalone')
    if (-not $Quick) { $updateArgs += '-full' }
    Invoke-Gogrepo @updateArgs

    $downloadArgs = @('download', $SaveDir) + $Filter + $SkipFiles
    if ($NoExtras) { $downloadArgs += '-skipextras' }
    $previewArgs = $downloadArgs + '-dryrun'

    Write-Host 'Comparing the library with the local folder...'
    $out = Invoke-GogrepoQuiet @previewArgs
    $downloads = @(Get-PlannedDownloads $out)
    $downloadSize = Get-Total $out '\| (\S+) left to download'
    $cleanPreviewArgs = @('clean', $SaveDir, '-dryrun')
    $out = Invoke-GogrepoQuiet @cleanPreviewArgs
    $deletions = @(Get-PlannedDeletions $out)
    $deleteSize = Get-Total $out 'total size of newly orphaned files: (\S+)'

    if (-not $downloads -and -not $deletions) {
        'Local folder is up to date, nothing to do.' | Tee-Object -FilePath $Log -Append
        return
    }
    Show-Files "To download: $($downloads.Count) file(s), $downloadSize" $downloads Green
    Show-Files "To delete: $($deletions.Count) item(s), $(if ($deletions) { $deleteSize } else { '0' })" $deletions Red
    Write-Host ''
    if ($DryRun) { return }

    $answer = Read-Host 'Apply these changes? [y/N]'
    if ($answer -notmatch '^(y|yes|j|ja)$') {
        'Cancelled, nothing was changed.' | Tee-Object -FilePath $Log -Append
        return
    }

    # Moves the files to delete into !orphaned; they are only deleted after a complete download.
    $cleaned = $true
    Invoke-Gogrepo clean $SaveDir
    Invoke-Gogrepo @downloadArgs

    if ($Verify) {
        $verifyArgs = @('verify', $SaveDir) + $Filter + $SkipFiles
        if ($NoExtras) { $verifyArgs += '-skipextras' }
        Invoke-Gogrepo @verifyArgs
    }

    # gogrepoc exits with 0 even when single files fail, so check that nothing is left to download.
    $remaining = @(Get-PlannedDownloads (Invoke-GogrepoQuiet @previewArgs))
    if ($remaining) {
        throw "$($remaining.Count) file(s) did not download completely ($(($remaining.File | Select-Object -First 5) -join ', ')), see $Log"
    }
    $success = $true
}
finally {
    if ($cleaned -and $success) {
        if (Test-Path -LiteralPath $OrphanDir) {
            "deleting $OrphanDir" | Tee-Object -FilePath $Log -Append
            Remove-Item -LiteralPath $OrphanDir -Recurse -Force
        }
    }
    elseif ($cleaned) {
        Write-Warning 'Download incomplete, moving the old files back.'
        Restore-Orphans
    }
    else {
        # Only removes the empty folder left by the preview.
        Restore-Orphans
    }
    if (-not $NoSort) { Invoke-Organize sort }
}
