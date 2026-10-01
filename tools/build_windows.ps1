param(
    [string]$PythonLauncher = "py",
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DistPath = Join-Path $ProjectRoot "dist"
$BuildPath = Join-Path $ProjectRoot "build"
$SpecFile = Join-Path $ProjectRoot "tools\AnkiQuickAdd-pyside6.spec"
$FontDir = Join-Path $ProjectRoot "fonts"
$LicenseDir = Join-Path $ProjectRoot "licenses"

function Ensure-RemoteFile {
    param(
        [Parameter(Mandatory=$true)][string]$Uri,
        [Parameter(Mandatory=$true)][string]$Destination,
        [int64]$MinimumBytes = 1024
    )

    if ((Test-Path -LiteralPath $Destination) -and ((Get-Item -LiteralPath $Destination).Length -ge $MinimumBytes)) {
        Write-Host "Using cached asset: $Destination"
        return
    }

    $Temp = "$Destination.download"
    if (Test-Path -LiteralPath $Temp) { Remove-Item -Force -LiteralPath $Temp }
    Write-Host "Downloading pinned asset: $Uri"
    Invoke-WebRequest -Uri $Uri -OutFile $Temp -UseBasicParsing
    if (-not (Test-Path -LiteralPath $Temp)) {
        throw "Download failed: $Uri"
    }
    if ((Get-Item -LiteralPath $Temp).Length -lt $MinimumBytes) {
        Remove-Item -Force -LiteralPath $Temp
        throw "Downloaded asset is unexpectedly small: $Uri"
    }
    Move-Item -Force -LiteralPath $Temp -Destination $Destination
}

function Ensure-SourceHanSansSCStatic {
    param(
        [Parameter(Mandatory=$true)][string]$RegularDestination,
        [Parameter(Mandatory=$true)][string]$MediumDestination
    )

    $MinimumBytes = 10000000
    $RegularReady = (Test-Path -LiteralPath $RegularDestination) -and ((Get-Item -LiteralPath $RegularDestination).Length -ge $MinimumBytes)
    $MediumReady = (Test-Path -LiteralPath $MediumDestination) -and ((Get-Item -LiteralPath $MediumDestination).Length -ge $MinimumBytes)
    if ($RegularReady -and $MediumReady) {
        Write-Host "Using cached Source Han Sans SC static fonts"
        return
    }

    $Uri = "https://github.com/adobe-fonts/source-han-sans/releases/download/2.005R/09_SourceHanSansSC.zip"
    $Archive = Join-Path $FontDir "SourceHanSansSC-2.005R.download.zip"
    $ExtractDir = Join-Path $FontDir ".source-han-sans-sc-2.005R-extract"

    if (Test-Path -LiteralPath $Archive) { Remove-Item -Force -LiteralPath $Archive }
    if (Test-Path -LiteralPath $ExtractDir) { Remove-Item -Recurse -Force -LiteralPath $ExtractDir }

    try {
        Write-Host "Downloading pinned asset: $Uri"
        Invoke-WebRequest -Uri $Uri -OutFile $Archive -UseBasicParsing
        New-Item -ItemType Directory -Force -Path $ExtractDir | Out-Null
        Expand-Archive -LiteralPath $Archive -DestinationPath $ExtractDir -Force

        $Regular = Get-ChildItem -LiteralPath $ExtractDir -Recurse -File -Filter "SourceHanSansSC-Regular.otf" | Select-Object -First 1
        $Medium = Get-ChildItem -LiteralPath $ExtractDir -Recurse -File -Filter "SourceHanSansSC-Medium.otf" | Select-Object -First 1
        if ($null -eq $Regular -or $null -eq $Medium) {
            throw "Source Han Sans SC Regular/Medium OTFs were not found in the official 2.005R archive"
        }
        if ($Regular.Length -lt $MinimumBytes -or $Medium.Length -lt $MinimumBytes) {
            throw "Source Han Sans SC static OTF is unexpectedly small"
        }

        Copy-Item -Force -LiteralPath $Regular.FullName -Destination $RegularDestination
        Copy-Item -Force -LiteralPath $Medium.FullName -Destination $MediumDestination
    }
    finally {
        if (Test-Path -LiteralPath $Archive) { Remove-Item -Force -LiteralPath $Archive }
        if (Test-Path -LiteralPath $ExtractDir) { Remove-Item -Recurse -Force -LiteralPath $ExtractDir }
    }
}

function Ensure-InterFont {
    param(
        [Parameter(Mandatory=$true)][string]$Destination
    )

    $MinimumBytes = 500000
    if ((Test-Path -LiteralPath $Destination) -and ((Get-Item -LiteralPath $Destination).Length -ge $MinimumBytes)) {
        Write-Host "Using cached asset: $Destination"
        return
    }

    # The previous build script accidentally used Inter's source-repository
    # commit as though it were a google/fonts commit, producing a 404.  Fetch
    # the immutable official v4.1 release archive instead and extract the
    # published InterVariable.ttf from it.
    $Uri = "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip"
    $Archive = Join-Path $FontDir "Inter-4.1.download.zip"
    $ExtractDir = Join-Path $FontDir ".inter-4.1-extract"

    if (Test-Path -LiteralPath $Archive) { Remove-Item -Force -LiteralPath $Archive }
    if (Test-Path -LiteralPath $ExtractDir) { Remove-Item -Recurse -Force -LiteralPath $ExtractDir }

    try {
        Write-Host "Downloading pinned asset: $Uri"
        Invoke-WebRequest -Uri $Uri -OutFile $Archive -UseBasicParsing
        if (-not (Test-Path -LiteralPath $Archive)) {
            throw "Download failed: $Uri"
        }
        New-Item -ItemType Directory -Force -Path $ExtractDir | Out-Null
        Expand-Archive -LiteralPath $Archive -DestinationPath $ExtractDir -Force
        $Candidate = Get-ChildItem -LiteralPath $ExtractDir -Recurse -File -Filter "InterVariable.ttf" |
            Select-Object -First 1
        if ($null -eq $Candidate) {
            throw "InterVariable.ttf was not found in the Inter 4.1 release archive"
        }
        if ($Candidate.Length -lt $MinimumBytes) {
            throw "InterVariable.ttf is unexpectedly small: $($Candidate.Length) bytes"
        }
        Copy-Item -Force -LiteralPath $Candidate.FullName -Destination $Destination
    }
    finally {
        if (Test-Path -LiteralPath $Archive) { Remove-Item -Force -LiteralPath $Archive }
        if (Test-Path -LiteralPath $ExtractDir) { Remove-Item -Recurse -Force -LiteralPath $ExtractDir }
    }

    if (-not (Test-Path -LiteralPath $Destination) -or ((Get-Item -LiteralPath $Destination).Length -lt $MinimumBytes)) {
        throw "Inter font extraction failed: $Destination"
    }
}

New-Item -ItemType Directory -Force -Path $DistPath, $BuildPath, $FontDir, $LicenseDir | Out-Null
if (-not (Test-Path -LiteralPath $SpecFile)) {
    throw "Missing PySide6 spec: $SpecFile"
}

# Pin the actual UI fonts at build time so the packaged EXE does not depend on
# whatever CJK fonts happen to be installed on the target PC. Chinese UI uses
# official Adobe Source Han Sans SC 2.005R static Regular/Medium OTFs; Japanese
# keeps the pinned Noto Sans JP variable face; Inter comes from its official
# 4.1 release archive.
Ensure-InterFont -Destination (Join-Path $FontDir "Inter-VF.ttf")
Ensure-SourceHanSansSCStatic `
    -RegularDestination (Join-Path $FontDir "SourceHanSansSC-Regular.otf") `
    -MediumDestination (Join-Path $FontDir "SourceHanSansSC-Medium.otf")

$PinnedFonts = @(
    @{
        Name = "NotoSansJP-VF.ttf"
        Uri = "https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/Variable/TTF/Subset/NotoSansJP-VF.ttf"
        MinimumBytes = 1000000
    }
)

foreach ($Font in $PinnedFonts) {
    Ensure-RemoteFile `
        -Uri $Font.Uri `
        -Destination (Join-Path $FontDir $Font.Name) `
        -MinimumBytes $Font.MinimumBytes
}


$pythonArguments = if ($PythonLauncher -eq "py") {
    @("-$PythonVersion", "-m", "PyInstaller")
} else {
    @("-m", "PyInstaller")
}

$arguments = @(
    "--noconfirm",
    "--clean",
    "--distpath", $DistPath,
    "--workpath", $BuildPath,
    $SpecFile
)

Write-Host "Building AnkiQuickAdd from: $ProjectRoot"
& $PythonLauncher @pythonArguments @arguments
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$PackageDir = Join-Path $DistPath "AnkiQuickAdd"
$Executable = Join-Path $PackageDir "AnkiQuickAdd.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Build completed without the expected executable: $Executable"
}

# Keep version history and third-party notices visible at the package root.
Copy-Item -Force -LiteralPath (Join-Path $ProjectRoot "VERSION") -Destination (Join-Path $PackageDir "VERSION")
Copy-Item -Force -LiteralPath (Join-Path $ProjectRoot "CHANGELOG.md") -Destination (Join-Path $PackageDir "CHANGELOG.md")
Copy-Item -Force -LiteralPath (Join-Path $ProjectRoot "LICENSE") -Destination (Join-Path $PackageDir "LICENSE.txt")
Copy-Item -Force -LiteralPath (Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md") -Destination (Join-Path $PackageDir "THIRD_PARTY_NOTICES.md")
$PackageLicenseDir = Join-Path $PackageDir "licenses"
New-Item -ItemType Directory -Force -Path $PackageLicenseDir | Out-Null
Get-ChildItem -LiteralPath $LicenseDir -File | Copy-Item -Destination $PackageLicenseDir -Force

$PackageSizeMB = [math]::Round(((Get-ChildItem -LiteralPath $PackageDir -Recurse -File | Measure-Object -Property Length -Sum).Sum) / 1MB, 2)
Write-Host "Built package: $PackageDir ($PackageSizeMB MB uncompressed)"
