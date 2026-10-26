# 한국 섹터 브리핑

섹터 대표 ETF 24개의 흐름과 ETF 구성종목 중 시가총액 상위 5종목의 등락을 매일·매주·매월 자동으로 정리합니다. 데이터는 네이버증권에서 받습니다.

- **웹페이지**: https://kmy38844432-spec.github.io/sector-briefing/
- **일간**: 평일 16:10 KST (휴장일은 건너뜀)
- **주간**: 금요일 16:40 KST
- **월간**: 매월 1일 09:00 KST (지난달)

GitHub Actions의 예약 실행은 서버 사정에 따라 수십 분 늦게 시작될 수 있습니다.

## 구성

| 파일 | 역할 |
|---|---|
| `sector_flow.py` | 데이터 수집, 리포트 HTML 렌더링 |
| `commentary.py` | 상단 해석 작성 (`ANTHROPIC_API_KEY`가 있으면 Claude, 없으면 규칙 기반 자동 해설) |
| `build_site.py` | `reports/`로 GitHub Pages 사이트 생성 |
| `briefing.py` | 위 단계를 한 번에 실행 |
| `.github/workflows/briefing.yml` | 예약 실행과 Pages 배포 |

## 수동 실행

Actions 탭 → **섹터 브리핑** → **Run workflow**에서 기간(day/week/month)과 기준일을 골라 실행합니다.

로컬에서는:

```bash
pip install -r requirements.txt
python briefing.py day
python sector_flow.py --period week --asof 20261023 --png
```

## Claude 해설 켜기

저장소 Settings → Secrets and variables → Actions → **New repository secret**에 이름 `ANTHROPIC_API_KEY`로 API 키를 넣으면 다음 실행부터 Claude가 해설을 씁니다.

투자 권유가 아닌 참고용 자료입니다.
