"""
채용공고 크롤러 JSON 전용 (링커리어·원티드 통합)
- 플랫폼: 링커리어, 원티드
- 직군: 마케팅, 기획, 인사, 영업, 개발
- 신규 기준: 공고의 게시일/시작일이 아니라 크롤러가 처음 발견한 날짜
- 최초 실행: 직군별 리스트를 최대 10페이지까지 모두 확인해 기존 공고 기준 데이터를 생성
- 이후 실행: 최신 페이지부터 확인하다 기존 공고만 나온 페이지에서 종료
- 담당자 화면: 최초 발견일이 한국시간 기준 최근 7일인 공고를 노출

실행 예시
  pip install -r requirements.txt
  python job_crawler.py --platform 링커리어 --category all --max-pages 10
  python job_crawler.py --platform 링커리어 --category 마케팅 --manual
  python job_crawler.py --init-seen   # 기준 데이터를 처음부터 다시 생성

결과
  site/index.html
  site/jobs.json
  site/jobs.csv
  data/seen_jobs.json
  debug/*.png, debug/*.html
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException, WebDriverException, NoSuchWindowException, InvalidSessionIdException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# ---------------------------------------------------------------------------
# 원티드 목록·상세 크롤러 (기존 wanted_crawler.py 통합)
# ---------------------------------------------------------------------------
import argparse
import csv
import hashlib
import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait

try:
    from webdriver_manager.chrome import ChromeDriverManager
except Exception:  # pragma: no cover
    ChromeDriverManager = None

WANTED_QUERY = (
    "country=kr"
    "&job_sort=job.latest_order"
    "&years=0"
    "&years=3"
    "&locations=all"
)

BASE_URL = f"https://www.wanted.co.kr/wdlist?{WANTED_QUERY}"

# 오늘을 포함한 최근 7일 동안 처음 발견한 공고를 담당자 화면에 유지합니다.
DISPLAY_WINDOW_DAYS = 7
# 직군별 수집 설정입니다.
# roles가 비어 있으면 해당 상위 직군 전체를 수집합니다.
TARGET_CATEGORIES: dict[str, dict[str, object]] = {
    "기획": {
        "parent_id": "507",
        "parent_name": "경영·비즈니스",
        "roles": {
            "564": "사업개발·기획자",
            "559": "PM·PO",
            "565": "서비스 기획자",
            "563": "전략 기획자",
            "10232": "상품기획자(BM)",
        },
    },
    "인사": {
        "parent_id": "517",
        "parent_name": "HR",
        "roles": {},
    },
    "개발": {
        "parent_id": "518",
        "parent_name": "개발",
        "roles": {},
    },
    "영업": {
        "parent_id": "530",
        "parent_name": "영업",
        "roles": {},
    },
    "마케팅": {
        "parent_id": "523",
        "parent_name": "마케팅·광고",
        "roles": {},
    },
}

# UI 통합 선택 테스트는 기존 기획 5개 직무에만 사용합니다.
# 운영/확장 테스트는 direct 전략을 사용합니다.
PARENT_CATEGORY_ID = "507"
PARENT_CATEGORY_NAME = "경영·비즈니스"
TARGET_ROLES: dict[str, str] = dict(TARGET_CATEGORIES["기획"]["roles"])

META_RE = re.compile(
    r"(서울|경기|인천|부산|대구|대전|광주|세종|울산|강원|충북|충남|전북|전남|경북|경남|제주|"
    r"경력|신입|년 이상|응답률|합격보상금|채용보상금|북마크|적극 채용|D-\d+|상시채용|마감)"
)
TITLE_HINT_RE = re.compile(
    r"(기획|PM|PO|Product|Manager|매니저|담당|사업개발|전략|서비스|상품|BM|MD|운영|인턴|"
    r"개발|엔지니어|프론트엔드|백엔드|데이터|DevOps|QA|영업|세일즈|Sales|마케팅|광고|브랜드|콘텐츠|CRM|"
    r"Planner|Planning|Development|Developer|Engineer|Strategy|Marketing)",
    re.IGNORECASE,
)
EMPLOYMENT_LINE_RE = re.compile(
    r"(?:고용\s*형태|고용조건|근무\s*형태|채용\s*형태|계약\s*형태)\s*[:：]?\s*([^\n]+)",
    re.IGNORECASE,
)

WANTED_KST = ZoneInfo("Asia/Seoul")
UNIFIED_FIELDS = [
    "platform",
    "category",
    "is_new",
    "company",
    "title",
    "start_date",
    "deadline",
    "job_type",
    "raw_category",
    "description_type",
    "description_text",
    "link",
    "detail_url",
    "apply_url",
    "first_seen_at",
    "fingerprint",
]


@dataclass
class WantedJob:
    wanted_id: str
    category: str
    title: str
    position_title: str
    company: str
    employment_type: str
    matched_role: str
    deadline: str
    requirements: str
    preferred_requirements: str
    main_tasks: str
    position_detail: str
    benefits: str
    hiring_process: str
    location: str
    career: str
    link: str
    raw_card_text: str
    source_strategy: str
    detail_status: str
    collected_at: str
    is_new: bool = True
    first_seen_at: str = ""


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


BROKEN_NUMBER_UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*\n\s*(개월|년|주|일|시간|분|명|만원|원|%)"
)


def repair_broken_number_units(text: str) -> str:
    """줄바꿈 때문에 분리된 ``6\n개월`` 같은 숫자+단위를 복구합니다.

    원티드/링커리어 DOM의 ``br`` 또는 반응형 레이아웃 때문에 숫자와 단위가
    서로 다른 줄로 추출되는 경우가 있습니다. 목록 번호인 ``1)`` 등은 건드리지
    않고, 뒤에 기간·수량·금액 단위가 실제로 오는 경우에만 붙입니다.
    """
    return BROKEN_NUMBER_UNIT_RE.sub(r"\1\2", text or "")


def clean_multiline(text: str) -> str:
    """상세 본문의 줄바꿈은 유지하되 불필요한 공백과 빈 줄을 정리합니다."""
    lines: list[str] = []
    repaired = repair_broken_number_units((text or "").replace("\r", ""))
    for raw in repaired.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if not line:
            continue
        if not lines or lines[-1] != line:
            lines.append(line)
    return "\n".join(lines)


def create_wanted_driver(headless: bool) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=ko-KR")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    try:
        driver = webdriver.Chrome(options=options)
    except Exception as first_error:
        if ChromeDriverManager is None:
            raise RuntimeError(
                "ChromeDriver를 준비하지 못했습니다. requirements.txt 설치와 Chrome 설치를 확인해 주세요."
            ) from first_error
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
        except Exception as second_error:
            raise RuntimeError(
                "ChromeDriver 실행 실패. Chrome 설치 여부와 네트워크/권한을 확인해 주세요."
            ) from second_error

    driver.set_page_load_timeout(60)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


def visible(elements: Iterable[WebElement]) -> list[WebElement]:
    result: list[WebElement] = []
    for element in elements:
        try:
            if element.is_displayed():
                result.append(element)
        except Exception:
            continue
    return result


def js_click(driver: webdriver.Chrome, element: WebElement) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
    time.sleep(0.15)
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)
    time.sleep(0.6)


def click_text(driver: webdriver.Chrome, labels: Iterable[str]) -> Optional[str]:
    for label in labels:
        literal = json.dumps(label, ensure_ascii=False)
        xpaths = [
            f"//button[normalize-space(.)={literal}]",
            f"//*[@role='button' and normalize-space(.)={literal}]",
            f"//button[contains(normalize-space(.), {literal})]",
            f"//*[@role='button' and contains(normalize-space(.), {literal})]",
            f"//*[self::span or self::p][normalize-space(.)={literal}]/ancestor::button[1]",
        ]
        for xpath in xpaths:
            try:
                elements = visible(driver.find_elements(By.XPATH, xpath))
                if elements:
                    js_click(driver, elements[0])
                    return label
            except Exception:
                continue
    return None


def wait_for_job_list(driver: webdriver.Chrome, timeout: int = 20) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, "a[href*='/wd/']")) > 0
    )


def role_button(driver: webdriver.Chrome, item_id: str) -> Optional[WebElement]:
    elements = visible(driver.find_elements(By.CSS_SELECTOR, f"button[data-itemid='{item_id}']"))
    return elements[0] if elements else None


def is_role_checked(button: WebElement) -> bool:
    try:
        if (button.get_attribute("aria-checked") or "").lower() == "true":
            return True
        class_name = (button.get_attribute("class") or "").lower()
        if "ischecked" in class_name or "selected" in class_name:
            return True
        checks = button.find_elements(By.CSS_SELECTOR, "[role='checkbox']")
        return any((c.get_attribute("aria-checked") or "").lower() == "true" for c in checks)
    except Exception:
        return False


def open_job_filter(driver: webdriver.Chrome) -> bool:
    if any(role_button(driver, item_id) is not None for item_id in TARGET_ROLES):
        return True

    click_text(driver, ["직군·직무", "직군・직무", "직군/직무", "직무"])
    time.sleep(1.0)
    if any(role_button(driver, item_id) is not None for item_id in TARGET_ROLES):
        return True

    inputs = visible(
        driver.find_elements(
            By.XPATH,
            f"//input[@readonly and (@value='{PARENT_CATEGORY_NAME}' or contains(@value,'경영'))]",
        )
    )
    if inputs:
        try:
            parent = inputs[0].find_element(By.XPATH, "ancestor::*[self::button or @role='button'][1]")
            js_click(driver, parent)
        except Exception:
            js_click(driver, inputs[0])
        time.sleep(1.0)

    if any(role_button(driver, item_id) is not None for item_id in TARGET_ROLES):
        return True

    click_text(driver, [PARENT_CATEGORY_NAME])
    time.sleep(1.0)
    return any(role_button(driver, item_id) is not None for item_id in TARGET_ROLES)


def apply_five_role_filter(driver: webdriver.Chrome) -> tuple[bool, dict[str, bool]]:
    if not open_job_filter(driver):
        return False, {name: False for name in TARGET_ROLES.values()}

    selected: dict[str, bool] = {}
    for item_id, role_name in TARGET_ROLES.items():
        button = role_button(driver, item_id)
        if button is None:
            print(f"  ❌ 직무 버튼을 찾지 못함: {role_name} ({item_id})")
            selected[role_name] = False
            continue
        if not is_role_checked(button):
            js_click(driver, button)
        selected[role_name] = is_role_checked(button)
        print(f"  {'✅' if selected[role_name] else '⚠️'} {role_name} ({item_id})")

    apply_buttons: list[WebElement] = []
    for text in ["적용", "확인", "완료"]:
        apply_buttons.extend(
            visible(
                driver.find_elements(
                    By.XPATH,
                    f"//button[normalize-space(.)='{text}' or .//*[normalize-space(.)='{text}']]",
                )
            )
        )
    if apply_buttons:
        js_click(driver, apply_buttons[-1])
    time.sleep(2.0)
    return all(selected.values()), selected


def scroll_until_stable(
    driver: webdriver.Chrome,
    max_scrolls: int,
    stable_rounds: int = 3,
    delay: float = 1.3,
) -> int:
    last_count = -1
    unchanged = 0
    for idx in range(max_scrolls):
        current_count = len(driver.find_elements(By.CSS_SELECTOR, "a[href*='/wd/']"))
        print(f"  - 스크롤 {idx + 1}/{max_scrolls}: 공고 링크 {current_count}개")
        unchanged = unchanged + 1 if current_count == last_count else 0
        if unchanged >= stable_rounds:
            break
        last_count = current_count
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(delay)
    return len(driver.find_elements(By.CSS_SELECTOR, "a[href*='/wd/']"))


def normalize_job_url(href: str) -> str:
    if not href:
        return ""
    absolute = urljoin("https://www.wanted.co.kr", href)
    parsed = urlparse(absolute)
    match = re.search(r"/wd/(\d+)", parsed.path)
    return f"https://www.wanted.co.kr/wd/{match.group(1)}" if match else ""


def card_text(driver: webdriver.Chrome, anchor: WebElement) -> str:
    script = r"""
    const a = arguments[0];
    let node = a;
    let best = (a.innerText || '').trim();
    for (let i = 0; i < 7 && node; i++, node = node.parentElement) {
      const text = (node.innerText || '').trim();
      const links = node.querySelectorAll ? node.querySelectorAll("a[href*='/wd/']").length : 0;
      if (text.length >= 8 && text.length <= 900 && links <= 3) best = text;
      if (node.matches && node.matches("li, article, [class*='JobCard'], [class*='Card']")) {
        if (text.length >= 8 && text.length <= 900) return text;
      }
    }
    return best;
    """
    try:
        return driver.execute_script(script, anchor) or ""
    except Exception:
        return anchor.text or ""


def parse_card(raw_text: str) -> tuple[str, str, str]:
    lines: list[str] = []
    for line in re.split(r"[\r\n]+", raw_text or ""):
        line = normalize(line)
        if not line or line in lines or line in {"북마크", "지원하기", "자세히 보기"}:
            continue
        lines.append(line)

    meta_lines = [line for line in lines if META_RE.search(line)]
    main_lines = [line for line in lines if line not in meta_lines and len(line) <= 120]

    title = "제목 확인 필요"
    company = "회사명 확인 필요"
    if len(main_lines) >= 2:
        first, second = main_lines[0], main_lines[1]
        first_is_title = bool(TITLE_HINT_RE.search(first))
        second_is_title = bool(TITLE_HINT_RE.search(second))
        if first_is_title and not second_is_title:
            title, company = first, second
        elif second_is_title and not first_is_title:
            title, company = second, first
        else:
            title, company = first, second
    elif len(main_lines) == 1:
        title = main_lines[0]

    location_career = meta_lines[0] if meta_lines else ""
    return title, company, location_career


def new_job_from_card(
    wanted_id: str,
    category: str,
    position_title: str,
    company: str,
    matched_role: str,
    link: str,
    raw_card_text: str,
    source_strategy: str,
    location_career: str,
) -> WantedJob:
    return WantedJob(
        wanted_id=wanted_id,
        category=category,
        title=f"[{company}] {position_title}" if company and position_title else position_title,
        position_title=position_title,
        company=company,
        employment_type="",
        matched_role=matched_role,
        deadline="",
        requirements="",
        preferred_requirements="",
        main_tasks="",
        position_detail="",
        benefits="",
        hiring_process="",
        location=location_career,
        career="",
        link=link,
        raw_card_text=raw_card_text.strip(),
        source_strategy=source_strategy,
        detail_status="not_visited",
        collected_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def collect_current_page(
    driver: webdriver.Chrome,
    category: str,
    matched_role: str,
    source_strategy: str,
    limit: int,
) -> list[WantedJob]:
    result: list[WantedJob] = []
    seen: set[str] = set()
    for anchor in driver.find_elements(By.CSS_SELECTOR, "a[href*='/wd/']"):
        link = normalize_job_url(anchor.get_attribute("href") or "")
        if not link or link in seen:
            continue
        seen.add(link)
        match = re.search(r"/wd/(\d+)", link)
        if not match:
            continue
        raw = card_text(driver, anchor)
        position_title, company, location_career = parse_card(raw)
        result.append(
            new_job_from_card(
                wanted_id=match.group(1),
                category=category,
                position_title=position_title,
                company=company,
                matched_role=matched_role,
                link=link,
                raw_card_text=raw,
                source_strategy=source_strategy,
                location_career=location_career,
            )
        )
        if limit and len(result) >= limit:
            break
    return result


def driver_session_alive(driver) -> bool:
    """Chrome 창/세션이 살아 있는지 가볍게 확인합니다."""
    if driver is None:
        return False
    try:
        handles = driver.window_handles
        if not handles:
            return False
        _ = driver.current_url
        return True
    except (NoSuchWindowException, InvalidSessionIdException, WebDriverException):
        return False
    except Exception:
        return False


def save_wanted_debug(driver: webdriver.Chrome, debug_dir: Path, name: str) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    if not driver_session_alive(driver):
        return
    safe_name = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", str(name or "wanted_debug"))[:120]
    try:
        html_text = driver.page_source
        if html_text:
            (debug_dir / f"{safe_name}.html").write_text(html_text, encoding="utf-8")
    except Exception as exc:
        print(f"  ⚠️ HTML 저장 실패: {type(exc).__name__}")
    try:
        driver.save_screenshot(str(debug_dir / f"{safe_name}.png"))
    except Exception as exc:
        print(f"  ⚠️ 스크린샷 저장 실패: {type(exc).__name__}")


def direct_category_url(parent_id: str, item_id: str = "") -> str:
    path = f"{parent_id}/{item_id}" if item_id else parent_id
    return f"https://www.wanted.co.kr/wdlist/{path}?{WANTED_QUERY}"


def direct_sources() -> list[dict[str, str]]:
    """직군 설정을 실제 목록 수집 단위로 펼칩니다."""
    sources: list[dict[str, str]] = []
    for category, config in TARGET_CATEGORIES.items():
        parent_id = str(config["parent_id"])
        parent_name = str(config["parent_name"])
        roles = config.get("roles") or {}
        if isinstance(roles, dict) and roles:
            for item_id, role_name in roles.items():
                sources.append(
                    {
                        "category": category,
                        "raw_category": str(role_name),
                        "item_id": str(item_id),
                        "url": direct_category_url(parent_id, str(item_id)),
                    }
                )
        else:
            sources.append(
                {
                    "category": category,
                    "raw_category": parent_name,
                    "item_id": parent_id,
                    "url": direct_category_url(parent_id),
                }
            )
    return sources




DETAIL_ARTICLE_SELECTOR = "article[class*='JobDescription']"

WANTED_SECTION_ALIASES: dict[str, str] = {
    "주요업무": "duties", "담당업무": "duties", "업무내용": "duties", "주요역할": "duties",
    "자격요건": "requirements", "지원자격": "requirements", "필수요건": "requirements", "필요요건": "requirements",
    "고용조건": "employment", "고용형태": "employment", "근무조건": "employment", "근무기간": "employment", "채용형태": "employment",
    "우대사항": "preferred", "우대요건": "preferred", "우대자격": "preferred",
    "우대조건": "preferred", "선호사항": "preferred", "선호요건": "preferred",
    "PreferredQualifications": "preferred", "PreferredRequirements": "preferred",
    "혜택및복지": "benefits", "복리후생": "benefits", "혜택": "benefits",
    "채용전형": "process", "전형절차": "process", "채용절차": "process", "채용프로세스": "process",
}


def normalize_section_heading(value: str) -> str:
    return re.sub(r"[\s:：]+", "", value or "").strip()


def extract_detail_payload_from_html(html_text: str) -> dict[str, object]:
    """현재 DOM HTML에서 원티드 상세 영역을 h3 제목 기준으로 추출합니다."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    article = None
    for candidate in soup.select(DETAIL_ARTICLE_SELECTOR):
        headings = {
            normalize_section_heading(node.get_text(" ", strip=True))
            for node in candidate.select("h2, h3")
        }
        has_position = "포지션상세" in headings or "포지션소개" in headings
        has_core = any(
            heading in WANTED_SECTION_ALIASES
            and WANTED_SECTION_ALIASES[heading] in {"duties", "requirements"}
            for heading in headings
        )
        if has_position and has_core:
            article = candidate
            break

    result: dict[str, object] = {
        "article_found": bool(article),
        "sections": {},
        "position_detail": "",
        "article_text": "",
    }
    if article is None:
        return result

    sections: dict[str, str] = {}
    for heading in article.find_all("h3"):
        raw_heading = normalize(heading.get_text(" ", strip=True))
        normalized = normalize_section_heading(raw_heading)
        if normalized not in WANTED_SECTION_ALIASES:
            continue
        block = heading.parent
        if not isinstance(block, Tag):
            continue
        content_parts: list[str] = []
        for child in block.children:
            if not isinstance(child, Tag) or child is heading:
                continue
            value = clean_multiline(child.get_text("\n", strip=True))
            if value:
                content_parts.append(value)
        content = clean_multiline("\n".join(content_parts))
        if content:
            sections[raw_heading] = clean_multiline(
                (sections.get(raw_heading, "") + "\n" + content).strip()
            )

    position_detail = ""
    position_heading = next(
        (
            node for node in article.find_all("h2")
            if normalize_section_heading(node.get_text(" ", strip=True)) in {"포지션상세", "포지션소개"}
        ),
        None,
    )
    wrapper = position_heading.find_next_sibling() if position_heading else None
    if isinstance(wrapper, Tag):
        intro_parts: list[str] = []
        for child in wrapper.children:
            if not isinstance(child, Tag):
                continue
            if child.find("h3") is not None:
                break
            value = clean_multiline(child.get_text("\n", strip=True))
            if value:
                intro_parts.append(value)
        position_detail = clean_multiline("\n".join(intro_parts))

    result["sections"] = sections
    result["position_detail"] = position_detail
    result["article_text"] = clean_multiline(article.get_text("\n", strip=True))
    return result


def scroll_to_description_article(driver: webdriver.Chrome, timeout: int = 12) -> Optional[WebElement]:
    """포지션 상세 article이 지연 렌더링되어도 화면에 불러옵니다."""
    deadline = time.time() + timeout
    ratios = [0.35, 0.5, 0.65, 0.8]
    attempt = 0
    while time.time() < deadline:
        articles = driver.find_elements(By.CSS_SELECTOR, DETAIL_ARTICLE_SELECTOR)
        if articles:
            article = articles[0]
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", article)
                time.sleep(0.35)
            except Exception:
                pass
            return article
        ratio = ratios[min(attempt, len(ratios) - 1)]
        try:
            driver.execute_script(
                "window.scrollTo(0, Math.max(0, document.body.scrollHeight * arguments[0]));",
                ratio,
            )
        except Exception:
            pass
        attempt += 1
        time.sleep(0.45)
    return None

def detail_article_state(driver: webdriver.Chrome) -> dict[str, object]:
    """상세 설명 영역이 펼쳐졌는지 판단하기 위한 간단한 상태값을 반환합니다."""
    script = r"""
    const articles = Array.from(document.querySelectorAll("article[class*='JobDescription']"));
    const article = articles.find(el => {
      const headings = Array.from(el.querySelectorAll('h2,h3'))
        .map(node => (node.innerText || '').replace(/\s+/g, ''));
      return headings.includes('포지션상세');
    }) || null;
    if (!article) return {exists:false, text_length:0, heading_count:0};
    const text = (article.innerText || article.textContent || '').trim();
    return {
      exists: true,
      text_length: text.length,
      heading_count: article.querySelectorAll('h3').length,
      headings: Array.from(article.querySelectorAll('h3')).map(el => (el.innerText || '').trim())
    };
    """
    value = driver.execute_script(script)
    return value if isinstance(value, dict) else {}


def stabilize_wanted_detail_article(driver: webdriver.Chrome, timeout: float = 10.0) -> dict[str, object]:
    """상세 article 끝까지 스크롤하고 h3/텍스트가 안정될 때까지 기다립니다.

    원티드는 주요업무·자격요건이 먼저 보이고 우대사항/고용조건이 뒤늦게 DOM에
    붙는 공고가 있어, 핵심 제목 하나만 발견했다고 바로 수집하면 아래 섹션이
    빠질 수 있습니다. 각 h3를 순서대로 화면에 노출한 뒤 상태가 3회 연속 같을 때
    최종 DOM으로 판단합니다.
    """
    deadline = time.time() + max(2.0, timeout)
    previous_signature = None
    stable_count = 0
    last_state: dict[str, object] = {}

    while time.time() < deadline and driver_session_alive(driver):
        try:
            state = driver.execute_script(
                r"""
                const articles = Array.from(document.querySelectorAll("article[class*='JobDescription']"));
                const article = articles.find(el => {
                  const hs = Array.from(el.querySelectorAll('h2,h3')).map(h =>
                    String(h.innerText || h.textContent || '').replace(/\s+/g, '')
                  );
                  return hs.includes('포지션상세') || hs.includes('포지션소개');
                }) || articles[0] || null;
                if (!article) return {exists:false, text_length:0, heading_count:0, headings:[]};
                const headings = Array.from(article.querySelectorAll('h3'));
                for (const heading of headings) {
                  try { heading.scrollIntoView({block:'center', inline:'nearest'}); } catch (_) {}
                }
                try { article.scrollIntoView({block:'end', inline:'nearest'}); } catch (_) {}
                try { window.scrollBy(0, 420); } catch (_) {}
                const text = String(article.innerText || article.textContent || '').trim();
                return {
                  exists:true,
                  text_length:text.length,
                  heading_count:headings.length,
                  headings:headings.map(h => String(h.innerText || h.textContent || '').trim()),
                  scroll_height:article.scrollHeight || 0
                };
                """
            )
            if not isinstance(state, dict):
                state = {}
            last_state = state
            signature = (
                int(state.get("text_length") or 0),
                int(state.get("heading_count") or 0),
                int(state.get("scroll_height") or 0),
                tuple(str(v) for v in (state.get("headings") or [])),
            )
            if signature == previous_signature and signature[0] > 0:
                stable_count += 1
            else:
                stable_count = 0
            previous_signature = signature
            if stable_count >= 2:
                break
        except (NoSuchWindowException, InvalidSessionIdException, WebDriverException):
            break
        except Exception:
            pass
        time.sleep(0.55)

    return last_state


def find_description_expand_control(driver: webdriver.Chrome) -> Optional[WebElement]:
    """JobDescription와 연결된 '상세 정보 더보기' 컨트롤을 찾습니다."""
    article = scroll_to_description_article(driver, timeout=8)

    labels = [
        "상세 정보 더보기", "상세정보 더보기",
        "상세 포지션 보기", "상세포지션 보기",
        "상세 더보기", "내용 더보기",
    ]
    for label in labels:
        literal = json.dumps(label, ensure_ascii=False)
        xpaths = [
            f"//*[self::button or @role='button'][contains(normalize-space(.), {literal})]",
            f"//*[contains(normalize-space(.), {literal})]/ancestor::*[self::button or @role='button'][1]",
            f"//*[contains(normalize-space(.), {literal}) and @wds-component='with-interaction']",
        ]
        for xpath in xpaths:
            controls = visible(driver.find_elements(By.XPATH, xpath))
            if controls:
                return controls[0]

    try:
        candidate = driver.execute_script(
            r"""
            const article = arguments[0];
            const isVisible = (el) => {
              if (!el) return false;
              const r = el.getBoundingClientRect();
              const st = getComputedStyle(el);
              return r.width > 0 && r.height > 0 && st.display !== 'none' &&
                     st.visibility !== 'hidden' && Number(st.opacity || 1) > 0;
            };
            const textOf = (el) => String(
              el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || ''
            ).replace(/\s+/g, ' ').trim();
            const bad = /지원|북마크|공유|팔로우|메뉴|홈으로|로그인/;
            const good = /상세\s*(정보|포지션)?\s*(더\s*)?보기|상세\s*보기|내용\s*더\s*보기/;
            const roots = [];
            if (article) {
              roots.push(article);
              if (article.parentElement) roots.push(article.parentElement);
              if (article.parentElement && article.parentElement.parentElement) {
                roots.push(article.parentElement.parentElement);
              }
            }
            roots.push(document);
            const seen = new Set();
            const candidates = [];
            for (const root of roots) {
              for (const el of root.querySelectorAll(
                "[wds-component='with-interaction'][role='presentation'], button, [role='button']"
              )) {
                if (!seen.has(el)) { seen.add(el); candidates.push(el); }
              }
            }
            const ar = article ? article.getBoundingClientRect() : null;
            let best = null;
            let bestScore = -Infinity;
            for (const raw of candidates) {
              const el = raw.closest('button,[role="button"]') || raw;
              if (!isVisible(el)) continue;
              const text = textOf(el);
              if (bad.test(text)) continue;
              const r = el.getBoundingClientRect();
              let score = 0;
              if (good.test(text)) score += 220;
              if (raw.getAttribute('wds-component') === 'with-interaction') score += 45;
              if (article && article.contains(raw)) score += 130;
              if (article && article.parentElement && article.parentElement.contains(raw)) score += 45;
              if (ar) {
                const overlapX = Math.max(0, Math.min(r.right, ar.right) - Math.max(r.left, ar.left));
                const overlapY = Math.max(0, Math.min(r.bottom, ar.bottom) - Math.max(r.top, ar.top));
                if (overlapX > 0 && overlapY > 0) score += 100;
                const bottomDistance = Math.min(Math.abs(r.top - ar.bottom), Math.abs(r.bottom - ar.bottom));
                if (bottomDistance < 220) score += 80 - bottomDistance / 4;
                const centerDistance = Math.abs((r.left + r.right) / 2 - (ar.left + ar.right) / 2);
                if (centerDistance < ar.width * 0.4) score += 30;
              }
              if (r.width >= 80) score += 10;
              if (score > bestScore) { bestScore = score; best = el; }
            }
            return bestScore >= 45 ? best : null;
            """,
            article,
        )
        if candidate is not None:
            return candidate
    except Exception:
        pass
    return None


def _force_click_expand_control(driver: webdriver.Chrome, control: WebElement) -> None:
    """일반 click이 막혀도 pointer/mouse/click 이벤트를 순서대로 보냅니다."""
    driver.execute_script(
        r"""
        const el = arguments[0];
        el.scrollIntoView({block:'center', inline:'nearest'});
        const opts = {bubbles:true, cancelable:true, view:window};
        for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {
          try {
            const Ctor = type.startsWith('pointer') ? PointerEvent : MouseEvent;
            el.dispatchEvent(new Ctor(type, opts));
          } catch (_) {
            el.dispatchEvent(new Event(type, {bubbles:true, cancelable:true}));
          }
        }
        """,
        control,
    )

def expand_job_description(driver: webdriver.Chrome, timeout: int = 15) -> str:
    """더보기 컨트롤이 있으면 핵심 섹션이 이미 보여도 반드시 클릭합니다.

    원티드는 접힌 상태에서도 주요업무·자격요건 일부를 먼저 렌더링하는 공고가
    있습니다. 따라서 ``주요업무가 보인다 = 전체 상세가 펼쳐졌다``고 판단하지
    않고, 더보기 컨트롤의 존재 여부를 먼저 확인합니다.
    """
    article = scroll_to_description_article(driver, timeout=min(timeout, 10))
    if article is None:
        return "article_not_found_before_expand"

    before = detail_article_state(driver)
    core_headings = {"주요업무", "담당업무", "자격요건", "지원자격", "필수요건"}
    before_headings = {normalize(str(v)) for v in (before.get("headings") or [])}
    before_text_length = int(before.get("text_length") or 0)
    before_heading_count = int(before.get("heading_count") or 0)

    # 핵심 섹션이 이미 보여도 더보기 컨트롤이 있으면 무조건 클릭합니다.
    control = find_description_expand_control(driver)
    if control is None:
        if before_headings & core_headings:
            return "already_expanded"
        return "expand_control_not_found"

    before_url = driver.current_url
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", control)
        time.sleep(0.25)
        try:
            control.click()
        except Exception:
            _force_click_expand_control(driver, control)
    except Exception as exc:
        return f"expand_click_failed:{type(exc).__name__}"

    def detail_changed_after_click(d: webdriver.Chrome) -> bool:
        try:
            state = detail_article_state(d)
            headings = {normalize(str(v)) for v in (state.get("headings") or [])}
            text_length = int(state.get("text_length") or 0)
            heading_count = int(state.get("heading_count") or 0)
            new_heading = bool(headings - before_headings)
            grew = text_length > before_text_length + 20 or heading_count > before_heading_count
            return bool(headings & core_headings) and (new_heading or grew)
        except Exception:
            return False

    changed = False
    try:
        WebDriverWait(driver, timeout).until(detail_changed_after_click)
        changed = True
    except TimeoutException:
        # 일반 click이 먹지 않은 경우 이벤트를 강제로 한 번 더 보냅니다.
        try:
            retry_control = find_description_expand_control(driver)
            if retry_control is not None:
                _force_click_expand_control(driver, retry_control)
                WebDriverWait(driver, 5).until(detail_changed_after_click)
                changed = True
        except Exception:
            pass

    if driver.current_url != before_url and "/wd/" not in driver.current_url:
        try:
            driver.back()
            scroll_to_description_article(driver, timeout=8)
        except Exception:
            pass
        return "expand_unexpected_navigation"

    after = detail_article_state(driver)
    after_headings = {normalize(str(v)) for v in (after.get("headings") or [])}
    if changed and after_headings & core_headings:
        return "expanded"
    if after_headings & core_headings:
        return "clicked_no_detected_change"
    return "clicked_but_sections_missing"

def extract_detail_payload(driver: webdriver.Chrome) -> dict[str, object]:
    """클릭 후 현재 DOM을 h3 제목 기준으로 추출합니다."""
    structured = extract_detail_payload_from_html(driver.page_source)

    metadata_script = r"""
    const cleanText = (value) => String(value || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\r/g, '')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n[ \t]+/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
    const normalizeHeading = (value) => String(value || '')
      .replace(/\s+/g, '').replace(/[:：]+$/, '').trim();
    const textOf = (el) => el ? cleanText(el.innerText || el.textContent || '') : '';

    const h1Elements = Array.from(document.querySelectorAll('h1'));
    const h1Texts = h1Elements.map(textOf).filter(Boolean);
    const companyEl = document.querySelector('[data-company-name]') ||
                      document.querySelector("a[href^='/company/']") ||
                      document.querySelector("a[class*='Company'][class*='Link']") ||
                      document.querySelector('h1.wds-1f8kxw2');
    const company = companyEl
      ? cleanText(companyEl.getAttribute('data-company-name') || textOf(companyEl))
      : '';
    const positionEl = h1Elements.find(el => el !== companyEl) || h1Elements[0] || null;

    const headingValue = (labels) => {
      const labelSet = new Set(labels.map(normalizeHeading));
      const heading = Array.from(document.querySelectorAll('h2,h3,dt'))
        .find(el => labelSet.has(normalizeHeading(textOf(el))));
      if (!heading) return '';
      let sibling = heading.nextElementSibling;
      while (sibling) {
        const value = textOf(sibling);
        if (value) return value;
        sibling = sibling.nextElementSibling;
      }
      return '';
    };

    let location = headingValue(['근무지역', '근무 지역']);
    if (/합격보상금|채용보상금|보상금/.test(location)) location = '';
    const deadline = headingValue(['마감일', '마감 기한', '마감기한']);

    let headerText = '';
    if (companyEl) {
      let node = companyEl.parentElement;
      for (let i = 0; i < 5 && node; i++, node = node.parentElement) {
        const value = textOf(node);
        if (value && value.length < 400 && (!company || value.includes(company))) {
          headerText = value;
        }
      }
    }
    return {
      company,
      position_title: positionEl ? textOf(positionEl) : '',
      h1_texts: h1Texts,
      deadline,
      location,
      header_text: headerText,
      body_text: textOf(document.body)
    };
    """
    metadata = driver.execute_script(metadata_script)
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.update(structured)
    return metadata

def first_section(sections: dict[str, str], names: Iterable[str]) -> str:
    def section_key(value: str) -> str:
        return re.sub(r"[\s:：]+", "", value or "").strip()

    normalized_map = {section_key(key): clean_multiline(value) for key, value in sections.items()}
    for name in names:
        target = section_key(name)
        if target in normalized_map:
            return normalized_map[target]
    for key, value in normalized_map.items():
        if any(section_key(name) in key for name in names):
            return value
    return ""


EMPLOYMENT_TERM_RE = re.compile(
    r"정규직|계약직|인턴|기간제|파견직|프리랜서|위촉직|전환형|정규직\s*전환|수습기간",
    re.IGNORECASE,
)


def sanitize_employment_value(value: str, *, explicit_section: bool = False) -> str:
    """고용조건 값을 정리합니다.

    ``h3`` 고용조건 블록에서 가져온 값은 제목 자체가 근거이므로, ``3개월``처럼
    고용 키워드가 없는 줄도 그대로 보존합니다. 페이지 전체 텍스트에서 찾은
    보조 값은 정규직·계약직·인턴 등의 키워드가 있는 줄만 허용합니다.
    값이 없으면 추정 문구를 만들지 않고 빈 문자열을 반환합니다.
    """
    cleaned = clean_multiline(value)
    if not cleaned:
        return ""

    selected: list[str] = []
    for raw_line in cleaned.split("\n"):
        line = re.sub(r"^[•∙·\-–—|]+\s*", "", raw_line).strip()
        if not line or re.fullmatch(r"[\[\](){}<>:：|\-–—]+", line):
            continue
        if re.search(r"고용\s*형태\s*안내|고용조건\s*안내|근무\s*형태\s*안내", line):
            continue
        if explicit_section or EMPLOYMENT_TERM_RE.search(line):
            selected.append(line)
        if len(selected) >= 5:
            break

    return "\n".join(selected)


def extract_employment_type(
    sections: dict[str, str],
    article_text: str,
    position_title: str,
) -> str:
    """명시적으로 표시된 고용조건만 저장합니다.

    제목이나 일반 본문에 '인턴', '계약직', '정규직'이라는 단어가 있어도
    고용조건으로 단정하지 않습니다. 별도 고용조건 섹션 또는
    '고용형태: ...'처럼 라벨이 붙은 문장이 없으면 '확인 필요'를 반환합니다.
    """
    del position_title  # 제목 기반 추정은 의도적으로 사용하지 않습니다.

    for heading in [
        "고용조건", "고용 조건", "고용 형태", "고용형태",
        "근무조건", "근무 조건", "근무 형태", "근무기간",
        "채용형태", "계약형태",
    ]:
        value = first_section(sections, [heading])
        if value:
            return sanitize_employment_value(value, explicit_section=True)

    match = EMPLOYMENT_LINE_RE.search(article_text or "")
    if match:
        return sanitize_employment_value(match.group(1), explicit_section=False)

    # 공고에 고용조건 섹션이 없는 경우는 정상입니다. 추정하지 않고 비워 둡니다.
    return ""


def parse_header_meta(header_text: str, company: str) -> tuple[str, str]:
    tokens = [normalize(token) for token in re.split(r"[∙·|\n]", header_text or "") if normalize(token)]
    tokens = [token for token in tokens if token != company and token not in {"팔로우"}]
    career = next((t for t in tokens if re.search(r"신입|경력|년 이상|년 이하|무관", t)), "")
    location = next(
        (
            t
            for t in tokens
            if re.search(r"서울|경기|인천|부산|대구|대전|광주|세종|울산|강원|충북|충남|전북|전남|경북|경남|제주", t)
        ),
        "",
    )
    return location, career


def enrich_job_detail(
    driver: webdriver.Chrome,
    job: WantedJob,
    debug_dir: Path,
    detail_delay: float,
    save_each_detail: bool,
) -> None:
    try:
        driver.get(job.link)
        WebDriverWait(driver, 20).until(
            lambda d: bool(d.find_elements(By.TAG_NAME, "h1"))
        )
        time.sleep(detail_delay)

        # 요청한 순서대로 '상세 포지션 보기'를 먼저 클릭한 뒤 JobDescription을 수집합니다.
        expand_status = expand_job_description(driver)
        print(f"    - 상세 펼치기: {expand_status}")
        time.sleep(max(0.4, detail_delay))

        try:
            WebDriverWait(driver, 12).until(
                lambda d: bool(
                    d.find_elements(By.CSS_SELECTOR, "article[class*='JobDescription']")
                )
            )
        except TimeoutException:
            # 추출 함수가 article_found=False로 명확하게 반환하도록 계속 진행합니다.
            pass

        stable_state = stabilize_wanted_detail_article(
            driver, timeout=max(7.0, detail_delay + 5.0)
        )
        stable_headings = [
            normalize(str(value)) for value in (stable_state.get("headings") or []) if normalize(str(value))
        ]
        if stable_headings:
            print(f"    - 상세 섹션: {', '.join(stable_headings)}")

        payload = extract_detail_payload(driver)
        # 첫 시도에서 article 또는 핵심 섹션이 없으면 스크롤·더보기 클릭을 한 번 더
        # 수행합니다. 원티드는 상세 영역을 지연 렌더링하는 공고가 있습니다.
        first_sections = payload.get("sections") or {}
        if not payload.get("article_found") or not first_sections:
            retry_status = expand_job_description(driver, timeout=8)
            print(f"    - 상세 재시도: {retry_status}")
            time.sleep(max(0.5, detail_delay))
            stable_state = stabilize_wanted_detail_article(driver, timeout=8.0)
            payload = extract_detail_payload(driver)

        sections = payload.get("sections") or {}
        if not isinstance(sections, dict):
            sections = {}
        sections = {str(k): str(v) for k, v in sections.items()}

        # 화면의 h3에는 우대사항/고용조건이 있는데 파싱 결과가 비어 있으면
        # 해당 섹션까지 다시 스크롤한 뒤 최종 DOM을 한 번 더 읽습니다.
        normalized_heading_set = {normalize_section_heading(value) for value in stable_headings}
        preferred_heading_present = any(
            WANTED_SECTION_ALIASES.get(value) == "preferred"
            for value in normalized_heading_set
        )
        employment_heading_present = any(
            WANTED_SECTION_ALIASES.get(value) == "employment"
            for value in normalized_heading_set
        )
        preferred_value_now = first_section(
            sections,
            [
                "우대사항", "우대 사항", "우대요건", "우대 요건",
                "우대자격", "우대 자격", "우대조건", "우대 조건",
                "선호사항", "선호 사항", "선호요건", "선호 요건",
                "Preferred Qualifications", "Preferred Requirements",
            ],
        )
        employment_value_now = first_section(
            sections,
            [
                "고용조건", "고용 조건", "고용형태", "고용 형태",
                "근무조건", "근무 조건", "근무형태", "근무 형태",
                "근무기간", "채용형태", "계약형태",
            ],
        )
        if (preferred_heading_present and not preferred_value_now) or (
            employment_heading_present and not employment_value_now
        ):
            print("    - 하단 섹션 재수집: 우대사항/고용조건 확인")
            stabilize_wanted_detail_article(driver, timeout=8.0)
            payload = extract_detail_payload(driver)
            retried_sections = payload.get("sections") or {}
            if isinstance(retried_sections, dict):
                sections = {str(k): str(v) for k, v in retried_sections.items()}

        payload_company = normalize(str(payload.get("company") or ""))
        payload_position = normalize(str(payload.get("position_title") or ""))
        h1_texts_raw = payload.get("h1_texts") or []
        h1_texts = []
        if isinstance(h1_texts_raw, list):
            for value in h1_texts_raw:
                text = normalize(str(value or ""))
                if text and text not in h1_texts:
                    h1_texts.append(text)

        invalid_company_values = {"", "회사명 확인 필요", "확인 필요"}
        known_position = normalize(job.position_title)
        known_company = normalize(job.company)

        # 목록에서 알고 있던 포지션명과 가장 잘 맞는 h1을 우선 선택합니다.
        position_title = payload_position
        if known_position and known_position not in invalid_company_values:
            matched_position = next(
                (text for text in h1_texts if text == known_position),
                None,
            )
            if matched_position:
                position_title = matched_position
        if not position_title or position_title == payload_company:
            position_title = next(
                (text for text in h1_texts if text != payload_company),
                known_position or payload_position,
            )

        company = payload_company
        if company in invalid_company_values or company == position_title:
            # 신규 UI처럼 회사명이 별도 h1인 경우, 포지션명을 제외한 h1을 회사명으로 사용합니다.
            company_candidate = next(
                (text for text in h1_texts if text != position_title),
                "",
            )
            if company_candidate:
                company = company_candidate
            elif known_company not in invalid_company_values:
                company = known_company

        article_text = clean_multiline(str(payload.get("article_text") or ""))
        header_text = clean_multiline(str(payload.get("header_text") or ""))
        header_location, career = parse_header_meta(header_text, company)

        job.company = company or job.company
        job.position_title = position_title or job.position_title
        job.title = f"[{job.company}] {job.position_title}".strip()
        job.employment_type = extract_employment_type(sections, article_text, job.position_title)
        job.deadline = clean_multiline(str(payload.get("deadline") or "")) or "확인 필요"
        job.requirements = first_section(
            sections,
            ["자격요건", "필요요건", "지원자격", "필수요건", "필요 요건"],
        )
        job.preferred_requirements = first_section(
            sections,
            [
                "우대사항", "우대 사항", "우대요건", "우대 요건",
                "우대자격", "우대 자격", "우대조건", "우대 조건",
                "선호사항", "선호 사항", "선호요건", "선호 요건",
                "Preferred Qualifications", "Preferred Requirements",
            ],
        )
        job.main_tasks = first_section(sections, ["주요업무", "담당업무", "주요 업무"])
        job.benefits = first_section(sections, ["혜택 및 복지", "복지", "혜택"])
        job.hiring_process = first_section(sections, ["채용 전형", "채용절차", "전형절차", "채용 프로세스"])
        job.position_detail = clean_multiline(str(payload.get("position_detail") or ""))
        location_value = clean_multiline(str(payload.get("location") or ""))
        if re.search(r"합격보상금|채용보상금|보상금", location_value):
            location_value = ""
        job.location = location_value or header_location or job.location
        job.career = career

        article_found = bool(payload.get("article_found"))
        required_fields = [article_found, job.company, job.position_title, job.main_tasks, job.requirements]
        missing_expected_sections: list[str] = []
        if preferred_heading_present and not job.preferred_requirements:
            missing_expected_sections.append("우대사항")
        if employment_heading_present and not job.employment_type:
            missing_expected_sections.append("고용조건")
        job.detail_status = "ok" if all(required_fields) and not missing_expected_sections else "partial"
        if missing_expected_sections:
            print(f"    - 섹션 내용 누락 경고: {', '.join(missing_expected_sections)}")
        if save_each_detail:
            save_wanted_debug(driver, debug_dir, f"detail_{job.wanted_id}")
    except Exception as exc:
        job.detail_status = f"failed: {type(exc).__name__}: {exc}"
        save_wanted_debug(driver, debug_dir, f"detail_failed_{job.wanted_id}")


def enrich_all_details(
    driver: webdriver.Chrome,
    jobs: list[WantedJob],
    debug_dir: Path,
    detail_delay: float,
    save_each_detail: bool,
    output_dir: Path,
    base_report: dict,
) -> None:
    """상세 페이지를 순차 방문하고, 공고 1건 완료 때마다 결과를 자동 저장합니다."""
    print(f"\n[상세 페이지 직접 방문: {len(jobs)}건]")
    completed_jobs: list[WantedJob] = []

    try:
        for idx, job in enumerate(jobs, start=1):
            print(f"  [{idx}/{len(jobs)}] {job.link}")
            enrich_job_detail(driver, job, debug_dir, detail_delay, save_each_detail)
            completed_jobs.append(job)
            print(f"    - {job.detail_status}: {job.title}")

            progress_report = dict(base_report)
            progress_report.update(
                {
                    "run_status": "running",
                    "planned_detail_count": len(jobs),
                    "completed_detail_count": len(completed_jobs),
                    "detail_ok_count": sum(item.detail_status == "ok" for item in completed_jobs),
                    "detail_partial_count": sum(item.detail_status == "partial" for item in completed_jobs),
                    "detail_failed_count": sum(item.detail_status.startswith("failed") for item in completed_jobs),
                    "last_autosaved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            write_outputs(completed_jobs, progress_report, output_dir)
            print(f"    - 자동 저장 완료: {len(completed_jobs)}건")
    except KeyboardInterrupt:
        # 마지막으로 완료된 공고까지는 위에서 이미 저장되어 있습니다.
        interrupted_report = dict(base_report)
        interrupted_report.update(
            {
                "run_status": "interrupted",
                "planned_detail_count": len(jobs),
                "completed_detail_count": len(completed_jobs),
                "detail_ok_count": sum(item.detail_status == "ok" for item in completed_jobs),
                "detail_partial_count": sum(item.detail_status == "partial" for item in completed_jobs),
                "detail_failed_count": sum(item.detail_status.startswith("failed") for item in completed_jobs),
                "interrupted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        if completed_jobs:
            write_outputs(completed_jobs, interrupted_report, output_dir)
        print(f"\n중간 종료: 완료된 {len(completed_jobs)}건까지 저장했습니다.")
        raise


def crawl_ui(
    driver: webdriver.Chrome,
    limit: int,
    max_scrolls: int,
    debug_dir: Path,
) -> tuple[list[WantedJob], dict]:
    print("\n[UI 통합 선택 테스트]")
    driver.get(BASE_URL)
    time.sleep(4.0)
    save_wanted_debug(driver, debug_dir, "01_base_page")

    filter_ok, selected = apply_five_role_filter(driver)
    save_wanted_debug(driver, debug_dir, "02_after_ui_filter")
    try:
        wait_for_job_list(driver, timeout=20)
    except TimeoutException:
        pass
    scroll_until_stable(driver, max_scrolls=max_scrolls)
    jobs = collect_current_page(driver, "기획", "5개 직무 통합", "ui", limit)
    save_wanted_debug(driver, debug_dir, "03_after_ui_scroll")
    return jobs, {
        "strategy": "ui",
        "filter_ok": filter_ok,
        "selected_roles": selected,
        "final_url": driver.current_url,
        "job_count_before_detail": len(jobs),
    }


def crawl_direct(
    driver: webdriver.Chrome,
    limit: int,
    max_scrolls: int,
    debug_dir: Path,
) -> tuple[list[WantedJob], dict]:
    print("\n[직군·직무별 공식 URL 목록 수집]")
    sources = direct_sources()
    source_results: list[tuple[dict[str, str], list[WantedJob]]] = []
    per_source: dict[str, int] = {}
    per_source_limit = max(1, (limit + len(sources) - 1) // len(sources)) if limit else 0

    for idx, source in enumerate(sources, start=1):
        category = source["category"]
        raw_category = source["raw_category"]
        item_id = source["item_id"]
        url = source["url"]
        label = f"{category} > {raw_category}"
        print(f"\n  [{idx}/{len(sources)}] {label}: {url}")
        driver.get(url)
        time.sleep(3.2)
        try:
            wait_for_job_list(driver, timeout=18)
        except TimeoutException:
            print("    ⚠️ 공고 링크 대기 시간 초과")
        scroll_until_stable(driver, max_scrolls=max_scrolls)
        jobs = collect_current_page(
            driver, category, raw_category, "direct", per_source_limit
        )
        per_source[label] = len(jobs)
        source_results.append((source, jobs))
        save_wanted_debug(driver, debug_dir, f"direct_{idx}_{item_id}")

    # 먼저 같은 담당자용 직군 안에서 세부 직무 결과를 합친 뒤,
    # 최종 제한을 적용할 때 기획만 앞에서 많이 차지하지 않도록 직군별 라운드로빈으로 섞습니다.
    category_buckets: dict[str, list[WantedJob]] = {}
    for source, source_jobs in source_results:
        category_buckets.setdefault(source["category"], []).extend(source_jobs)

    ordered_jobs: list[WantedJob] = []
    max_category_length = max((len(items) for items in category_buckets.values()), default=0)
    for row_index in range(max_category_length):
        for category in TARGET_CATEGORIES:
            items = category_buckets.get(category, [])
            if row_index < len(items):
                ordered_jobs.append(items[row_index])

    merged: dict[str, WantedJob] = {}
    for job in ordered_jobs:
        existing = merged.get(job.link)
        if existing is None:
            merged[job.link] = job
            continue
        # 통합 화면의 category는 하나의 담당자용 직군만 유지합니다.
        # 같은 공고가 다른 원티드 직군에도 노출되면 최초 직군을 대표값으로 두고,
        # 실제 발견 직무만 raw_category(matched_role)에 합칩니다.
        existing_roles = existing.matched_role.split(" | ")
        if job.matched_role not in existing_roles:
            existing.matched_role += f" | {job.matched_role}"

    jobs = list(merged.values())
    if limit:
        jobs = jobs[:limit]
    selected = {f"{s['category']} > {s['raw_category']}": True for s in sources}
    return jobs, {
        "strategy": "direct",
        "filter_ok": True,
        "selected_roles": selected,
        "per_source_raw_count": per_source,
        "job_count_before_detail": len(jobs),
    }


def now_kst_text() -> str:
    return datetime.now(WANTED_KST).strftime("%Y-%m-%d %H:%M:%S")


def wanted_fingerprint(job: WantedJob) -> str:
    key = (job.link or f"원티드|{job.company}|{job.position_title}").strip()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def build_description_text(job: WantedJob) -> str:
    """실제 상세 본문이 확인된 경우에만 JSON용 원문을 만듭니다."""
    sections = [
        ("포지션 상세", job.position_detail),
        ("주요업무", job.main_tasks),
        ("필요 요건", job.requirements),
        ("고용조건", job.employment_type if job.employment_type != "확인 필요" else ""),
        ("우대 요건", job.preferred_requirements),
        ("혜택 및 복지", job.benefits),
        ("채용 전형", job.hiring_process),
    ]
    has_real_body = any(
        clean_multiline(value)
        for value in [
            job.position_detail,
            job.main_tasks,
            job.requirements,
            job.preferred_requirements,
        ]
    )
    if not has_real_body:
        return ""

    parts: list[str] = []
    for label, value in sections:
        cleaned = clean_multiline(value)
        if cleaned:
            parts.append(f"[{label}]\n{cleaned}")
    if job.location:
        parts.append(f"[근무지역]\n{clean_multiline(job.location)}")
    if job.career:
        parts.append(f"[경력]\n{clean_multiline(job.career)}")
    return "\n\n".join(parts)

# 통합 크롤러에서 사용하던 이름을 그대로 유지합니다.
WANTED_TARGET_CATEGORIES = TARGET_CATEGORIES
build_wanted_description_text = build_description_text
crawl_wanted_direct = crawl_direct
enrich_wanted_job_detail = enrich_job_detail

# ---------------------------------------------------------------------------
# 링커리어·원티드 통합 애플리케이션
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

try:
    from webdriver_manager.chrome import ChromeDriverManager
    USE_WDM = True
except Exception:
    USE_WDM = False

KST = ZoneInfo("Asia/Seoul") if ZoneInfo else None
BASE_DIR = Path(__file__).resolve().parent
SITE_DIR = BASE_DIR / "site"
DATA_DIR = BASE_DIR / "data"
DEBUG_DIR = BASE_DIR / "debug"
STATE_PATH = DATA_DIR / "seen_jobs.json"

CATEGORY_ORDER = ["마케팅", "기획", "인사", "영업", "개발"]
CATEGORY_ICONS = {"마케팅": "📢", "기획": "📐", "인사": "🤝", "영업": "💼", "개발": "🤖"}
CATEGORY_COLORS = {"마케팅": "#F472B6", "기획": "#A78BFA", "인사": "#34D399", "영업": "#FBBF24", "개발": "#60A5FA"}
PLATFORM_COLORS = {"원티드": "#3366FF", "링커리어": "#00C473"}

# 사용자가 확정한 매핑.
# click_sequence는 Selenium이 순서대로 클릭을 시도하는 텍스트입니다.
# 실제 사이트 UI가 바뀌거나 자동 클릭이 실패하면 --manual 옵션으로 직접 필터를 눌러서 테스트할 수 있습니다.
FILTER_CONFIG = {
    "링커리어": {
        # 채용 홈에서 채용형태를 먼저 선택한 뒤 직무를 선택합니다.
        # 사용자가 확인한 UI 순서: 채용 > 채용형태 > 직무
        "base_url": "https://linkareer.com/list/recruit",
        "link_patterns": [r"/activity/\d+", r"/recruit/\d+", r"/list/recruit/\d+"],
        "career_open_buttons": ["채용형태"],
        "career_click_sequence": ["신입", "인턴", "계약직"],
        "career_apply_buttons": ["적용", "확인", "완료"],
        "categories": {
            "기획": {
                "open_buttons": ["직무"],
                # 사용자가 확정한 기준: 기획/경영 하위의 3개 세부 직무를 선택합니다.
                # 경영분석/컨설팅은 제외합니다.
                "click_sequence": [
                    "기획/경영",
                    "경영기획/전략",
                    "사업기획/신규사업",
                    "서비스기획/운영",
                ],
                "apply_buttons": ["적용", "확인", "완료"],
                "manual_hint": "채용형태 > 신입/인턴/계약직 선택 후, 직무 > 기획/경영 > 경영기획/전략, 사업기획/신규사업, 서비스기획/운영 선택",
            },
            "마케팅": {
                "open_buttons": ["직무"],
                "click_sequence": ["마케팅/광고"],
                "apply_buttons": ["적용", "확인", "완료"],
                "manual_hint": "채용형태 > 신입/인턴/계약직 선택 후, 직무 > 마케팅/광고 선택",
            },
            "인사": {
                "open_buttons": ["직무"],
                "click_sequence": ["기획/경영", "인사/채용/노무"],
                "apply_buttons": ["적용", "확인", "완료"],
                "manual_hint": "채용형태 > 신입/인턴/계약직 선택 후, 직무 > 기획/경영 > 인사/채용/노무 선택",
            },
            "영업": {
                "open_buttons": ["직무"],
                "click_sequence": ["영업/CS"],
                "apply_buttons": ["적용", "확인", "완료"],
                "manual_hint": "채용형태 > 신입/인턴/계약직 선택 후, 직무 > 영업/CS 선택",
            },
            "개발": {
                "open_buttons": ["직무"],
                "click_sequence": ["IT/개발"],
                "apply_buttons": ["적용", "확인", "완료"],
                "manual_hint": "채용형태 > 신입/인턴/계약직 선택 후, 직무 > IT/개발 선택",
            },
        },
    },
    "원티드": {
        # years=0: 신입 필터를 URL에 우선 적용. UI 클릭도 추가로 시도합니다.
        "base_url": "https://www.wanted.co.kr/wdlist?country=kr&job_sort=job.latest_order&years=0&locations=all",
        "link_patterns": [r"/wd/\d+"],
        "career_open_buttons": ["경력", "신입~3년", "신입", "채용조건"],
        "career_click_sequence": ["신입"],
        "categories": {
            "기획": {
                "open_buttons": ["직군·직무", "직군・직무", "직군/직무", "직무"],
                "click_sequence": ["경영·비즈니스", "사업개발·기획자", "PM·PO", "서비스 기획자", "전략 기획자", "상품기획자(BM)"],
                "apply_buttons": ["적용"],
                "manual_hint": "직군·직무 > 경영·비즈니스 > 사업개발·기획자, PM·PO, 서비스 기획자, 전략 기획자, 상품기획자(BM) 선택 / 경력 신입",
            },
            "마케팅": {
                "open_buttons": ["직군·직무", "직군・직무", "직군/직무", "직무"],
                "click_sequence": ["마케팅·광고", "직군 전체", "전체"],
                "apply_buttons": ["적용"],
                "manual_hint": "직군·직무 > 마케팅·광고 전체 선택 / 경력 신입",
            },
            "인사": {
                "open_buttons": ["직군·직무", "직군・직무", "직군/직무", "직무"],
                "click_sequence": ["HR", "인사담당", "평가·보상", "HRD", "급여담당", "HRBP", "조직문화", "노무·노사"],
                "apply_buttons": ["적용"],
                "manual_hint": "직군·직무 > HR > 인사담당/평가·보상/HRD/급여담당/HRBP/조직문화/노무·노사 선택 / 경력 신입",
            },
            "영업": {
                "open_buttons": ["직군·직무", "직군・직무", "직군/직무", "직무"],
                "click_sequence": ["영업", "직군 전체", "전체"],
                "apply_buttons": ["적용"],
                "manual_hint": "직군·직무 > 영업 전체 선택 / 경력 신입",
            },
            "개발": {
                "open_buttons": ["직군·직무", "직군・직무", "직군/직무", "직무"],
                "click_sequence": ["개발", "직군 전체", "전체"],
                "apply_buttons": ["적용"],
                "manual_hint": "직군·직무 > 개발 전체 선택 / 경력 신입",
            },
        },
    },
}

NOISE_WORDS = {
    "북마크", "공유", "지원하기", "자세히 보기", "공고 보기", "더보기", "마감", "상세", "인턴", "신입",
    "채용", "직무", "기업", "지역", "산업", "태그 전체", "한국", "채용조건", "기술스택", "적극 채용 중인 회사",
}

DEADLINE_RE = re.compile(r"(D-\d+|D\s*-\s*\d+|오늘마감|내일마감|상시|상시채용|채용시|마감|~\s*\d{1,2}[./]\d{1,2}|\d{4}[./-]\d{1,2}[./-]\d{1,2})")

@dataclass
class Job:
    title: str
    company: str
    platform: str
    category: str
    deadline: str
    link: str
    detail_url: str = ""
    apply_url: str = ""
    start_date: str = ""
    job_type: str = ""
    raw_category: str = ""
    description_type: str = ""  # text / image / empty
    description_text: str = ""  # 텍스트형 상세 공고 본문. 이미지 공고는 비워둠
    is_new: bool = False
    first_seen_at: str = ""

    @property
    def fingerprint(self) -> str:
        # 링크가 가장 안정적. 링크가 없을 경우 제목+회사+플랫폼으로 대체.
        key = self.detail_url.strip() or self.link.strip() or f"{self.platform}|{self.company}|{self.title}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def now_kst() -> datetime:
    return datetime.now(KST) if KST else datetime.now()


def ensure_dirs() -> None:
    SITE_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    DEBUG_DIR.mkdir(exist_ok=True)


def create_driver(headless: bool) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1440,1100")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=ko-KR")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if USE_WDM:
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
        except Exception as e:
            print(f"  ⚠️ ChromeDriver 자동 다운로드 실패, Selenium 기본 실행으로 재시도: {e}")
            driver = webdriver.Chrome(options=options)
    else:
        driver = webdriver.Chrome(options=options)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
    except Exception:
        pass
    driver.set_page_load_timeout(45)
    return driver


def safe_filename(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", text).strip("_")[:80]


def save_debug(driver, platform: str, category: str, suffix: str = "") -> None:
    name = safe_filename(f"{platform}_{category}_{suffix}" if suffix else f"{platform}_{category}")
    try:
        (DEBUG_DIR / f"{name}.html").write_text(driver.page_source, encoding="utf-8")
    except Exception:
        pass
    try:
        driver.save_screenshot(str(DEBUG_DIR / f"{name}.png"))
    except Exception:
        pass


def scroll_page(driver, times: int = 2, delay: float = 1.0) -> None:
    last_height = 0
    for _ in range(times):
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(delay)
            height = driver.execute_script("return document.body.scrollHeight")
            if height == last_height:
                break
            last_height = height
        except Exception:
            break


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def parse_yyyy_mm_dd(text: str) -> Optional[date]:
    """2026.07.06 / 2026-07-06 / 2026/07/06 형태를 date로 변환합니다."""
    if not text:
        return None
    m = re.search(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})", text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def detail_text_after_label(dd, label: str) -> str:
    """링커리어 접수기간 dd 내부의 span 구조에서 시작일/마감일 값을 가져옵니다."""
    spans = [normalize_text(x.get_text(" ", strip=True)) for x in dd.find_all("span")]
    for i, txt in enumerate(spans):
        if txt == label and i + 1 < len(spans):
            return spans[i + 1]
    full = normalize_text(dd.get_text(" ", strip=True))
    if label == "시작일":
        m = re.search(r"시작일\s*(20\d{2}[./-]\d{1,2}[./-]\d{1,2})", full)
        return m.group(1) if m else ""
    if label == "마감일":
        m = re.search(r"마감일\s*(.+)$", full)
        return m.group(1).strip() if m else ""
    return ""


def extract_company_from_title(title: str) -> str:
    """[회사명] [형태] 제목 형식에서 회사명을 임시 추출합니다."""
    m = re.match(r"^\[([^\]]+)\]", title or "")
    if m:
        return m.group(1).strip()
    return "회사명 확인 필요"


def linkareer_allowed_raw_categories(category: str) -> Optional[list[str]]:
    """링커리어 리스트의 recruit-category 기준 허용 직무 키워드.

    주의: 링커리어 리스트는 `경영기획/전략, 사업기획/신규사업 외 1`처럼
    여러 직무를 한 문자열로 줄여 보여주는 경우가 많습니다.
    따라서 정확히 같은 문자열인지 비교하지 않고, 포함 여부로 판단합니다.
    """
    mapping = {
        # 사용자가 확정한 기준: 기획은 아래 3개 세부직무만 통과
        # - 경영기획/전략
        # - 사업기획/신규사업
        # - 서비스기획/운영
        # 경영분석/컨설팅은 제외
        "기획": ["경영기획/전략", "사업기획/신규사업", "서비스기획/운영"],
        "마케팅": ["마케팅/광고"],
        # 사용자가 확정한 기준: 인사 / 노무 / HRD / HR / HRBP / 총무만 통과
        # 주의: 단순 "채용"은 신입채용/공개채용 등 오탐이 많아서 제외
        "인사": ["인사", "노무", "HRD", "HR", "HRBP", "총무"],
        # 사용자가 확정한 기준: 영업 또는 CS 중 하나라도 들어가면 통과
        "영업": ["영업", "CS"],
        # 사용자가 확정한 기준: 아래 키워드 중 하나라도 들어가면 통과
        # IT / 개발 / 데이터 분석 / 보안 / 클라우드 / DevOps / AI / 백엔드 / 프론트엔드 / 서버
        "개발": [
            "IT",
            "개발",
            "데이터 분석",
            "데이터분석",
            "보안",
            "클라우드",
            "DevOps",
            "AI",
            "백엔드",
            "프론트엔드",
            "서버",
        ],
    }
    return mapping.get(category)


def linkareer_raw_category_matches(category: str, raw_category: str) -> bool:
    """링커리어 리스트 직무 문자열이 요청 직군에 맞는지 판단합니다."""
    raw = normalize_text(raw_category or "")
    allowed = linkareer_allowed_raw_categories(category)
    if not allowed:
        return True
    if not raw:
        # 리스트에서 직무가 안 보이면 상세에서 확인할 수 있도록 우선 통과
        return True
    return any(keyword in raw for keyword in allowed)


def linkareer_list_text_matches(category: str, raw_category: str, title: str) -> bool:
    """링커리어 리스트 후보 통과 여부를 판단합니다.

    마케팅은 링커리어 리스트에서 실제 마케팅 공고인데도
    `서비스기획/운영`, `기획/경영`처럼 보이는 경우가 있어
    raw_category뿐 아니라 제목 키워드도 함께 봅니다.
    """
    raw = normalize_text(raw_category or "")
    title = normalize_text(title or "")
    text = f"{raw} {title}"

    if category == "마케팅":
        # 사용자가 확정한 기준: 리스트 직무/공고명 중 "마케팅"이라는 단어가 들어가면 통과
        return "마케팅" in text

    if category == "인사":
        # 사용자가 확정한 인사 기준
        # 인사 / 노무 / HRD / HR / HRBP / 총무 중 하나라도 있으면 통과
        # 단순 "채용"은 신입채용/공개채용 등 오탐이 많아서 제외
        upper_raw = raw.upper()
        upper_title = title.upper()

        raw_match = (
            "인사" in raw
            or "노무" in raw
            or "총무" in raw
            or "HRD" in upper_raw
            or "HRBP" in upper_raw
            or re.search(r"(?<![A-Z])HR(?![A-Z])", upper_raw) is not None
        )
        title_match = (
            "인사" in title
            or "노무" in title
            or "총무" in title
            or "HRD" in upper_title
            or "HRBP" in upper_title
            or re.search(r"(?<![A-Z])HR(?![A-Z])", upper_title) is not None
        )
        return raw_match or title_match

    if category == "영업":
        # 사용자가 확정한 기준: 리스트 직무/공고명 중 "영업" 또는 "CS" 중 하나라도 들어가면 통과
        return ("영업" in text) or ("CS" in text.upper())

    if category == "개발":
        # 사용자가 확정한 기준: 리스트 직무/공고명 중 아래 키워드 중 하나라도 들어가면 통과
        # IT / 개발 / 데이터 분석 / 보안 / 클라우드 / DevOps / AI / 백엔드 / 프론트엔드 / 서버
        upper_text = text.upper()
        return (
            "IT" in upper_text
            or "개발" in text
            or "데이터 분석" in text
            or "데이터분석" in text
            or "보안" in text
            or "클라우드" in text
            or "DEVOPS" in upper_text
            or "AI" in upper_text
            or "백엔드" in text
            or "프론트엔드" in text
            or "서버" in text
        )

    return linkareer_raw_category_matches(category, raw)


def xpath_literal(s: str) -> str:
    # XPath 문자열 escape
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    return "concat(" + ", \"'\", ".join([f"'{p}'" for p in s.split("'")]) + ")"


def clickable_xpaths(label: str, exact: bool = True) -> list[str]:
    lit = xpath_literal(label)
    if exact:
        text_expr = f"normalize-space(.)={lit}"
    else:
        text_expr = f"contains(normalize-space(.), {lit})"
    return [
        f"//button[{text_expr}]",
        f"//*[@role='button' and {text_expr}]",
        f"//label[{text_expr}]",
        f"//span[{text_expr}]/ancestor::*[self::button or @role='button' or self::label][1]",
        f"//*[self::div or self::span or self::p or self::a][{text_expr}]",
    ]


def click_label(driver, label: str, timeout: float = 3.5, allow_contains: bool = False) -> bool:
    wait = WebDriverWait(driver, timeout)
    for exact in ([True, False] if allow_contains else [True]):
        for xp in clickable_xpaths(label, exact=exact):
            try:
                el = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
                driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
                time.sleep(0.15)
                try:
                    wait.until(EC.element_to_be_clickable((By.XPATH, xp)))
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                time.sleep(0.7)
                return True
            except Exception:
                continue
    return False


def click_first_available(driver, labels: Iterable[str], timeout: float = 2.5) -> Optional[str]:
    for label in labels:
        if click_label(driver, label, timeout=timeout, allow_contains=False):
            return label
    for label in labels:
        if click_label(driver, label, timeout=timeout, allow_contains=True):
            return label
    return None



def find_linkareer_tab(driver, tab_name: str):
    """클래스명이 바뀌어도 화면의 탭 텍스트로 찾습니다."""
    literal = xpath_literal(tab_name)
    xpaths = [
        f"//button[contains(@class,'tab-button')][.//span[contains(@class,'tab-label') and normalize-space()={literal}]]",
        f"//button[normalize-space(.)={literal}]",
        f"//*[@role='button' and normalize-space(.)={literal}]",
        f"//*[self::span or self::div][normalize-space(.)={literal}]/ancestor::*[self::button or @role='button'][1]",
    ]
    last_error = None
    for xp in xpaths:
        try:
            return WebDriverWait(driver, 2.5).until(
                EC.presence_of_element_located((By.XPATH, xp))
            )
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise TimeoutException(f"링커리어 탭을 찾지 못함: {tab_name}")


def click_linkareer_tab(driver, tab_name: str) -> bool:
    try:
        el = find_linkareer_tab(driver, tab_name)
        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
        time.sleep(0.2)
        driver.execute_script("arguments[0].click();", el)
        time.sleep(0.7)
        print(f"  - 링커리어 탭 열기: {tab_name}")
        return True
    except Exception as e:
        print(f"  ⚠️ 링커리어 탭 열기 실패: {tab_name} / {e}")
        return False


def find_linkareer_chip(driver, chip_name: str):
    """필터 칩 클래스가 바뀌어도 정확한 표시 텍스트로 찾습니다."""
    literal = xpath_literal(chip_name)
    xpaths = [
        f"//button[contains(@class,'filter-chip') and normalize-space(.)={literal}]",
        f"//button[normalize-space(.)={literal}]",
        f"//*[@role='button' and normalize-space(.)={literal}]",
        f"//label[normalize-space(.)={literal}]",
        f"//*[self::span or self::div][normalize-space(.)={literal}]/ancestor::*[self::button or @role='button' or self::label][1]",
    ]
    last_error = None
    for xp in xpaths:
        try:
            return WebDriverWait(driver, 2.5).until(
                EC.presence_of_element_located((By.XPATH, xp))
            )
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise TimeoutException(f"링커리어 필터 칩을 찾지 못함: {chip_name}")


def is_selected_chip(el) -> bool:
    try:
        cls = (el.get_attribute("class") or "").lower()
        aria_pressed = (el.get_attribute("aria-pressed") or "").lower()
        aria_checked = (el.get_attribute("aria-checked") or "").lower()
        data_selected = (el.get_attribute("data-selected") or "").lower()
        return (
            "selected" in cls
            or "active" in cls
            or aria_pressed == "true"
            or aria_checked == "true"
            or data_selected == "true"
        )
    except Exception:
        return False


def clear_linkareer_selected_chips_on_open_tab(driver, keep: Optional[set[str]] = None, max_clicks: int = 40) -> int:
    """현재 열려 있는 링커리어 필터 탭에서 선택된 칩을 해제합니다.

    직군을 연속 수집할 때 이전 직군의 선택값이 남아 있으면
    URL에 categoryIDs가 누적되어 엉뚱한 공고가 뜰 수 있어,
    각 직군 수집 시작 전에 선택된 칩을 모두 지웁니다.
    """
    keep = keep or set()
    cleared = 0
    for _ in range(max_clicks):
        selected_buttons = []
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, "button.filter-chip")
        except Exception:
            break
        for btn in buttons:
            try:
                label = normalize_text(btn.text)
                if not label or label in keep:
                    continue
                if is_selected_chip(btn):
                    selected_buttons.append((label, btn))
            except Exception:
                continue
        if not selected_buttons:
            break

        label, btn = selected_buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", btn)
            time.sleep(0.15)
            driver.execute_script("arguments[0].click();", btn)
            cleared += 1
            print(f"    · 기존 선택 해제: {label}")
            time.sleep(0.35)
        except Exception:
            break
    return cleared


def reset_linkareer_filters(driver) -> None:
    """링커리어 직군별 수집 전 필터를 완전히 초기화합니다.

    순서:
    1. 완전 기본 URL 접속
    2. 기존 선택 필터 초기화
    3. 이후 apply_linkareer_filters에서 채용형태/직무를 다시 선택
    """
    base_url = FILTER_CONFIG["링커리어"]["base_url"]
    print("  - 링커리어 필터 초기화: 기본 URL 재접속")
    driver.get(base_url)
    time.sleep(2.5)

    for tab_name in ["채용형태", "직무"]:
        if click_linkareer_tab(driver, tab_name):
            cleared = clear_linkareer_selected_chips_on_open_tab(driver)
            if cleared:
                time.sleep(0.7)

    # 선택 해제 후 쿼리스트링/SPA 상태를 한 번 더 기본 URL로 정리합니다.
    driver.get(base_url)
    time.sleep(2.0)


def ensure_linkareer_chip_selected(driver, chip_name: str) -> bool:
    """링커리어 칩은 이미 선택된 상태에서 다시 클릭하면 해제될 수 있으므로 선택 여부를 먼저 확인합니다."""
    try:
        el = find_linkareer_chip(driver, chip_name)
        selected = is_selected_chip(el)
        if selected:
            print(f"    · {chip_name}: 이미 선택됨")
            return True
        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
        time.sleep(0.2)
        driver.execute_script("arguments[0].click();", el)
        time.sleep(0.7)
        try:
            el2 = find_linkareer_chip(driver, chip_name)
            selected_after = is_selected_chip(el2)
        except Exception:
            selected_after = True
        print(f"    · {chip_name}: {'클릭/선택' if selected_after else '클릭했지만 선택 확인 안 됨'}")
        return True
    except Exception as e:
        print(f"    · {chip_name}: 실패 / {e}")
        return False


def apply_linkareer_filters(driver, category: str) -> bool:
    ok = True
    # 1) 채용형태: 신입 / 인턴 / 계약직
    if not click_linkareer_tab(driver, "채용형태"):
        ok = False
    else:
        # 직군별 반복 수집 중 이전 선택값이 남아 있을 수 있으므로 먼저 지우고 다시 선택합니다.
        clear_linkareer_selected_chips_on_open_tab(driver)
        for chip in ["신입", "인턴", "계약직"]:
            ok = ensure_linkareer_chip_selected(driver, chip) and ok
        time.sleep(1.0)

    # 2) 직무
    if not click_linkareer_tab(driver, "직무"):
        ok = False
    else:
        # 이전 직군의 하위 직무 선택값이 남아 있는 문제를 막기 위해 직무 칩도 먼저 초기화합니다.
        clear_linkareer_selected_chips_on_open_tab(driver)
    mapping = {
        # 사용자가 확정한 기준: 기획/경영 > 경영기획/전략, 사업기획/신규사업, 서비스기획/운영 선택
        "기획": [
            "기획/경영",
            "경영기획/전략",
            "사업기획/신규사업",
            "서비스기획/운영",
        ],
        "마케팅": ["마케팅/광고"],
        "인사": ["기획/경영", "인사/채용/노무"],
        # 사용자가 확정한 기준: 영업 또는 CS 중 하나라도 들어가면 통과
        "영업": ["영업/CS"],
        "개발": ["IT/개발"],
    }
    for chip in mapping.get(category, []):
        ok = ensure_linkareer_chip_selected(driver, chip) and ok
        time.sleep(0.2)
    # 필터 적용 후 리스트 영역 갱신 대기
    time.sleep(2.2)
    return ok

def apply_auto_filters(driver, platform: str, category: str) -> bool:
    cfg = FILTER_CONFIG[platform]
    cat_cfg = cfg["categories"][category]
    ok = True

    # 링커리어는 tab-button/filter-chip 구조가 확인되어 전용 선택 함수를 사용합니다.
    # 이미 선택된 칩을 다시 클릭해서 해제하지 않도록 class=selected를 먼저 확인합니다.
    if platform == "링커리어":
        return apply_linkareer_filters(driver, category)

    # 원티드: 경력 > 신입
    if platform == "원티드":
        opened = click_first_available(driver, cfg.get("career_open_buttons", []), timeout=2.0)
        if opened:
            print(f"  - 경력 필터 열기: {opened}")
            for label in cfg.get("career_click_sequence", []):
                click_label(driver, label, timeout=2.0, allow_contains=True)
            click_first_available(driver, ["적용", "확인", "완료"], timeout=1.5)
            time.sleep(1.0)

    # 2) 직무 필터 적용
    opened = click_first_available(driver, cat_cfg.get("open_buttons", []), timeout=3.0)
    if opened:
        print(f"  - 직무 필터 열기: {opened}")
    else:
        print("  ⚠️ 직무 필터를 열지 못했습니다.")
        ok = False

    for label in cat_cfg.get("click_sequence", []):
        clicked = click_label(driver, label, timeout=2.5, allow_contains=True)
        print(f"    · {label}: {'클릭' if clicked else '실패'}")
        if not clicked:
            ok = False
        time.sleep(0.25)

    # 적용 버튼. 어떤 UI는 클릭 즉시 반영되므로 실패해도 치명적이지 않음.
    click_first_available(driver, cat_cfg.get("apply_buttons", ["적용", "확인", "완료"]), timeout=2.0)
    time.sleep(2.0)
    return ok

def manual_filter_prompt(driver, platform: str, category: str) -> None:
    hint = FILTER_CONFIG[platform]["categories"][category]["manual_hint"]
    print("\n" + "=" * 72)
    print(f"[{platform}] {category} 필터를 브라우저에서 직접 선택해줘.")
    print(f"가이드: {hint}")
    print("선택이 끝나고 리스트가 뜨면, VS Code 터미널에서 Enter를 눌러줘.")
    print("=" * 72)
    input()
    print(f"선택 완료 URL: {driver.current_url}")
    time.sleep(1.0)


def absolute_url(platform: str, href: str) -> str:
    if not href:
        return ""
    if platform == "원티드":
        return urljoin("https://www.wanted.co.kr", href).split("?")[0]
    return urljoin("https://linkareer.com", href).split("?")[0]


def is_valid_job_link(platform: str, href: str) -> bool:
    if not href:
        return False
    patterns = FILTER_CONFIG[platform]["link_patterns"]
    return any(re.search(p, href) for p in patterns)


def clean_lines(text: str) -> list[str]:
    raw_lines = [normalize_text(x) for x in re.split(r"[\n\r]+", text or "")]
    lines: list[str] = []
    seen = set()
    for line in raw_lines:
        if not line or line in seen:
            continue
        seen.add(line)
        if len(line) <= 1:
            continue
        if line in NOISE_WORDS:
            continue
        if re.fullmatch(r"[|·•/\-_,.\s]+", line):
            continue
        lines.append(line)
    return lines


def parse_deadline(lines: list[str]) -> str:
    for line in lines:
        if DEADLINE_RE.search(line):
            return line
    return "마감일 확인 필요"


def looks_like_meta(line: str) -> bool:
    if not line:
        return True
    if line in NOISE_WORDS:
        return True
    if DEADLINE_RE.search(line):
        return True
    if re.search(r"\d+명|\d+개 포지션|조회|스크랩|댓글|합격축하금|기술스택", line):
        return True
    if len(line) > 90:
        return True
    return False


def parse_title_company(platform: str, lines: list[str]) -> tuple[str, str]:
    usable = [x for x in lines if not looks_like_meta(x)]
    if not usable:
        return "제목 확인 필요", "회사명 확인 필요"

    # 원티드는 대개 카드 텍스트에서 공고명/회사명 순서가 잡힙니다.
    # 링커리어는 공고명/회사명 또는 회사명/공고명이 섞일 수 있어 휴리스틱으로 처리합니다.
    title = usable[0]
    company = usable[1] if len(usable) > 1 else "회사명 확인 필요"

    # 회사명이 너무 제목처럼 길면, 짧은 라인을 회사명 후보로 교체.
    if len(company) > 35:
        short_candidates = [x for x in usable[1:] if 2 <= len(x) <= 35 and not re.search(r"모집|채용|인턴|담당자|매니저|기획자|개발자", x)]
        if short_candidates:
            company = short_candidates[0]

    return title, company


def extract_card_context(a_tag) -> str:
    # 링크 자체 텍스트가 비어 있으면 부모 카드 쪽 텍스트를 가져옵니다.
    texts = []
    try:
        texts.append(a_tag.get_text("\n", strip=True))
    except Exception:
        pass
    parent = a_tag
    for _ in range(5):
        parent = getattr(parent, "parent", None)
        if parent is None:
            break
        try:
            txt = parent.get_text("\n", strip=True)
            if txt and len(txt) > max(len(t) for t in texts or [""]):
                texts.append(txt)
        except Exception:
            continue
    return max(texts, key=len) if texts else ""





def get_linkareer_active_page(driver) -> str:
    """링커리어 페이지네이션의 현재 페이지 번호를 읽습니다."""
    try:
        el = driver.find_element(By.CSS_SELECTOR, "button.button-page-number.active-page")
        return normalize_text(el.text)
    except Exception:
        return ""


def has_linkareer_next_page(driver) -> bool:
    """다음 페이지 버튼이 활성 상태인지 확인합니다."""
    try:
        btn = driver.find_element(By.CSS_SELECTOR, "button.button-arrow-next")
        cls = btn.get_attribute("class") or ""
        disabled_attr = btn.get_attribute("disabled")
        aria_disabled = btn.get_attribute("aria-disabled")
        return ("Mui-disabled" not in cls) and (disabled_attr is None) and (aria_disabled != "true")
    except Exception:
        return False


def click_linkareer_next_page(driver) -> bool:
    """링커리어 다음 페이지로 이동합니다.

    주의: 링커리어의 오른쪽 화살표는 환경에 따라 `다음 페이지`가 아니라
    페이지 묶음 이동처럼 동작할 수 있습니다. 그래서 먼저 현재 페이지+1 숫자
    버튼을 직접 클릭하고, 그 버튼이 보이지 않을 때만 화살표를 보조로 씁니다.
    """
    before_page = get_linkareer_active_page(driver)
    try:
        before_num = int(before_page) if str(before_page).isdigit() else None
    except Exception:
        before_num = None

    def click_numeric_page(target_num: int) -> bool:
        xpath = (
            "//button[contains(@class, 'button-page-number')]"
            f"[.//span[normalize-space()='{target_num}'] or normalize-space()='{target_num}']"
        )
        buttons = driver.find_elements(By.XPATH, xpath)
        if not buttons:
            return False
        btn = buttons[0]
        cls = btn.get_attribute("class") or ""
        if "Mui-disabled" in cls or btn.get_attribute("disabled") is not None:
            return False
        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", btn)
        time.sleep(0.25)
        driver.execute_script("arguments[0].click();", btn)
        WebDriverWait(driver, 10).until(lambda d: get_linkareer_active_page(d) == str(target_num))
        time.sleep(1.2)
        print(f"  → 다음 페이지 이동: {before_page or '?'} → {get_linkareer_active_page(driver) or '?'}")
        return True

    try:
        # 1순위: 1 → 2 → 3처럼 다음 숫자 버튼을 직접 클릭
        if before_num is not None and click_numeric_page(before_num + 1):
            return True

        # 2순위: 다음 숫자 버튼이 화면에 없을 때만 오른쪽 화살표 사용
        btn = driver.find_element(By.CSS_SELECTOR, "button.button-arrow-next")
        cls = btn.get_attribute("class") or ""
        if "Mui-disabled" in cls or btn.get_attribute("disabled") is not None:
            return False
        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", btn)
        time.sleep(0.25)
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(1.2)

        # 화살표 클릭 후 목표 페이지 번호가 보이면 다시 숫자 버튼으로 확정 클릭
        if before_num is not None and click_numeric_page(before_num + 1):
            return True

        # 만약 사이트가 화살표 클릭만으로 다음 페이지로 이동했다면 그대로 인정
        after_page = get_linkareer_active_page(driver)
        if after_page and after_page != before_page:
            print(f"  → 다음 페이지 이동: {before_page or '?'} → {after_page or '?'}")
            return True
        return False
    except Exception as e:
        print(f"  ⚠️ 다음 페이지 이동 실패: {e}")
        return False


def collect_linkareer_list_candidates(driver, category: str, limit: int = 0, seen_links: Optional[set[str]] = None) -> list[Job]:
    """링커리어 리스트에서 공고 상세 링크와 리스트에 보이는 제목/세부직무를 위에서부터 수집합니다."""
    soup = BeautifulSoup(driver.page_source, "html.parser")
    jobs: list[Job] = []
    if seen_links is None:
        seen_links = set()
    allowed = linkareer_allowed_raw_categories(category)

    anchors = soup.select("a.recruit-link[href]")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if re.search(r"/activity/\d+", a.get("href", ""))]

    for a in anchors:
        href = a.get("href", "")
        if not is_valid_job_link("링커리어", href):
            continue
        link = absolute_url("링커리어", href)
        if not link or link in seen_links:
            continue

        title_el = a.select_one(".recruit-name")
        cat_el = a.select_one(".recruit-category")
        title = normalize_text(title_el.get_text(" ", strip=True)) if title_el else normalize_text(a.get_text(" ", strip=True))
        raw_category = normalize_text(cat_el.get_text(" ", strip=True)) if cat_el else ""

        if allowed and raw_category and not linkareer_list_text_matches(category, raw_category, title):
            print(f"    · 리스트/제목 직무 불일치로 제외: {raw_category} / {title[:40]}")
            continue

        seen_links.add(link)
        jobs.append(Job(
            title=title or "제목 확인 필요",
            company=extract_company_from_title(title),
            platform="링커리어",
            category=category,
            deadline="상세 확인 필요",
            link=link,
            detail_url=link,
            raw_category=raw_category,
        ))
        if limit > 0 and len(jobs) >= limit:
            break
    return jobs



def collect_linkareer_paginated_candidates(
    driver,
    category: str,
    max_pages: int,
    scroll_times: int,
    baseline_mode: bool,
    known_fingerprints: set[str],
    known_page_stop: int = 1,
    limit: int = 0,
) -> list[Job]:
    """링커리어 리스트 후보를 페이지 단위로 수집합니다.

    - 최초 기준 데이터 생성 시: 최대 max_pages까지 전부 확인합니다.
    - 이후 실행 시: 최신 페이지부터 확인하고, 신규가 하나도 없는 페이지가
      known_page_stop회 연속 나오면 종료합니다.
    - 상세 페이지는 이 함수가 반환한 후보만 확인하므로 이후 실행에서는
      기존 공고를 다시 상세 크롤링하지 않습니다.
    """
    all_candidates: list[Job] = []
    seen_links: set[str] = set()
    page_idx = 1
    consecutive_known_only_pages = 0

    while True:
        active_page = get_linkareer_active_page(driver) or str(page_idx)
        print(f"  → 링커리어 리스트 페이지 {active_page} 확인 중")
        scroll_page(driver, times=scroll_times, delay=1.0)
        page_candidates = collect_linkareer_list_candidates(
            driver,
            category,
            limit=0,
            seen_links=seen_links,
        )

        if baseline_mode:
            selected = page_candidates
            print(f"    · 기준 데이터 후보 {len(selected)}건 / 누적 {len(all_candidates) + len(selected)}건")
        else:
            selected = [job for job in page_candidates if job.fingerprint not in known_fingerprints]
            known_count = len(page_candidates) - len(selected)
            print(f"    · 오늘 처음 본 후보 {len(selected)}건 / 기존 {known_count}건")
            if selected:
                consecutive_known_only_pages = 0
            else:
                consecutive_known_only_pages += 1

        all_candidates.extend(selected)
        save_debug(driver, "링커리어", category, f"page_{active_page}")

        if limit > 0 and len(all_candidates) >= limit:
            all_candidates = all_candidates[:limit]
            print(f"  → 테스트 상한 {limit}건 도달로 후보 수집 종료")
            break
        if not baseline_mode and consecutive_known_only_pages >= max(1, known_page_stop):
            print(f"  → 신규 없는 페이지 {consecutive_known_only_pages}회 확인으로 증분 수집 종료")
            break
        if max_pages and page_idx >= max_pages:
            print(f"  → max-pages {max_pages} 도달로 후보 수집 종료")
            break
        if not has_linkareer_next_page(driver):
            print("  → 다음 페이지 버튼 비활성화로 후보 수집 종료")
            break
        if not click_linkareer_next_page(driver):
            break
        page_idx += 1

    return all_candidates



def _find_linkareer_detail_content(soup: BeautifulSoup):
    """링커리어 상세내용 영역을 우선 찾습니다."""
    # 사용자가 확인한 실제 상세내용 영역
    target = soup.select_one("section[class*='ActivityDetailTabContent']")
    if target:
        return target

    # class명이 바뀌어도 h2가 '상세내용'인 부모 section/div를 찾습니다.
    for h2 in soup.find_all(["h2", "h3"]):
        if normalize_text(h2.get_text(" ", strip=True)) == "상세내용":
            parent = h2.find_parent("section") or h2.find_parent("div")
            if parent:
                return parent
    return None


LINKAREER_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "div", "dl", "dt", "dd",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
    "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}


def _linkareer_text_preserve_layout(root: Tag) -> str:
    """링커리어 HTML을 사람이 보는 문장 구조에 가깝게 텍스트로 변환합니다.

    ``get_text("\\n")``는 span/strong/a 같은 인라인 태그 경계까지 줄바꿈으로
    바꾸기 때문에 ``미국 / 영국`` 같은 한 문장이 조각납니다. 이 함수는 ``br``과
    실제 블록 요소만 줄바꿈으로 취급하고 인라인 요소의 텍스트는 이어 붙입니다.
    """
    parts: list[str] = []

    def add_newline() -> None:
        if not parts:
            return
        if not parts[-1].endswith("\n"):
            parts.append("\n")

    def walk(node) -> None:
        if isinstance(node, NavigableString):
            value = str(node).replace("\xa0", " ")
            if value:
                parts.append(value)
            return
        if not isinstance(node, Tag):
            return

        name = (node.name or "").lower()
        if name == "br":
            add_newline()
            return

        is_block = name in LINKAREER_BLOCK_TAGS
        if is_block:
            add_newline()
        for child in node.children:
            walk(child)
        if is_block:
            add_newline()

    walk(root)
    text = "".join(parts)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 인라인 태그가 나뉘어도 괄호·쉼표·슬래시 앞뒤에 불필요한 공백이 생기지 않게 정리합니다.
    text = re.sub(r"\s+([,./)\]}:;])", r"\1", text)
    text = re.sub(r"([([{])\s+", r"\1", text)
    return text.strip()


def _clean_detail_text(text: str) -> str:
    """상세 본문 텍스트에서 UI성 문구를 제거합니다."""
    lines: list[str] = []
    skip_exact = {
        "상세내용", "지원하기", "공유", "스크랩", "북마크", "홈페이지", "접수기간", "채용형태", "모집직무", "근무지역",
        "공고 확인하기", "지원하러 가기", "목록으로", "뒤로가기",
    }
    footer_start_exact = {
        "문의 및 지원하기",
        "문의 및 지원",
    }
    repaired_text = repair_broken_number_units(text)
    for line in repaired_text.split("\n"):
        line = normalize_text(line)
        # 원문 글머리표와 후처리 글머리표가 겹친 ``• • 문장``을 하나로 정리합니다.
        line = re.sub(r"^(?:[•▪◦]\s*){2,}", "• ", line)
        if not line:
            continue
        # 공고 이미지 아래 반복되는 공통 연락처 푸터는 실제 담당업무가 아닙니다.
        # 시작 문구가 나오면 그 아래 내용을 전부 버립니다.
        if line in footer_start_exact:
            break
        if line in skip_exact:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


LINKAREER_BODY_SIGNAL_RE = re.compile(
    r"담당업무|주요업무|업무내용|자격요건|지원자격|필수요건|"
    r"우대사항|근무조건|고용조건|모집부문|복리후생"
)
LINKAREER_CONTACT_FOOTER_RE = re.compile(
    r"홈페이지|블로그|지원문의|상담신청|카카오톡|카톡|이메일|메일로 지원|"
    r"https?://|www\.|@"
)


def _is_linkareer_contact_footer_only(text: str) -> bool:
    """이미지 공고 아래의 연락처·링크 푸터만 남았는지 판별합니다."""
    cleaned = (text or "").strip()
    if not cleaned or LINKAREER_BODY_SIGNAL_RE.search(cleaned):
        return False
    contact_line_count = sum(
        1 for line in cleaned.splitlines() if LINKAREER_CONTACT_FOOTER_RE.search(line)
    )
    return contact_line_count >= 2


def extract_linkareer_description_text(soup: BeautifulSoup) -> tuple[str, str]:
    """링커리어 상세 페이지의 '상세내용' 영역에서 본문 텍스트를 추출합니다.

    기준:
    - 텍스트만 있으면 description_type='text', description_text=텍스트
    - 이미지만 있으면 description_type='image', description_text=''
    - 이미지와 텍스트가 같이 있으면 description_type='text_image', description_text=텍스트만
    - OCR은 하지 않습니다. 이미지 안 글자는 사람이 직접 보고 JSON 입력칸에 붙여넣는 구조입니다.
    """
    target = _find_linkareer_detail_content(soup)

    # 1순위: 상세내용 영역만 본다. 사용자가 준 예시의 section.ActivityDetailTabContent가 여기에 해당.
    if target is not None:
        work = BeautifulSoup(str(target), "html.parser")
        for tag in work.select("script, style, noscript, svg, button"):
            tag.decompose()
        image_count = len(work.select("img"))
        text = _clean_detail_text(_linkareer_text_preserve_layout(work))[:5000]

        if image_count > 0 and _is_linkareer_contact_footer_only(text):
            text = ""

        if text and image_count > 0:
            return "text_image", text
        if text:
            return "text", text
        if image_count > 0:
            return "image", ""
        return "empty", ""

    # 2순위 fallback: 상세내용 영역을 못 찾았을 때만 페이지 전체에서 최대한 추출.
    work = BeautifulSoup(str(soup), "html.parser")
    for tag in work.select("script, style, noscript, svg, button, header, footer, nav"):
        tag.decompose()

    image_count = len(work.select("img"))

    # 상단 메타 영역은 회사명/접수기간/채용형태 등이라 JSON 변환용 본문과 구분하기 위해 제거.
    for tag in work.select("h1, h2.organization-name, dl"):
        try:
            tag.decompose()
        except Exception:
            pass

    selectors = [
        "article", "main",
        "section[class*='detail']", "section[class*='Detail']",
        "div[class*='detail']", "div[class*='Detail']",
        "div[class*='content']", "div[class*='Content']",
        "div[class*='description']", "div[class*='Description']",
    ]

    chunks: list[str] = []
    seen: set[str] = set()
    for sel in selectors:
        for el in work.select(sel):
            txt = _clean_detail_text(_linkareer_text_preserve_layout(el))
            if not txt or len(txt) < 40:
                continue
            key = txt[:160]
            if key in seen:
                continue
            seen.add(key)
            chunks.append(txt)

    if not chunks:
        txt = _clean_detail_text(_linkareer_text_preserve_layout(work))
        if txt:
            chunks.append(txt)

    text = "\n".join(chunks).strip()[:5000]

    if image_count > 0 and _is_linkareer_contact_footer_only(text):
        text = ""

    if text and image_count > 0:
        return "text_image", text
    if text:
        return "text", text
    if image_count > 0:
        return "image", ""
    return "empty", ""

def parse_linkareer_detail_html(html_text: str, fallback: Job) -> Job:
    """링커리어 상세 페이지 상단 article에서 회사명/시작일/마감일/채용형태 등을 추출합니다."""
    soup = BeautifulSoup(html_text, "html.parser")
    company_el = soup.select_one("h2.organization-name")
    company = normalize_text(company_el.get_text(" ", strip=True)) if company_el else fallback.company

    details: dict[str, str] = {}
    start_date = ""
    deadline = fallback.deadline
    for dl in soup.find_all("dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if not dt or not dd:
            continue
        key = normalize_text(dt.get_text(" ", strip=True))
        if key == "접수기간":
            start_date = detail_text_after_label(dd, "시작일")
            end_date = detail_text_after_label(dd, "마감일")
            deadline = end_date or normalize_text(dd.get_text(" ", strip=True)) or deadline
            details[key] = f"시작일 {start_date} / 마감일 {deadline}".strip()
        else:
            details[key] = normalize_text(dd.get_text(" ", strip=True))

    description_type, description_text = extract_linkareer_description_text(soup)

    return Job(
        title=fallback.title,
        company=company or fallback.company,
        platform=fallback.platform,
        category=fallback.category,
        deadline=deadline or "마감일 확인 필요",
        link=fallback.link,
        detail_url=fallback.detail_url or fallback.link,
        apply_url=fallback.apply_url,
        start_date=start_date,
        job_type=next(
            (
                details.get(label, "")
                for label in ["채용형태", "고용형태", "근무형태", "고용조건", "계약형태"]
                if details.get(label, "")
            ),
            "",
        ),
        raw_category=details.get("모집직무", "") or fallback.raw_category,
        description_type=description_type,
        description_text=description_text,
    )



def find_homepage_url_from_detail(html_text: str) -> str:
    """상세 상단의 '홈페이지' URL을 찾습니다.

    주의: 이 URL은 회사 대표 홈페이지인 경우가 많아서 웹사이트 대표 링크로 쓰지 않습니다.
    지원 버튼 클릭 결과가 이 URL과 같으면, 채용공고 링크로 보기 어렵다고 판단하고 detail_url을 사용합니다.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    for dl in soup.find_all("dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if not dt or not dd:
            continue
        key = normalize_text(dt.get_text(" ", strip=True))
        if key == "홈페이지":
            a = dd.find("a", href=True)
            if a:
                return a.get("href", "").strip()
    return ""


def canonical_url_for_compare(url: str) -> str:
    """비교용 URL. query/hash/trailing slash 차이를 줄입니다."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        netloc = parsed.netloc.lower().replace("www.", "")
        path = re.sub(r"/+$", "", parsed.path or "/")
        return f"{parsed.scheme.lower()}://{netloc}{path}"
    except Exception:
        return url.strip().rstrip("/")


def is_homepage_fallback_url(apply_url: str, homepage_url: str) -> bool:
    """지원 링크로 잡힌 값이 상세 상단의 회사 홈페이지와 사실상 같으면 제외합니다."""
    if not apply_url or not homepage_url:
        return False
    return canonical_url_for_compare(apply_url) == canonical_url_for_compare(homepage_url)


def capture_apply_url_linkareer(driver, detail_url: str, detail_html: str, timeout: float = 8.0) -> str:
    """링커리어 상세 페이지의 '홈페이지 지원' 버튼을 클릭해서 실제 지원 URL을 캡처합니다.

    v3.9 수정:
    - 상세 상단의 '홈페이지' URL을 더 이상 대표 링크로 쓰지 않습니다.
      이 값은 회사 메인 홈페이지인 경우가 많아 웹사이트에서 잘못된 링크로 보였습니다.
    - 지원 버튼 클릭 결과가 실제 외부 지원 URL로 확인될 때만 apply_url로 저장합니다.
    - 지원 버튼 클릭이 실패하거나 회사 메인 홈페이지로만 잡히면 빈 문자열을 반환하고, 호출부에서 링커리어 상세 URL을 대표 링크로 사용합니다.
    """
    homepage_url = find_homepage_url_from_detail(detail_html)

    try:
        original_window = driver.current_window_handle
        before_handles = set(driver.window_handles)
        before_url = driver.current_url
    except Exception:
        return ""

    button_xpaths = [
        "//button[contains(@class,'apply-button') and contains(normalize-space(.),'지원')]",
        "//button[contains(normalize-space(.),'홈페이지 지원')]",
        "//button[contains(normalize-space(.),'지원')]",
    ]

    clicked = False
    for xp in button_xpaths:
        try:
            btn = WebDriverWait(driver, 4).until(EC.presence_of_element_located((By.XPATH, xp)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", btn)
            time.sleep(0.2)
            try:
                btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", btn)
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        return ""

    end_time = time.time() + timeout
    apply_url = ""

    while time.time() < end_time:
        try:
            handles = list(driver.window_handles)
        except Exception:
            handles = []

        # 1) 새 탭/새 창이 열렸으면 그 URL을 우선 사용
        new_handles = [h for h in handles if h not in before_handles]
        if new_handles:
            try:
                driver.switch_to.window(new_handles[-1])
                time.sleep(0.8)
                apply_url = driver.current_url
                # 새 탭은 나중에 외부 cleanup에서 정리해도 되지만, 여기서 닫을 수 있으면 닫습니다.
                try:
                    driver.close()
                except Exception:
                    pass
                # 원래 상세 탭이 살아 있으면 되돌아갑니다. 닫혔으면 호출부 cleanup이 메인 탭으로 복구합니다.
                try:
                    if original_window in driver.window_handles:
                        driver.switch_to.window(original_window)
                except Exception:
                    pass
                break
            except Exception:
                pass

        # 2) 현재 탭이 외부 지원 페이지로 이동했으면 현재 URL을 사용
        try:
            current_url = driver.current_url
            if current_url and current_url != before_url and not current_url.startswith("about:"):
                apply_url = current_url
                break
        except Exception:
            # 현재 상세 탭이 닫힌 경우. 남은 탭이 있으면 URL 확인을 시도합니다.
            try:
                handles = list(driver.window_handles)
                for h in handles:
                    if h != original_window:
                        driver.switch_to.window(h)
                        current_url = driver.current_url
                        if current_url and current_url != before_url and not current_url.startswith("about:"):
                            apply_url = current_url
                            break
                if apply_url:
                    break
            except Exception:
                pass

        time.sleep(0.3)

    if (not apply_url) or apply_url == before_url or apply_url.startswith("about:"):
        return ""

    # 회사 대표 홈페이지로만 잡힌 경우는 잘못된 대표 링크가 되므로 제외합니다.
    if is_homepage_fallback_url(apply_url, homepage_url):
        print("    · 지원 버튼 URL이 회사 홈페이지와 같아 상세 링크로 대체")
        return ""

    return apply_url


def cleanup_windows_keep(driver, keep_handle: str | None):
    """상세/지원 탭을 닫고 메인 리스트 탭 하나만 남깁니다."""
    try:
        handles = list(driver.window_handles)
    except Exception:
        return

    # keep_handle이 사라졌다면 남은 첫 탭을 keep으로 잡습니다.
    if not keep_handle or keep_handle not in handles:
        keep_handle = handles[0] if handles else None

    for h in list(handles):
        if keep_handle and h == keep_handle:
            continue
        try:
            driver.switch_to.window(h)
            driver.close()
        except Exception:
            pass

    try:
        if keep_handle and keep_handle in driver.window_handles:
            driver.switch_to.window(keep_handle)
    except Exception:
        pass

def crawl_linkareer_detail(driver, candidate: Job, sleep: float = 0.8) -> Job:
    """링커리어 상세 페이지에 들어가 상단 정보만 확인합니다.

    v3.10 수정:
    - 카카오 로그인 이슈 때문에 `홈페이지 지원` 버튼은 클릭하지 않습니다.
    - 웹사이트/CSV의 대표 링크는 항상 링커리어 상세 주소(detail_url)로 고정합니다.
    - 메인 리스트 탭은 남겨두고, 상세 페이지는 새 탭에서 열어 안정적으로 다음 공고로 넘어갑니다.
    """
    keep_handle = None
    try:
        keep_handle = driver.current_window_handle
    except Exception:
        keep_handle = None

    try:
        try:
            driver.switch_to.new_window('tab')
        except Exception:
            driver.execute_script("window.open('about:blank', '_blank');")
            driver.switch_to.window(driver.window_handles[-1])

        driver.get(candidate.link)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "article, h2.organization-name"))
        )
        time.sleep(sleep)
        detail_html = driver.page_source
        job = parse_linkareer_detail_html(detail_html, candidate)
        job.apply_url = ""
        job.detail_url = candidate.link
        job.link = candidate.link
        print(f"    · 상세 확인: {job.company} / 시작 {job.start_date or '-'} / 마감 {job.deadline} / 링크 상세")
        return job
    except Exception as e:
        print(f"    · 상세 확인 실패, 리스트 정보 유지: {candidate.title[:40]} / {e}")
        candidate.detail_url = candidate.detail_url or candidate.link
        candidate.link = candidate.detail_url or candidate.link
        candidate.apply_url = ""
        return candidate
    finally:
        cleanup_windows_keep(driver, keep_handle)

def collect_linkareer_with_details(
    driver,
    category: str,
    max_pages: int = 10,
    scroll_times: int = 4,
    baseline_mode: bool = False,
    known_fingerprints: Optional[set[str]] = None,
    known_page_stop: int = 1,
    limit: int = 0,
) -> list[Job]:
    known_fingerprints = known_fingerprints or set()
    candidates = collect_linkareer_paginated_candidates(
        driver,
        category,
        max_pages=max_pages,
        scroll_times=scroll_times,
        baseline_mode=baseline_mode,
        known_fingerprints=known_fingerprints,
        known_page_stop=known_page_stop,
        limit=limit,
    )
    mode_label = "기준 데이터" if baseline_mode else "오늘 신규"
    print(f"  → 리스트에서 {mode_label} 상세 후보 {len(candidates)}건 확보")

    jobs: list[Job] = []
    for idx, cand in enumerate(candidates, start=1):
        print(f"  [{idx}/{len(candidates)}] 상세 진입: {cand.title[:60]}")
        jobs.append(crawl_linkareer_detail(driver, cand))
    return jobs


def collect_jobs_from_current_page(driver, platform: str, category: str, limit: int) -> list[Job]:
    soup = BeautifulSoup(driver.page_source, "html.parser")
    jobs: list[Job] = []
    seen_links: set[str] = set()

    anchors = [a for a in soup.find_all("a", href=True) if is_valid_job_link(platform, a.get("href", ""))]

    for a in anchors:
        link = absolute_url(platform, a.get("href", ""))
        if not link or link in seen_links:
            continue
        seen_links.add(link)

        context = extract_card_context(a)
        lines = clean_lines(context)
        title, company = parse_title_company(platform, lines)
        deadline = parse_deadline(lines)

        # 제목이 너무 일반적이면 링크 주변 텍스트를 한 번 더 사용
        if title in {"제목 확인 필요", "상세보기", "공고 보기"} or len(title) < 3:
            title = lines[0] if lines else "제목 확인 필요"

        jobs.append(Job(
            title=title,
            company=company,
            platform=platform,
            category=category,
            deadline=deadline,
            link=link,
            detail_url=link,
        ))
        if len(jobs) >= limit:
            break

    return jobs



def wanted_job_to_job(job: WantedJob) -> Job:
    """원티드 상세 결과를 링커리어와 동일한 통합 Job 스키마로 변환합니다."""
    description_text = build_wanted_description_text(job)
    return Job(
        title=job.title or f"[{job.company}] {job.position_title}".strip(),
        company=job.company or "회사명 확인 필요",
        platform="원티드",
        category=job.category,
        deadline=job.deadline or "확인 필요",
        link=job.link,
        detail_url=job.link,
        apply_url=job.link,
        start_date="",
        job_type=job.employment_type or "",
        raw_category=job.matched_role,
        description_type="text" if description_text else "empty",
        description_text=description_text,
    )


def _limit_jobs_per_category(jobs: list[WantedJob], categories: list[str], limit: int) -> list[WantedJob]:
    """원티드 후보를 담당자용 직군별로 같은 상한만큼 유지합니다."""
    if limit <= 0:
        return [job for job in jobs if job.category in categories]
    counts = {category: 0 for category in categories}
    selected: list[WantedJob] = []
    for job in jobs:
        if job.category not in counts or counts[job.category] >= limit:
            continue
        counts[job.category] += 1
        selected.append(job)
    return selected


def _save_wanted_partial(jobs: list[Job]) -> None:
    """중간 종료 시 완료된 원티드 상세 결과를 확인할 수 있도록 체크포인트를 남깁니다."""
    path = DATA_DIR / "wanted_partial_jobs.json"
    data = [asdict(job) | {"fingerprint": job.fingerprint} for job in jobs]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_wanted_integrated(
    driver,
    categories: list[str],
    limit_per_category: int,
    scroll_times: int,
    baseline_categories: set[str],
    known_fingerprints: set[str],
    detail_delay: float,
    save_each_detail: bool,
    force_test_details: bool = False,
    headless: bool = False,
) -> list[Job]:
    """원티드 목록을 한 번 모은 뒤 직군별 기준/증분 모드로 처리합니다.

    아직 기준 데이터가 없는 직군은 목록 정보만 baseline으로 저장합니다. 이미 기준이
    있는 직군은 이전 이력에 없는 URL만 상세 페이지에 순차 방문합니다.
    """
    print("\n[원티드] 직군·직무별 공식 URL 수집 시작")
    raw_jobs, report = crawl_wanted_direct(
        driver,
        limit=0,
        max_scrolls=max(1, scroll_times),
        debug_dir=DEBUG_DIR,
    )
    raw_jobs = [job for job in raw_jobs if job.category in categories]

    baseline_jobs: list[WantedJob] = []
    detail_candidates: list[WantedJob] = []
    for category in categories:
        category_jobs = [job for job in raw_jobs if job.category == category]
        if force_test_details:
            selected = _limit_jobs_per_category(category_jobs, [category], limit_per_category or 10)
            detail_candidates.extend(selected)
            print(f"  → 원티드 {category}: 상세 강제 테스트 {len(selected)}건")
            continue
        if category in baseline_categories:
            selected = _limit_jobs_per_category(category_jobs, [category], limit_per_category)
            missing_company_values = {"", "회사명 확인 필요", "확인 필요"}
            company_repair_jobs = [
                job for job in selected
                if normalize_text(job.company) in missing_company_values
            ]
            baseline_jobs.extend([
                job for job in selected
                if normalize_text(job.company) not in missing_company_values
            ])
            detail_candidates.extend(company_repair_jobs)
            print(
                f"  → 원티드 {category}: 기준 데이터 {len(selected)}건 "
                f"(회사명 보정 상세 방문 {len(company_repair_jobs)}건)"
            )
            continue

        unseen = [
            job for job in category_jobs
            if wanted_job_to_job(job).fingerprint not in known_fingerprints
        ]
        selected = _limit_jobs_per_category(unseen, [category], limit_per_category)
        detail_candidates.extend(selected)
        print(f"  → 원티드 {category}: 오늘 처음 본 후보 {len(selected)}건")

    completed: list[Job] = [wanted_job_to_job(job) for job in baseline_jobs]
    print(f"\n[원티드 상세 페이지 직접 방문: {len(detail_candidates)}건]")
    active_driver = driver
    replacement_driver_created = False

    def restart_wanted_browser() -> None:
        nonlocal active_driver, replacement_driver_created
        try:
            if active_driver is not None:
                active_driver.quit()
        except Exception:
            pass
        print("    - Chrome 창이 종료되어 새 브라우저로 재시작합니다.")
        active_driver = create_driver(headless=headless)
        replacement_driver_created = True

    try:
        for index, wanted_job in enumerate(detail_candidates, start=1):
            print(f"  [{index}/{len(detail_candidates)}] {wanted_job.link}")
            if not driver_session_alive(active_driver):
                restart_wanted_browser()

            enrich_wanted_job_detail(
                active_driver,
                wanted_job,
                DEBUG_DIR,
                detail_delay=max(0.0, detail_delay),
                save_each_detail=save_each_detail,
            )

            # Chrome 탭/세션이 중간에 닫힌 경우 같은 공고를 새 브라우저에서 1회 재시도합니다.
            session_failed = (
                wanted_job.detail_status.startswith("failed")
                and any(
                    token in wanted_job.detail_status
                    for token in ["NoSuchWindow", "InvalidSession", "WebDriver"]
                )
            ) or not driver_session_alive(active_driver)
            if session_failed:
                restart_wanted_browser()
                print("    - 현재 공고 상세 재시도 1/1")
                enrich_wanted_job_detail(
                    active_driver,
                    wanted_job,
                    DEBUG_DIR,
                    detail_delay=max(0.0, detail_delay),
                    save_each_detail=save_each_detail,
                )

            integrated = wanted_job_to_job(wanted_job)
            completed.append(integrated)
            _save_wanted_partial(completed)
            print(f"    - {wanted_job.detail_status}: {integrated.title}")
    except KeyboardInterrupt:
        print(f"\n원티드 중간 종료: 완료된 결과 {len(completed)}건까지 반영합니다.")
        return completed
    finally:
        if replacement_driver_created and active_driver is not driver:
            try:
                active_driver.quit()
            except Exception:
                pass

    partial_path = DATA_DIR / "wanted_partial_jobs.json"
    try:
        partial_path.unlink(missing_ok=True)
    except Exception:
        pass
    return completed

def _empty_state() -> dict:
    return {"__meta__": {"version": 3, "platforms": {}}}


def _state_job_items(seen: dict):
    for fp, record in seen.items():
        if not str(fp).startswith("__") and isinstance(record, dict):
            yield fp, record


def normalize_category_name(category: str) -> str:
    return "개발" if category == "AI/개발" else (category or "")


def load_seen() -> dict:
    if not STATE_PATH.exists():
        return _empty_state()
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _empty_state()

    if not isinstance(raw, dict) or not raw:
        return _empty_state()

    now_text = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    was_flat_legacy = "__meta__" not in raw
    if was_flat_legacy:
        # v13 이하의 평면 상태는 실제 최초 발견일을 보장할 수 없으므로 baseline으로 마이그레이션합니다.
        migrated = _empty_state()
        migrated["__meta__"].update({
            "initialized_at": now_text,
            "migrated_from_legacy": True,
        })
        for fp, old in raw.items():
            if not isinstance(old, dict):
                continue
            record = dict(old)
            record["platform"] = record.get("platform") or "링커리어"
            record["category"] = normalize_category_name(record.get("category", ""))
            record.update({
                "is_baseline": True,
                "first_seen_at": "",
                "first_seen_date": "",
                "baseline_seen_at": old.get("first_seen_at", ""),
                "last_seen_at": old.get("first_seen_at", ""),
            })
            migrated[fp] = record
        raw = migrated

    meta = raw.setdefault("__meta__", {})
    original_version = int(meta.get("version", 0) or 0)
    had_platforms_key = isinstance(meta.get("platforms"), dict)
    infer_category_metadata = was_flat_legacy or original_version < 3 or not had_platforms_key
    platforms = meta.setdefault("platforms", {})
    meta["version"] = 3
    inferred_at = meta.get("initialized_at") or now_text

    # 카테고리명은 항상 최신 명칭으로 보정합니다. 플랫폼/직군 초기화 여부 추론은
    # v3 이전 상태에서만 수행해, 실패한 신규 baseline이 다음 실행에서 완료로 오인되지 않게 합니다.
    for _, record in _state_job_items(raw):
        platform = str(record.get("platform") or "링커리어")
        category = normalize_category_name(str(record.get("category") or ""))
        record["platform"] = platform
        record["category"] = category
        if not infer_category_metadata or not category:
            continue
        platform_meta = platforms.setdefault(platform, {})
        categories = platform_meta.setdefault("categories", {})
        categories.setdefault(category, {
            "initialized_at": inferred_at,
            "inferred_from_legacy": True,
        })

    return raw


def save_seen(seen: dict) -> None:
    STATE_PATH.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def state_is_initialized(seen: dict) -> bool:
    return bool(seen.get("__meta__", {}).get("initialized_at"))


def category_is_initialized(seen: dict, platform: str, category: str) -> bool:
    category = normalize_category_name(category)
    category_meta = (
        seen.get("__meta__", {})
        .get("platforms", {})
        .get(platform, {})
        .get("categories", {})
        .get(category, {})
    )
    return bool(category_meta.get("initialized_at"))


def set_category_initialized(
    seen: dict,
    platform: str,
    category: str,
    initialized: bool,
    note: str = "",
) -> None:
    category = normalize_category_name(category)
    meta = seen.setdefault("__meta__", {"version": 3, "platforms": {}})
    meta["version"] = 3
    platforms = meta.setdefault("platforms", {})
    platform_meta = platforms.setdefault(platform, {})
    categories = platform_meta.setdefault("categories", {})
    if initialized:
        now_text = now_kst().strftime("%Y-%m-%d %H:%M:%S")
        categories[category] = {"initialized_at": now_text, "note": note}
        platform_meta.setdefault("initialized_at", now_text)
        meta.setdefault("initialized_at", now_text)
    else:
        categories.pop(category, None)
        if not categories:
            platform_meta.pop("initialized_at", None)


def _job_record(job: Job) -> dict:
    record = asdict(job)
    record.pop("is_new", None)
    return record


def _minimal_job_record(job: Job) -> dict:
    return {
        "title": job.title,
        "company": job.company,
        "platform": job.platform,
        "category": job.category,
        "link": job.link,
        "detail_url": job.detail_url,
        "apply_url": job.apply_url,
    }


def compact_old_state_records(seen: dict, keep_days: int = DISPLAY_WINDOW_DAYS - 1) -> None:
    """비교에 필요 없는 오래된 상세본문을 제거해 seen_jobs.json이 계속 커지는 것을 막습니다."""
    today = now_kst().date()
    keep_dates = {(today - timedelta(days=i)).isoformat() for i in range(keep_days + 1)}
    keep_keys = {
        "title", "company", "platform", "category", "link", "detail_url", "apply_url",
        "is_baseline", "first_seen_at", "first_seen_date", "baseline_seen_at", "last_seen_at",
    }
    for _, record in _state_job_items(seen):
        if record.get("is_baseline") or record.get("first_seen_date") not in keep_dates:
            for key in list(record.keys()):
                if key not in keep_keys:
                    record.pop(key, None)


def register_crawled_jobs(jobs: list[Job], seen: dict, baseline_mode: bool, platform: str = "") -> tuple[dict, int]:
    now = now_kst()
    now_text = now.strftime("%Y-%m-%d %H:%M:%S")
    today_text = now.date().isoformat()
    new_count = 0

    for job in jobs:
        fp = job.fingerprint
        existing = seen.get(fp)
        if existing is None:
            record = _minimal_job_record(job) if baseline_mode else _job_record(job)
            if baseline_mode:
                record.update({
                    "is_baseline": True,
                    "first_seen_at": "",
                    "first_seen_date": "",
                    "baseline_seen_at": now_text,
                    "last_seen_at": now_text,
                })
            else:
                record.update({
                    "is_baseline": False,
                    "first_seen_at": now_text,
                    "first_seen_date": today_text,
                    "baseline_seen_at": "",
                    "last_seen_at": now_text,
                })
                new_count += 1
            seen[fp] = record
            existing = record
        else:
            preserved = {
                "is_baseline": existing.get("is_baseline", False),
                "first_seen_at": existing.get("first_seen_at", ""),
                "first_seen_date": existing.get("first_seen_date", ""),
                "baseline_seen_at": existing.get("baseline_seen_at", ""),
            }
            existing.update(_job_record(job))
            existing.update(preserved)
            existing["last_seen_at"] = now_text

        job.first_seen_at = existing.get("first_seen_at", "")
        job.is_new = existing.get("first_seen_date") == today_text

    meta = seen.setdefault("__meta__", {"version": 3, "platforms": {}})
    meta["version"] = 3
    meta.setdefault("platforms", {})
    meta.setdefault("initialized_at", now_text)
    meta["last_run_at"] = now_text
    meta["last_run_date"] = today_text
    meta["last_run_mode"] = "baseline" if baseline_mode else "incremental"
    if platform:
        platform_meta = meta.setdefault("platforms", {}).setdefault(platform, {})
        platform_meta["last_run_at"] = now_text
        platform_meta["last_run_mode"] = "baseline" if baseline_mode else "incremental"
    compact_old_state_records(seen, keep_days=DISPLAY_WINDOW_DAYS - 1)
    save_seen(seen)
    return seen, new_count


def _record_to_job(record: dict) -> Optional[Job]:
    required_defaults = {
        "title": "제목 확인 필요",
        "company": "회사명 확인 필요",
        "platform": "링커리어",
        "category": "기타",
        "deadline": "마감일 확인 필요",
        "link": record.get("detail_url", ""),
    }
    values = {}
    for name in Job.__dataclass_fields__:
        if name == "is_new":
            continue
        if name in record:
            values[name] = record.get(name)
        elif name in required_defaults:
            values[name] = required_defaults[name]
    try:
        job = Job(**values)
    except TypeError:
        return None
    job.first_seen_at = record.get("first_seen_at", "")
    job.is_new = record.get("first_seen_date") == now_kst().date().isoformat()
    return job


def jobs_from_recent_discovery_window(seen: dict, days: int = DISPLAY_WINDOW_DAYS - 1) -> list[Job]:
    today = now_kst().date()
    allowed_dates = {(today - timedelta(days=i)).isoformat() for i in range(days + 1)}
    jobs: list[Job] = []
    for _, record in _state_job_items(seen):
        if record.get("is_baseline"):
            continue
        if record.get("first_seen_date") not in allowed_dates:
            continue
        job = _record_to_job(record)
        if job:
            jobs.append(job)
    return jobs


def crawl_platform_category(
    driver,
    platform: str,
    category: str,
    limit: int,
    scroll_times: int,
    max_pages: int,
    manual: bool,
    auto_only: bool,
    baseline_mode: bool,
    known_fingerprints: set[str],
    known_page_stop: int,
) -> list[Job]:
    base_url = FILTER_CONFIG[platform]["base_url"]
    print(f"\n[{platform}] {category} 수집 시작")
    print(f"  URL: {base_url}")
    if platform == "링커리어":
        reset_linkareer_filters(driver)
    else:
        driver.get(base_url)
        time.sleep(3.0)

    applied = False
    if not manual:
        applied = apply_auto_filters(driver, platform, category)
        if applied:
            print("  ✅ 자동 필터 클릭 시도 완료")
        else:
            print("  ⚠️ 자동 필터 클릭이 일부 실패했습니다.")

    if manual or (not applied and not auto_only):
        manual_filter_prompt(driver, platform, category)

    save_debug(driver, platform, category, "after_filter")

    if platform == "링커리어":
        jobs = collect_linkareer_with_details(
            driver,
            category,
            max_pages=max_pages,
            scroll_times=scroll_times,
            baseline_mode=baseline_mode,
            known_fingerprints=known_fingerprints,
            known_page_stop=known_page_stop,
            limit=limit,
        )
        print(f"  → 상세 확인 기준 {len(jobs)}건 수집")
        return jobs

    scroll_page(driver, times=scroll_times, delay=1.2)
    page_jobs = collect_jobs_from_current_page(driver, platform, category, limit=limit or 1000)
    if baseline_mode:
        return page_jobs
    return [job for job in page_jobs if job.fingerprint not in known_fingerprints]


def dedupe_jobs(jobs: list[Job]) -> list[Job]:
    out: list[Job] = []
    seen: set[str] = set()
    for j in jobs:
        fp = j.fingerprint
        # 같은 공고가 여러 직군에 잡히면 먼저 잡힌 직군을 유지합니다.
        if fp in seen:
            continue
        seen.add(fp)
        out.append(j)
    return out


def write_json_csv(jobs: list[Job]) -> None:
    json_path = SITE_DIR / "jobs.json"
    csv_path = SITE_DIR / "jobs.csv"
    data = [asdict(job) | {"fingerprint": job.fingerprint} for job in jobs]
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 링커리어 기존 CSV와 원티드 CSV를 동일한 16개 컬럼 순서로 유지합니다.
    fields = [
        "platform", "category", "is_new", "company", "title",
        "start_date", "deadline", "job_type", "raw_category", "description_type",
        "description_text", "link", "detail_url", "apply_url", "first_seen_at", "fingerprint",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in data:
            writer.writerow({key: row.get(key, "") for key in fields})



def generate_html(jobs: list[Job], init_seen: bool = False) -> None:
    now = now_kst()
    now_str = now.strftime("%Y년 %m월 %d일 %H:%M")
    today_text = now.date().isoformat()
    yesterday_text = (now.date() - timedelta(days=1)).isoformat()
    new_jobs = [j for j in jobs if (j.first_seen_at or "")[:10] == today_text]
    yesterday_jobs = [j for j in jobs if (j.first_seen_at or "")[:10] == yesterday_text]

    by_category = {cat: [] for cat in CATEGORY_ORDER}
    for job in jobs:
        by_category.setdefault(job.category, []).append(job)

    jobs_data = [asdict(j) | {"fingerprint": j.fingerprint} for j in jobs]
    jobs_json = json.dumps(jobs_data, ensure_ascii=False)

    summary_cards = []
    for cat in CATEGORY_ORDER:
        count = len(by_category.get(cat, []))
        new_count = sum(1 for j in by_category.get(cat, []) if (j.first_seen_at or "")[:10] == today_text)
        yesterday_count = sum(1 for j in by_category.get(cat, []) if (j.first_seen_at or "")[:10] == yesterday_text)
        color = CATEGORY_COLORS.get(cat, "#94A3B8")
        icon = CATEGORY_ICONS.get(cat, "•")
        summary_cards.append(f"""
        <a href="#section-{html.escape(cat)}" class="sum-card" data-category="{html.escape(cat)}" style="border-color:{color}30;">
          <div class="sum-icon">{icon}</div>
          <div class="sum-num" style="color:{color};">{count}</div>
          <div class="sum-label">{html.escape(cat)} · 최근 7일 {count} · 오늘 {new_count} · 어제 {yesterday_count}</div>
        </a>
        """)

    def render_job_card(j: Job) -> str:
        platform_color = PLATFORM_COLORS.get(j.platform, "#94A3B8")
        category_color = CATEGORY_COLORS.get(j.category, "#94A3B8")
        first_date = (j.first_seen_at or "")[:10]
        try:
            days_ago = (now.date() - date.fromisoformat(first_date)).days if first_date else -1
        except (TypeError, ValueError):
            days_ago = -1
        if init_seen or not first_date:
            new_badge = '<span class="status-badge baseline">기준</span>'
        elif first_date == today_text:
            new_badge = '<span class="status-badge new">오늘</span>'
        elif first_date == yesterday_text:
            new_badge = '<span class="status-badge yesterday">어제</span>'
        else:
            badge_text = f"{days_ago}일 전" if 2 <= days_ago < DISPLAY_WINDOW_DAYS else "기존"
            new_badge = f'<span class="status-badge old">{badge_text}</span>' 
        start_label = html.escape(j.start_date or '확인 필요')
        deadline_label = html.escape(j.deadline or '마감일 확인 필요')
        company_label = html.escape(j.company or '회사명 확인 필요')
        job_type_label = html.escape(j.job_type or '공고 내 고용조건 항목 없음')
        raw_category_label = html.escape(j.raw_category or '직무 확인 필요')
        if j.description_type == "text":
            desc_label = "텍스트형"
            desc_class = "text"
        elif j.description_type == "text_image":
            desc_label = "텍스트+이미지"
            desc_class = "text"
        elif j.description_type == "image":
            desc_label = "이미지/수동"
            desc_class = "manual"
        else:
            desc_label = "본문 없음"
            desc_class = "manual"
        return f"""
        <article class="card {'new-card' if j.is_new else ''}" data-fp="{html.escape(j.fingerprint)}" data-platform="{html.escape(j.platform)}" data-category="{html.escape(j.category)}" data-days-ago="{days_ago}">
          <div class="select-top">
            <label class="check-label">
              <input type="checkbox" class="job-check" value="{html.escape(j.fingerprint)}" aria-label="공고 선택">
              <span>선택</span>
            </label>
            <button class="select-toggle" type="button" data-fp="{html.escape(j.fingerprint)}">JSON용 선택</button>
            <span class="desc-pill {desc_class}">{desc_label}</span>
          </div>
          <div class="platform-line">
            <span class="platform-dot" style="background:{platform_color};"></span>
            <span class="platform-name">{html.escape(j.platform)}</span>
            {new_badge}
          </div>
          <div class="category-line" style="color:{category_color};">{html.escape(j.category)}</div>
          <div class="date-line">
            <span><b>시작</b> {start_label}</span>
            <span><b>마감</b> {deadline_label}</span>
          </div>
          <h3 class="job-title">{html.escape(j.title)}</h3>
          <div class="job-meta">
            <div><span>회사</span>{company_label}</div>
            <div><span>고용형태</span>{job_type_label}</div>
            <div><span>직무</span>{raw_category_label}</div>
          </div>
          <a href="{html.escape(j.link)}" target="_blank" rel="noopener noreferrer" class="btn">공고 확인하기</a>
        </article>
        """

    category_sections = []
    for cat in CATEGORY_ORDER:
        job_list = by_category.get(cat, [])
        color = CATEGORY_COLORS.get(cat, "#94A3B8")
        icon = CATEGORY_ICONS.get(cat, "•")
        cards = ''.join(render_job_card(j) for j in job_list) if job_list else '<div class="empty-mini">수집된 공고 없음</div>'
        category_sections.append(f"""
        <section class="cat-section" id="section-{html.escape(cat)}" data-category="{html.escape(cat)}">
          <h2 style="color:{color};border-bottom:3px solid {color};">
            {icon} {html.escape(cat)} <span class="count-badge" style="background:{color}22;color:{color};">{len(job_list)}건</span>
          </h2>
          <div class="grid">{cards}</div>
        </section>
        """)

    template = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>채용공고 모음</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;padding:18px;background:#fff;color:#0f172a;font-family:Pretendard,-apple-system,BlinkMacSystemFont,system-ui,sans-serif}
.wrap{max-width:1060px;margin:0 auto}
.hdr{text-align:center;margin-bottom:14px;padding:22px 18px;background:linear-gradient(135deg,#eff6ff,#f5f3ff);border:1px solid #e2e8f0;border-radius:18px}
.hdr h1{font-size:24px;margin:0 0 8px;font-weight:950;letter-spacing:-.03em}.sub{color:#64748b;font-size:12px;line-height:1.55}.badge{display:inline-flex;margin:10px 3px 0;padding:6px 12px;border-radius:999px;font-size:12px;font-weight:850}.badge.total{background:#dbeafe;color:#1d4ed8}.badge.new{background:#ffe4e6;color:#be123c}.badge.yesterday{background:#e0f2fe;color:#0369a1}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-bottom:14px}.sum-card{display:block;text-decoration:none;text-align:center;padding:12px 8px;border-radius:14px;background:#fff;border:1px solid #e2e8f0;transition:.2s;box-shadow:0 6px 18px rgba(15,23,42,.04)}.sum-card:hover{transform:translateY(-2px);background:#f8fafc}.sum-icon{font-size:20px;margin-bottom:3px}.sum-num{font-size:22px;font-weight:950}.sum-label{font-size:11px;color:#64748b;font-weight:750;margin-top:2px}
.filter-panel{background:#fff;border:1px solid #dbeafe;border-radius:16px;padding:14px;margin:0 0 14px;box-shadow:0 8px 24px rgba(15,23,42,.06)}.filter-panel h2{margin:0 0 12px;padding:0;border:0;font-size:17px;color:#0f172a}.filter-group{display:flex;gap:10px;align-items:flex-start;margin:9px 0}.filter-label{width:58px;flex:0 0 58px;padding-top:7px;font-size:12px;font-weight:950;color:#334155}.filter-options{display:flex;flex-wrap:wrap;gap:7px}.filter-chip{cursor:pointer}.filter-chip input{position:absolute;opacity:0;pointer-events:none}.filter-chip span{display:inline-flex;align-items:center;justify-content:center;min-height:32px;padding:7px 10px;border:1px solid #cbd5e1;border-radius:999px;background:#fff;color:#475569;font-size:12px;font-weight:850;transition:.15s}.filter-chip span:hover{border-color:#93c5fd;background:#f8fafc}.filter-chip input:checked+span{border-color:#2563eb;background:#eff6ff;color:#1d4ed8;box-shadow:inset 0 0 0 1px #2563eb}.filter-footer{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:12px;padding-top:10px;border-top:1px solid #e2e8f0}.filter-result{margin-right:auto;font-size:12px;color:#334155;font-weight:850}.filter-note{font-size:11px;color:#64748b}.filter-empty{margin:0 0 20px}.card[hidden],.cat-section[hidden],.sum-card[hidden],.filter-empty[hidden]{display:none!important}
.tool-panel{position:sticky;top:10px;z-index:10;background:rgba(255,255,255,.96);backdrop-filter:blur(12px);border:1px solid #e2e8f0;border-radius:16px;padding:14px;margin:0 0 18px;box-shadow:0 12px 36px rgba(15,23,42,.09)}.tool-panel h2{border:0;margin:0 0 8px;padding:0;color:#0f172a;font-size:17px}.tool-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.selected-count{font-size:12px;color:#334155;font-weight:850;margin-right:auto}.tool-btn{border:0;border-radius:10px;padding:9px 12px;background:#2563eb;color:#fff;font-weight:900;cursor:pointer}.tool-btn.secondary{background:#f1f5f9;color:#0f172a;border:1px solid #cbd5e1}.tool-btn.pink{background:#7c3aed}.tool-btn:disabled{opacity:.42;cursor:not-allowed}.hint{font-size:12px;color:#64748b;line-height:1.55;margin:8px 0 0}
.output-box,.json-zone{display:none;margin-top:12px;background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:12px}.output-box.active,.json-zone.active{display:block}.json-zone.active{max-height:min(78vh,780px);overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;-webkit-overflow-scrolling:touch}.json-zone.fullscreen{max-height:none!important;overflow-y:auto!important}.result-panel{position:relative}.result-panel.collapsed>.result-body{display:none}.result-panel.fullscreen{position:fixed!important;inset:14px!important;z-index:9999!important;margin:0!important;overflow:auto!important;background:#fff!important;border:1px solid #cbd5e1!important;border-radius:16px!important;padding:14px!important;box-shadow:0 24px 80px rgba(15,23,42,.2)!important}.result-header{display:flex;align-items:center;gap:8px;margin-bottom:8px;position:sticky;top:0;z-index:3;background:inherit;padding:2px 0 8px}.result-title{font-weight:950}.window-controls{display:flex;gap:6px;margin-left:auto}.window-btn{width:30px;height:30px;border:1px solid #cbd5e1;border-radius:8px;background:#f8fafc;color:#0f172a;font-weight:950;cursor:pointer;display:inline-flex;align-items:center;justify-content:center}.window-btn:hover{background:#e2e8f0}.window-btn.close:hover{background:#fee2e2;border-color:#f87171;color:#b91c1c}.result-panel.collapsed .result-header{margin-bottom:0}.body-lock{overflow:hidden}
.share-block{border:1px solid #e2e8f0;border-radius:12px;padding:10px;margin:10px 0;background:#fff}.share-block h3{margin:0 0 8px;font-size:14px}.share-block textarea,.output-box textarea{width:100%;min-height:172px;background:#fff;color:#0f172a;border:1px solid #cbd5e1;border-radius:10px;padding:12px;font-family:inherit;font-size:13px;line-height:1.6}.small-btn{border:1px solid #cbd5e1;background:#f8fafc;color:#0f172a;border-radius:8px;padding:7px 9px;font-size:12px;font-weight:850;cursor:pointer}.small-btn:hover{background:#e2e8f0}
.json-set{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:14px;margin:12px 0}.json-set h3{margin:0 0 10px;font-size:16px}.manual-note{font-size:12px;color:#64748b;line-height:1.5;margin:6px 0 10px}.manual-text{width:100%;min-height:120px;background:#fff;color:#0f172a;border:1px solid #cbd5e1;border-radius:10px;padding:10px;font-family:inherit;font-size:12px;line-height:1.5;margin-bottom:10px}.ai-demo-box{background:#f8fafc;border:1px solid #dbeafe;border-radius:14px;padding:14px;margin:10px 0 14px}.ai-demo-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:9px}.ai-badge{font-size:11px;font-weight:950;background:#fff7ed;color:#c2410c;border:1px solid #fed7aa;padding:4px 8px;border-radius:999px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.form-field{display:flex;flex-direction:column;gap:5px}.form-field.full{grid-column:1/-1}.form-field label{font-size:11px;color:#334155;font-weight:900}.form-field input,.form-field textarea,.form-field select{width:100%;background:#fff;color:#0f172a;border:1px solid #cbd5e1;border-radius:9px;padding:9px;font-family:inherit;font-size:12px}.form-field textarea{min-height:76px;line-height:1.5;resize:vertical}.demo-actions{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px}.json-preview{width:100%;min-height:300px;background:#fff;color:#0f172a;border:1px solid #cbd5e1;border-radius:10px;padding:12px;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;line-height:1.6;resize:vertical}
section{margin-bottom:24px}section h2{font-size:18px;font-weight:950;margin:0 0 12px;padding-bottom:8px;letter-spacing:-.02em}.count-badge{font-size:12px;padding:3px 8px;border-radius:999px;margin-left:5px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:14px;transition:.2s;min-height:250px;display:flex;flex-direction:column;box-shadow:0 6px 18px rgba(15,23,42,.04)}.card:hover{border-color:#93c5fd;transform:translateY(-2px)}.card.selected{outline:2px solid #2563eb;background:#eff6ff}.new-card{border-color:#fda4af}.select-top{display:flex;align-items:center;gap:7px;font-size:11px;font-weight:850;color:#334155;margin-bottom:9px}.check-label{display:flex;align-items:center;gap:5px;cursor:pointer}.check-label input{width:16px;height:16px;accent-color:#2563eb}.select-toggle{border:1px solid #93c5fd;background:#eff6ff;color:#1d4ed8;border-radius:999px;padding:5px 9px;font-size:11px;font-weight:900;cursor:pointer}.card.selected .select-toggle{background:#2563eb;color:#fff}.desc-pill{margin-left:auto;border-radius:999px;padding:3px 7px;font-size:10px;font-weight:900}.desc-pill.text{background:#ecfdf5;color:#047857}.desc-pill.manual{background:#fffbeb;color:#b45309}.platform-line{display:flex;align-items:center;gap:6px;color:#334155;font-size:12px;font-weight:850}.platform-dot{width:7px;height:7px;border-radius:50%;display:inline-block}.status-badge{margin-left:auto;font-size:10px;font-weight:900;padding:3px 7px;border-radius:999px}.status-badge.new{background:#ffe4e6;color:#be123c}.status-badge.yesterday{background:#e0f2fe;color:#0369a1}.status-badge.baseline{background:#fef3c7;color:#b45309}.status-badge.old{background:#f1f5f9;color:#64748b}.category-line{font-size:17px;font-weight:950;line-height:1.05;letter-spacing:-.03em;margin:6px 0}.date-line{display:flex;gap:6px;flex-wrap:wrap;font-size:11px;color:#b45309}.date-line span{background:#fffbeb;padding:4px 7px;border-radius:7px}.date-line b{color:#92400e;margin-right:4px}.job-title{font-weight:900;font-size:14px;color:#0f172a;line-height:1.42;margin:12px 0 10px;letter-spacing:-.02em}.job-meta{display:flex;flex-direction:column;gap:5px;margin-bottom:12px;color:#334155;font-size:12px;line-height:1.4}.job-meta div{display:grid;grid-template-columns:56px 1fr;gap:7px;align-items:start}.job-meta span{color:#64748b;font-size:11px;font-weight:850}.btn{margin-top:auto;display:inline-flex;align-items:center;justify-content:center;width:100%;text-decoration:none;color:#1d4ed8;background:#eff6ff;border:1px solid #93c5fd;padding:9px 10px;border-radius:10px;font-weight:900;font-size:13px}.empty-mini,.empty-box{background:#f8fafc;border:1px dashed #cbd5e1;border-radius:14px;padding:18px;color:#64748b;text-align:center}.ft{text-align:center;margin-top:28px;padding-top:14px;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b}
@media(max-width:640px){body{padding:12px}.grid{grid-template-columns:1fr}.tool-panel{position:relative;top:auto}.form-grid{grid-template-columns:1fr}.form-field.full{grid-column:auto}}
</style>
</head>
<body>
<div class="wrap">
  <header class="hdr">
    <h1>📋 채용공고 모음</h1>
    <div class="sub">수집 시각 __NOW_STR__<br>카톡 공유글과 Figma용 JSON은 선택한 공고를 기준으로 생성됩니다. · JSON 전용 라이트 버전</div>
    <span class="badge total">최근 7일 __TOTAL__건</span>
    <span class="badge new">오늘 신규 __NEW__건</span>
    <span class="badge yesterday">어제 발견 __YESTERDAY__건</span>
  </header>
  <div class="summary">__SUMMARY__</div>
  <section class="filter-panel" id="job-filters">
    <h2>🔎 공고 필터</h2>
    <div class="filter-group">
      <div class="filter-label">플랫폼</div>
      <div class="filter-options">
        <label class="filter-chip"><input type="checkbox" class="job-filter platform-filter" value="원티드" checked><span>원티드</span></label>
        <label class="filter-chip"><input type="checkbox" class="job-filter platform-filter" value="링커리어" checked><span>링커리어</span></label>
      </div>
    </div>
    <div class="filter-group">
      <div class="filter-label">발견일</div>
      <div class="filter-options">
        <label class="filter-chip"><input type="checkbox" class="job-filter day-filter" value="0" checked><span>오늘</span></label>
        <label class="filter-chip"><input type="checkbox" class="job-filter day-filter" value="1" checked><span>어제</span></label>
        <label class="filter-chip"><input type="checkbox" class="job-filter day-filter" value="2"><span>2일 전</span></label>
        <label class="filter-chip"><input type="checkbox" class="job-filter day-filter" value="3"><span>3일 전</span></label>
        <label class="filter-chip"><input type="checkbox" class="job-filter day-filter" value="4"><span>4일 전</span></label>
        <label class="filter-chip"><input type="checkbox" class="job-filter day-filter" value="5"><span>5일 전</span></label>
        <label class="filter-chip"><input type="checkbox" class="job-filter day-filter" value="6"><span>6일 전</span></label>
      </div>
    </div>
    <div class="filter-footer">
      <div class="filter-result">표시 중 <b id="filterResultCount">0</b>건</div>
      <span class="filter-note">기본값: 플랫폼 전체 · 오늘/어제</span>
      <button class="small-btn" id="resetJobFilters" type="button">기본값으로</button>
    </div>
  </section>
  <div id="filterEmpty" class="empty-box filter-empty" hidden>선택한 조건에 맞는 공고가 없습니다.</div>
  <section class="tool-panel" id="share-tools">
    <h2>✅ 공유글 / JSON 생성</h2>
    <div class="tool-row">
      <div class="selected-count">선택한 공고 <b id="selectedCount">0</b>개 · 카톡은 선택한 공고 전체, JSON은 첫 번째 선택 공고 1개로 생성</div>
      <button class="tool-btn secondary" id="clearSelection" type="button">선택 해제</button>
      <button class="tool-btn" id="makeAllKakao" type="button" disabled>선택 공고 카톡 공유글 생성</button>
      <button class="tool-btn pink" id="makeJson" type="button" disabled>선택 공고 JSON 생성</button>
    </div>
    <p class="hint">카톡 공유글에는 선택한 공고만 들어갑니다. 여러 직무를 선택하면 직무별로 나뉘며, 선택한 공고 수만큼 모두 포함됩니다. JSON은 첫 번째 선택 공고 1개로 생성됩니다.</p>
    <div class="output-box result-panel" id="kakaoBox">
      <div class="result-header">
        <span class="result-title">선택 공고 카톡 공유글</span>
        <button class="small-btn" id="copyAllKakao" type="button">전체 복사</button>
        <div class="window-controls">
          <button class="window-btn" type="button" title="접기/펼치기" onclick="toggleResultCollapse('kakaoBox', this)">—</button>
          <button class="window-btn" type="button" title="전체화면/복원" onclick="toggleResultFullscreen('kakaoBox', this)">□</button>
          <button class="window-btn close" type="button" title="닫기" onclick="closeResultPanel('kakaoBox')">×</button>
        </div>
      </div>
      <div class="result-body"><div id="shareBlocks"></div></div>
    </div>
    <div class="json-zone result-panel" id="jsonZone"></div>
  </section>
  __CATEGORY_SECTIONS__
  <footer class="ft">Generated by Selenium job crawler · OCR은 하지 않습니다. 상세내용 영역에서 텍스트만 자동 저장하고, 이미지형 공고는 JSON 필드를 직접 입력하면 됩니다.</footer>
</div>
<script>
const JOBS = __JOBS_JSON__;
const CATEGORY_ORDER = ["마케팅", "기획", "인사", "영업", "개발"];
const CATEGORY_LABELS = {"기획":"기획/운영", "마케팅":"마케팅", "인사":"인사", "영업":"영업", "개발":"개발"};
const JOB_MAP = new Map(JOBS.map(j => [j.fingerprint, j]));
const selectedFps = new Set();
function checkedValues(selector){return new Set(Array.from(document.querySelectorAll(selector+':checked')).map(el=>el.value));}
function applyJobFilters(){
  const platforms=checkedValues('.platform-filter');
  const days=new Set(Array.from(document.querySelectorAll('.day-filter:checked')).map(el=>Number(el.value)));
  const counts=Object.fromEntries(CATEGORY_ORDER.map(cat=>[cat,0]));
  let visibleCount=0;
  document.querySelectorAll('.card').forEach(card=>{
    const daysAgo=Number(card.dataset.daysAgo);
    const show=platforms.has(card.dataset.platform)&&days.has(daysAgo);
    card.hidden=!show;
    if(show){visibleCount+=1; const cat=card.dataset.category||''; counts[cat]=(counts[cat]||0)+1;}
  });
  document.querySelectorAll('.cat-section').forEach(section=>{
    const cat=section.dataset.category||'';
    const count=counts[cat]||0;
    section.hidden=count===0;
    const badge=section.querySelector('.count-badge');
    if(badge)badge.textContent=`${count}건`;
  });
  document.querySelectorAll('.sum-card').forEach(card=>{
    const cat=card.dataset.category||'';
    const count=counts[cat]||0;
    card.hidden=count===0;
    const num=card.querySelector('.sum-num');
    const label=card.querySelector('.sum-label');
    if(num)num.textContent=String(count);
    if(label)label.textContent=`${cat} · 필터 결과 ${count}건`;
  });
  document.getElementById('filterResultCount').textContent=String(visibleCount);
  document.getElementById('filterEmpty').hidden=visibleCount!==0;
}
function resetJobFilters(){
  document.querySelectorAll('.platform-filter').forEach(el=>{el.checked=true;});
  document.querySelectorAll('.day-filter').forEach(el=>{el.checked=el.value==='0'||el.value==='1';});
  applyJobFilters();
}
function escapeHtml(str){return String(str || '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));}
function escapeRegExp(str){return String(str || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');}
function cleanTitle(job){let title=(job.title||'').trim(); const company=(job.company||'').trim(); if(company){title=title.replace(new RegExp('^\\['+escapeRegExp(company)+'\\]\\s*'),''); title=title.replace(new RegExp('^'+escapeRegExp(company)+'\\s*'),'');} return title || '공고명 확인 필요';}
function displayDeadline(job){const d=(job.deadline||'').trim().replace(/\s*\(?공고 확인 요망\)?\s*/g,' ').trim(); if(!d)return '마감일 확인 필요'; if(d.includes('채용 시')||d.includes('채용시')||d.includes('상시')) return '채용 시 마감'; return d;}
function byCategory(cat){return JOBS.filter(j => j.category === cat);}
function getSelectedJobs(){return Array.from(selectedFps).map(fp => JOB_MAP.get(fp)).filter(Boolean);}
function updateSelectionUI(){document.getElementById('selectedCount').textContent = selectedFps.size; const none=selectedFps.size === 0; document.getElementById('makeJson').disabled = none; document.getElementById('makeAllKakao').disabled = none; document.querySelectorAll('.card').forEach(card => {const fp=card.dataset.fp; const on=selectedFps.has(fp); card.classList.toggle('selected', on); const cb=card.querySelector('.job-check'); if(cb) cb.checked=on; const btn=card.querySelector('.select-toggle'); if(btn) btn.textContent=on?'선택됨':'공고 선택';});}
function toggleFp(fp, force){if(!fp) return; if(force === true) selectedFps.add(fp); else if(force === false) selectedFps.delete(fp); else if(selectedFps.has(fp)) selectedFps.delete(fp); else selectedFps.add(fp); updateSelectionUI();}
function kakaoTextFor(cat, jobs){const label=CATEGORY_LABELS[cat] || cat; const picks = jobs; const head=`오늘의 ${label} 공고 추천!🚨`; if(!picks.length) return `${head}\n선택한 공고가 없습니다.`; const items=picks.map(job => `📍 [${job.company || '회사명 확인 필요'}] ${cleanTitle(job)}\n📌 근무 형태 : ${job.job_type || '근무 형태 확인 필요'}\n⏰ 마감 기한 : ${displayDeadline(job)}\n👉 ${job.link || job.detail_url || ''}`); return `${head}\n${items.join('\n\n')}`;}
function renderShareBlocks(){const selected=getSelectedJobs(); if(!selected.length){alert('카톡 공유글로 만들 공고를 선택해주세요.'); return;} const grouped=new Map(); selected.forEach(job=>{const cat=job.category || '기타'; if(!grouped.has(cat)) grouped.set(cat, []); grouped.get(cat).push(job);}); const orderedCats=[...CATEGORY_ORDER.filter(cat=>grouped.has(cat)), ...Array.from(grouped.keys()).filter(cat=>!CATEGORY_ORDER.includes(cat))]; const box=document.getElementById('shareBlocks'); box.innerHTML = orderedCats.map(cat => {const text=kakaoTextFor(cat, grouped.get(cat) || []); return `<div class="share-block"><div class="tool-row" style="margin-bottom:8px;"><h3>${escapeHtml(CATEGORY_LABELS[cat] || cat)}</h3><button class="small-btn copy-one" type="button">복사</button></div><textarea>${escapeHtml(text)}</textarea></div>`;}).join(''); document.getElementById('kakaoBox').classList.add('active'); document.getElementById('kakaoBox').scrollIntoView({behavior:'smooth', block:'center'});}
function textSnippet(job, fallback){const t=(job.description_text || '').replace(/\s+/g,' ').trim(); if(!t) return fallback; return t.slice(0,160)+(t.length>160?'...':'');}
function hookText(job){return `${job.company || '오늘의 기업'}에서,\n${cleanTitle(job)} 채용!`;}
function roleText(job){return cleanTitle(job).replace(/\s+/g,' ').slice(0,60) || (job.raw_category || job.category || '직무 확인 필요');}
function repairBrokenUnits(text){return String(text||'').replace(/(\d+(?:[.,]\d+)?)\s*\n\s*(개월|년|주|일|시간|분|명|만원|원|%)/g,'$1$2');}
function cleanListPrefix(value){return String(value||'').replace(/\r/g,'').trim().replace(/^\s*(?:(?:[-–—*•▪◦])\s*)+/,'').replace(/^\s*\d{1,2}\s*[.)]\s+/,'').trim();}
function isKnownSectionLine(value){const c=cleanListPrefix(value).replace(/^\s*[\[【(]\s*/,'').replace(/\s*[\]】)]\s*$/,'').replace(/\s+/g,'').replace(/[:：]+$/,''); return /^(담당업무|주요업무|업무내용|담당직무|직무내용|주요역할|지원자격|자격요건|필수요건|필요요건|지원요건|고용조건|고용형태|근무조건|근무기간|근무시간|채용형태|계약형태|우대사항|우대자격|우대요건|우대조건|선호사항|선호요건|포지션상세|포지션소개|혜택및복지|복리후생|혜택|채용전형|전형절차|채용절차|채용프로세스|근무지|근무지역|접수방법|기타사항|회사소개|채용정보|모집기간|경력)$/i.test(c);}
function bracketDelta(value){const s=String(value||''); const open=(s.match(/[([{]/g)||[]).length; const close=(s.match(/[)\]}]/g)||[]).length; return open-close;}
function joinListFragment(left,right){const a=String(left||'').trim(); const b=String(right||'').trim(); if(!a)return b; if(!b)return a; const noSpace=/[([{\/]$/.test(a)||/^[)\]}\/,.:;]/.test(b); return a+(noSpace?'':' ')+b;}
function splitItems(text){
  let raw=repairBrokenUnits(text).replace(/\r/g,'');
  // 같은 줄에 이어진 실제 글머리표만 새 줄로 바꿉니다. 괄호 안의 '/'·','는 건드리지 않습니다.
  raw=raw.replace(/([^\n])\s+([•▪◦])\s+(?=\S)/g,'$1\n$2 ');
  const lines=raw.split(/\n/).map(v=>v.trim()).filter(Boolean);
  const items=[]; let current=''; let depth=0;
  const flush=()=>{const v=cleanListPrefix(current); if(v)items.push(v); current=''; depth=0;};
  for(const line of lines){
    const marked=/^\s*(?:(?:[-–—*•▪◦])+|\d{1,2}\s*[.)])\s*/.test(line);
    const cleaned=cleanListPrefix(line);
    if(!cleaned)continue;
    if(isKnownSectionLine(line)){flush(); items.push(cleaned); continue;}
    if(marked){flush(); current=cleaned; depth=Math.max(0,bracketDelta(cleaned)); continue;}
    if(!current){current=cleaned; depth=Math.max(0,bracketDelta(cleaned)); continue;}
    const punctuationOnly=/^[()[\]{},/·:;]+$/.test(cleaned);
    const shortAcronym=/^[A-Za-z][A-Za-z0-9+.#/-]{0,9}$/.test(current.trim());
    if(depth>0||punctuationOnly||shortAcronym||/^[)\]}\/,.:;]/.test(cleaned)){
      current=joinListFragment(current,cleaned);
      depth=Math.max(0,depth+bracketDelta(cleaned));
    }else{
      flush(); current=cleaned; depth=Math.max(0,bracketDelta(cleaned));
    }
  }
  flush(); return items;
}
function uniqueItems(items, max=20){const seen=new Set(); return items.filter(v=>{const k=v.replace(/\s+/g,''); if(!k||seen.has(k)) return false; seen.add(k); return true;}).slice(0,max);}
function normalizeSectionLabel(line){return cleanListPrefix(line).replace(/^\s*[\[【(]\s*/,'').replace(/\s*[\]】)]\s*$/,'').replace(/\s+/g,'').replace(/[:：]+$/,'');}
function detectJobSection(line){
  const compact=normalizeSectionLabel(line);
  if(/^(담당업무|주요업무|업무내용|담당직무|직무내용|주요역할|Responsibilities?)$/i.test(compact)) return 'duties';
  if(/^(지원자격|자격요건|필수요건|필요요건|지원요건|Qualifications?|Requirements?)$/i.test(compact)) return 'requirements';
  if(/^(고용조건|고용형태|근무조건|근무기간|근무시간|채용형태|계약형태)$/i.test(compact)) return 'employment';
  if(/^(우대사항|우대자격|우대요건|우대조건|선호사항|선호요건|PreferredQualifications?|PreferredRequirements?)$/i.test(compact)) return 'preferred';
  if(/^(포지션상세|포지션소개|혜택및복지|복리후생|혜택|채용전형|전형절차|채용절차|채용프로세스|근무지|근무지역|접수방법|기타사항|회사소개|채용정보|모집기간|경력)$/i.test(compact)) return 'ignore';
  return '';
}
function classifyDemoText(raw, job){
  const lines=splitItems(raw);
  const duties=[], requirements=[], employment=[], preferred=[];
  const dutyRe=/(담당|업무|기획|운영|제작|관리|지원|분석|모니터링|작성|개선|진행|협업|리서치|온보딩)/;
  const prefRe=/(우대|경험이?\s*있는|경력|활용.*경험|이해도가?\s*높|능숙|전공자|포트폴리오)/;
  const reqRe=/(필수|자격|가능하신|가능한|근무 가능|커뮤니케이션|영어|성향|태도|역량|졸업|재학|책임감)/;
  let section='', structured=false;
  for(const line of lines){
    const nextSection=detectJobSection(line);
    if(nextSection){section=nextSection; structured=true; continue;}
    if(section==='duties'){duties.push(line); continue;}
    if(section==='requirements'){requirements.push(line); continue;}
    if(section==='employment'){employment.push(line); continue;}
    if(section==='preferred'){preferred.push(line); continue;}
    if(section==='ignore') continue;
    if(!structured){
      if(prefRe.test(line)) preferred.push(line);
      else if(reqRe.test(line)) requirements.push(line);
      else if(dutyRe.test(line)) duties.push(line);
    }
  }
  const title=cleanTitle(job);
  const parsedEmployment=uniqueItems(employment,5).join(' · ');
  const storedEmployment=String(job.job_type||'').trim();
  const safeStoredEmployment=/확인 필요|공고 확인 필요/.test(storedEmployment)?'':storedEmployment;
  const platform=String(job.platform||'').trim();
  // 링커리어는 상세 상단에 '채용형태'가 명시되어 있으므로 그 값을 그대로 우선 사용합니다.
  // 예: 계약직, 체험형 인턴, 채용연계형 인턴. 본문 문장 분류는 보조값으로만 씁니다.
  const resolvedEmployment=platform==='링커리어'
    ? (safeStoredEmployment||parsedEmployment)
    : (parsedEmployment||safeStoredEmployment);
  return {
    company: job.company||'회사명 확인 필요',
    role: title||job.raw_category||'채용 직무 확인 필요',
    employment: resolvedEmployment,
    deadline: displayDeadline(job),
    posts: '371',
    followers: '2.3만',
    following: '7',
    headline: `${job.company||'오늘의 기업'}에서\n${title||'인턴'} 채용 중!`,
    duties: uniqueItems(duties,20),
    requirements: uniqueItems(requirements,20),
    preferred: uniqueItems(preferred,20)
  };
}
function itemsToText(items){return (items||[]).map(v=>`• ${v}`).join('\n');}
function readJsonForm(fp){const q=id=>document.getElementById(id+'_'+fp); return {company:q('f_company').value.trim(),role:q('f_role').value.trim(),employment:q('f_employment').value.trim(),deadline:q('f_deadline').value.trim(),headline:q('f_headline').value.trim(),duties:splitItems(q('f_duties').value),requirements:splitItems(q('f_requirements').value),preferred:splitItems(q('f_preferred').value),posts:(q('f_posts').value||'371').trim(),followers:(q('f_followers').value||'2.3만').trim(),following:(q('f_following').value||'7').trim()};}
function jsonPayload(fp){const d=readJsonForm(fp); return {company:d.company,headline:d.headline,role:d.role,employment:d.employment,deadline:d.deadline,duties:d.duties,requirements:d.requirements,preferred:d.preferred,posts:d.posts,followers:d.followers,following:d.following};}
function refreshJsonPreview(fp){const preview=document.getElementById('json_preview_'+fp); if(!preview)return ''; const text=JSON.stringify(jsonPayload(fp),null,2); preview.value=text; return text;}
function applyDemoAI(job){const fp=job.fingerprint; const raw=document.getElementById('source_'+fp).value; const d=classifyDemoText(raw,job); const set=(id,val)=>document.getElementById(id+'_'+fp).value=val; set('f_company',d.company);set('f_role',d.role);set('f_employment',d.employment);set('f_deadline',d.deadline);set('f_posts',d.posts||'371');set('f_followers',d.followers||'2.3만');set('f_following',d.following||'7');set('f_headline',d.headline);set('f_duties',itemsToText(d.duties));set('f_requirements',itemsToText(d.requirements));set('f_preferred',itemsToText(d.preferred));refreshJsonPreview(fp); alert('API 미연결 예시 문구가 생성되었습니다. 실제 GPT API 연결 시 이 단계만 API 응답으로 교체하면 됩니다.');}
function closeResultPanel(id){const el=document.getElementById(id);if(!el)return;el.classList.remove('active','fullscreen','collapsed');document.body.classList.remove('body-lock');if(id==='jsonZone')el.innerHTML='';}
function toggleResultCollapse(id,btn){const el=document.getElementById(id);if(!el)return;if(el.classList.contains('fullscreen')){el.classList.remove('fullscreen');document.body.classList.remove('body-lock');}el.classList.toggle('collapsed');if(btn)btn.textContent=el.classList.contains('collapsed')?'＋':'—';}
function toggleResultFullscreen(id,btn){const el=document.getElementById(id);if(!el)return;el.classList.remove('collapsed');el.classList.toggle('fullscreen');document.body.classList.toggle('body-lock',el.classList.contains('fullscreen'));if(btn)btn.textContent=el.classList.contains('fullscreen')?'❐':'□';}
async function copyFigmaJson(fp){const text=refreshJsonPreview(fp); try{await navigator.clipboard.writeText(text); alert('Figma용 JSON 복사 완료');}catch(e){prompt('아래 JSON을 복사해주세요.',text);}}
function createJsonEditor(job){const fp=job.fingerprint; const demo=classifyDemoText(job.description_text||'',job); const typeText=job.description_type==='text'?'크롤링된 텍스트를 원문 입력칸에 넣었습니다.':'이미지 공고는 원문을 보며 필요한 내용을 붙여넣은 뒤 예시 문구 생성 버튼을 누르세요.'; return `<article class="json-set"><h3>[${escapeHtml(job.company||'회사명 확인 필요')}] ${escapeHtml(cleanTitle(job))}</h3><p class="manual-note">${typeText}</p><div class="ai-demo-box"><div class="ai-demo-head"><b>1. 공고 원문 입력</b><span class="ai-badge">GPT API 미연결 · 데모 로직</span></div><textarea id="source_${fp}" class="manual-text" placeholder="공고 내용을 순서 없이 붙여넣어도 됩니다. 예: 담당업무, 필요요건, 우대사항, 근무기간, 마감일 등">${escapeHtml(job.description_text||'')}</textarea><div class="demo-actions"><button class="tool-btn" type="button" onclick="applyDemoAI(JOB_MAP.get('${fp}'))">✨ AI 문구 생성 예시</button><button class="tool-btn secondary" type="button" onclick="refreshJsonPreview('${fp}')">JSON 새로고침</button><button class="tool-btn secondary" type="button" onclick="copyFigmaJson('${fp}')">Figma용 JSON 복사</button></div><div class="ai-demo-head"><b>2. 구조화 결과 확인·수정</b></div><div class="form-grid"><div class="form-field"><label>회사명</label><input id="f_company_${fp}" value="${escapeHtml(demo.company)}"></div><div class="form-field"><label>채용 직무</label><input id="f_role_${fp}" value="${escapeHtml(demo.role)}"></div><div class="form-field"><label>고용 형태</label><input id="f_employment_${fp}" value="${escapeHtml(demo.employment)}"></div><div class="form-field"><label>마감 기한</label><input id="f_deadline_${fp}" value="${escapeHtml(demo.deadline)}"></div><div class="form-field full"><label>헤드라인</label><textarea id="f_headline_${fp}">${escapeHtml(demo.headline)}</textarea></div><div class="form-field full"><label>담당 업무</label><textarea id="f_duties_${fp}">${escapeHtml(itemsToText(demo.duties))}</textarea></div><div class="form-field full"><label>필요 요건</label><textarea id="f_requirements_${fp}">${escapeHtml(itemsToText(demo.requirements))}</textarea></div><div class="form-field full"><label>우대 요건</label><textarea id="f_preferred_${fp}">${escapeHtml(itemsToText(demo.preferred))}</textarea></div><div class="form-field"><label>P3 게시물</label><input id="f_posts_${fp}" value="${escapeHtml(demo.posts||'371')}"></div><div class="form-field"><label>P3 팔로워</label><input id="f_followers_${fp}" value="${escapeHtml(demo.followers||'2.3만')}"></div><div class="form-field"><label>P3 팔로잉</label><input id="f_following_${fp}" value="${escapeHtml(demo.following||'7')}"></div></div><div class="ai-demo-head" style="margin-top:14px"><b>3. JSON 결과</b><span class="ai-badge">입력값 수정 시 자동 반영</span></div><textarea id="json_preview_${fp}" class="json-preview" readonly></textarea></div></article>`;}
document.addEventListener('click', e => {const btn=e.target.closest('.select-toggle'); if(btn){toggleFp(btn.dataset.fp); return;} if(e.target.classList.contains('copy-one')){const t=e.target.closest('.share-block').querySelector('textarea').value; navigator.clipboard.writeText(t).then(()=>alert('복사 완료')).catch(()=>alert('textarea 내용을 직접 복사해주세요.'));}});
document.addEventListener('change', e => {if(e.target.classList.contains('job-check')) toggleFp(e.target.value, e.target.checked); if(e.target.classList.contains('job-filter')) applyJobFilters();});
document.getElementById('resetJobFilters').addEventListener('click', resetJobFilters);
document.getElementById('clearSelection').addEventListener('click', () => {selectedFps.clear(); updateSelectionUI();});
document.getElementById('makeAllKakao').addEventListener('click', renderShareBlocks);
document.getElementById('copyAllKakao').addEventListener('click', async () => {const text=Array.from(document.querySelectorAll('#shareBlocks textarea')).map(t=>t.value).join('\n\n--------------------\n\n'); try{await navigator.clipboard.writeText(text); alert('전체 복사 완료');}catch(e){alert('복사가 안 되면 각 textarea 내용을 직접 복사해주세요.');}});
document.getElementById('makeJson').addEventListener('click', () => {const job=getSelectedJobs()[0]; if(!job){alert('JSON으로 만들 공고 1개를 선택해주세요.'); return;} const zone=document.getElementById('jsonZone'); zone.innerHTML=`<div class="result-header"><span class="result-title">Figma용 JSON</span><div class="window-controls"><button class="window-btn" type="button" title="접기/펼치기" onclick="toggleResultCollapse('jsonZone', this)">—</button><button class="window-btn" type="button" title="전체화면/복원" onclick="toggleResultFullscreen('jsonZone', this)">□</button><button class="window-btn close" type="button" title="닫기" onclick="closeResultPanel('jsonZone')">×</button></div></div><div class="result-body"><p class="manual-note">이미지 미리보기 없이 구조화된 JSON만 생성합니다. 필드를 수정하면 아래 JSON이 자동으로 갱신됩니다.</p>${createJsonEditor(job)}</div>`; zone.classList.remove('collapsed','fullscreen'); document.body.classList.remove('body-lock'); zone.classList.add('active'); zone.querySelectorAll('input, textarea').forEach(el=>{if(el.id.startsWith('f_')) el.addEventListener('input',()=>refreshJsonPreview(job.fingerprint));}); refreshJsonPreview(job.fingerprint); zone.scrollIntoView({behavior:'smooth', block:'start'});});
updateSelectionUI();
applyJobFilters();
</script>
</body>
</html>"""
    index_html = (template
        .replace('__NOW_STR__', html.escape(now_str))
        .replace('__TOTAL__', str(len(jobs)))
        .replace('__NEW__', str(len(new_jobs)))
        .replace('__YESTERDAY__', str(len(yesterday_jobs)))
        .replace('__SUMMARY__', ''.join(summary_cards))
        .replace('__CATEGORY_SECTIONS__', ''.join(category_sections))
        .replace('__JOBS_JSON__', jobs_json)
    )
    (SITE_DIR / "index.html").write_text(index_html, encoding="utf-8")

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="링커리어·원티드 채용공고 통합 크롤러 FINAL v6")
    p.add_argument("--limit", type=int, default=0, help="직군별 수집 상한. 0이면 스크롤/페이지 범위에서 모두 수집")
    p.add_argument("--max-pages", type=int, default=10, help="링커리어 기준 데이터/증분 확인 최대 페이지 수")
    p.add_argument("--known-page-stop", type=int, default=1, help="링커리어에서 신규 없는 페이지가 연속 몇 번이면 멈출지")
    p.add_argument("--scroll", type=int, default=6, help="원티드·리스트 페이지 최대 스크롤 횟수")
    p.add_argument("--wanted-detail-delay", type=float, default=0.8, help="원티드 상세 페이지 로딩 후 추가 대기 초")
    p.add_argument("--save-wanted-debug", action="store_true", help="원티드 상세 페이지별 PNG/HTML 저장")
    p.add_argument(
        "--wanted-test-details",
        action="store_true",
        help="원티드 이력/기준 모드와 무관하게 목록 상단 공고를 상세 방문해 테스트",
    )
    p.add_argument("--headless", action="store_true", help="브라우저 숨김 모드")
    p.add_argument("--manual", action="store_true", help="링커리어 자동 클릭 대신 브라우저에서 직접 필터 선택")
    p.add_argument("--auto-only", action="store_true", help="링커리어 자동 클릭 실패 시 수동 선택으로 넘어가지 않음")
    p.add_argument("--init-seen", action="store_true", help="선택한 플랫폼·직군 이력을 모두 지우고 기준 데이터를 다시 생성")
    p.add_argument("--platform", choices=["all", "링커리어", "원티드"], default="all", help="수집할 플랫폼")
    p.add_argument("--category", choices=["all"] + CATEGORY_ORDER, default="all", help="수집할 담당자용 직군")
    return p.parse_args()


def reset_selected_seen_state(seen: dict, platforms: list[str], categories: list[str]) -> None:
    """선택한 플랫폼·직군만 기준 이력에서 제거합니다.

    예: --platform 원티드 --init-seen 실행 시 링커리어 이력은 보존합니다.
    """
    normalized_categories = {normalize_category_name(category) for category in categories}
    for fingerprint, record in list(_state_job_items(seen)):
        platform = str(record.get("platform") or "")
        category = normalize_category_name(str(record.get("category") or ""))
        if platform in platforms and category in normalized_categories:
            seen.pop(fingerprint, None)

    meta = seen.setdefault("__meta__", {"version": 3, "platforms": {}})
    platform_meta_map = meta.setdefault("platforms", {})
    for platform in platforms:
        platform_meta = platform_meta_map.get(platform)
        if not isinstance(platform_meta, dict):
            continue
        category_meta = platform_meta.setdefault("categories", {})
        for category in normalized_categories:
            category_meta.pop(category, None)
        if not category_meta:
            platform_meta.pop("initialized_at", None)


def main() -> int:
    args = parse_args()
    ensure_dirs()

    selected_platforms = ["링커리어", "원티드"] if args.platform == "all" else [args.platform]
    selected_categories = CATEGORY_ORDER if args.category == "all" else [args.category]
    seen = load_seen()
    if args.init_seen:
        reset_selected_seen_state(seen, selected_platforms, selected_categories)
    baseline_flags = {
        (platform, category): args.init_seen or not category_is_initialized(seen, platform, category)
        for platform in selected_platforms
        for category in selected_categories
    }
    known_fingerprints = {fp for fp, _ in _state_job_items(seen)}

    print("\n링커리어·원티드 채용공고 통합 크롤러 FINAL v6")
    print(f"- 플랫폼: {', '.join(selected_platforms)}")
    print(f"- 직군: {', '.join(selected_categories)}")
    for platform in selected_platforms:
        baseline_categories = [c for c in selected_categories if baseline_flags[(platform, c)]]
        incremental_categories = [c for c in selected_categories if not baseline_flags[(platform, c)]]
        if baseline_categories:
            print(f"- {platform} 기준 데이터 생성: {', '.join(baseline_categories)}")
        if incremental_categories:
            print(f"- {platform} 신규 증분 수집: {', '.join(incremental_categories)}")
    print(f"- 담당자 화면: 최초 발견일이 최근 {DISPLAY_WINDOW_DAYS}일인 공고")

    driver = None
    jobs_by_key: dict[tuple[str, str], list[Job]] = {
        (platform, category): []
        for platform in selected_platforms
        for category in selected_categories
    }
    errors_by_key: dict[tuple[str, str], int] = {key: 0 for key in jobs_by_key}

    try:
        driver = create_driver(headless=args.headless)
        for platform in selected_platforms:
            if platform == "원티드":
                wanted_baseline_categories = {
                    category for category in selected_categories
                    if baseline_flags[(platform, category)]
                }
                try:
                    wanted_jobs = collect_wanted_integrated(
                        driver=driver,
                        categories=selected_categories,
                        limit_per_category=args.limit,
                        scroll_times=args.scroll,
                        baseline_categories=wanted_baseline_categories,
                        known_fingerprints=known_fingerprints,
                        detail_delay=args.wanted_detail_delay,
                        save_each_detail=args.save_wanted_debug,
                        force_test_details=args.wanted_test_details,
                        headless=args.headless,
                    )
                    for job in wanted_jobs:
                        key = ("원티드", normalize_category_name(job.category))
                        if key in jobs_by_key:
                            jobs_by_key[key].append(job)
                except KeyboardInterrupt:
                    print("\n원티드 수집을 중단했습니다. 완료된 체크포인트는 data/wanted_partial_jobs.json에서 확인할 수 있습니다.")
                except Exception as exc:
                    print(f"  ❌ 원티드 수집 오류: {exc}")
                    for category in selected_categories:
                        errors_by_key[("원티드", category)] += 1
                    try:
                        save_debug(driver, "원티드", "전체", "error")
                    except Exception:
                        pass
                continue

            for category in selected_categories:
                key = (platform, category)
                try:
                    jobs = crawl_platform_category(
                        driver=driver,
                        platform=platform,
                        category=category,
                        limit=args.limit,
                        scroll_times=args.scroll,
                        max_pages=args.max_pages,
                        manual=args.manual,
                        auto_only=args.auto_only,
                        baseline_mode=baseline_flags[key],
                        known_fingerprints=known_fingerprints,
                        known_page_stop=args.known_page_stop,
                    )
                    jobs_by_key[key].extend(jobs)
                except KeyboardInterrupt:
                    print(f"\n{platform} {category} 수집을 중단했습니다.")
                    break
                except Exception as exc:
                    errors_by_key[key] += 1
                    print(f"  ❌ {platform} {category} 오류: {exc}")
                    try:
                        save_debug(driver, platform, category, "error")
                    except Exception:
                        pass
    except WebDriverException as exc:
        print(f"\n브라우저 실행 오류: {exc}")
        print("Chrome 설치 여부와 ChromeDriver 권한을 확인해 주세요.")
        return 1
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    total_new_count = 0
    baseline_display_jobs: list[Job] = []
    all_crawled_jobs: list[Job] = []

    for key, key_jobs in jobs_by_key.items():
        platform, category = key
        key_jobs = dedupe_jobs(key_jobs)
        all_crawled_jobs.extend(key_jobs)
        baseline_mode = baseline_flags[key]
        seen, new_count = register_crawled_jobs(
            key_jobs,
            seen,
            baseline_mode=baseline_mode,
            platform=platform,
        )
        total_new_count += new_count

        if baseline_mode:
            baseline_complete = errors_by_key[key] == 0 and bool(key_jobs)
            set_category_initialized(
                seen,
                platform,
                category,
                initialized=baseline_complete,
                note="baseline completed" if baseline_complete else "baseline incomplete",
            )
            if baseline_complete:
                baseline_display_jobs.extend(key_jobs)
            else:
                print(f"  ⚠️ {platform} {category} 기준 데이터가 비어 있거나 오류가 있어 다음 실행에서 다시 시도합니다.")

    save_seen(seen)

    recent_jobs = jobs_from_recent_discovery_window(seen, days=DISPLAY_WINDOW_DAYS - 1)
    if args.wanted_test_details:
        # 상세 강제 테스트에서는 신규 여부와 관계없이 방금 방문한 공고를 모두
        # CSV/JSON/웹 화면에 출력해 즉시 검수할 수 있게 합니다.
        display_jobs = dedupe_jobs(all_crawled_jobs)
    else:
        display_jobs = dedupe_jobs(baseline_display_jobs + recent_jobs)

    today_text = now_kst().date().isoformat()
    yesterday_text = (now_kst().date() - timedelta(days=1)).isoformat()

    def discovery_rank(job: Job) -> int:
        seen_date = (job.first_seen_at or "")[:10]
        try:
            return (now_kst().date() - date.fromisoformat(seen_date)).days
        except (TypeError, ValueError):
            return 999

    display_jobs.sort(key=lambda job: (
        discovery_rank(job),
        CATEGORY_ORDER.index(job.category) if job.category in CATEGORY_ORDER else 99,
        job.platform,
        job.company,
        job.title,
    ))

    write_json_csv(display_jobs)
    generate_html(display_jobs, init_seen=bool(baseline_display_jobs))

    print("\n완료")
    print(f"- 이번 크롤링 신규: {total_new_count}건")
    if baseline_display_jobs:
        print(f"- 이번에 생성한 기준 데이터 점검: {len(baseline_display_jobs)}건")
    print(f"- 담당자 화면 노출: {len(display_jobs)}건 (최근 {DISPLAY_WINDOW_DAYS}일 및 이번 기준 점검)")
    print(f"- HTML: {SITE_DIR / 'index.html'}")
    print(f"- CSV : {SITE_DIR / 'jobs.csv'}")
    print(f"- JSON: {SITE_DIR / 'jobs.json'}")
    print(f"- 이력 : {STATE_PATH}")
    print("\n운영 방식:")
    print("1) 플랫폼·직군별 최초 실행은 기존 공고 URL을 기준 데이터로 저장")
    print("2) 다음 실행부터 처음 보는 URL만 상세 페이지를 확인")
    print(f"3) 담당자 화면에는 최근 {DISPLAY_WINDOW_DAYS}일 안에 처음 발견한 공고를 표시")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
