<#
    FaceProof end-to-end demo.

    Runs the whole pipeline in the order a judge needs to see it:
      face scan -> live web/social search -> verification -> blockchain anchor
      -> re-verification -> tamper proof

    Built for screen recording: each stage announces itself, and the script
    pauses between stages so nothing important scrolls past unread.

    Usage
      .\demo.ps1                    full demo on the local chain
      .\demo.ps1 -Fast              headless browser, no pauses (quick check)
      .\demo.ps1 -Network sepolia   anchor on a public testnet instead
      .\demo.ps1 -SkipTests         leave the test suites out
#>

[CmdletBinding()]
param(
    [string] $Network = "localhost",
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
    Read-Host "  Press Enter to continue"
}

function Fail([string]$msg) {
    Write-Host ""
    Write-Host "  FAILED: $msg" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------- prerequisites

Banner "0/7" "Checking prerequisites"

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

# The local chain has to be up before anything can be deployed to it.
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
    Note "chain       using public network '$Network' (needs a funded PRIVATE_KEY in .env)"
}

Pause-Step "deploy the FaceProofRegistry contract"

# ---------------------------------------------------------------- deploy

Banner "1/7" "Deploy the evidence registry contract"

$deployment = "deployments\$Network.json"
if ((-not $Local) -and (Test-Path $deployment)) {
    Note "already deployed on $Network - reusing it (re-deploying would cost gas)"
    Get-Content $deployment
} else {
    npx hardhat run scripts/deploy.js --network $Network
    if (-not $?) { Fail "deployment failed" }
}

Pause-Step "scan a public figure (Virat Kohli) end to end"

# ------------------------------------------------------------- scan one

Banner "2/7" "Scan 1 - Virat Kohli (high-volume match)"
Note "Watch the browser window: this is a live query to Yandex, not a canned result."

# Separate output directory so this run's report survives scan 2.
$scanArgs = @("-m","pipeline.cli","scan","samples\virat_kohli.jpg",
              "--backend","yandex","--network",$Network,
              "--max-checks","12","--out","out_kohli","--open-report")
if ($Fast) { $scanArgs += "--headless" }

& $Python @scanArgs
if (-not $?) { Note "scan 1 did not find a verifiable match - continuing to scan 2" }

Pause-Step "scan KL Rahul, where the verifier rejects the wrong person"

# ------------------------------------------------------------- scan two

Banner "3/7" "Scan 2 - KL Rahul (watch the rejections)"
Note "Yandex returns Virat Kohli posts and Bing labels the face 'Rohit Sharma'."
Note "Both are wrong. Our own model scores them 0.09-0.24 and throws them out,"
Note "then matches his real account at ~0.5 against a completely different photo."

$scanArgs2 = @("-m","pipeline.cli","scan","samples\kl_rahul.jpg",
               "--backend","both","--network",$Network,
               "--limit","80","--max-checks","30","--open-report")
if ($Fast) { $scanArgs2 += "--headless" }

& $Python @scanArgs2
if (-not $?) { Fail "scan 2 failed" }

Pause-Step "re-verify the saved evidence against the blockchain"

# ---------------------------------------------------------------- verify

Banner "4/7" "Re-verify the evidence against the chain"
Note "Re-hashes evidence.json from disk and asks the contract if it knows that digest."

& $Python -m pipeline.cli verify out\evidence.json --network $Network
if (-not $?) { Fail "verification failed" }

Pause-Step "tamper with the evidence and watch verification break"

# ---------------------------------------------------------------- tamper

Banner "5/7" "Tamper proof - change one character"
Note "Edits a single character of the post URL and re-hashes. The digest changes"
Note "completely, and that new digest is not on the chain. The record cannot be faked."

& $Python -m pipeline.cli tamper out\evidence.json --network $Network

Pause-Step "show the chain state"

# ------------------------------------------------------------------ info

Banner "6/7" "Chain state"
& $Python -m pipeline.cli info --network $Network

# ----------------------------------------------------------------- tests

if (-not $SkipTests) {
    Pause-Step "run both test suites"
    Banner "7/7" "Test suites"
    Note "Contract tests (Hardhat):"
    npx hardhat test
    Write-Host ""
    Note "Pipeline tests (pytest) - includes the negative control proving that"
    Note "unrelated faces score near zero, which is what makes a match meaningful:"
    & $Python -m pytest tests/ -q
}

# ---------------------------------------------------------------- wrap up

Banner "DONE" "Pipeline demonstrated end to end"

$report = Join-Path $PSScriptRoot "out\report.html"
Write-Host "  Evidence bundle : out\evidence.json"                  -ForegroundColor Green
Write-Host "  Chain receipt   : out\receipt.json"                   -ForegroundColor Green
Write-Host "  Visual report   : out\report.html        (KL Rahul)"  -ForegroundColor Green
Write-Host "  Visual report   : out_kohli\report.html  (Virat Kohli)" -ForegroundColor Green
Write-Host ""

if (Test-Path $report) {
    Note "Opening the visual report..."
    Start-Process $report
} else {
    Note "No report was generated (no verified match in the last scan)."
}

Write-Host ""
