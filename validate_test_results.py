#!/usr/bin/env python3
"""site/jobs.csv에서 링커리어·원티드 테스트 결과를 간단히 점검합니다."""
from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "site" / "jobs.csv"


def has_section(text: str, labels: list[str]) -> bool:
    compact = (text or "").replace(" ", "")
    return any(f"[{label.replace(' ', '')}]" in compact for label in labels)


def main() -> int:
    if not CSV_PATH.exists():
        print(f"FAIL: 결과 파일이 없습니다: {CSV_PATH}")
        return 1

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    counts = Counter((row.get("platform") or "미상") for row in rows)
    print("\n[플랫폼별 수집 결과]")
    for platform in ["링커리어", "원티드"]:
        print(f"- {platform}: {counts.get(platform, 0)}건")

    wanted = [row for row in rows if row.get("platform") == "원티드"]
    linkareer = [row for row in rows if row.get("platform") == "링커리어"]

    wanted_body = [row for row in wanted if (row.get("description_text") or "").strip()]
    wanted_preferred = [
        row for row in wanted
        if has_section(row.get("description_text") or "", ["우대 요건", "우대사항", "우대요건"])
    ]
    wanted_employment = [
        row for row in wanted
        if (row.get("job_type") or "").strip()
        or has_section(row.get("description_text") or "", ["고용조건", "고용 형태"])
    ]
    broken_month = [
        row for row in rows
        if "\n개월" in (row.get("description_text") or "")
        or "• 개월" in (row.get("description_text") or "")
    ]
    fragmented_inline = [
        row for row in linkareer
        if re.search(r"(?m)^\s*[()/,\[\]]\s*$", row.get("description_text") or "")
        or "• •" in (row.get("description_text") or "")
    ]

    print("\n[원티드 상세 점검]")
    print(f"- 상세 본문 있음: {len(wanted_body)}/{len(wanted)}건")
    print(f"- 우대사항 섹션 있음: {len(wanted_preferred)}/{len(wanted)}건 (공고에 원래 없으면 정상적으로 0일 수 있음)")
    print(f"- 고용조건 있음: {len(wanted_employment)}/{len(wanted)}건 (공고에 원래 없으면 빈칸이 정상)")
    print(f"- 숫자 없이 '개월'만 남은 의심 건: {len(broken_month)}건")

    linkareer_body = [row for row in linkareer if (row.get("description_text") or "").strip()]
    print("\n[링커리어 상세 점검]")
    print(f"- 상세 텍스트 있음: {len(linkareer_body)}/{len(linkareer)}건")
    print(f"- 괄호·슬래시 등이 한 줄씩 분리된 의심 건: {len(fragmented_inline)}건")
    print("  이미지형 공고는 description_type=image이고 본문이 비어 있어도 정상입니다.")

    problems: list[str] = []
    if not linkareer:
        problems.append("링커리어 결과가 0건입니다. 자동 필터 실패 안내가 나왔다면 브라우저에서 필터를 선택한 뒤 Enter를 눌러주세요.")
    if not wanted:
        problems.append("원티드 결과가 0건입니다.")
    if wanted and not wanted_body:
        problems.append("원티드 상세 본문이 전부 비어 있습니다.")
    if broken_month:
        problems.append("'6개월' 같은 숫자+단위 복구가 안 된 행이 있습니다.")
    if fragmented_inline:
        problems.append("링커리어 문장이 span 경계에서 괄호·슬래시 단위로 분리된 행이 있습니다.")

    if problems:
        print("\n[확인 필요]")
        for item in problems:
            print(f"- {item}")
        return 1

    print("\nPASS: 두 플랫폼 결과가 생성됐고 기본 점검을 통과했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
