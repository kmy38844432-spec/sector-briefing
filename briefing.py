"""수집 → 해설 → 렌더 → 사이트 생성을 한 번에 실행한다 (GitHub Actions용).

    python briefing.py day                  # 오늘이 거래일이 아니면 건너뜀
    python briefing.py week
    python briefing.py month --asof yesterday   # 매월 1일에 지난달 리포트
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import build_site
import commentary

BASE = os.path.dirname(os.path.abspath(__file__))
KST = timezone(timedelta(hours=9))


def run(*args):
    out = subprocess.run([sys.executable, os.path.join(BASE, "sector_flow.py"), *args],
                         check=True, capture_output=True, text=True, encoding="utf-8",
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"}).stdout
    print(out, end="")
    return dict(re.findall(r"^([A-Z_]+): (\S+)", out, re.M))


def set_output(name, value):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("period", choices=["day", "week", "month"])
    ap.add_argument("--asof", help="YYYYMMDD 또는 yesterday")
    a = ap.parse_args()

    today = datetime.now(KST)
    args = ["--period", a.period]
    if a.asof:
        asof = (today - timedelta(days=1)).strftime("%Y%m%d") if a.asof == "yesterday" else a.asof
        args += ["--asof", asof]

    info = run(*args)
    if a.period == "day" and info["LAST_TRADING_DAY"] != today.strftime("%Y%m%d"):
        print("휴장일 — 최근 거래일", info["LAST_TRADING_DAY"])
        set_output("skipped", "true")
        return
    if a.period == "week" and not a.asof:
        last = datetime.strptime(info["LAST_TRADING_DAY"], "%Y%m%d")
        if last.isocalendar()[:2] != today.isocalendar()[:2]:
            print("이번 주는 거래일이 없음")
            set_output("skipped", "true")
            return

    stem = os.path.join(BASE, "reports", f"{a.period}_{info['KEY']}")
    commentary.write(stem + ".json", stem + ".txt", stem + "_commentary.html")
    run("--period", a.period, "--render", info["KEY"])
    build_site.build()
    set_output("skipped", "false")
    set_output("key", info["KEY"])


if __name__ == "__main__":
    main()
