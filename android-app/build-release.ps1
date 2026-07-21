# Refuel Android 릴리스 빌드 (원스톱)
#
# ⚠️ 함정 주의: docs/ 를 고친 뒤 gradle 만 돌리면 옛 화면이 그대로 들어간다.
#    Capacitor 는 `cap copy` 를 해야 www -> android/app/src/main/assets/public 로 복사되기 때문.
#    이 스크립트는 그 순서를 강제하고, 결과물에 최신 코드가 들어갔는지 검증까지 한다.
#
# 사용법:  powershell -ExecutionPolicy Bypass -File build-release.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$JDK = "C:\Users\sm937\.bubblewrap\jdk17\jdk-17.0.19+10"
$env:JAVA_HOME = $JDK
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
$env:Path = "$JDK\bin;" + $env:Path
$BT = "$env:LOCALAPPDATA\Android\Sdk\build-tools\34.0.0"
$KS = Join-Path (Split-Path -Parent $root) "android\android.keystore"
$SECRETS = Join-Path (Split-Path -Parent $root) "android\keystore-secrets.txt"

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
