"""
매일 아침 AI 기술 뉴스를 생성해서 daily-tech-news/YYYY-MM-DD.md 로 저장하고
카카오톡 "나에게 보내기"로 요약 메시지를 전송한다.

환경변수(필수):
- GEMINI_API_KEY
- NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
- KAKAO_REST_API_KEY, KAKAO_CLIENT_SECRET, KAKAO_REFRESH_TOKEN
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

from google import genai

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST)
DATE_STR = TODAY.strftime("%Y-%m-%d")
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"][TODAY.weekday()]

REPO_ROOT = Path(__file__).resolve().parent.parent
NEWS_DIR = REPO_ROOT / "daily-tech-news"
NEWS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH = NEWS_DIR / f"{DATE_STR}.md"


def env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        sys.exit(f"[ERROR] 환경변수 {key}가 설정되지 않았습니다")
    return value


def naver_news(query: str, display: int = 5) -> list[dict]:
    """네이버 뉴스 검색 (한국어 소스)."""
    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not (client_id and client_secret):
        return []

    url = "https://openapi.naver.com/v1/search/news.json?" + urllib.parse.urlencode(
        {"query": query, "display": display, "sort": "date"}
    )
    req = urllib.request.Request(
        url,
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("items", [])
    except Exception as e:
        print(f"[WARN] Naver 검색 실패 ({query}): {e}")
        return []


def strip_html(s: str) -> str:
    import re

    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return s.strip()


def collect_korean_news() -> str:
    """네이버 뉴스에서 한국어 기술 뉴스 수집."""
    queries = [
        "AI 인공지능",
        "Claude Anthropic",
        "사이버보안 취약점",
        "개발자 도구",
        "CCTV 교통 AI",
    ]
    sections = []
    for q in queries:
        items = naver_news(q, display=3)
        if not items:
            continue
        lines = [f"### 네이버 뉴스: {q}"]
        for it in items:
            title = strip_html(it.get("title", ""))
            desc = strip_html(it.get("description", ""))[:120]
            link = it.get("link", "")
            lines.append(f"- **{title}** — {desc} [[링크]({link})]")
        sections.append("\n".join(lines))
    return "\n\n".join(sections) if sections else "(네이버 뉴스 수집 실패)"


def _gemini_call_with_fallback(client, prompt: str, primary: str, fallback: str) -> str:
    """Gemini 호출. 503 등 실패 시 fallback 모델로 재시도."""
    import time

    last_err = None
    for model in (primary, fallback):
        for attempt in range(3):
            try:
                resp = client.models.generate_content(model=model, contents=prompt)
                return resp.text.strip()
            except Exception as e:
                last_err = e
                print(f"[WARN] {model} 시도 {attempt+1} 실패: {e}")
                time.sleep(5 * (attempt + 1))
    sys.exit(f"[ERROR] Gemini 전체 실패: {last_err}")


def generate_news_markdown(korean_news_context: str) -> tuple[str, str]:
    """Gemini API로 전체 뉴스 마크다운 + 카톡 요약 생성."""
    client = genai.Client(api_key=env("GEMINI_API_KEY"))

    prompt_full = f"""당신은 한국어 기술 뉴스 큐레이터입니다. ITS(지능형 교통 시스템) 분야 개발자를 위해 AI/LLM, 보안, 개발 도구 소식을 간결하게 정리합니다.

포맷 규칙:
- 각 항목: **제목** → 요약 (2-3문장) → "**왜 주목할만한가:**" 섹션 (1-2문장)
- CCTV/교통 AI 관련 항목에만 ⭐ 표시
- 마지막에 "직접 해볼 것", "미팅 추천 토픽" 섹션 포함
- 방대하지 않게, 핵심만

오늘은 {DATE_STR} ({WEEKDAY_KO}요일)입니다.

아래는 네이버에서 수집한 한국어 뉴스 원천 자료입니다:

{korean_news_context}

---

이 자료와 당신이 알고 있는 최신 AI/기술 동향을 종합하여, 다음 형식의 한국어 기술 뉴스 마크다운을 생성하세요:

# Tech News - {DATE_STR} ({WEEKDAY_KO})

---

## 1. AI / LLM
(3-4개 항목)

## 2. 보안
(2-3개 항목)

## 3. 개발 도구 & 트렌드
(2-3개 항목)

---

## 직접 해볼 것
(테스트해볼 것, 공부할 것)

---

## 미팅 추천 토픽
(3개)

중요: 마크다운 본문만 출력하고, 코드 블록 ```로 감싸지 마세요."""

    markdown = _gemini_call_with_fallback(
        client, prompt_full, "gemini-2.5-flash", "gemini-2.5-flash-lite"
    )
    # 코드 블록 감싸기 제거 (혹시 모델이 감싸면)
    if markdown.startswith("```"):
        lines = markdown.split("\n")
        markdown = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    # 카톡 요약 (feed 템플릿용)
    summary_prompt = f"""다음 기술 뉴스 마크다운에서 카카오톡 feed 메시지용 요약을 만드세요.

{markdown}

출력 형식(정확히 JSON으로만 출력, 코드블록 금지):
{{
  "title": "🤖 오늘의 AI/기술 뉴스 ({DATE_STR})",
  "description": "카테고리별로 핵심 항목들을 한눈에 볼 수 있게 정리. 줄바꿈(\\n) 사용. 최대 800자. 각 카테고리 앞에 이모지(🧠, 🔒, 🛠️) 사용. 각 항목은 한 줄로 '• 제목 — 한줄설명' 형식."
}}

예시 description:
"🧠 AI/LLM\\n• GPT-5.4 출시 — 1M 컨텍스트, OSWorld 75%\\n• Claude Code Pro 제거 실험 철회 — 커뮤니티 반발\\n\\n🔒 보안\\n• CISA Cisco 긴급 패치 — 오늘(4/23) 기한\\n• Kyber 랜섬웨어 등장 — ESXi+Windows 공격\\n\\n🛠️ 개발 도구\\n• GitHub Copilot opt-out D-1 — 내일 마감!"

중요:
- 반드시 유효한 JSON 형식으로만 출력
- title/description 외의 필드 금지
- ITS/CCTV 관련 항목 있으면 ⭐ 추가
- description 내부에서 한 줄로 \\n 이스케이프 사용"""

    raw = _gemini_call_with_fallback(
        client, summary_prompt, "gemini-2.5-flash-lite", "gemini-2.5-flash"
    )
    # 코드블록 제거
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        parsed = json.loads(raw)
        title = parsed.get("title", f"🤖 오늘의 AI 뉴스 ({DATE_STR})")
        description = parsed.get("description", "")
    except json.JSONDecodeError:
        title = f"🤖 오늘의 AI 뉴스 ({DATE_STR})"
        description = raw[:800]

    return markdown, {"title": title, "description": description}


def refresh_kakao_access_token() -> str:
    """Kakao refresh_token으로 새 access_token 발급."""
    rest_api_key = env("KAKAO_REST_API_KEY")
    refresh_token = env("KAKAO_REFRESH_TOKEN")
    client_secret = os.environ.get("KAKAO_CLIENT_SECRET", "")

    data = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token,
    }
    if client_secret:
        data["client_secret"] = client_secret

    req = urllib.request.Request(
        "https://kauth.kakao.com/oauth/token",
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    access_token = result.get("access_token")
    if not access_token:
        sys.exit(f"[ERROR] Kakao 토큰 갱신 실패: {result}")
    return access_token


def send_kakao_message(summary: dict, access_token: str) -> None:
    """카카오톡 나에게 보내기 (feed 템플릿으로 더 풍부한 정보 전송)."""
    repo_url = "https://github.com/leeloocyr/daily-tech-news"
    news_url = f"{repo_url}/blob/main/daily-tech-news/{DATE_STR}.md"
    template = {
        "object_type": "feed",
        "content": {
            "title": summary["title"],
            "description": summary["description"],
            "image_url": "https://raw.githubusercontent.com/github/explore/main/topics/artificial-intelligence/artificial-intelligence.png",
            "link": {"web_url": news_url, "mobile_web_url": news_url},
        },
        "buttons": [
            {
                "title": "전체 뉴스 읽기",
                "link": {"web_url": news_url, "mobile_web_url": news_url},
            },
            {
                "title": "GitHub 레포",
                "link": {"web_url": repo_url, "mobile_web_url": repo_url},
            },
        ],
    }
    data = urllib.parse.urlencode({"template_object": json.dumps(template)}).encode()
    req = urllib.request.Request(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        data=data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    if result.get("result_code") != 0:
        sys.exit(f"[ERROR] 카카오 전송 실패: {result}")
    print(f"[OK] 카카오 전송 성공")


def main():
    print(f"[INFO] 뉴스 생성 시작: {DATE_STR} ({WEEKDAY_KO})")

    print("[1/4] 네이버 뉴스 수집...")
    korean = collect_korean_news()

    print("[2/4] Gemini로 뉴스 생성...")
    markdown, summary = generate_news_markdown(korean)

    print(f"[3/4] 파일 저장: {OUTPUT_PATH}")
    OUTPUT_PATH.write_text(markdown, encoding="utf-8")

    print("[4/4] 카카오톡 전송...")
    access_token = refresh_kakao_access_token()
    send_kakao_message(summary, access_token)

    print(f"[DONE] 뉴스 생성 완료")


if __name__ == "__main__":
    main()
