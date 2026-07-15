#!/usr/bin/env python3
"""카카오톡 공유글이 선택한 공고 집합을 사용하는지 정적 검증합니다."""
from pathlib import Path

source = (Path(__file__).resolve().parent / "job_crawler.py").read_text(encoding="utf-8")
assert "function getSelectedJobs()" in source
assert "function renderShareBlocks(){const selected=getSelectedJobs();" in source
assert "const picks = jobs.slice(0, 2)" not in source
print("PASS: 카카오톡 공유글은 선택한 공고만 사용합니다.")
