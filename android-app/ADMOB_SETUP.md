# AdMob 실제 ID 교체 가이드

> 현재 빌드는 **Google 공식 테스트 광고**로 동작한다(테스트 배너가 뜸).
> 실제 수익을 받으려면 아래 2개 ID를 네 것으로 바꾸고 재빌드해야 한다.
> ⚠️ **테스트 ID로 출시하면 수익 0이고, 반대로 실제 ID로 개발 중 광고를 직접 클릭하면 계정 정지된다.**

## 1단계 — AdMob 계정 만들고 앱 등록
1. https://admob.google.com 접속 → 구글 계정으로 가입 (애드센스 연결됨, 지급 정보 입력 필요)
2. 앱 → **앱 추가** → 플랫폼 **Android** → "앱이 스토어에 등록되어 있나요?" → 아직이면 **아니요**
3. 앱 이름 `Refuel` 입력 → 추가
4. 생성된 **앱 ID** 복사 — 형식: `ca-app-pub-0000000000000000~1111111111`  ← **물결(~)** 표시

## 2단계 — 배너 광고 단위 만들기
1. 방금 만든 앱 → **광고 단위** → **광고 단위 추가** → **배너** 선택
2. 이름 `Refuel 하단 배너` → 만들기
3. 생성된 **광고 단위 ID** 복사 — 형식: `ca-app-pub-0000000000000000/2222222222`  ← **슬래시(/)** 표시

## 3단계 — 코드 2곳 교체
### (1) 앱 ID — `android-app/android/app/src/main/AndroidManifest.xml`
```xml
<meta-data
    android:name="com.google.android.gms.ads.APPLICATION_ID"
    android:value="여기에_앱ID(~형식)" />
```

### (2) 광고 단위 ID + 테스트 끄기 — `docs/index.html` 하단 AdMob 블록
```js
const TEST_BANNER='여기에_광고단위ID(/형식)';
const IS_TESTING=false;   // 출시 시 반드시 false
```

## 4단계 — 재빌드
```powershell
cd "C:\Claude Projects\Refuel\android-app"
Copy-Item "..\docs\*" www -Recurse -Force   # 웹 자산 다시 번들
npx cap sync android
cd android
.\gradlew.bat bundleRelease assembleRelease
```
그 뒤 서명(프로젝트 루트 기준 `android/android.keystore` 재사용).

## 5단계 — Play Console 필수 변경 (안 하면 정책 위반)
- 앱 콘텐츠 → **광고 포함: 예**
- 데이터 안전성 → **기기 또는 기타 ID(광고 ID)** 수집 추가 / 목적 **광고 또는 마케팅** / **Google과 공유: 예**
- **개인정보처리방침에 광고 문단 추가** (docs/privacy.html — AdMob 사용·광고ID·구글 정책 링크)
- 광고 ID 권한 선언(AD_ID) — 광고 목적

## 수익 현실 (기대치 조정용)
개발자 대상 니치 유틸은 배너 eCPM이 낮고 애드블록·광고 회피가 많다.
설치 수천 명 기준 **월 수천~수만 원** 수준으로 보는 게 현실적이다.
큰 수익을 원하면 프리미엄 IAP(멀티 에이전트·히스토리 유료화) 병행이 훨씬 유리하다.
