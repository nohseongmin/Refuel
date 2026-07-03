# ⛽ Refuel

> **AI 코딩 에이전트 연료 게이지** — 토큰 얼마나 썼고, 언제 다시 충전되는지, 알아서 알려주는 트레이 앱.

[![Release](https://img.shields.io/github/v/release/nohseongmin/Refuel?label=release&color=46e08a)](https://github.com/nohseongmin/Refuel/releases/latest)
[![Windows](https://img.shields.io/badge/Windows-10%2F11-5a8dee)](https://github.com/nohseongmin/Refuel/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.12-f5c451)](https://www.python.org/)
[![Privacy](https://img.shields.io/badge/network-0%20calls-46e08a)](#-프라이버시)

토큰 다 쓰고 리셋 기다리는데 **언제 풀리는지 기억 안 나서** 답답했던 적 있다면 — 이 앱이 대신 기억하고, 풀리면 알려준다.

---

## ✨ 기능

| | |
|---|---|
| ⏳ **재충전 카운트다운** | 5시간 윈도우 리셋까지 실시간 카운트다운 + 리셋 시각 |
| 📅 **주간 한도 추적** | 5시간이 풀려도 주간 한도가 병목이면 주간 리셋 카운트다운으로 자동 전환 |
| 🔔 **알아서 알림** | 재충전 완료 · 리셋 임박(30분 전) · 사용량 경고(80%) · 주간 리셋 — 에이전트 이름과 함께 |
| 🤖 **에이전트 자동 발견** | 설치된 에이전트 로그를 스스로 찾음. 경로 설정 없음 (Claude Code 지원, Codex 실험적) |
| 📊 **사용량 대시보드** | 현재 윈도우(입력/출력/캐시 분리) · 오늘 · 이번 주 · 최근 7일 일별 그래프 |
| 🎯 **한도 자동 추정** | 과거 사용 패턴(최대 윈도우/주간)으로 한도 % 자동 계산 — 입력할 것 없음 |
| 🖥️ **트레이 상주** | 닫으면 트레이로, 호버하면 카운트다운, 우클릭 종료. 단일 인스턴스 |

## 📥 설치

[**최신 릴리스에서 Refuel.exe 다운로드**](https://github.com/nohseongmin/Refuel/releases/latest) → 더블클릭. 끝.

> ⚠️ 미서명 exe라 Windows SmartScreen 경고가 뜰 수 있어요 → **"추가 정보" → "실행"**.
> 🔕 알림이 안 뜨면 → Windows **방해 금지(집중 지원)** 가 켜져 있는지 확인하세요. (설정 → 시스템 → 알림)

### 소스로 실행 / 빌드

```bash
pip install -r requirements.txt
python run.py          # 실행
build.bat              # exe 빌드 → dist\Refuel.exe
```

## 🔒 프라이버시 & 보안

- **기본 동작은 네트워크 호출 0회.** 모든 데이터는 PC 안에서만 처리.
- 로그 파일을 **읽기만** 하며, 코드/프롬프트가 아닌 **토큰 수·시각**만 집계.
- 저장 위치: `~/.refuel/` (설정 `config.json` · 히스토리 `history.db` · 로그 `refuel.log`)
- **폰 연동(옵트인, 기본 OFF)** 시에도:
  - 나가는 데이터 = 토큰 수·시각·에이전트명뿐. 코드/프롬프트/키는 절대 안 나감.
  - 상태 페이로드는 **AES-GCM 종단간 암호화** — 릴레이(ntfy)는 암호문만 봄, 위조 주입도 인증 태그로 차단.
  - 채널 = 166비트 랜덤 비밀 토픽. 암호화 키는 **QR 프래그먼트(#)로만 전달** — 어떤 서버로도 전송되지 않음.
  - 유출 의심 시 QR 창에서 **토픽·키 재발급** 원클릭.
- 소스 전체 공개 — 직접 확인 가능.

## ⚙️ 설정 (앱 내 ⚙ 버튼)

주간 리셋 요일/시각 · 창 닫으면 트레이로 · 윈도우 시작 시 자동 실행 · 강조 색상 · 테스트 알림

> 💡 주간 리셋 요일/시각을 본인 플랜의 실제 리셋(Claude Code `/usage` 참고)으로 맞추면 주간 카운트다운이 정확해집니다.

## 🧠 동작 원리

```
에이전트 로그(~/.claude/projects/*.jsonl 등)
   → 자동 발견·파싱 (mtime 캐시)
   → 5시간 롤링 윈도우 + 주간 버킷 계산
   → 한도 추정(과거 최대) → 게이지·카운트다운·알림
```

- 5시간 윈도우: 첫 메시지 시각 + 5h = 리셋. 로그 타임스탬프 기반 *추정*이라 공식 `/usage`와 1~2분 오차 가능.
- 한도 %: 실제 플랜 천장이 아니라 **내 과거 최대 사용량** 기준. 데이터가 쌓일수록 정확해짐.

## 🗺️ 로드맵

- [x] 0.x — 데스크톱 트레이 앱 (지금)
- [ ] 에이전트 추가 (Codex 검증, Gemini CLI …)
- [ ] **1.0 — 폰 연결**: PC 수집기 → 클라우드 → 폰 푸시 알림 (iOS/Android)
- [ ] 한도 도달 시각 *예측*, 멀티 PC 합산

## 🧰 스택

Python 3.12 · Tkinter(GUI) · pystray(트레이/알림) · winotify(토스트 폴백) · SQLite(히스토리) · PyInstaller(단일 exe) · GitHub Actions(자동 릴리스)

---

Made with ⛽ by [nohseongmin](https://github.com/nohseongmin) — *Your code never leaves your machine. Only the gauge does.*
