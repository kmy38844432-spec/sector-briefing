"""reports/ 의 리포트로 GitHub Pages용 site/ 를 만든다.

site/index.html   최신 일간
site/week.html    최신 주간
site/month.html   최신 월간
site/archive.html 지난 리포트 목록
site/r/*.html     리포트 원본
"""
import glob
import os
import re
import shutil

BASE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(BASE, "reports")
SITE = os.path.join(BASE, "site")
LABEL = {"day": "일간", "week": "주간", "month": "월간"}
PAGE = {"day": "index.html", "week": "week.html", "month": "month.html"}
KEY_RE = {"day": r"\d{8}", "week": r"\d{4}-W\d{2}", "month": r"\d{4}-\d{2}"}

NAV_CSS = """<style>
.sitenav{display:flex;gap:6px;flex-wrap:wrap;max-width:1300px;margin:0 auto;padding:14px 24px 0;font-family:var(--sans)}
.sitenav a{font-size:13px;padding:5px 12px;border-radius:999px;color:var(--muted);text-decoration:none;border:1px solid var(--line)}
.sitenav a.on{background:var(--fg);color:var(--bg);border-color:var(--fg)}
.sitenav a:focus-visible{outline:2px solid var(--dn);outline-offset:2px}
@media (max-width:420px){.sitenav{padding:12px 16px 0}}
</style>"""


def nav(active, prefix=""):
    links = [("day", "일간"), ("week", "주간"), ("month", "월간"), ("archive", "지난 리포트")]
    items = "".join(
        f'<a href="{prefix}{"archive.html" if k == "archive" else PAGE[k]}"{" class=on" if k == active else ""}>{t}</a>'
        for k, t in links)
    return NAV_CSS + f'<nav class="sitenav">{items}</nav>'


def with_nav(src, active, prefix):
    with open(src, encoding="utf-8") as f:
        page = f.read()
    return page.replace("<body>", "<body>" + nav(active, prefix), 1)


def reports(period):
    out = []
    for path in glob.glob(os.path.join(REPORTS, f"{period}_*.html")):
        m = re.fullmatch(rf"{period}_({KEY_RE[period]})\.html", os.path.basename(path))
        if m:
            out.append((m.group(1), path))
    return sorted(out, reverse=True)


def key_label(period, key):
    if period == "day":
        return f"{key[:4]}-{key[4:6]}-{key[6:]}"
    if period == "week":
        return f"{key[:4]}년 {int(key[6:])}주차"
    return f"{key[:4]}년 {int(key[5:])}월"


def build():
    if os.path.exists(SITE):
        shutil.rmtree(SITE)
    os.makedirs(os.path.join(SITE, "r"))
    sections = []
    for period in ("day", "week", "month"):
        items = reports(period)
        for key, path in items:
            with open(os.path.join(SITE, "r", os.path.basename(path)), "w", encoding="utf-8") as f:
                f.write(with_nav(path, period, "../"))
        if items:
            with open(os.path.join(SITE, PAGE[period]), "w", encoding="utf-8") as f:
                f.write(with_nav(items[0][1], period, ""))
        else:
            with open(os.path.join(SITE, PAGE[period]), "w", encoding="utf-8") as f:
                f.write(empty_page(period, f"아직 {LABEL[period]} 리포트가 없습니다."))
        links = "".join(f'<li><a href="r/{os.path.basename(p)}">{key_label(period, k)}</a></li>' for k, p in items)
        sections.append(f"<section><h2>{LABEL[period]}</h2><ul>{links or '<li>없음</li>'}</ul></section>")
    with open(os.path.join(SITE, "archive.html"), "w", encoding="utf-8") as f:
        f.write(empty_page("archive", "".join(sections), title="지난 리포트"))
    open(os.path.join(SITE, ".nojekyll"), "w").close()
    print("SITE:", SITE)


def empty_page(active, body, title="섹터 브리핑"):
    from sector_flow import CSS
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;700&display=swap">
<style>{CSS}
section{{display:flex;flex-direction:column;gap:4px}} section ul{{margin:0;padding-left:18px;line-height:1.9}}
section a{{color:var(--dn)}}</style></head><body>{nav(active)}
<div class="wrap"><h1>{title}</h1>{body}</div></body></html>'''


if __name__ == "__main__":
    build()
