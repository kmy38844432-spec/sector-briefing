#!/usr/bin/env python3
"""코스닥 전수 스크린: ① 수주(계약부채) 증가 ② 캐팩스 정점 통과 ③ 영업레버리지 / FCF 10 / DCF 10
필요: pip install requests   환경변수: DART_API_KEY (직접 입력, 코드에 쓰지 말 것)
사용: python kosdaq_screen.py            # 전체 실행 (약 20~40분)
      python kosdaq_screen.py --limit 30 # 시험 실행
      python kosdaq_screen.py --test     # 네트워크 없이 계산 로직만 검사
"""
import argparse, csv, io, json, os, re, sys, time, zipfile, datetime as dt
import xml.etree.ElementTree as ET
BASE = "https://opendart.fss.or.kr/api"
YEAR = 2026
CACHE = "cache"
G1, GT, R = 0.05, 0.02, 0.10      # DCF: 5년 성장 5%, 영구 2%, 할인율 10%


def get(path, **p):
    import requests
    p["crtfc_key"] = os.environ["DART_API_KEY"]
    for i in range(3):
        try:
            r = requests.get(f"{BASE}/{path}", params=p, timeout=30)
            if path.endswith(".xml") or "document" in path:
                return r.content
            return r.json()
        except Exception:
            time.sleep(1 + i)
    return {}


def kosdaq_list():
    f = f"{CACHE}/kosdaq.json"
    if os.path.exists(f):
        return json.load(open(f))
    z = zipfile.ZipFile(io.BytesIO(get("corpCode.xml")))
    root = ET.fromstring(z.read(z.namelist()[0]))
    cands = [(e.findtext("corp_code"), e.findtext("corp_name"), e.findtext("stock_code").strip())
             for e in root.iter("list") if (e.findtext("stock_code") or "").strip()]
    out = []
    for i, (cc, nm, sc) in enumerate(cands):
        j = get("company.json", corp_code=cc)
        if j.get("corp_cls") == "K":
            out.append(dict(corp=cc, name=nm, code=sc))
        time.sleep(0.05)
        if i % 300 == 0:
            print("분류", i, "/", len(cands), file=sys.stderr)
    os.makedirs(CACHE, exist_ok=True)
    json.dump(out, open(f, "w"), ensure_ascii=False)
    return out


def num(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None


def pick(rows, divs, ids=(), names=(), exclude=()):
    for r in rows:
        if r.get("sj_div") in divs and (r.get("account_id") in ids or any(n in r.get("account_nm", "") for n in names)) \
                and not any(x in r.get("account_nm", "") for x in exclude):
            return r
    return None


def parse(rows, kind):
    """kind: 'A' 사업보고서 / 'Q' 분기·반기 (손익은 누적 add_amount 사용)
    반환: dict(cur, prv) 각각 rev, op, cfo, capex, cl(계약부채+선수금), debt, eq"""
    out = {"cur": {}, "prv": {}}
    def grab(div, ids, names, excl=(), flow=True):
        r = pick(rows, div, ids, names, excl)
        if not r:
            return None, None
        if kind == "Q" and "IS" in div and flow:
            c, p = num(r.get("thstrm_add_amount")), num(r.get("frmtrm_add_amount"))
            if c is None: c = num(r.get("thstrm_amount"))
            if p is None: p = num(r.get("frmtrm_amount"))
            return c, p
        return num(r.get("thstrm_amount")), num(r.get("frmtrm_amount"))
    IS = ("IS", "CIS")
    rev = grab(IS, ("ifrs-full_Revenue", "ifrs_Revenue"), ("매출액", "영업수익", "수익(매출액)"), ("원가",))
    op = grab(IS, ("dart_OperatingIncomeLoss",), ("영업이익",))
    cfo = grab(("CF",), ("ifrs-full_CashFlowsFromUsedInOperatingActivities",), ("영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동 현금흐름"))
    cp1 = grab(("CF",), ("ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",), ("유형자산의 취득", "유형자산 취득"))
    cp2 = grab(("CF",), ("ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",), ("무형자산의 취득", "무형자산 취득"))
    cl = [grab(("BS",), (), (n,), flow=False) for n in ("계약부채", "선수금")]
    debt = grab(("BS",), ("ifrs-full_Liabilities",), ("부채총계",), flow=False)
    eq = grab(("BS",), ("ifrs-full_Equity",), ("자본총계",), flow=False)
    for i, k in enumerate(("cur", "prv")):
        capex = sum(abs(x[i]) for x in (cp1, cp2) if x[i] is not None) if (cp1[i] is not None or cp2[i] is not None) else None
        out[k] = dict(rev=rev[i], op=op[i], cfo=cfo[i], capex=capex,
                      cl=sum(x[i] for x in cl if x[i] is not None) if any(x[i] is not None for x in cl) else None,
                      debt=debt[i], eq=eq[i])
    return out


def fin(corp, rcode):
    f = f"{CACHE}/fin_{corp}_{YEAR}_{rcode}.json"
    if os.path.exists(f):
        return json.load(open(f))
    yr = YEAR - 1 if rcode == "11011" else YEAR
    j = get("fnlttSinglAcntAll.json", corp_code=corp, bsns_year=yr, reprt_code=rcode, fs_div="CFS")
    if j.get("status") != "000":
        j = get("fnlttSinglAcntAll.json", corp_code=corp, bsns_year=yr, reprt_code=rcode, fs_div="OFS")
    rows = j.get("list") or []
    os.makedirs(CACHE, exist_ok=True)
    json.dump(rows, open(f, "w"), ensure_ascii=False)
    time.sleep(0.07)
    return rows


def dcf(fcf):
    if not fcf or fcf <= 0: return None
    v, f = 0, fcf
    for t in range(1, 6):
        f *= 1 + G1; v += f / (1 + R) ** t
    tv = f * (1 + GT) / (R - GT) / (1 + R) ** 5
    return v + tv


def metrics(A, H, Q):
    """A=사업보고서(2025, cur=2025/prv=2024), H=반기(2026), Q=3분기(2026 누적, prv=2025 누적)"""
    m = {}
    q, p = Q["cur"], Q["prv"]
    m["rev26"], m["rev25"] = q["rev"], p["rev"]
    m["op26"], m["op25"] = q["op"], p["op"]
    ok = all(x not in (None, 0) for x in (q["rev"], p["rev"])) and q["op"] is not None and p["op"] is not None
    m["rev_g"] = q["rev"] / p["rev"] - 1 if ok else None
    m["op_g"] = (q["op"] / p["op"] - 1) if ok and p["op"] > 0 and q["op"] > 0 else None
    m["dol"] = m["op_g"] / m["rev_g"] if m["op_g"] is not None and m["rev_g"] and m["rev_g"] > 0 else None
    m["inc_margin"] = (q["op"] - p["op"]) / (q["rev"] - p["rev"]) if ok and q["rev"] != p["rev"] else None
    m["opm_q3"] = q["op"] / q["rev"] if ok else None
    h = H["cur"]
    q3_rev = q["rev"] - h["rev"] if None not in (q["rev"], h["rev"]) else None
    q3_op = q["op"] - h["op"] if None not in (q["op"], h["op"]) else None
    m["opm_3q_only"] = q3_op / q3_rev if q3_rev else None
    m["opm_h1"] = h["op"] / h["rev"] if h["rev"] and h["op"] is not None else None
    # 캐팩스: 3분기 단독 vs 상반기 분기평균
    cq, ch = q["capex"], h["capex"]
    m["capex_ytd"] = cq
    m["capex_int"] = cq / q["rev"] if cq is not None and q["rev"] else None
    m["capex_q3"] = cq - ch if cq is not None and ch is not None else None
    m["capex_h1q"] = ch / 2 if ch is not None else None
    m["capex_decel"] = (m["capex_q3"] / m["capex_h1q"]) if m["capex_q3"] is not None and m["capex_h1q"] else None
    m["capex25"] = A["cur"]["capex"]
    # 계약부채+선수금: 3Q26 vs 2025말
    cl_now, cl_end = q["cl"], A["cur"]["cl"]
    m["cl_g"] = cl_now / cl_end - 1 if cl_now is not None and cl_end and cl_end > 0 else None
    # FCF TTM = FY25 + YTD26 - YTD25
    def fcf(c, cx): return None if c is None or cx is None else c - cx
    f_fy = fcf(A["cur"]["cfo"], A["cur"]["capex"])
    f_26, f_25 = fcf(q["cfo"], q["capex"]), fcf(p["cfo"], p["capex"])
    m["fcf_fy24"] = fcf(A["prv"]["cfo"], A["prv"]["capex"])
    m["fcf_fy25"] = f_fy
    m["fcf_ttm"] = f_fy + f_26 - f_25 if None not in (f_fy, f_26, f_25) else None
    d, e = q["debt"], q["eq"]
    m["debt_ratio"] = d / e if d is not None and e and e > 0 else None
    return m


def score(m):
    c1 = m["cl_g"] is not None and m["cl_g"] >= 0.30
    c2 = (m["capex_int"] is not None and m["capex_int"] >= 0.05 and m["capex_decel"] is not None and m["capex_decel"] <= 0.8)
    c3 = (m["rev_g"] is not None and m["rev_g"] >= 0.15 and m["dol"] is not None and m["dol"] >= 1.5
          and m["opm_3q_only"] is not None and m["opm_h1"] is not None and m["opm_3q_only"] >= m["opm_h1"])
    return int(c1), int(c2), int(c3)


def last_price_cap(code, shares):
    import requests
    try:
        r = requests.get(f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=3&requestType=0",
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}, timeout=15)
        root = ET.fromstring(r.content.decode("euc-kr", "ignore").replace('encoding="euc-kr"', ""))
        items = [i.get("data").split("|") for i in root.iter("item")]
        px = float(items[-1][4])
        return px, px * shares
    except Exception:
        return None, None


def shares_out(corp):
    j = get("stockTotqySttus.json", corp_code=corp, bsns_year=YEAR, reprt_code="11014")
    for r in j.get("list") or []:
        if r.get("se", "").startswith("보통주") or r.get("se") == "합계":
            a, b = num(r.get("istc_totqy")), num(r.get("tesstk_co")) or 0
            if a: return a - b
    return None


def backlog_text(corp):
    """3Q 보고서 본문에서 '수주잔고' 주변 문장 추출 (사람이 확인용)"""
    j = get("list.json", corp_code=corp, bgn_de=f"{YEAR}0801", end_de=dt.date.today().strftime("%Y%m%d"), pblntf_ty="A", page_count=20)
    rc = next((x["rcept_no"] for x in (j.get("list") or []) if "분기보고서" in x["report_nm"]), None)
    if not rc: return ""
    try:
        z = zipfile.ZipFile(io.BytesIO(get("document.xml", rcept_no=rc)))
        t = re.sub(r"<[^>]+>", " ", z.read(z.namelist()[0]).decode("utf-8", "ignore"))
        t = re.sub(r"\s+", " ", t)
        hits = [t[max(0, m.start() - 40): m.end() + 160] for m in re.finditer("수주잔고", t)][:2]
        return " / ".join(hits)
    except Exception:
        return ""


def pct(x): return "-" if x is None else f"{x*100:+.0f}%"
def eok(x): return "-" if x is None else f"{x/1e8:,.0f}"


def write_html(rows, A_list, F_list, D_list, path):
    h = ["<meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
         "<style>body{font-family:sans-serif;margin:14px;max-width:820px}table{border-collapse:collapse;width:100%;font-size:13px;margin-bottom:22px}"
         "td,th{padding:5px 6px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap}td:first-child,th:first-child{text-align:left}th{color:#666}.s{overflow-x:auto}</style>",
         f"<h2>코스닥 전수 스크린</h2><p style='color:#666;font-size:12px'>{dt.date.today()} 생성 · 2026 3Q 누적(DART) · 단위 억원 · 투자 권유가 아닌 참고용입니다.</p>"]
    def tbl(title, head, body):
        h.append(f"<h3>{title}</h3><div class=s><table><tr>" + "".join(f"<th>{x}</th>" for x in head) + "</tr>")
        for b in body: h.append("<tr>" + "".join(f"<td>{x}</td>" for x in b) + "</tr>")
        h.append("</table></div>")
    tbl("A. 수주·캐팩스·레버리지 (3조건 점수순)", ["종목", "점수", "계약부채증감", "캐팩스/매출", "3Q캐팩스/상반기평균", "매출YoY", "영업이익YoY", "레버리지", "수주잔고 본문"],
        [[r["name"], f'{r["s1"]}{r["s2"]}{r["s3"]}', pct(r["cl_g"]), pct(r["capex_int"]), "-" if r["capex_decel"] is None else f'{r["capex_decel"]:.2f}',
          pct(r["rev_g"]), pct(r["op_g"]), "-" if r["dol"] is None else f'{r["dol"]:.1f}x', (r.get("backlog") or "")[:80]] for r in A_list])
    tbl("B. FCF 상위 10 (최근 12개월, 억원)", ["종목", "FCF(TTM)", "FY25", "FY24", "부채비율"],
        [[r["name"], eok(r["fcf_ttm"]), eok(r["fcf_fy25"]), eok(r["fcf_fy24"]), pct(r["debt_ratio"])] for r in F_list])
    tbl("C. DCF 저평가 상위 10 (DCF가치/시총)", ["종목", "시총", "DCF가치", "배수", "FCF(TTM)"],
        [[r["name"], eok(r["cap"]), eok(r["dcf"]), f'{r["dcf_mult"]:.1f}x', eok(r["fcf_ttm"])] for r in D_list])
    h.append(f"<p style='color:#888;font-size:11px'>점수 3자리=①계약부채+선수금 +30%↑ ②캐팩스/매출 5%↑·3Q캐팩스가 상반기 분기평균의 0.8배↓ ③매출+15%↑·레버리지1.5x↑·3Q영업이익률≥상반기. DCF: 5년 성장{G1:.0%}, 영구{GT:.0%}, 할인율{R:.0%}, FCF 적자 제외.</p>")
    open(path, "w", encoding="utf-8").write("\n".join(h))


def run(limit):
    lst = kosdaq_list()[:limit] if limit else kosdaq_list()
    res = []
    for i, c in enumerate(lst):
        try:
            A, H, Q = (parse(fin(c["corp"], rc), k) for rc, k in (("11011", "A"), ("11012", "Q"), ("11014", "Q")))
            m = metrics(A, H, Q)
        except Exception as e:
            continue
        if m["rev26"] is None: continue
        m.update(name=c["name"], code=c["code"], corp=c["corp"])
        m["s1"], m["s2"], m["s3"] = score(m)
        res.append(m)
        if i % 100 == 0: print("재무", i, "/", len(lst), file=sys.stderr)
    # 시총·DCF (후보 FCF>0만)
    pos = sorted([r for r in res if r["fcf_ttm"] and r["fcf_ttm"] > 0], key=lambda r: -r["fcf_ttm"])[:150]
    for r in pos:
        sh = shares_out(r["corp"])
        px, cap = last_price_cap(r["code"], sh) if sh else (None, None)
        r["cap"] = cap; r["dcf"] = dcf(r["fcf_ttm"])
        r["dcf_mult"] = r["dcf"] / cap if cap and r["dcf"] else None
    F = [r for r in sorted(res, key=lambda r: -(r["fcf_ttm"] or -1e18))
         if r["fcf_ttm"] and r["fcf_fy25"] and r["fcf_fy25"] > 0 and (r["debt_ratio"] or 9) < 1.5][:10]
    D = sorted([r for r in pos if r.get("dcf_mult") and r["cap"] >= 1e11 and r["fcf_fy25"] and r["fcf_fy25"] > 0 and r["fcf_fy24"] and r["fcf_fy24"] > 0
                and (r["debt_ratio"] or 9) < 1.5], key=lambda r: -r["dcf_mult"])[:10]
    A_ = sorted([r for r in res if sum((r["s1"], r["s2"], r["s3"])) >= 2], key=lambda r: (-(r["s1"] + r["s2"] + r["s3"]), -(r["dol"] or 0)))[:40]
    for r in A_[:25]:
        r["backlog"] = backlog_text(r["corp"])
    os.makedirs("out", exist_ok=True)
    keys = ["name", "code", "s1", "s2", "s3", "rev26", "op26", "rev_g", "op_g", "dol", "inc_margin", "cl_g", "capex_int", "capex_decel", "fcf_fy24", "fcf_fy25", "fcf_ttm", "debt_ratio", "cap", "dcf", "dcf_mult"]
    with open("out/kosdaq_all.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, keys, extrasaction="ignore"); w.writeheader(); w.writerows(res)
    write_html(res, A_, F, D, "out/kosdaq.html")
    print(f"완료: {len(res)}종목 → out/kosdaq.html, out/kosdaq_all.csv")


def selftest():
    def row(div, nm, aid, c, p, ca=None, pa=None):
        return dict(sj_div=div, account_nm=nm, account_id=aid, thstrm_amount=c, frmtrm_amount=p, thstrm_add_amount=ca, frmtrm_add_amount=pa)
    rows = lambda rev, op, cfo, cap, cl: [
        row("IS", "매출액", "ifrs-full_Revenue", "0", "0", rev[0], rev[1]), row("IS", "영업이익", "dart_OperatingIncomeLoss", "0", "0", op[0], op[1]),
        row("CF", "영업활동현금흐름", "ifrs-full_CashFlowsFromUsedInOperatingActivities", cfo[0], cfo[1]),
        row("CF", "유형자산의 취득", "x", cap[0], cap[1]), row("BS", "계약부채", "y", cl[0], cl[1]),
        row("BS", "부채총계", "ifrs-full_Liabilities", "400", "300"), row("BS", "자본총계", "ifrs-full_Equity", "800", "700")]
    A = parse([row("IS", "매출액", "ifrs-full_Revenue", "1,000", "800"), row("IS", "영업이익", "dart_OperatingIncomeLoss", "100", "60"),
               row("CF", "영업활동현금흐름", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "150", "90"), row("CF", "유형자산의 취득", "x", "-80", "-60"),
               row("BS", "계약부채", "y", "100", "50")], "A")
    H = parse(rows(("600", "450"), ("60", "40"), ("90", "60"), ("-100", "-30"), ("0", "0")), "Q")
    Q = parse(rows(("1,000", "700"), ("120", "50"), ("160", "100"), ("-120", "-60"), ("160", "0")), "Q")
    m = metrics(A, H, Q)
    assert abs(m["rev_g"] - 1000 / 700 + 1) < 1e-9 and abs(m["op_g"] - 1.4) < 1e-9, m
    assert abs(m["dol"] - 1.4 / (1000 / 700 - 1)) < 1e-9
    assert m["fcf_ttm"] == (150 - 80) + (160 - 120) - (100 - 60), m["fcf_ttm"]
    assert abs(m["capex_decel"] - (20 / 50)) < 1e-9, m["capex_decel"]
    assert abs(m["cl_g"] - 0.6) < 1e-9, m["cl_g"]
    assert dcf(100) and dcf(-1) is None
    print("자체 점검 통과:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items() if k in ("rev_g", "op_g", "dol", "fcf_ttm", "capex_decel", "cl_g", "debt_ratio")}, score(m))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int); ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if a.test: selftest(); sys.exit()
    if not os.environ.get("DART_API_KEY"): sys.exit("DART_API_KEY 환경변수가 필요합니다 (GitHub Secrets에 등록).")
    run(a.limit)
