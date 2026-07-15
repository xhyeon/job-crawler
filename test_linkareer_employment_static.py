#!/usr/bin/env python3
"""링커리어 상단 채용형태와 JSON 우선순위를 검증합니다."""
from pathlib import Path
from job_crawler import Job, parse_linkareer_detail_html

html = """
<html><body>
<h2 class="organization-name">테스트회사</h2>
<dl><dt>채용형태</dt><dd>계약직</dd></dl>
<dl><dt>모집직무</dt><dd>마케팅/광고</dd></dl>
<div>상세내용</div>
</body></html>
"""
fallback = Job(
    title="테스트 공고", company="", platform="링커리어", category="마케팅",
    deadline="", link="https://example.com/job"
)
job = parse_linkareer_detail_html(html, fallback)
assert job.job_type == "계약직", job.job_type

source = (Path(__file__).resolve().parent / "job_crawler.py").read_text(encoding="utf-8")
assert "platform==='링커리어'" in source
assert "? (safeStoredEmployment||parsedEmployment)" in source
print("PASS: 링커리어 채용형태 '계약직'을 그대로 저장하고 JSON에서 우선 사용합니다.")
