# 링커리어·원티드 JSON 크롤러 FINAL v6

## 이번 수정

- 링커리어 기획 공고는 `기획/경영 > 경영기획/전략`, `사업기획/신규사업`, `서비스기획/운영` 3개 세부 직무를 수집

- 링커리어 본문은 `<br>`·블록 요소만 줄바꿈으로 처리하고 `span`/`strong`/`a` 같은 인라인 태그 조각은 한 문장으로 연결
- JSON 변환도 괄호 안에서 분리된 `미국 / 영국`, 짧은 영문 토큰 `IT` 등을 앞뒤 문장과 복원하고 중복 글머리표 제거
- 원티드는 `주요업무`나 `자격요건`이 먼저 보여도 **상세 정보 더보기 컨트롤이 있으면 무조건 클릭**
- 클릭 전후의 본문 길이·섹션 수·새 섹션 출현을 비교한 뒤 전체 상세가 열린 것으로 판단
- `h3` 제목 기준으로 주요업무·자격요건·우대사항·고용조건 수집
- 공고에 우대사항/고용조건이 없으면 빈칸으로 정상 처리
- `6` + 줄바꿈 + `개월`을 `6개월`로 복구
- 링커리어의 상단 `채용형태`/`고용형태`/`근무형태` 값을 직접 저장
- 링커리어 JSON의 고용 형태는 본문 추측보다 상단 값(예: 계약직, 체험형 인턴)을 우선 사용
- 카카오톡 공유글은 **사용자가 선택한 공고만** 생성

## 링커리어 10개 + 원티드 10개 테스트

아래 블록을 빈 줄 없이 그대로 붙여넣습니다.

```bash
cd ~/Downloads
unzip -o "job-crawler-FINAL-LINKAREER-WANTED-v6.zip"
cd "job-crawler-FINAL-LINKAREER-WANTED-v6"
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
```

`--platform all`은 링커리어와 원티드를 모두 실행합니다.

링커리어 자동 필터가 실패해 수동 선택 안내가 나오면 브라우저에서 다음을 선택한 뒤 터미널에서 Enter를 누릅니다.

- 채용형태: 신입, 인턴, 계약직
- 직무: 마케팅/광고

## 오프라인 코드 테스트

```bash
python3 -m py_compile job_crawler.py
python3 test_wanted_expand_priority_static.py
python3 test_wanted_preferred_static.py
python3 test_wanted_employment_static.py
python3 test_linkareer_employment_static.py
python3 test_linkareer_inline_fragments_static.py
python3 test_linkareer_planning_categories_static.py
node test_json_fragment_join.js
python3 test_selected_share_static.py
```

## 결과 파일

- `site/index.html`: 공고 선택, 카카오톡 문구, JSON 확인·수정·복사
- `site/jobs.csv`: 크롤링 결과
- `site/jobs.json`: 크롤링 결과 JSON
- `debug/`: 원티드 상세 HTML·스크린샷
- `data/seen_jobs.json`: 이전 수집 이력

## 링커리어 기획 테스트

기획 직무의 3개 세부 카테고리가 모두 선택되는지 확인하려면:

```bash
python3 job_crawler.py \
  --platform 링커리어 \
  --category 기획 \
  --limit 10 \
  --max-pages 3 \
  --scroll 4 \
  --init-seen
open site/index.html
```

자동 필터가 실패할 경우 직접 다음 항목을 선택합니다.

- 채용형태: 신입, 인턴, 계약직
- 직무: 기획/경영 > 경영기획/전략, 사업기획/신규사업, 서비스기획/운영

## GitHub Actions 자동 실행 설정

이 패키지에는 `.github/workflows/daily-crawl.yml`이 포함되어 있습니다.

- 실행 시각: 매일 한국시간 오후 4시 (`07:00 UTC`)
- 수동 실행: GitHub 저장소의 `Actions` → `Daily Job Crawl` → `Run workflow`
- 자동 저장: `data/seen_jobs.json`, `site/index.html`, `site/jobs.csv`, `site/jobs.json`
- 웹 배포: `site/` 폴더를 GitHub Pages로 배포
- 최초 실행: 빈 `seen_jobs.json`을 기준으로 기존 공고 기준 데이터를 생성
- 이후 실행: 처음 발견한 신규 공고만 상세 확인하고 오늘·어제 발견 공고를 화면에 표시

### 1. GitHub 저장소에 올리기

터미널에서 이 폴더로 이동한 뒤 실행합니다.

```bash
git init
git add .
git commit -m "Initial job crawler v6"
git branch -M main
git remote add origin https://github.com/깃허브아이디/저장소이름.git
git push -u origin main
```

이미 `origin`이 등록되어 있다면 `git remote add origin ...`은 실행하지 않습니다.

### 2. Actions 쓰기 권한 켜기

GitHub 저장소에서 다음 설정을 선택합니다.

1. `Settings` → `Actions` → `General`
2. `Workflow permissions`에서 `Read and write permissions` 선택
3. `Save`

이 권한이 있어야 액션이 매일 갱신된 공고와 `seen_jobs.json`을 저장소에 다시 커밋할 수 있습니다.

### 3. GitHub Pages 켜기

1. `Settings` → `Pages`
2. `Build and deployment`의 `Source`를 `GitHub Actions`로 선택

이후 `Actions`에서 `Daily Job Crawl`을 한 번 수동 실행해 정상 작동을 확인합니다.

### 운영 시 주의사항

- 자동 실행에서는 브라우저 입력을 기다리지 않도록 `--headless --auto-only`를 사용합니다.
- `--init-seen`은 정기 워크플로우에 넣지 않습니다. 넣으면 매번 기준 이력이 초기화됩니다.
- GitHub의 예약 실행은 서버 부하에 따라 실제 시작 시각이 몇 분 늦어질 수 있습니다.
- 사이트 구조가 바뀌어 자동 필터가 실패하면 해당 실행의 로그와 `crawler-debug-*` 아티팩트를 확인합니다.
