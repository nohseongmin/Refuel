# Refuel Android release build, including signing.
#
# Careful: editing docs/ then running gradle alone leaves the old screen inside the APK.
#          Capacitor sync copies www and generates the native plugin project.
#          This script enforces that order and verifies the packaged APK really has the
#          latest code before reporting success.
#
# Usage:  powershell -ExecutionPolicy Bypass -File build-release.ps1
#
# Paths come from environment variables and fall back to auto-detection, so nothing
# machine-specific is hard-coded:
#   JAVA_HOME / ANDROID_HOME / REFUEL_KEYSTORE / REFUEL_KEYSTORE_SECRETS

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$parent = Split-Path -Parent $root

$JDK = $env:JAVA_HOME
if (-not $JDK -or -not (Test-Path "$JDK\bin\jarsigner.exe")) {
    $JDK = Get-ChildItem "$env:USERPROFILE\.bubblewrap\jdk17" -Directory -ErrorAction SilentlyContinue |
           Select-Object -First 1 -ExpandProperty FullName
}
if (-not $JDK -or -not (Test-Path "$JDK\bin\jarsigner.exe")) {
    throw "Could not find JDK 17. Set JAVA_HOME."
}
$env:JAVA_HOME = $JDK
$env:Path = "$JDK\bin;" + $env:Path

if (-not $env:ANDROID_HOME) { $env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk" }
$BT = Get-ChildItem "$env:ANDROID_HOME\build-tools" -Directory -ErrorAction SilentlyContinue |
      Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $BT) { throw "Android build-tools not found. Check ANDROID_HOME." }

$KS = $env:REFUEL_KEYSTORE
if (-not $KS) { $KS = Join-Path $parent "android\android.keystore" }
$SECRETS = $env:REFUEL_KEYSTORE_SECRETS
if (-not $SECRETS) { $SECRETS = Join-Path $parent "android\keystore-secrets.txt" }
if (-not (Test-Path $KS)) { throw "Keystore not found: $KS" }
if (-not (Test-Path $SECRETS)) { throw "Keystore password file not found: $SECRETS" }

Write-Host "`n[1/5] Bundling web assets (docs -> www)" -ForegroundColor Cyan
New-Item -ItemType Directory -Path www -Force | Out-Null
Copy-Item "..\docs\*" www -Recurse -Force

Write-Host "[2/5] Capacitor sync (www -> android assets)" -ForegroundColor Cyan
npx cap sync android | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Capacitor sync failed." }

Write-Host "[3/5] Gradle release build" -ForegroundColor Cyan
Push-Location android
try {
    & ".\gradlew.bat" bundleRelease assembleRelease --no-daemon | Select-Object -Last 3
    if ($LASTEXITCODE -ne 0) { throw "Gradle release build failed." }
} finally { Pop-Location }

Write-Host "[4/5] Signing" -ForegroundColor Cyan
$sec = Get-Content $SECRETS | ConvertFrom-StringData
$sp = $sec.KEYSTORE_PASSWORD
if (-not $sp) { throw "Keystore password is missing." }
$out = "android\app\build\outputs"
Copy-Item "$out\bundle\release\app-release.aab" ".\Refuel.aab" -Force
$env:REFUEL_SIGNING_PASSWORD = $sp
try {
    & "$JDK\bin\jarsigner.exe" -keystore $KS -storepass:env REFUEL_SIGNING_PASSWORD -keypass:env REFUEL_SIGNING_PASSWORD `
        -digestalg SHA-256 -sigalg SHA256withRSA "Refuel.aab" refuel | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Bundle signing failed." }
    & "$BT\zipalign.exe" -f -p 4 "$out\apk\release\app-release-unsigned.apk" ".\Refuel.apk"
    if ($LASTEXITCODE -ne 0) { throw "APK alignment failed." }
    & "$BT\apksigner.bat" sign --ks $KS --ks-pass env:REFUEL_SIGNING_PASSWORD --key-pass env:REFUEL_SIGNING_PASSWORD `
        --ks-key-alias refuel "Refuel.apk"
    if ($LASTEXITCODE -ne 0) { throw "APK signing failed." }
    & "$BT\apksigner.bat" verify "Refuel.apk"
    if ($LASTEXITCODE -ne 0) { throw "APK signature verification failed." }
} finally { Remove-Item Env:\REFUEL_SIGNING_PASSWORD -ErrorAction SilentlyContinue }

Write-Host "[5/5] Verifying the packaged code is current" -ForegroundColor Cyan
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead("$root\Refuel.apk")
try {
    $entry = $zip.GetEntry("assets/public/index.html")
    if (-not $entry) { throw "Web assets are missing from the APK." }
    $reader = New-Object System.IO.StreamReader($entry.Open(), [System.Text.Encoding]::UTF8)
    try { $html = $reader.ReadToEnd() } finally { $reader.Dispose() }
} finally { $zip.Dispose() }
if ($html -cne [System.IO.File]::ReadAllText((Join-Path $parent 'docs\index.html'))) {
    throw "Packaged web assets differ from docs/index.html."
}

$checks = @{
    "diagnostic log (rlog)" = $html.Contains("function rlog")
    "log UI (logbox)"      = $html.Contains("logbox")
    "demo mode"            = $html.Contains("demoState")
    "in-app QR scanner"    = $html.Contains("function startScan")
    "grass and streaks"    = $html.Contains("function grassHTML")
    "alert diagnostics"    = $html.Contains("function selfTest")
    "PC alert receiving"   = $html.Contains("function pollAlerts")
}
# Without the exact-alarm permission, Android 12+ silently delays reset alerts, so the
# build fails here rather than shipping it.
$perm = & "$BT\aapt2.exe" dump badging "Refuel.apk" 2>$null | Select-String "SCHEDULE_EXACT_ALARM"
$checks["exact alarm permission"] = [bool]$perm
$fail = $false
foreach ($k in $checks.Keys) {
    if ($checks[$k]) { Write-Host "  OK   $k" -ForegroundColor Green }
    else { Write-Host "  FAIL $k" -ForegroundColor Red; $fail = $true }
}
& "$BT\aapt2.exe" dump badging "Refuel.apk" 2>$null | Select-String "targetSdkVersion"

if ($fail) { Write-Host "`nVerification failed. The APK does not contain the latest code." -ForegroundColor Red; exit 1 }
Get-ChildItem Refuel.aab, Refuel.apk |
    ForEach-Object { "{0,-20} {1,8:N2} MB" -f $_.Name, ($_.Length / 1MB) }
Write-Host "`nBuild complete. Sideload: Refuel.apk / store bundle: Refuel.aab" -ForegroundColor Green
