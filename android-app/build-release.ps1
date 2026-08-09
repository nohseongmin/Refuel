# Refuel Android release build, including signing.
#
# Careful: editing docs/ then running gradle alone leaves the old screen inside the APK.
#          Capacitor only copies www to android/app/src/main/assets/public on `cap copy`.
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
Copy-Item "..\docs\*" www -Recurse -Force

Write-Host "[2/5] Capacitor sync (www -> android assets)" -ForegroundColor Cyan
npx cap copy android | Out-Null

Write-Host "[3/5] Gradle release build" -ForegroundColor Cyan
Push-Location android
& ".\gradlew.bat" bundleRelease assembleRelease --no-daemon | Select-Object -Last 3
Pop-Location

Write-Host "[4/5] Signing" -ForegroundColor Cyan
$sec = Get-Content $SECRETS | ConvertFrom-StringData
$sp = $sec.KEYSTORE_PASSWORD
$out = "android\app\build\outputs"
Copy-Item "$out\bundle\release\app-release.aab" ".\Refuel-admob.aab" -Force
& "$JDK\bin\jarsigner.exe" -keystore $KS -storepass $sp -keypass $sp `
    -digestalg SHA-256 -sigalg SHA256withRSA "Refuel-admob.aab" refuel | Out-Null
& "$BT\zipalign.exe" -f -p 4 "$out\apk\release\app-release-unsigned.apk" ".\Refuel-admob.apk"
& "$BT\apksigner.bat" sign --ks $KS --ks-pass "pass:$sp" --key-pass "pass:$sp" `
    --ks-key-alias refuel "Refuel-admob.apk"

Write-Host "[5/5] Verifying the packaged code is current" -ForegroundColor Cyan
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead("$root\Refuel-admob.apk")
$entry = $zip.Entries | Where-Object { $_.FullName -eq "assets/public/index.html" }
$reader = New-Object System.IO.StreamReader($entry.Open(), [System.Text.Encoding]::UTF8)
$html = $reader.ReadToEnd(); $reader.Close(); $zip.Dispose()

$checks = @{
    "diagnostic log (rlog)" = $html.Contains("function rlog")
    "log UI (logbox)"      = $html.Contains("logbox")
    "AdMob banner"         = $html.Contains("showBanner")
    "demo mode"            = $html.Contains("demoState")
    "in-app QR scanner"    = $html.Contains("function startScan")
    "grass and streaks"    = $html.Contains("function grassHTML")
    "alert diagnostics"    = $html.Contains("function selfTest")
    "PC alert receiving"   = $html.Contains("function pollAlerts")
}
# Without the exact-alarm permission, Android 12+ silently delays reset alerts, so the
# build fails here rather than shipping it.
$perm = & "$BT\aapt2.exe" dump badging "Refuel-admob.apk" 2>$null | Select-String "SCHEDULE_EXACT_ALARM"
$checks["exact alarm permission"] = [bool]$perm
$fail = $false
foreach ($k in $checks.Keys) {
    if ($checks[$k]) { Write-Host "  OK   $k" -ForegroundColor Green }
    else { Write-Host "  FAIL $k" -ForegroundColor Red; $fail = $true }
}
& "$BT\aapt2.exe" dump badging "Refuel-admob.apk" 2>$null | Select-String "targetSdkVersion"

if ($fail) { Write-Host "`nVerification failed. The APK does not contain the latest code." -ForegroundColor Red; exit 1 }
Get-ChildItem Refuel-admob.aab, Refuel-admob.apk |
    ForEach-Object { "{0,-20} {1,8:N2} MB" -f $_.Name, ($_.Length / 1MB) }
Write-Host "`nBuild complete. Store bundle: Refuel-admob.aab / sideload: Refuel-admob.apk" -ForegroundColor Green
