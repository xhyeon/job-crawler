#!/usr/bin/env python3
"""고용조건 섹션의 존재/부재와 6개월 복구를 오프라인 검증합니다."""
from job_crawler import (
    extract_detail_payload_from_html,
    extract_employment_type,
)

HTML_WITH_EMPLOYMENT = """
<article class="JobDescription_JobDescription__sample">
  <h2>포지션 상세</h2>
  <div>
    <span>회사와 포지션 소개입니다.</span>
    <div><h3>우대사항</h3><span>• 협업 경험</span></div>
    <div><h3>고용조건</h3><span>• 정규직전환형<br>• 인턴 6<br>개월</span></div>
    <div><h3>자격요건</h3><span>• 웹 개발 경험</span></div>
    <div><h3>주요업무</h3><span>• 신규 기능 개발</span></div>
  </div>
</article>
"""

HTML_WITHOUT_EMPLOYMENT = """
<article class="JobDescription_JobDescription__sample">
  <h2>포지션 상세</h2>
  <div>
    <span>회사와 포지션 소개입니다.</span>
    <div><h3>주요업무</h3><span>• 신규 기능 개발</span></div>
    <div><h3>자격요건</h3><span>• 웹 개발 경험</span></div>
    <div><h3>우대사항</h3><span>• 협업 경험</span></div>
  </div>
</article>
"""

with_payload = extract_detail_payload_from_html(HTML_WITH_EMPLOYMENT)
with_sections = with_payload.get("sections") or {}
with_value = extract_employment_type(
    with_sections,
    str(with_payload.get("article_text") or ""),
    "테스트 포지션",
)
assert "정규직전환형" in with_value
assert "인턴 6개월" in with_value

without_payload = extract_detail_payload_from_html(HTML_WITHOUT_EMPLOYMENT)
without_sections = without_payload.get("sections") or {}
without_value = extract_employment_type(
    without_sections,
    str(without_payload.get("article_text") or ""),
    "테스트 포지션",
)
assert without_value == ""

print("PASS: 고용조건이 있으면 제목 기준으로 수집했습니다.")
print("PASS: 인턴 6\\n개월을 인턴 6개월로 복구했습니다.")
print("PASS: 고용조건이 없는 공고는 오류 없이 빈 값으로 유지했습니다.")
