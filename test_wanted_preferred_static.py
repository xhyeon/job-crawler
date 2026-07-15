#!/usr/bin/env python3
"""우대사항 제목 매핑, 순서 독립성, 숫자+단위 복구를 오프라인으로 검증합니다."""
from job_crawler import extract_detail_payload_from_html, repair_broken_number_units

HTML = """
<article class="JobDescription_JobDescription__sample">
  <h2>포지션 상세</h2>
  <div>
    <span>회사와 포지션 소개입니다.</span>
    <div><h3>우대사항</h3><span>• 제품 경험 3년 이상<br>• 협업 경험</span></div>
    <div><h3>자격요건</h3><span>• 웹 개발 경험</span></div>
    <div><h3>고용조건</h3><span>• 인턴 6<br>개월</span></div>
    <div><h3>주요업무</h3><span>• 신규 기능 개발</span></div>
  </div>
</article>
"""

payload = extract_detail_payload_from_html(HTML)
sections = payload.get("sections") or {}

assert payload.get("article_found") is True
assert "제품 경험 3년 이상" in sections.get("우대사항", "")
assert "웹 개발 경험" in sections.get("자격요건", "")
assert "신규 기능 개발" in sections.get("주요업무", "")
assert repair_broken_number_units(sections.get("고용조건", "")).find("6개월") >= 0

print("PASS: 우대사항/자격요건/주요업무를 순서와 무관하게 제목으로 분리했습니다.")
print("PASS: 6\\n개월을 6개월로 복구했습니다.")
