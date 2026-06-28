# Refuel

AI 코딩 에이전트의 토큰 사용량과 "재충전(5시간 윈도우)" 시간을 알려주는 데스크톱 모니터.
서버·계정 연결 없이 **로컬 로그만** 읽어 동작 — 코드/프롬프트는 PC 밖으로 나가지 않는다.

현재: Claude Code 지원 (Cursor·Copilot 등 멀티 에이전트는 로드맵).

## 기능
- 재충전까지 남은 시간(5h 윈도우) + 리셋 시각 카운트다운
- 현재 윈도우 / 오늘 / 최근 7일 토큰, 모델별 누적
- 트레이 상주 (창 닫으면 트레이로) + 토스트 알림
  - 재충전 완료 · 리셋 임박(30분 전) · 사용량 경고(한도 80%)

## 개발 실행
```bash
pip install -r requirements.txt
python run.py
```

## exe 빌드
```bat
build.bat
```
결과물: `dist\Refuel.exe` (단일 실행 파일)

## 설정 (`refuel/core.py`)
- `BLOCK_TOKEN_LIMIT` — 플랜 토큰 한도. 설정해야 %게이지/사용량 경고 작동.
- `WARN_RATIO` — 사용량 경고 임계(기본 0.8)
- `RESET_SOON_MIN` — 리셋 임박 알림(분)

## 한계 (개발 초기)
- 5h 윈도우 시작 시각은 로그 타임스탬프 기반 *추정* — Claude Code `/usage` 와 대조 보정 필요.
- 표시 토큰은 cache_read 포함이라 부풀려짐 → 추후 입력/출력/캐시 가중 분리 예정.
- 플랜 한도 자동 감지 미구현(`BLOCK_TOKEN_LIMIT` 수동).
