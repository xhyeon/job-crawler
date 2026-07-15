#!/usr/bin/env python3
"""링커리어 인라인 태그 조각이 한 문장으로 복원되는지 검증합니다."""
from bs4 import BeautifulSoup
from job_crawler import extract_linkareer_description_text

html = """
<section class="ActivityDetailTabContent_test">
  <h2>상세내용</h2>
  <div>
    <span>우대사항</span><br>
    <span>• </span><span>유관 자격증 소지자 우대</span><span>(</span><span>미국</span><span>/</span><span>영국</span><span>/</span><span>호주 회계사</span><span>,</span><span> 보험계리사</span><span>, CISA, CIA, CISM, SQLD 등</span><span>)</span><br>
    <span>• </span><span>IT</span><span> 관련한 경험 및 금융회사 경험 우대</span>
  </div>
</section>
"""
kind, text = extract_linkareer_description_text(BeautifulSoup(html, "html.parser"))
assert kind == "text", kind
assert "유관 자격증 소지자 우대(미국/영국/호주 회계사, 보험계리사, CISA, CIA, CISM, SQLD 등)" in text, text
assert "IT 관련한 경험 및 금융회사 경험 우대" in text, text
for fragment in ["\n(\n", "\n미국\n", "\n/\n", "\n영국\n", "\n,\n"]:
    assert fragment not in text, (fragment, text)
print("PASS: 링커리어 인라인 태그 조각을 문장으로 이어서 수집합니다.")
