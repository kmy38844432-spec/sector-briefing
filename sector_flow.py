"""한국 섹터별 흐름 리포트 (일간 / 주간 / 월간).

섹터 대표 ETF의 흐름 + 각 ETF 구성종목 중 시가총액 상위 5종목 등락을 카드 형태로 그린다.
데이터: 네이버증권.

    python sector_flow.py                      # 일간: 가장 최근 거래일
    python sector_flow.py --period week        # 주간: 가장 최근 거래일이 속한 주 (월~금)
    python sector_flow.py --period month       # 월간: 가장 최근 거래일이 속한 달
    python sector_flow.py --png                # PNG도 저장 (Edge headless)
    python sector_flow.py --period week --asof 20261023    # 지난 기간 (그 날짜가 속한 주/달)
    python sector_flow.py --period week --render 2026-W44   # 재수집 없이 저장된 데이터 + 해설로 다시 그리기

산출물 (KEY = 일간 YYYYMMDD / 주간 YYYY-Www / 월간 YYYY-MM):
    reports/<period>_KEY.json   수집 데이터
    reports/<period>_KEY.txt    해설 작성용 요약
    reports/<period>_KEY.html   독립 실행용 리포트 (PNG는 같은 이름 .png)
    artifact/<period>.html      아티팩트 게시용 페이지
해설: reports/<period>_KEY_commentary.html (HTML 조각)이 있으면 상단 해석 칸에 들어간다.
"""
import argparse
import html
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

# (섹터명, ETF 코드, 구성종목 표시 여부). 앞의 PINNED개는 고정, 나머지는 등락률 순 정렬.
SECTORS = [
    ("시장 전체", "069500", False),
    ("코스닥150", "229200", True),
    ("반도체", "091160", True),
    ("IT", "139260", True),
    ("코스닥 IT·반도체", "261060", True),
    ("2차전지", "305720", True),
    ("자동차", "091180", True),
    ("조선·중공업", "139230", True),
    ("방산", "449450", True),
    ("로봇", "445290", True),
    ("인터넷·SW", "157490", True),
    ("게임", "300610", True),
    ("미디어·엔터", "228810", True),
    ("헬스케어", "266420", True),
    ("코스닥 바이오", "261070", True),
    ("은행", "091170", True),
    ("증권", "102970", True),
    ("보험", "140700", True),
    ("철강", "117680", True),
    ("에너지화학", "117460", True),
    ("건설", "117700", True),
    ("운송", "140710", True),
    ("경기소비재", "266390", True),
    ("필수소비재", "266410", True),
]
PINNED = 2
TOP_N = 5
HIGHLIGHT = {"day": 3.0, "week": 5.0, "month": 10.0}  # 칠한 칸 기준(±%)
WD = "월화수목금토일"

PERIOD = {
    "day": {"label": "일간", "when": "오늘", "base": "전일 종가", "line": "장중 흐름(09:00–15:30)"},
    "week": {"label": "주간", "when": "이번 주", "base": "전주 종가", "line": "주간 흐름(분봉)"},
    "month": {"label": "월간", "when": "이번 달", "base": "전월 종가", "line": "월간 흐름(일봉 종가)"},
}

BASE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(BASE, "reports")
ARTIFACTS = os.path.join(BASE, "artifact")

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})


# ---------------------------------------------------------------- 수집

def fchart(code, timeframe, count):
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe={timeframe}&count={count}&requestType=0"
    return re.findall(r'data="(\d+)\|[^|]*\|[^|]*\|[^|]*\|(\d+)\|', S.get(url, timeout=10).text)


def minutes(code):
    """[(YYYYMMDD, HHMM, price)] 최근 약 6거래일, 09:00~15:30."""
    return [(t[:8], t[8:], float(p)) for t, p in fchart(code, "minute", 500) if "0900" <= t[8:] <= "1530"]


def daily(code):
    """[(YYYYMMDD, close)] 최근 60거래일."""
    return [(t, float(p)) for t, p in fchart(code, "day", 60)]


def realtime(codes):
    out = {}
    for i in range(0, len(codes), 40):
        url = "https://polling.finance.naver.com/api/realtime/domestic/stock/" + ",".join(codes[i:i + 40])
        for d in S.get(url, timeout=10).json()["datas"]:
            out[d["itemCode"]] = {"name": d["stockName"], "mcap": float(d.get("marketValueFullRaw") or 0)}
    return out


def holdings(code):
    d = S.get(f"https://m.stock.naver.com/api/stock/{code}/etfAnalysis", timeout=10).json()
    return [x["itemCode"] for x in d.get("etfTop10MajorConstituentAssets") or []
            if re.fullmatch(r"\d{6}", x.get("itemCode", ""))]


def period_key(period, day):
    dt = datetime.strptime(day, "%Y%m%d")
    if period == "day":
        return day
    if period == "week":
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"
    return dt.strftime("%Y-%m")


def split_period(period, bars, key):
    """일봉 bars에서 (기간 내 거래일 목록, 기준가)를 구한다. 기준가 = 기간 직전 거래일 종가."""
    inside = [i for i, (d, _) in enumerate(bars) if period_key(period, d) == key]
    if not inside or inside[0] == 0:
        return [], None
    return [bars[i][0] for i in inside], bars[inside[0] - 1][1]


def series(period, code, days, bars, mins):
    """차트용 점 [[x(0~1), price, label]]과 눈금 [[x, label]]."""
    if period == "month":
        closes = dict(bars)
        n = max(len(days) - 1, 1)
        pts = [[i / n, closes[d], f"{int(d[4:6])}/{int(d[6:])}"] for i, d in enumerate(days)]
        ticks = [[i / n, f"{int(d[6:])}일"] for i, d in enumerate(days)
                 if i == 0 or datetime.strptime(d, "%Y%m%d").weekday() == 0]
        return pts, ticks
    # 일간·주간: 분봉. 하루를 같은 폭(390분)으로 이어 붙인다.
    pts, ticks = [], []
    span = len(days) * 390
    for k, d in enumerate(days):
        dt = datetime.strptime(d, "%Y%m%d")
        if period == "week":
            ticks.append([k * 390 / span, f"{WD[dt.weekday()]}"])
        for dd, t, p in mins:
            if dd == d:
                m = (int(t[:2]) - 9) * 60 + int(t[2:])
                lab = f"{t[:2]}:{t[2:]}" if period == "day" else f"{int(d[4:6])}/{int(d[6:])}({WD[dt.weekday()]}) {t[:2]}:{t[2:]}"
                pts.append([(k * 390 + m) / span, p, lab])
    return pts, ticks


def pattern(period, pts, base):
    """기간 중 흐름을 한 줄로 요약."""
    if len(pts) < 3:
        return ""
    prices = [p for _, p, _ in pts]
    hi, lo, last = max(prices), min(prices), prices[-1]
    i_hi, i_lo = prices.index(hi), prices.index(lo)
    pos = (last - lo) / ((hi - lo) or 1)  # 종가의 기간 중 위치 0~1
    tail = "고가권 마감" if pos >= 0.8 else "저가권 마감" if pos <= 0.2 else "중간 마감"
    if i_hi < i_lo and pos < 0.5:
        shape = f"{pts[i_hi][2]} 고점 후 밀림"
    elif i_lo < i_hi and pos > 0.5:
        shape = f"{pts[i_lo][2]} 저점 후 반등"
    else:
        shape = "등락 반복"
    rng = f"고저폭 {(hi - lo) / base * 100:.1f}%p"
    if period == "day":
        return f"시초 {(prices[0] / base - 1) * 100:+.2f}% · {shape} · {tail}"
    return f"{shape} · {tail} · {rng}"


def collect(period, asof=None):
    codes = [c for _, c, _ in SECTORS]
    etfs = [c for _, c, show in SECTORS if show]
    with ThreadPoolExecutor(8) as ex:
        hold = dict(zip(etfs, ex.map(holdings, etfs)))
        stocks = sorted({s for v in hold.values() for s in v} - set(codes))
        bars = dict(zip(codes + stocks, ex.map(daily, codes + stocks)))
        mins = dict(zip(codes, ex.map(minutes, codes))) if period != "month" else {}
    rt = realtime(codes + stocks)
    if asof:  # 지난 기간: asof 이후 데이터는 버린다
        bars = {k: [b for b in v if b[0] <= asof] for k, v in bars.items()}

    last_day = bars[codes[0]][-1][0]
    key = period_key(period, last_day)

    def change(code):
        days, base = split_period(period, bars[code], key)
        if not days:
            return None, days, base
        return (bars[code][-1][1] / base - 1) * 100, days, base

    cards = []
    for name, code, show in SECTORS:
        chg, days, base = change(code)
        pts, ticks = series(period, code, days, bars[code], mins.get(code, []))
        top = []
        if show:
            members = []
            for s in hold.get(code, []):
                c = change(s)[0]
                if s in rt and c is not None:
                    members.append({"code": s, "name": rt[s]["name"], "chg": c, "mcap": rt[s]["mcap"]})
            top = sorted(members, key=lambda m: -m["mcap"])[:TOP_N]
        cards.append({"name": name, "code": code, "etf": {"name": rt[code]["name"], "chg": chg, "base": base},
                      "pts": pts, "ticks": ticks, "top": top, "pattern": pattern(period, pts, base)})
    cards = cards[:PINNED] + sorted(cards[PINNED:], key=lambda c: -c["etf"]["chg"])
    days0 = split_period(period, bars[codes[0]], key)[0]
    return {"period": period, "key": key, "first": days0[0], "last": days0[-1], "cards": cards}


# ---------------------------------------------------------------- 렌더링

def sign(v):
    return "up" if v > 0 else "dn" if v < 0 else "flat"


def spark(card, idx, w=240, h=130):
    pts, base = card["pts"], card["etf"]["base"]
    if not pts:
        return f'<svg class="spark" viewBox="0 0 {w} {h}"></svg>'
    prices = [p for _, p, _ in pts]
    lo, hi = min(prices + [base]), max(prices + [base])
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    gh = h - (12 if card["ticks"] else 0)  # 눈금 글자 자리

    def X(x):
        return 4 + (w - 8) * x

    def Y(p):
        return gh - (p - lo) / (hi - lo) * gh

    line = " ".join(f"{X(x):.1f},{Y(p):.1f}" for x, p, _ in pts)
    by = Y(base)
    area = f"{X(pts[0][0]):.1f},{by:.1f} {line} {X(pts[-1][0]):.1f},{by:.1f}"
    i_hi, i_lo = prices.index(max(prices)), prices.index(min(prices))
    ticks = "".join(
        f'<line class="tick" x1="{X(x):.1f}" x2="{X(x):.1f}" y1="0" y2="{gh}"/>'
        + (f'<text class="tl" x="{X(x) - 2:.1f}" y="{h - 1}" text-anchor="end">' if x > 0.85 else
           f'<text class="tl" x="{X(x) + 2:.1f}" y="{h - 1}">')
        + f'{html.escape(t)}</text>' for x, t in card["ticks"])
    return f'''<svg class="spark" viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(card["name"])} 흐름">
<defs><clipPath id="u{idx}"><rect x="0" y="0" width="{w}" height="{by:.1f}"/></clipPath>
<clipPath id="d{idx}"><rect x="0" y="{by:.1f}" width="{w}" height="{gh - by:.1f}"/></clipPath></defs>
{ticks}
<polygon class="fill-up" points="{area}" clip-path="url(#u{idx})"/>
<polygon class="fill-dn" points="{area}" clip-path="url(#d{idx})"/>
<line class="base" x1="0" x2="{w}" y1="{by:.1f}" y2="{by:.1f}"/>
<polyline class="ln {sign(card["etf"]["chg"])}" points="{line}"/>
<circle class="dot up" cx="{X(pts[i_hi][0]):.1f}" cy="{Y(prices[i_hi]):.1f}" r="3.2"><title>고가 {html.escape(pts[i_hi][2])}</title></circle>
<circle class="dot dn" cx="{X(pts[i_lo][0]):.1f}" cy="{Y(prices[i_lo]):.1f}" r="3.2"><title>저가 {html.escape(pts[i_lo][2])}</title></circle>
</svg>'''


def pct(v, hl, big=False):
    cls = f"pct {sign(v)}" + (" hl" if abs(v) >= hl else "") + (" big" if big else "")
    return f'<span class="{cls}">{v:.2f}</span>'


def chip(name, v):
    return f'{html.escape(name)} <b class="{sign(v)}">{v:+.2f}%</b>'


def summary(cards):
    sectors = cards[1:]
    mkt = cards[0]["etf"]["chg"]
    ranked = sorted(sectors, key=lambda c: -c["etf"]["chg"])
    n_up = sum(c["etf"]["chg"] > 0 for c in sectors)
    n_dn = sum(c["etf"]["chg"] < 0 for c in sectors)
    beat = sum(c["etf"]["chg"] > mkt for c in sectors)
    stocks = {m["code"]: m for c in sectors for m in c["top"]}
    s_up = sum(m["chg"] > 0 for m in stocks.values())
    s_dn = sum(m["chg"] < 0 for m in stocks.values())
    movers = sorted(stocks.values(), key=lambda m: -abs(m["chg"]))[:6]
    kosdaq = cards[1]["etf"]["chg"]
    return f'''<div class="stats">
<div class="stat"><span class="lbl">KODEX 200</span><span class="num {sign(mkt)}">{mkt:+.2f}%</span></div>
<div class="stat"><span class="lbl">KODEX 코스닥150</span><span class="num {sign(kosdaq)}">{kosdaq:+.2f}%</span></div>
<div class="stat"><span class="lbl">섹터 상승 / 하락</span><span class="num"><b class="up">{n_up}</b> / <b class="dn">{n_dn}</b></span></div>
<div class="stat"><span class="lbl">대표종목 상승 / 하락</span><span class="num"><b class="up">{s_up}</b> / <b class="dn">{s_dn}</b></span></div>
</div>
<ul class="facts">
<li><span class="k">시장 흐름</span>{html.escape(cards[0]["pattern"])} · 시장보다 강한 섹터 {beat}개</li>
<li><span class="k">강세</span>{", ".join(chip(c["name"], c["etf"]["chg"]) for c in ranked[:3])}</li>
<li><span class="k">약세</span>{", ".join(chip(c["name"], c["etf"]["chg"]) for c in ranked[-3:][::-1])}</li>
<li><span class="k">큰 변동</span>{", ".join(chip(m["name"], m["chg"]) for m in movers)}</li>
</ul>'''


CSS = """
:root{
  --bg:#ffffff; --fg:#1d2230; --muted:#6b7385; --faint:#9aa1b1; --line:#e3e6ec; --panel:#f5f7fa;
  --up:#d93838; --dn:#2f64d2; --flat:#6b7385; --up-fill:#fadcdc; --dn-fill:#dfe7f8; --base:#9aa1b1; --grid:#eceef2;
  --sans:"Noto Sans KR","Malgun Gothic","Apple SD Gothic Neo",sans-serif;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#12151c; --fg:#e7eaf0; --muted:#9aa2b3; --faint:#6c7487; --line:#2a303c; --panel:#1a1f28;
  --up:#ff6b6b; --dn:#6d9bff; --flat:#9aa2b3; --up-fill:#4a2228; --dn-fill:#1f2d4d; --base:#6c7487; --grid:#232833; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#12151c; --fg:#e7eaf0; --muted:#9aa2b3; --faint:#6c7487; --line:#2a303c; --panel:#1a1f28;
  --up:#ff6b6b; --dn:#6d9bff; --flat:#9aa2b3; --up-fill:#4a2228; --dn-fill:#1f2d4d; --base:#6c7487; --grid:#232833; color-scheme:dark}
body{background:var(--bg);color:var(--fg);font-family:var(--sans);margin:0}
.wrap{max-width:1300px;margin:0 auto;padding:28px 24px 24px;display:flex;flex-direction:column;gap:20px}
h1{font-size:clamp(22px,4vw,30px);margin:0;text-wrap:balance;letter-spacing:-.01em}
h2{font-size:15px;margin:0 0 10px;letter-spacing:.02em;color:var(--muted);font-weight:600}
.badge{display:inline-block;font-size:.55em;vertical-align:middle;padding:2px 8px;border-radius:4px;background:var(--panel);color:var(--muted);margin-right:8px;font-weight:600}
.sub{font-size:12px;color:var(--muted);margin-top:6px;line-height:1.6}
.up{color:var(--up)} .dn{color:var(--dn)} .flat{color:var(--flat)}
.top{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.25fr);gap:20px}
.panel{background:var(--panel);border-radius:8px;padding:16px 20px;min-width:0}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:12px}
.stat{display:flex;flex-direction:column;gap:2px}
.lbl{font-size:11px;color:var(--muted)}
.num{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.facts{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:6px;font-size:13.5px;line-height:1.6}
.facts .k{display:inline-block;min-width:64px;color:var(--muted);font-size:12px}
.comment{font-size:14px;line-height:1.75}
.comment p{margin:0 0 8px} .comment ul{margin:4px 0 8px;padding-left:18px} .comment li{margin:2px 0}
.comment h3{font-size:14px;margin:12px 0 4px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}
.card{min-width:0;border:1.5px solid var(--line);border-radius:6px;padding:14px 16px 8px;display:flex;flex-direction:column;gap:8px}
.head{display:flex;align-items:baseline;gap:12px;min-width:0}
.title{font-size:18px;font-weight:700;white-space:nowrap}
.etf{font-size:11px;color:var(--faint);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.inner{display:flex;gap:10px}
.chart{flex:0 0 58%;min-width:0}
.spark{width:100%;height:130px;display:block;overflow:visible}
.fill-up{fill:var(--up-fill)} .fill-dn{fill:var(--dn-fill)}
.base{stroke:var(--base);stroke-dasharray:3 3;stroke-width:1}
.tick{stroke:var(--grid);stroke-width:1}
.tl{fill:var(--faint);font-size:9px;font-family:var(--sans)}
.ln{fill:none;stroke-width:1.4;stroke-linejoin:round}
.ln.up{stroke:var(--up)} .ln.dn{stroke:var(--dn)} .ln.flat{stroke:var(--flat)}
.dot.up{fill:var(--up)} .dot.dn{fill:var(--dn)}
.list{flex:1;min-width:0;display:flex;flex-direction:column;justify-content:space-around;font-size:13px}
.row{display:flex;justify-content:space-between;gap:6px}
.nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.muted{color:var(--faint);justify-content:center}
.pct{font-variant-numeric:tabular-nums;padding:0 3px;border-radius:2px}
.pct.big{font-size:19px;font-weight:600}
.pct.hl.up{background:var(--up-fill)} .pct.hl.dn{background:var(--dn-fill)}
.pat{font-size:11px;color:var(--muted)}
.foot{font-size:11px;color:var(--faint)}
@media (max-width:820px){.top{grid-template-columns:minmax(0,1fr)}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:420px){.wrap{padding:20px 16px}.grid{grid-template-columns:minmax(0,1fr)}}
"""


def fmt_day(d, wd=True):
    dt = datetime.strptime(d, "%Y%m%d")
    return f"{dt:%Y-%m-%d}" + (f" ({WD[dt.weekday()]})" if wd else "")


def heading(rep):
    p, first, last = rep["period"], rep["first"], rep["last"]
    if p == "day":
        return fmt_day(last)
    if p == "week":
        return f"{fmt_day(first)} ~ {int(last[4:6])}/{int(last[6:])} ({WD[datetime.strptime(last, '%Y%m%d').weekday()]})"
    return f"{first[:4]}년 {int(first[4:6])}월 ({int(first[4:6])}/{int(first[6:])}~{int(last[4:6])}/{int(last[6:])})"


def render(rep, commentary=""):
    p, cards = rep["period"], rep["cards"]
    meta, hl = PERIOD[p], HIGHLIGHT[p]
    body = []
    for i, c in enumerate(cards):
        e = c["etf"]
        rows = "".join(
            f'<div class="row"><span class="nm" title="{html.escape(m["name"])}">{html.escape(m["name"])}</span>{pct(m["chg"], hl)}</div>'
            for m in c["top"]) or f'<div class="row muted">{html.escape(c["name"])}</div>'
        body.append(f'''<div class="card">
<div class="head"><span class="title">{html.escape(c["name"])}</span><span class="etf">{html.escape(e["name"])}</span>{pct(e["chg"], hl, True)}</div>
<div class="inner"><div class="chart">{spark(c, i)}</div><div class="list">{rows}</div></div>
<div class="pat">{html.escape(c["pattern"])}</div>
</div>''')
    comment_block = (f'<section class="panel"><h2>{meta["when"]}의 해석</h2><div class="comment">{commentary}</div></section>'
                     if commentary else "")
    return f'''<title>{meta["label"]} 섹터 브리핑</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header><h1><span class="badge">{meta["label"]}</span>한국 섹터별 흐름 · {heading(rep)}</h1>
<div class="sub">{datetime.now().strftime("%m/%d %H:%M")} 갱신 · 선={meta["line"]}, 점선={meta["base"]}(위 빨강 면·아래 파랑 면), 점=기간 중 고가·저가 · 빨강 상승, 파랑 하락, 칠한 칸 ±{hl:g}% 이상 · 오른쪽은 ETF 구성종목 중 시가총액 상위 {TOP_N}종목의 {meta["when"]} 등락률</div></header>
<div class="top">
<section class="panel"><h2>숫자로 본 {meta["when"]}</h2>{summary(cards)}</section>
{comment_block}
</div>
<div class="grid">{"".join(body)}</div>
<div class="foot">섹터 대표 ETF 기준 · 등락률은 {meta["base"]} 대비 · 네이버증권 데이터 · 투자 권유가 아닌 참고용</div>
</div>'''


def to_png(html_path, png_path, n_cards, has_comment):
    browser = next((p for p in [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ] if os.path.exists(p)), None)
    if not browser:
        print("Edge/Chrome을 찾지 못해 PNG는 건너뜀")
        return
    started = time.time()
    height = (700 if has_comment else 380) + ((n_cards + 2) // 3) * 238
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    f"--screenshot={png_path}", f"--window-size=1350,{height}",
                    "file:///" + html_path.replace("\\", "/")],
                   check=False, capture_output=True, timeout=60)
    # Edge는 이미 떠 있는 프로세스에 작업을 넘기고 먼저 끝날 수 있어, 파일이 새로 써질 때까지 기다린다.
    for _ in range(60):
        if os.path.exists(png_path) and os.path.getmtime(png_path) >= started:
            break
        time.sleep(0.5)
    print("PNG:", png_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", choices=["day", "week", "month"], default="day")
    ap.add_argument("--png", action="store_true", help="PNG 이미지도 저장")
    ap.add_argument("--asof", metavar="YYYYMMDD", help="이 날짜가 속한 기간으로 만들기 (지난 주/달)")
    ap.add_argument("--render", metavar="KEY", help="재수집 없이 저장된 데이터로 다시 그리기 (예: 20261026, 2026-W44, 2026-10)")
    a = ap.parse_args()
    os.makedirs(REPORTS, exist_ok=True)
    os.makedirs(ARTIFACTS, exist_ok=True)

    if a.render:
        with open(os.path.join(REPORTS, f"{a.period}_{a.render}.json"), encoding="utf-8") as f:
            rep = json.load(f)
    else:
        rep = collect(a.period, a.asof)
    stem = os.path.join(REPORTS, f"{a.period}_{rep['key']}")
    if not a.render:
        with open(stem + ".json", "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

    # 해설 작성용 요약 텍스트
    with open(stem + ".txt", "w", encoding="utf-8") as f:
        f.write(f"[{PERIOD[a.period]['label']}] {heading(rep)} · 등락률은 {PERIOD[a.period]['base']} 대비\n")
        for c in rep["cards"]:
            e = c["etf"]
            tops = ", ".join(f'{m["name"]} {m["chg"]:+.2f}' for m in c["top"])
            f.write(f'{c["name"]} ({e["name"]}) {e["chg"]:+.2f}% | {c["pattern"]} | {tops}\n')

    cpath = stem + "_commentary.html"
    commentary = open(cpath, encoding="utf-8").read() if os.path.exists(cpath) else ""
    page = render(rep, commentary)

    art = os.path.join(ARTIFACTS, f"{a.period}.html")
    with open(art, "w", encoding="utf-8") as f:
        f.write(page)
    with open(stem + ".html", "w", encoding="utf-8") as f:
        f.write('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1"></head><body>'
                + page + "</body></html>")
    print("KEY:", rep["key"])
    print("LAST_TRADING_DAY:", rep["last"])
    print("BRIEF:", stem + ".txt")
    print("COMMENTARY:", cpath, "(있음)" if commentary else "(없음)")
    print("ARTIFACT:", art)
    if a.png:
        to_png(stem + ".html", stem + ".png", len(rep["cards"]), bool(commentary))


if __name__ == "__main__":
    main()
