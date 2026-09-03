<#
    FaceProof end-to-end demo.

      face scan -> live web/social search -> independent verification
      -> blockchain anchor -> re-verification -> tamper proof

    By default it asks which image to scan: pick a bundled sample, browse for
    a file, or just drag a photo onto this window and press Enter.

    Usage
      .\demo.ps1                          interactive - choose an image
      .\demo.ps1 -Image C:\path\me.jpg    scan one specific image
      .\demo.ps1 -Full                    scripted two-sample showcase
      .\demo.ps1 -Fast                    headless browser, no pauses
      .\demo.ps1 -Network sepolia         anchor on a public testnet
#>

[CmdletBinding()]
param(
    [string] $Image,
    [string] $Network = "localhost",
    [ValidateSet("yandex", "bing", "both", "facecheck")]
    [string] $Backend = "both",
    [switch] $Full,        # run the scripted Kohli + KL Rahul showcase
    [switch] $Fast,        # headless browser + no pauses
    [switch] $SkipTests,
    [switch] $NoPause
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$Local  = ($Network -eq "localhost")
$Quiet  = ($Fast -or $NoPause)

# ---------------------------------------------------------------- helpers

function Banner([string]$n, [string]$title) {
    Write-Host ""
    Write-Host ("=" * 100) -ForegroundColor DarkCyan
    Write-Host "  $n  $title" -ForegroundColor Cyan
    Write-Host ("=" * 100) -ForegroundColor DarkCyan
    Write-Host ""
}

function Note([string]$msg) { Write-Host "  $msg" -ForegroundColor DarkGray }

function Pause-Step([string]$next) {
    if ($Quiet) { return }
    Write-Host ""
    Write-Host "  Next: $next" -ForegroundColor Yellow
    Read-Host "  Press Enter to continue" | Out-Null
}

function Fail([string]$msg) {
    Write-Host ""
    Write-Host "  FAILED: $msg" -ForegroundColor Red
    exit 1
}

# Dragging a file onto a console window types its path, often quoted, and
# sometimes with a stray trailing space. Normalise all of that away.
function Clean-Path([string]$raw) {
    if (-not $raw) { return "" }
    $p = $raw.Trim()
    $p = $p.Trim('"').Trim("'").Trim()
    if ($p.StartsWith("&")) { $p = $p.Substring(1).Trim().Trim("'").Trim('"') }
    return $p
}

function Show-FilePicker {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Title  = "Choose a face photo to scan"
        $dlg.Filter = "Images (*.jpg;*.jpeg;*.png;*.webp;*.bmp)|*.jpg;*.jpeg;*.png;*.webp;*.bmp|All files (*.*)|*.*"
        $dlg.InitialDirectory = [Environment]::GetFolderPath("MyPictures")
        if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dlg.FileName }
        return ""
    } catch {
        Note "file picker unavailable here - type or drag a path instead"
        return ""
    }
}

function Select-InputImage {
    $samples = @(Get-ChildItem -Path "samples" -Filter *.jpg -ErrorAction SilentlyContinue | Sort-Object Name)

    while ($true) {
        Write-Host ""
        Write-Host "  Which face do you want to scan?" -ForegroundColor White
        Write-Host ""
        for ($i = 0; $i -lt $samples.Count; $i++) {
            Write-Host ("    [{0}] {1}" -f ($i + 1), $samples[$i].Name) -ForegroundColor Gray
        }
        Write-Host "    [B] Browse for a file..." -ForegroundColor Gray
        Write-Host ""
        Write-Host "  Or drag a photo onto this window and press Enter." -ForegroundColor DarkGray
        Write-Host ""

        $choice = Read-Host "  Choice"
        $choice = Clean-Path $choice

        if ($choice -eq "") { continue }

        if ($choice -match '^[Bb]$') {
            $picked = Show-FilePicker
            if ($picked) { return $picked }
            continue
        }

        if ($choice -match '^\d+$') {
            $idx = [int]$choice - 1
            if ($idx -ge 0 -and $idx -lt $samples.Count) { return $samples[$idx].FullName }
            Write-Host "  No sample numbered $choice." -ForegroundColor Yellow
            continue
        }

        if (Test-Path -LiteralPath $choice -PathType Leaf) { return (Resolve-Path -LiteralPath $choice).Path }

        Write-Host "  Not a file: $choice" -ForegroundColor Yellow
    }
}

# Reject an unusable photo in a second, rather than after minutes of searching.
function Test-Face([string]$path) {
    Write-Host ""
    Note "checking the photo for a detectable face..."
    $line = & $Python scripts\precheck.py $path 2>&1 | Select-Object -Last 1
    $line = "$line"

    if ($line -like "OK *") {
        $bits = $line.Split(" ")
        Write-Host "  Face found." -ForegroundColor Green
        Note "faces in image : $($bits[1])"
        Note "face size      : $($bits[2]) px"
        Note "confidence     : $($bits[3])"
        return $true
    }
    if ($line -like "NO_FACE*") {
        Write-Host "  No face detected in that image." -ForegroundColor Yellow
        Note "Try a photo where the face is larger, front-facing and well lit."
        return $false
    }
    Write-Host "  Could not read that image: $line" -ForegroundColor Yellow
    return $false
}

# ---------------------------------------------------------- prerequisites

Banner "0" "Checking prerequisites"

if (-not (Test-Path $Python)) {
    Fail "No virtualenv. Run:  python -m venv .venv ; .venv\Scripts\python.exe -m pip install -r requirements.txt"
}
Note "python      $(& $Python --version 2>&1)"

if (-not (Test-Path "node_modules")) { Fail "Node deps missing. Run:  npm install" }
Note "node        $(node --version)"

if (-not (Test-Path "artifacts\contracts\FaceProofRegistry.sol\FaceProofRegistry.json")) {
    Note "contract not compiled yet - compiling..."
    npx hardhat compile
    if (-not $?) { Fail "hardhat compile failed" }
}
Note "contract    compiled"
Note "network     $Network"

if ($Local) {
    $listening = $null
    try { $listening = Get-NetTCPConnection -LocalPort 8545 -State Listen -ErrorAction SilentlyContinue } catch {}
    if ($listening) {
        Note "chain       already running on :8545"
    } else {
        Note "chain       starting 'npx hardhat node' in a new window..."
        Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$PSScriptRoot'; npx hardhat node"
        $ok = $false
        foreach ($i in 1..30) {
            Start-Sleep -Seconds 1
            try {
                $c = Get-NetTCPConnection -LocalPort 8545 -State Listen -ErrorAction SilentlyContinue
                if ($c) { $ok = $true; break }
            } catch {}
        }
        if (-not $ok) { Fail "local chain did not come up on :8545" }
        Note "chain       up"
    }
} else {
    Note "chain       public network '$Network' (needs a funded PRIVATE_KEY in .env)"
}

# ------------------------------------------------------------ pick input

$targets = @()

if ($Full) {
    $targets = @(
        @{ Path = "samples\virat_kohli.jpg"; Label = "Virat Kohli (high-volume match)";
           Backend = "yandex"; Out = "out_kohli"; MaxChecks = "12"; Limit = "60" },
        @{ Path = "samples\kl_rahul.jpg";    Label = "KL Rahul (watch the rejections)";
           Backend = "both";   Out = "out";       MaxChecks = "30"; Limit = "80" }
    )
} else {
    if ($Image) {
        $Image = Clean-Path $Image
        if (-not (Test-Path -LiteralPath $Image -PathType Leaf)) { Fail "No such image: $Image" }
        $Image = (Resolve-Path -LiteralPath $Image).Path
    } else {
        Banner "1" "Choose an image"
        while ($true) {
            $Image = Select-InputImage
            if (Test-Face $Image) { break }
            Write-Host ""
            Note "Pick a different photo."
        }
    }
    $targets = @(
        @{ Path = $Image; Label = (Split-Path $Image -Leaf);
           Backend = $Backend; Out = "out"; MaxChecks = "30"; Limit = "80" }
    )
}

Write-Host ""
Note "scanning: $($targets[0].Path)"
Pause-Step "deploy the FaceProofRegistry contract"

# ---------------------------------------------------------------- deploy

Banner "2" "Deploy the evidence registry contract"

$deployment = "deployments\$Network.json"
if ((-not $Local) -and (Test-Path $deployment)) {
    Note "already deployed on $Network - reusing it (re-deploying would cost gas)"
    Get-Content $deployment
} else {
    npx hardhat run scripts/deploy.js --network $Network
    if (-not $?) { Fail "deployment failed" }
}

# ------------------------------------------------------------------ scan

$step = 3
$lastOut = "out"

foreach ($t in $targets) {
    Pause-Step "scan $($t.Label)"
    Banner "$step" "Scan - $($t.Label)"
    Note "The browser window is a live query to a real search engine, not a canned result."
    if ($t.Backend -eq "both") {
        Note "Rejected candidates are shown too: a search engine returning the wrong"
        Note "person is normal, and catching it is the point of the verification stage."
    }

    $a = @("-m","pipeline.cli","scan",$t.Path,
           "--backend",$t.Backend,"--network",$Network,
           "--limit",$t.Limit,"--max-checks",$t.MaxChecks,
           "--out",$t.Out,"--open-report")
    if ($Fast) { $a += "--headless" }

    & $Python @a
    $code = $LASTEXITCODE
    $lastOut = $t.Out

    if ($code -ne 0) {
        Write-Host ""
        Note "No verifiable match for this image (exit $code)."
        Note "Free engines do image matching, not face recognition, so they find"
        Note "public figures far more reliably than private individuals."
        Note "Try:  -Backend both   or a photo already published online."
        if ($targets.Count -eq 1) {
            Banner "DONE" "Search ran, nothing cleared the threshold"
            exit 0
        }
    }
    $step++
}

Pause-Step "re-verify the saved evidence against the blockchain"

# ---------------------------------------------------------------- verify

Banner "$step" "Re-verify the evidence against the chain"
Note "Re-hashes evidence.json from disk and asks the contract if it knows that digest."
& $Python -m pipeline.cli verify "$lastOut\evidence.json" --network $Network
if (-not $?) { Fail "verification failed" }
$step++

Pause-Step "tamper with the evidence and watch verification break"

# ---------------------------------------------------------------- tamper

Banner "$step" "Tamper proof - change one character"
Note "Edits a single character of the post URL and re-hashes. The digest changes"
Note "completely, and that new digest is not on the chain. The record cannot be faked."
& $Python -m pipeline.cli tamper "$lastOut\evidence.json" --network $Network
$step++

Pause-Step "show the chain state"

# ------------------------------------------------------------------ info

Banner "$step" "Chain state"
& $Python -m pipeline.cli info --network $Network
$step++

# ----------------------------------------------------------------- tests

if (-not $SkipTests) {
    Pause-Step "run both test suites"
    Banner "$step" "Test suites"
    Note "Contract tests (Hardhat):"
    npx hardhat test
    Write-Host ""
    Note "Pipeline tests (pytest) - includes the negative control proving that"
    Note "unrelated faces score near zero, which is what makes a match meaningful:"
    & $Python -m pytest tests/ -q
}

# ---------------------------------------------------------------- wrap up

Banner "DONE" "Pipeline demonstrated end to end"

$report = Join-Path $PSScriptRoot "$lastOut\report.html"
Write-Host "  Evidence bundle : $lastOut\evidence.json" -ForegroundColor Green
Write-Host "  Chain receipt   : $lastOut\receipt.json"  -ForegroundColor Green
Write-Host "  Visual report   : $lastOut\report.html"   -ForegroundColor Green
if ($Full) {
    Write-Host "  Visual report   : out_kohli\report.html  (first scan)" -ForegroundColor Green
}
Write-Host ""

if (Test-Path $report) {
    Note "Opening the visual report..."
    Start-Process $report
} else {
    Note "No report was generated (no verified match)."
}

Write-Host ""
