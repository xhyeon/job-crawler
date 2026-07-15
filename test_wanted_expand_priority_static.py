#!/usr/bin/env python3
"""원티드 핵심 섹션이 보여도 더보기 컨트롤을 먼저 확인하는지 정적 검증합니다."""
from pathlib import Path

source = (Path(__file__).resolve().parent / "job_crawler.py").read_text(encoding="utf-8")
start = source.index("def expand_job_description")
end = source.index("def extract_detail_payload", start)
block = source[start:end]

assert "control = find_description_expand_control(driver)" in block
assert "if before_headings & core_headings:\n        return \"already_expanded\"\n\n    control" not in block
assert block.index("control = find_description_expand_control(driver)") < block.index("return \"already_expanded\"")
assert "더보기 컨트롤이 있으면 핵심 섹션이 이미 보여도 반드시 클릭" in block
print("PASS: 주요업무/자격요건이 먼저 보여도 더보기 컨트롤을 우선 클릭합니다.")
