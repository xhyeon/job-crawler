#!/usr/bin/env python3
"""원티드 상세 URL 1건을 직접 열어 섹션 수집 결과를 검증합니다."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path

from job_crawler import create_wanted_driver, enrich_job_detail, new_job_from_card

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "wanted_detail_test_output"
DEBUG_DIR = OUT_DIR / "debug"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="원티드 상세 공고 1건 직접 테스트")
    parser.add_argument("--url", required=True, help="https://www.wanted.co.kr/wd/숫자")
    parser.add_argument("--headless", action="store_true", help="브라우저 숨김 모드")
    parser.add_argument("--delay", type=float, default=1.0, help="상세 로딩 추가 대기 초")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    match = re.search(r"/wd/(\d+)", args.url)
    if not match:
        print("올바른 원티드 상세 URL을 입력해 주세요.")
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    job = new_job_from_card(
        wanted_id=match.group(1),
        category="테스트",
        position_title="제목 확인 필요",
        company="회사명 확인 필요",
        matched_role="상세 URL 직접 테스트",
        link=args.url,
        raw_card_text="",
        source_strategy="wanted_detail_direct_test",
        location_career="",
    )

    driver = create_wanted_driver(headless=args.headless)
    try:
        enrich_job_detail(
            driver,
            job,
            DEBUG_DIR,
            detail_delay=args.delay,
            save_each_detail=True,
        )
        result = asdict(job)
        result_path = OUT_DIR / "result.json"
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("\n[원티드 상세 테스트 결과]")
        print(f"상태: {job.detail_status}")
        print(f"회사: {job.company}")
        print(f"포지션: {job.position_title}")
        print(f"주요업무 글자 수: {len(job.main_tasks)}")
        print(f"자격요건 글자 수: {len(job.requirements)}")
        print(f"우대사항 글자 수: {len(job.preferred_requirements)}")
        print(f"고용조건: {job.employment_type}")
        print(f"결과 JSON: {result_path}")
        print(f"디버그 HTML/PNG: {DEBUG_DIR}")

        page_html = driver.page_source or ""
        has_preferred_heading = bool(
            re.search(r"<h3[^>]*>\s*우대\s*(?:사항|요건|자격|조건)\s*</h3>", page_html, re.I)
        )
        has_employment_heading = bool(
            re.search(
                r"<h3[^>]*>\s*(?:고용\s*(?:조건|형태)|근무\s*(?:조건|형태|기간)|채용\s*형태|계약\s*형태)\s*</h3>",
                page_html,
                re.I,
            )
        )

        if not job.main_tasks or not job.requirements:
            print("실패: 주요업무 또는 자격요건을 가져오지 못했습니다.")
            return 1
        if has_preferred_heading and not job.preferred_requirements:
            print("실패: 페이지에 우대사항 제목이 있지만 내용을 가져오지 못했습니다.")
            return 1
        if has_employment_heading and not job.employment_type:
            print("실패: 페이지에 고용조건 제목이 있지만 내용을 가져오지 못했습니다.")
            return 1
        if not has_employment_heading:
            print("안내: 이 공고에는 별도 고용조건 섹션이 없어 빈 값으로 유지했습니다.")

        print("성공: 주요업무·자격요건·우대사항·선택적 고용조건을 제목 기준으로 수집했습니다.")
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
