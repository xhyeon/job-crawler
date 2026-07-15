#!/bin/zsh
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt
python3 job_crawler.py \
  --platform all \
  --category 마케팅 \
  --limit 10 \
  --max-pages 3 \
  --known-page-stop 1 \
  --scroll 4 \
  --wanted-detail-delay 1.2 \
  --wanted-test-details \
  --save-wanted-debug \
  --init-seen
open site/index.html
python3 validate_test_results.py
