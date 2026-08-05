# Refuel Android 릴리스 빌드 (원스톱)
#
# ⚠️ 함정 주의: docs/ 를 고친 뒤 gradle 만 돌리면 옛 화면이 그대로 들어간다.
#    Capacitor 는 `cap copy` 를 해야 www -> android/app/src/main/assets/public 로 복사되기 때문.
#    이 스크립트는 그 순서를 강제하고, 결과물에 최신 코드가 들어갔는지 검증까지 한다.
#
# 사용법:  powershell -ExecutionPolicy Bypass -File build-release.ps1
#
# 경로는 환경변수로 바꿀 수 있고, 없으면 자동 탐지한다(머신마다 다르므로 고정하지 않는다):
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
    throw "JDK 17을 찾지 못했습니다. JAVA_HOME을 설정하세요."
}
$env:JAVA_HOME = $JDK
$env:Path = "$JDK\bin;" + $env:Path

if (-not $env:ANDROID_HOME) { $env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk" }
$BT = Get-ChildItem "$env:ANDROID_HOME\build-tools" -Directory -ErrorAction SilentlyContinue |
      Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $BT) { throw "Android build-tools가 없습니다. ANDROID_HOME을 확인하세요." }

$KS = $env:REFUEL_KEYSTORE
if (-not $KS) { $KS = Join-Path $parent "android\android.keystore" }
$SECRETS = $env:REFUEL_KEYSTORE_SECRETS
if (-not $SECRETS) { $SECRETS = Join-Path $parent "android\keystore-secrets.txt" }
if (-not (Test-Path $KS)) { throw "키스토어가 없습니다: $KS" }
if (-not (Test-Path $SECRETS)) { throw "키스토어 비밀번호 파일이 없습니다: $SECRETS" }

Write-Host "`n[1/5] 웹 자산 번들 (docs -> www)" -ForegroundColor Cyan
Copy-Item "..\docs\*" www -Recurse -Force

Write-Host "[2/5] Capacitor 동기화 (www -> android assets)" -ForegroundColor Cyan
npx cap copy android | Out-Null

Write-Host "[3/5] Gradle 릴리스 빌드" -ForegroundColor Cyan
Push-Location android
& ".\gradlew.bat" bundleRelease assembleRelease --no-daemon | Select-Object -Last 3
Pop-Location

Write-Host "[4/5] 서명" -ForegroundColor Cyan
$sec = Get-Content $SECRETS | ConvertFrom-StringData
$sp = $sec.KEYSTORE_PASSWORD
$out = "android\app\build\outputs"
Copy-Item "$out\bundle\release\app-release.aab" ".\Refuel-admob.aab" -Force
& "$JDK\bin\jarsigner.exe" -keystore $KS -storepass $sp -keypass $sp `
    -digestalg SHA-256 -sigalg SHA256withRSA "Refuel-admob.aab" refuel | Out-Null
& "$BT\zipalign.exe" -f -p 4 "$out\apk\release\app-release-unsigned.apk" ".\Refuel-admob.apk"
& "$BT\apksigner.bat" sign --ks $KS --ks-pass "pass:$sp" --key-pass "pass:$sp" `
    --ks-key-alias refuel "Refuel-admob.apk"

Write-Host "[5/5] 검증 (최신 코드가 실제로 들어갔는지)" -ForegroundColor Cyan
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead("$root\Refuel-admob.apk")
$entry = $zip.Entries | Where-Object { $_.FullName -eq "assets/public/index.html" }
$reader = New-Object System.IO.StreamReader($entry.Open(), [System.Text.Encoding]::UTF8)
$html = $reader.ReadToEnd(); $reader.Close(); $zip.Dispose()

$checks = @{
    "진단 로그(rlog)" = $html.Contains("function rlog")
    "로그 UI(logbox)" = $html.Contains("logbox")
    "AdMob 배너"      = $html.Contains("showBanner")
    "데모 모드"       = $html.Contains("demoState")
    "앱 내 QR 스캐너" = $html.Contains("function startScan")
    "잔디/연속기록"   = $html.Contains("function grassHTML")
}
$fail = $false
foreach ($k in $checks.Keys) {
    if ($checks[$k]) { Write-Host "  OK   $k" -ForegroundColor Green }
    else { Write-Host "  FAIL $k" -ForegroundColor Red; $fail = $true }
}
& "$BT\aapt2.exe" dump badging "Refuel-admob.apk" 2>$null | Select-String "targetSdkVersion"

if ($fail) { Write-Host "`n검증 실패 - APK에 최신 코드가 없습니다." -ForegroundColor Red; exit 1 }
Get-ChildItem Refuel-admob.aab, Refuel-admob.apk |
    ForEach-Object { "{0,-20} {1,8:N2} MB" -f $_.Name, ($_.Length / 1MB) }
Write-Host "`n빌드 완료. Play 업로드=Refuel-admob.aab / 폰 테스트=Refuel-admob.apk" -ForegroundColor Green
