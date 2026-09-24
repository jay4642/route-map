# 항로 기상 중첩도 아카이브

1. 새 회차의 중첩 지도 `.html` 1개와 원본 캡처(`.png`/`.jpg`)를 `inbox/`에 넣습니다. 합성 이미지는 지도 HTML의 레이어를 겹쳐 자동 생성됩니다 (직접 넣으려면 `composite.png`).
2. 이 폴더에서 `python3 add_entry.py`를 실행하고 기준시각(UTC, 예: `2026-09-23 1330`)·제목·메모를 입력합니다.
3. 스크립트가 등록 → inbox 비우기 → `git commit`/`push`까지 하며, 1~2분 뒤 GitHub Pages에 반영됩니다 (Pillow 필요: `python3 -m pip install --user Pillow`).
