from pathlib import Path

source = Path(__file__).with_name('job_crawler.py').read_text(encoding='utf-8')
required = [
    '"경영기획/전략"',
    '"사업기획/신규사업"',
    '"서비스기획/운영"',
]
for token in required:
    assert source.count(token) >= 3, f'기획 카테고리 설정 누락: {token}'

assert '"기획": ["경영기획/전략", "사업기획/신규사업", "서비스기획/운영"]' in source
print('PASS: 링커리어 기획 3개 세부 직무 설정 확인')
