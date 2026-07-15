#!/usr/bin/env python3
"""원티드 목록을 거치지 않고 상세 URL에서 회사명 추출만 직접 검증합니다."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from job_crawler import (
    create_wanted_driver,
    enrich_job_detail,
    extract_detail_payload,
    new_job_from_card,
)

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "company_test_output"
DEBUG_DIR = OUT_DIR / "debug"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="원티드 회사명 상세 URL 직접 테스트")
    parser.add_argument(
        "--url",
        default="https://www.wanted.co.kr/wd/251713",
        help="확인할 원티드 상세 공고 URL",
    )
    parser.add_argument("--headless", action="store_true", help="브라우저 숨김 모드")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    job = new_job_from_card(
        wanted_id=args.url.rstrip("/").split("/")[-1].split("?")[0],
        category="테스트",
        position_title="제목 확인 필요",
        company="회사명 확인 필요",
        matched_role="상세 URL 직접 테스트",
        link=args.url,
        raw_card_text="",
        source_strategy="detail_direct_test",
        location_career="",
    )

    print("[원티드 회사명 직접 테스트]")
    print(f"상세 URL: {args.url}")
    driver = create_wanted_driver(headless=args.headless)
    try:
        driver.get(args.url)
        WebDriverWait(driver, 25).until(
            lambda d: bool(d.find_elements(By.TAG_NAME, "h1"))
        )
        payload = extract_detail_payload(driver)
        print("페이지 h1:", payload.get("h1_texts", []))

        enrich_job_detail(
            driver,
            job,
            DEBUG_DIR,
            detail_delay=1.0,
            save_each_detail=True,
        )

        result = asdict(job)
        (OUT_DIR / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"회사명: {job.company}")
        print(f"포지션명: {job.position_title}")
        print(f"상태: {job.detail_status}")
        print(f"결과: {OUT_DIR / 'result.json'}")

        if job.company in {"", "회사명 확인 필요", "확인 필요"}:
            print("실패: 회사명을 추출하지 못했습니다. debug 폴더를 확인해 주세요.")
            return 2
        print("성공: 상세 페이지에서 회사명을 추출했습니다.")
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
