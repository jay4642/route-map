# 항로 기상 중첩도 아카이브

1. 원본 캡처를 `inbox/`에 넣고, `tools/configs/`의 기존 JSON을 복사해 새 회차 설정(파일명·가림 영역·georef)을 만든 뒤 `python3 tools/build_overlay.py tools/configs/<id>.json`으로 지도 HTML과 합성 이미지를 만듭니다 (georef는 `tools/georef.py`, 이미 만든 지도 HTML이 있으면 이 단계 생략).
2. 이 폴더에서 `python3 add_entry.py`를 실행하고 기준시각(UTC, 예: `2026-09-23 1330`)·제목·메모·태그(편명·구간, 예: `OZ222,ICN→JFK`)를 입력합니다.
3. 스크립트가 등록 → inbox 비우기 → `git commit`/`push`까지 하며, 1~2분 뒤 GitHub Pages에 반영됩니다 (필요 패키지: `python3 -m pip install --user Pillow numpy opencv-python-headless`).
