import asyncio
import hashlib
import re
from pathlib import Path

import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt

app = FastAPI()

AUDIO_DIR = Path(__file__).parent / "audio_cache"
AUDIO_DIR.mkdir(exist_ok=True)
TTS_VOICE = "ko-KR-InJoonNeural"

NEWS_DIR = Path(__file__).parent.parent / "daily-tech-news"
EXPERIMENTS_DIR = Path(__file__).parent.parent / "experiments"
WEEKLY_TESTS_DIR = Path(__file__).parent.parent / "weekly-tests"
md = MarkdownIt()


def md_to_plain_text(content: str) -> list[dict]:
    """마크다운을 섹션별 plain text로 변환 (TTS용)."""
    sections = []
    current_section = {"title": "", "text": ""}

    for line in content.split("\n"):
        # 메인 제목 (# Tech News ...) -> 인트로
        if line.startswith("# ") and not line.startswith("## "):
            if current_section["title"] or current_section["text"]:
                sections.append(current_section)
            current_section = {"title": "인트로", "text": line.lstrip("# ").strip()}
            continue

        # 섹션 제목 (## 1. AI / LLM)
        if line.startswith("## "):
            if current_section["title"] or current_section["text"]:
                sections.append(current_section)
            title = line.lstrip("# ").strip()
            current_section = {"title": title, "text": ""}
            continue

        # 소제목 (### Claude Mythos)
        if line.startswith("### "):
            title = line.lstrip("# ").strip()
            current_section["text"] += f"\n{title}.\n"
            continue

        # 구분선, 빈 줄 스킵
        if line.strip() in ("---", ""):
            continue

        # 체크박스 제거
        cleaned = re.sub(r"- \[[ x]\] ", "", line)
        # 볼드 마커 제거
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        # 링크에서 텍스트만 추출
        cleaned = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", cleaned)
        # 리스트 마커 정리
        cleaned = re.sub(r"^- ", "", cleaned.strip())

        if cleaned:
            current_section["text"] += cleaned + " "

    if current_section["title"] or current_section["text"]:
        sections.append(current_section)

    return sections


@app.get("/api/dates")
def get_dates():
    if not NEWS_DIR.exists():
        return {"dates": []}
    files = sorted(NEWS_DIR.glob("*.md"), reverse=True)
    dates = [f.stem for f in files]
    return {"dates": dates}


@app.get("/api/news/{date}")
def get_news(date: str):
    file_path = NEWS_DIR / f"{date}.md"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="해당 날짜의 뉴스가 없습니다")

    content = file_path.read_text(encoding="utf-8")
    html = md.render(content)
    sections = md_to_plain_text(content)

    return {"html": html, "sections": sections, "date": date}


@app.get("/api/tts/{date}")
async def generate_tts(date: str):
    """날짜별 뉴스를 AI TTS로 변환하여 mp3 반환."""
    file_path = NEWS_DIR / f"{date}.md"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="해당 날짜의 뉴스가 없습니다")

    # 캐시 확인 (같은 내용이면 재생성 안 함)
    content = file_path.read_text(encoding="utf-8")
    content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
    audio_path = AUDIO_DIR / f"{date}_{content_hash}.mp3"

    if not audio_path.exists():
        # 기존 캐시 제거
        for old in AUDIO_DIR.glob(f"{date}_*.mp3"):
            old.unlink()

        sections = md_to_plain_text(content)
        script = build_news_script(sections)
        communicate = edge_tts.Communicate(script, TTS_VOICE, rate="+0%")
        await communicate.save(str(audio_path))

    return FileResponse(audio_path, media_type="audio/mpeg")


def build_news_script(sections: list[dict]) -> str:
    """섹션들을 뉴스 앵커 스크립트로 변환."""
    parts = ["오늘의 테크 뉴스를 시작합니다."]

    for section in sections:
        if section["title"] == "인트로":
            parts.append(section["text"])
        elif "해볼 것" in section["title"]:
            parts.append(f"다음은 직접 해볼 것 섹션입니다. {section['text']}")
        elif "미팅" in section["title"]:
            parts.append(f"마지막으로 금요일 미팅 추천 토픽입니다. {section['text']}")
        else:
            parts.append(f"다음 소식입니다. {section['title']}. {section['text']}")

    parts.append("이상 오늘의 테크 뉴스였습니다.")
    return "\n\n".join(parts)


@app.get("/api/experiments")
def list_experiments():
    """체험 노트 파일 목록 반환."""
    if not EXPERIMENTS_DIR.exists():
        return {"experiments": []}
    files = sorted(EXPERIMENTS_DIR.glob("*.md"), reverse=True)
    items = []
    for f in files:
        # 첫 번째 # 라인을 제목으로 사용
        title = f.stem
        try:
            for line in f.read_text(encoding="utf-8").split("\n"):
                if line.startswith("# "):
                    title = line.lstrip("# ").strip()
                    break
        except Exception:
            pass
        items.append({"slug": f.stem, "title": title})
    return {"experiments": items}


@app.get("/api/experiment/{slug}")
def get_experiment(slug: str):
    file_path = EXPERIMENTS_DIR / f"{slug}.md"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="해당 체험 노트가 없습니다")

    content = file_path.read_text(encoding="utf-8")
    html = md.render(content)
    return {"html": html, "slug": slug}


@app.get("/api/weekly-tests")
def list_weekly_tests():
    """주간 테스트 파일 목록 반환."""
    if not WEEKLY_TESTS_DIR.exists():
        return {"weeks": []}
    files = sorted(WEEKLY_TESTS_DIR.glob("*.md"), reverse=True)
    items = []
    for f in files:
        title = f.stem
        try:
            for line in f.read_text(encoding="utf-8").split("\n"):
                if line.startswith("# "):
                    title = line.lstrip("# ").strip()
                    break
        except Exception:
            pass
        items.append({"slug": f.stem, "title": title})
    return {"weeks": items}


@app.get("/api/weekly-test/{slug}")
def get_weekly_test(slug: str):
    file_path = WEEKLY_TESTS_DIR / f"{slug}.md"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="해당 주간 테스트가 없습니다")

    content = file_path.read_text(encoding="utf-8")
    html = md.render(content)
    return {"html": html, "slug": slug}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True))
