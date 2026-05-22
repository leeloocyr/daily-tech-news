"""
지난 주(월~일)의 daily-tech-news 마크다운 7개를 Gemini로 종합 요약.
weekly-tech-news/{YYYY-Www}.md 로 저장.
"""
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google import genai

KST = ZoneInfo("Asia/Seoul")
TODAY = datetime.now(KST).date()
ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "daily-tech-news"
WEEKLY_DIR = ROOT / "weekly-tech-news"
WEEKLY_DIR.mkdir(exist_ok=True)


def env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        sys.exit(f"[ERROR] {key} 환경변수가 없습니다.")
    return val


def previous_week_range(today: date) -> tuple[date, date, str]:
    """오늘 기준 '지난 주' 월~일 반환."""
    days_since_monday = today.weekday()  # 월=0
    this_monday = today - timedelta(days=days_since_monday)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = last_monday + timedelta(days=6)
    iso_year, iso_week, _ = last_monday.isocalendar()
    week_id = f"{iso_year}-W{iso_week:02d}"
    return last_monday, last_sunday, week_id


def collect_daily_files(start: date, end: date) -> list[tuple[date, str]]:
    files = []
    cur = start
    while cur <= end:
        path = DAILY_DIR / f"{cur.isoformat()}.md"
        if path.exists():
            files.append((cur, path.read_text(encoding="utf-8")))
        cur += timedelta(days=1)
    return files


def _gemini_call_with_fallback(client, prompt, primary, fallback) -> str:
    for model in (primary, fallback):
        for attempt in range(3):
            try:
                resp = client.models.generate_content(model=model, contents=prompt)
                return resp.text.strip()
            except Exception as e:
                print(f"[WARN] {model} 시도 {attempt+1} 실패: {e}")
                time.sleep(5 * (attempt + 1))
    sys.exit("[ERROR] Gemini 전체 실패")


def build_prompt(start: date, end: date, week_id: str, daily_files: list) -> str:
    context = []
    for d, content in daily_files:
        weekday = ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]
        context.append(f"\n========== {d.isoformat()} ({weekday}) ==========\n{content}")
    context_text = "\n".join(context)

    return f"""당신은 한국어 기술 뉴스 큐레이터입니다. 지난 한 주의 일별 기술 뉴스 7개를 종합해 주간 다이제스트를 작성합니다.

기간: {start.isoformat()} ~ {end.isoformat()} ({week_id})

[입력: 일별 뉴스 7개]
{context_text}

[작성 지침]
- 한 주를 관통하는 흐름을 먼저 짚는다. 매일 반복된 이슈는 묶고, 한 번만 나온 핵심도 놓치지 않는다.
- "이 주의 핫이슈"는 1~2개만 선정. 원문 디테일과 톤을 보존해 요약하지 말고 정리.
- 카테고리별 항목은 한 줄 요약 (제목 + 핵심).
- 다음 주 주목할 것은 추측이 아닌 데이터 기반 (이번 주 흐름의 연장선).

[출력 형식 - 마크다운만, 코드블록 ``` 금지]

# Weekly Tech Digest - {week_id}
**{start.isoformat()} ~ {end.isoformat()}**

---

## 📌 이 주의 핫이슈
(1~2개. 제목 + 3~5문장 정리)

---

## 🤖 AI / LLM
(3개 항목, **제목** → 한 줄 핵심)

## 🔒 보안 & 취약점
(2~3개 항목)

## 🛠️ 개발 도구 & 프로그래밍
(2~3개 항목)

---

## 📅 주간 트렌드
(한 단락. 한 주의 흐름을 ITS 개발자 관점에서)

## 💡 다음 주 주목할 것
(2~3개. 이번 주 흐름의 자연스러운 연장)
"""


def main():
    start, end, week_id = previous_week_range(TODAY)
    print(f"[1/3] 지난 주: {start} ~ {end} ({week_id})")

    daily_files = collect_daily_files(start, end)
    if not daily_files:
        sys.exit(f"[ERROR] {start} ~ {end} 사이 일별 뉴스 파일이 없습니다.")
    print(f"[2/3] 수집된 일별 파일: {len(daily_files)}개")

    client = genai.Client(api_key=env("GEMINI_API_KEY"))
    prompt = build_prompt(start, end, week_id, daily_files)
    markdown = _gemini_call_with_fallback(
        client, prompt, "gemini-2.5-flash", "gemini-2.5-flash-lite"
    )
    if markdown.startswith("```"):
        lines = markdown.split("\n")
        markdown = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    out_path = WEEKLY_DIR / f"{week_id}.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"[3/3] 저장: {out_path}")


if __name__ == "__main__":
    main()
