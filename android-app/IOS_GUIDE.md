# Refuel iOS 가이드

> **핵심: iOS 앱은 Windows에서 못 만든다.** 애플이 Xcode(macOS 전용)를 강제한다.
> 그래서 두 갈래로 준비해 뒀다.

## 지금 당장 되는 것 — PWA (무료, 계정 불필요)
아이폰 사용자는 앱 없이도 바로 쓸 수 있다:
1. 사파리로 https://nohseongmin.github.io/Refuel/ 접속
2. 공유 버튼 → **홈 화면에 추가**
3. 전체화면 앱처럼 동작 (QR 스캔·대시보드·데모 모두 됨)

한계: iOS PWA는 백그라운드 푸시가 제한적(웹푸시는 iOS 16.4+만, 그마저도 홈화면 추가 필요).
알림을 확실히 하려면 아래 네이티브 앱이 필요하다.

## 네이티브 iOS 앱 (App Store) — 필요한 것
| 항목 | 내용 |
|---|---|
| Apple Developer | **연 $99** (필수) |
| 빌드 환경 | Mac + Xcode **또는** 이 저장소의 `ios-build` GitHub Actions(맥 서버) |
| 서명 | 인증서(.p12) + 프로비저닝 프로파일 |
| ⚠️ 심사 위험 | **가이드라인 4.2** — 단순 웹뷰 래퍼는 반려. 우리 앱은 AdMob·로컬알림·카메라 등 네이티브 기능이 있어 방어 가능하나, iOS는 안드로이드보다 엄격 |

## CI로 빌드 검증 (맥 없이)
`.github/workflows/ios.yml` = GitHub의 macOS 러너에서 iOS를 **서명 없이 컴파일**해 코드가 iOS 호환인지 검증한다.
- 실행: 저장소 Actions 탭 → ios-build → Run workflow (또는 `git tag ios-vX && git push origin ios-vX`)
- 성공하면 "iOS 컴파일 성공" — 코드 레벨 호환 확인됨.

## 정식 IPA / TestFlight 배포 (Apple 계정 생긴 뒤)
1. Apple Developer 가입($99) → App Store Connect에서 앱 등록(Bundle ID: `io.github.nohseongmin.refuel`)
2. 인증서·프로파일 생성(Mac이면 Xcode가 자동, 아니면 fastlane match)
3. GitHub Secrets에 인증서(.p12 base64)·프로파일·암호 등록
4. ios.yml의 빌드 스텝을 서명 빌드 + `xcodebuild -exportArchive`로 교체(요청 시 만들어 줌)
5. TestFlight 업로드 → 내부 테스트 → 심사 → 출시

## AdMob (iOS)
- Info.plist의 `GADApplicationIdentifier` = 현재 **iOS 테스트 앱 ID**. AdMob 콘솔에서 iOS 앱을 별도 등록해 실제 ID로 교체.
- iOS는 광고 추적에 **App Tracking Transparency(ATT)** 동의가 필요 → `NSUserTrackingUsageDescription` 이미 넣어둠.

## 추천 순서
1. **PWA로 아이폰 지원 시작**(무료·즉시) — 대부분의 니즈 커버
2. 안드로이드 출시·반응 확인
3. 수요 있으면 Apple $99 결제 → CI 서명 빌드로 App Store
