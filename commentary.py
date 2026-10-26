"""리포트 상단 '해석' 칸에 들어갈 HTML 조각을 만든다.

ANTHROPIC_API_KEY 가 있으면 Claude가 요약 텍스트(brief)를 읽고 해설을 쓰고,
없거나 호출이 실패하면 수집 데이터(json)로 규칙 기반 해설을 만든다.
"""
import html
import json
import os
import re

MODEL = "claude-opus-5-5"
WHEN = {"day": "오늘", "week": "이번 주", "month": "이번 달"}

SYSTEM = """너는 한국 주식시장 섹터 흐름을 정리하는 애널리스트다. 사용자가 섹터별 등락 요약을 주면 리포트 상단에 들어갈 해설을 한국어로 쓴다.

입력 형식: 첫 줄은 기간 정보, 이후 각 줄은 `섹터 (대표 ETF) 등락률 | 흐름 패턴 | ETF 구성종목 중 시가총액 상위 5종목 등락률`이다. 첫 두 줄은 시장 전체(KODEX 200)와 코스닥150이고, 나머지는 등락률 순이다.

출력 규칙:
- HTML 조각만 출력한다. 코드 펜스, html/head/body 태그, 설명 문구 없이 바로 시작한다.
- 첫 문단은 `<p><b>한 문장 결론</b> 시장 요약…</p>` 형식. 이어서 `<h3>소제목</h3>`과 `<p>` 또는 `<ul><li>` 블록을 3~4개 쓴다.
- 다룰 내용: 섹터 안에서 흐름이 갈린 곳(대형주와 중소형주 차이 등), 강세 섹터, 약세 섹터, 섹터와 반대로 움직인 개별 종목. 주간·월간이면 기간 중 고점·저점 시점과 변동성(고저폭)도 다룬다.
- 수치는 상승이면 `<b class="up">+1.02%</b>`, 하락이면 `<b class="dn">-0.93%</b>`로 감싼다. 텍스트 속 &는 &amp;로 쓴다.
- 입력에 없는 뉴스나 원인을 지어내지 않는다. 원인을 추정할 때는 "~로 보입니다"라고 쓴다. 매수·매도 권유는 하지 않는다.
- 문장은 짧고 평이하게. 일간 6~10문장, 주간 7~12문장, 월간 8~14문장."""


def _sign(v):
    return "up" if v > 0 else "dn" if v < 0 else "flat"


def _b(v):
    return f'<b class="{_sign(v)}">{v:+.2f}%</b>'


def rule_based(rep):
    """API 키 없이 데이터만으로 만드는 해설."""
    when = WHEN[rep["period"]]
    cards = rep["cards"]
    mkt, kq = cards[0], cards[1]
    sectors = cards[1:]
    ranked = sorted(sectors, key=lambda c: -c["etf"]["chg"])
    n_up = sum(c["etf"]["chg"] > 0 for c in sectors)
    n_dn = sum(c["etf"]["chg"] < 0 for c in sectors)
    m, k = mkt["etf"]["chg"], kq["etf"]["chg"]

    if n_up > n_dn * 2:
        tone = "대부분 섹터가 올랐습니다."
    elif n_dn > n_up * 2:
        tone = "대부분 섹터가 내렸습니다."
    else:
        tone = "섹터별로 엇갈린 흐름이었습니다."
    lead = "코스닥이 더 강했습니다." if k > m else "코스피 대형주 쪽이 더 강했습니다." if k < m else ""
    out = [f'<p><b>{tone}</b> KODEX 200 {_b(m)}, 코스닥150 {_b(k)}로 {lead} '
           f'23개 섹터 중 상승 {n_up}개, 하락 {n_dn}개입니다. 시장 흐름: {html.escape(mkt["pattern"])}.</p>']

    def item(c):
        tops = sorted(c["top"], key=lambda t: -abs(t["chg"]))[:2]
        detail = ", ".join(f'{html.escape(t["name"])} {_b(t["chg"])}' for t in tops)
        return f'<li>{html.escape(c["name"])} {_b(c["etf"]["chg"])}' + (f": {detail}" if detail else "") + "</li>"

    out.append("<h3>강세 섹터</h3><ul>" + "".join(item(c) for c in ranked[:3]) + "</ul>")
    out.append("<h3>약세 섹터</h3><ul>" + "".join(item(c) for c in ranked[-3:][::-1]) + "</ul>")

    # 섹터와 반대로 크게 움직인 종목
    contra, seen = [], set()
    for c in sectors:
        s = c["etf"]["chg"]
        for t in c["top"]:
            if t["code"] not in seen and s * t["chg"] < 0 and abs(t["chg"] - s) >= 3:
                contra.append((abs(t["chg"] - s), t, c))
                seen.add(t["code"])
    contra.sort(key=lambda x: -x[0])
    if contra:
        out.append("<h3>섹터와 반대로 움직인 종목</h3><ul>" + "".join(
            f'<li>{html.escape(t["name"])} {_b(t["chg"])} (섹터 {html.escape(c["name"])} {_b(c["etf"]["chg"])})</li>'
            for _, t, c in contra[:4]) + "</ul>")
    out.append(f'<p style="color:var(--muted);font-size:12px">{when} 해설은 데이터로 자동 작성되었습니다.</p>')
    return "\n".join(out)


def with_claude(brief):
    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": "medium"},
        # 안전 분류기가 거절하면 서버가 다른 모델로 자동 재시도
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": brief}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude가 요청을 거절함")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    text = re.sub(r"^```(?:html)?\s*|\s*```$", "", text)
    if "<p" not in text:
        raise RuntimeError("해설 형식이 올바르지 않음")
    return text


def write(json_path, brief_path, out_path):
    with open(json_path, encoding="utf-8") as f:
        rep = json.load(f)
    source = "auto"
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            with open(brief_path, encoding="utf-8") as f:
                body = with_claude(f.read())
            source = "claude"
        except Exception as e:  # 해설 실패로 리포트 전체를 막지 않는다
            print("Claude 해설 실패, 자동 해설로 대체:", e)
            body = rule_based(rep)
    else:
        body = rule_based(rep)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"COMMENTARY_SOURCE: {source}")
    return source
