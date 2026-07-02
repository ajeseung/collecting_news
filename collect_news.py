from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
from pathlib import Path
import re
import json
import time
import sys


# =========================
# 기본 설정
# =========================

NEWSPAPERS = {
    "조선일보": "023",
    "중앙일보": "025",
    "동아일보": "020",
    "한국일보": "469",
    "세계일보": "022",
    "한겨레": "028",
    "서울신문": "081",
    "경향신문": "032",
    "국민일보": "005",
    # "문화일보": "021",
    "매일경제": "009",
    "한국경제": "015",
    "서울경제": "011",
}

KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 운영 시에는 None 유지.
# 특정 날짜 테스트가 필요하면 "20260702"처럼 직접 입력.
TARGET_DATE_OVERRIDE = None

# 분류 검수용 제목 출력 여부
PRINT_TITLES = True


# =========================
# 공통 유틸
# =========================

def get_target_date():
    if len(sys.argv) >= 2:
        return sys.argv[1]

    if TARGET_DATE_OVERRIDE:
        return TARGET_DATE_OVERRIDE

    return datetime.now(KST).strftime("%Y%m%d")


def get_date_dir(target_date: str) -> Path:
    date_dir = Path("output") / target_date
    date_dir.mkdir(parents=True, exist_ok=True)
    return date_dir


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_page_label(page_label: str):
    """
    네이버 신문보기 면수 표기 파싱.

    지원:
    - A1 면
    - A1면
    - B1 면
    - E1 면
    - 1 면
    - 1면
    - 31 면
    """
    page_label = normalize_space(page_label)

    match = re.search(r"\b([A-Z])\s*(\d{1,2})\s*면\b", page_label)
    if match:
        section_code = match.group(1)
        page_number = int(match.group(2))
        page_code = f"{section_code}{page_number}"

        return {
            "page_label": page_label,
            "section_code": section_code,
            "page_number": page_number,
            "page_code": page_code,
            "normalized_page_code": page_code,
        }

    match = re.search(r"\b(\d{1,2})\s*면\b", page_label)
    if match:
        page_number = int(match.group(1))
        page_code = str(page_number)

        return {
            "page_label": page_label,
            "section_code": None,
            "page_number": page_number,
            "page_code": page_code,
            "normalized_page_code": f"A{page_number}",
        }

    return None


def get_article_key(url: str):
    """
    네이버 기사 URL에서 oid + aid 추출.
    """
    patterns = [
        r"/article/newspaper/(\d{3})/(\d+)",
        r"/article/(\d{3})/(\d+)",
        r"/mnews/article/(\d{3})/(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return f"{match.group(1)}_{match.group(2)}"

    return None


def extract_title(a_tag) -> str:
    strong = a_tag.select_one("strong")
    if strong:
        return normalize_space(strong.get_text(" ", strip=True))

    return normalize_space(a_tag.get_text(" ", strip=True))


def scroll_to_bottom(page, max_scrolls=6):
    last_height = 0

    for _ in range(max_scrolls):
        height = page.evaluate("document.body.scrollHeight")
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(700)

        if height == last_height:
            break

        last_height = height


# =========================
# 신문보기 기사 추출
# =========================

def extract_newspaper_articles(html: str, press_name: str, oid: str, target_date: str):
    """
    네이버 신문보기 HTML에서 전체 지면 기사 추출.

    핵심:
    - parent_context 기준으로 판단하지 않음.
    - 각 div.newspaper_inner 단위로 면수를 읽음.
    - A1 면 / 1 면 모두 지원.
    """
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    sections = soup.select("div.newspaper_inner")

    for section in sections:
        page_title_tag = section.select_one("h3")
        page_label = normalize_space(page_title_tag.get_text(" ", strip=True)) if page_title_tag else ""

        page_info = parse_page_label(page_label)
        if not page_info:
            continue

        link_tags = section.select(
            'a[href*="/article/newspaper/"], '
            'a[href*="n.news.naver.com/article/newspaper/"]'
        )

        for a in link_tags:
            title = extract_title(a)
            href = a.get("href")

            if not title or not href:
                continue

            full_url = urljoin("https://media.naver.com", href)
            article_key = get_article_key(full_url)

            if not article_key:
                continue

            if article_key in seen:
                continue

            seen.add(article_key)

            articles.append({
                "press": press_name,
                "oid": oid,
                "date": target_date,
                "page_label": page_info["page_label"],
                "section_code": page_info["section_code"],
                "page_number": page_info["page_number"],
                "page_code": page_info["page_code"],
                "normalized_page_code": page_info["normalized_page_code"],
                "article_key": article_key,
                "title": title,
                "url": full_url,
            })

    return articles


def get_page_summary(articles):
    pages = {}

    for item in articles:
        page_label = item["page_label"]
        pages.setdefault(page_label, 0)
        pages[page_label] += 1

    return pages


# =========================
# 사설/칼럼 탭 key 수집
# =========================

def collect_opinion_keys(page, oid: str):
    """
    네이버 언론사홈 사설/칼럼 탭 sid=110에서 기사 key 수집.
    지면 기사와 article_key를 대조하기 위함.
    """
    opinion_url = f"https://media.naver.com/press/{oid}?sid=110"

    page.goto(opinion_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    scroll_to_bottom(page, max_scrolls=4)

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    keys = set()

    for a in soup.select('a[href*="/article/"]'):
        href = a.get("href")
        if not href:
            continue

        full_url = urljoin("https://media.naver.com", href)
        key = get_article_key(full_url)

        if key:
            keys.add(key)

    return keys


# =========================
# 분류 로직
# =========================

def is_front_page_item(item):
    """
    일반 1면 판별.

    인정:
    - A1 면
    - 1 면

    제외:
    - B1 면, E1 면 등 섹션 1면
    """
    section_code = item.get("section_code")
    page_number = item.get("page_number")

    if page_number != 1:
        return False

    if section_code is None or section_code == "A":
        return True

    return False


def is_editorial_title(title: str) -> bool:
    """
    사설 판별.

    언론사별 표기 차이 대응:
    - [사설] 제목
    - 제목[사설]
    - 제목 [사설]
    - 【사설】 제목
    - 제목【사설】
    """
    title = normalize_space(title)

    editorial_patterns = [
        r"\[사설\]",
        r"【사설】",
        r"＜사설＞",
        r"<사설>",
        r"\(사설\)",
        r"〈사설〉",
        r"^사설[:\]\s]",
    ]

    return any(re.search(pattern, title) for pattern in editorial_patterns)


def looks_like_opinion_title(title: str) -> bool:
    """
    칼럼/오피니언성 제목 보조 판별.
    sid=110 탭 대조가 1순위이고, 이 함수는 보조 기준.
    """
    title = normalize_space(title)

    opinion_keywords = [
        "[칼럼]",
        "[시론]",
        "[기고]",
        "[논설",
        "[취재수첩]",
        "[기자의 눈]",
        "[데스크칼럼]",
        "[데스크 칼럼]",
        "[사내칼럼]",
        "[오늘과 내일]",
        "[횡설수설]",
        "[광화문에서]",
        "[만물상]",
        "[천자칼럼]",
        "[매경포럼]",
        "[한경에세이]",
        "[사설]",
        "[포럼]",
        "[시평]",
        "[오후여담]",
        "[뉴스와 시각]",
        "[목요일 아침에]",
        "[마감 후]",
        "[길섶에서]",
        "[데스크 시각]",
        "[씨줄날줄]",
    ]

    return any(keyword in title for keyword in opinion_keywords)


def classify_articles(articles, opinion_keys):
    result = {
        "1면": [],
        "사설": [],
        "칼럼_오피니언": [],
        "전체지면": articles,
    }

    for item in articles:
        title = item["title"]
        key = item["article_key"]

        is_front_page = is_front_page_item(item)
        is_editorial = is_editorial_title(title)

        is_opinion_by_tab = key in opinion_keys
        is_opinion_by_title = looks_like_opinion_title(title)
        is_opinion = is_opinion_by_tab or is_opinion_by_title

        item["is_front_page"] = is_front_page
        item["is_editorial"] = is_editorial
        item["is_opinion"] = is_opinion
        item["is_opinion_by_tab"] = is_opinion_by_tab
        item["is_opinion_by_title"] = is_opinion_by_title

        if is_front_page:
            result["1면"].append(item)

        if is_editorial:
            result["사설"].append(item)

        if is_opinion and not is_editorial:
            result["칼럼_오피니언"].append(item)

    return result


# =========================
# 출력/요약
# =========================

def print_titles(label, items):
    print(f"  [{label}]")
    for item in items:
        print(f"    - {item['page_label']} | {item['title']}")


def make_press_summary(classified, pages):
    return {
        "total_articles": len(classified["전체지면"]),
        "front_page_count": len(classified["1면"]),
        "editorial_count": len(classified["사설"]),
        "opinion_column_count": len(classified["칼럼_오피니언"]),
        "page_summary": pages,
    }


# =========================
# 언론사별 수집
# =========================

def collect_one_press(page, press_name: str, oid: str, target_date: str):
    newspaper_url = f"https://media.naver.com/press/{oid}/newspaper?date={target_date}"

    print(f"\n[{press_name}] 신문보기 접속: {newspaper_url}")

    page.goto(newspaper_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    scroll_to_bottom(page, max_scrolls=6)

    newspaper_html = page.content()

    articles = extract_newspaper_articles(
        html=newspaper_html,
        press_name=press_name,
        oid=oid,
        target_date=target_date,
    )

    pages = get_page_summary(articles)

    print(f"  전체 지면 기사 수: {len(articles)}")
    print(f"  파싱된 면수 목록: {pages}")

    opinion_keys = collect_opinion_keys(page, oid)
    print(f"  사설/칼럼 탭 기사 key 수: {len(opinion_keys)}")

    classified = classify_articles(articles, opinion_keys)

    print(f"  1면: {len(classified['1면'])}개")
    print(f"  사설: {len(classified['사설'])}개")
    print(f"  칼럼/오피니언: {len(classified['칼럼_오피니언'])}개")

    if PRINT_TITLES:
        print_titles("사설 제목", classified["사설"])
        print_titles("칼럼/오피니언 제목", classified["칼럼_오피니언"])

    return {
        "summary": make_press_summary(classified, pages),
        "data": classified,
    }


# =========================
# 메인
# =========================

def main():
    target_date = get_target_date()
    output_dir = get_date_dir(target_date)

    final_data = {
        "target_date": target_date,
        "created_at": datetime.now(KST).isoformat(),
        "presses": {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            viewport={"width": 1440, "height": 1800},
            user_agent=HEADERS["User-Agent"],
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )

        page = context.new_page()

        for press_name, oid in NEWSPAPERS.items():
            try:
                final_data["presses"][press_name] = collect_one_press(
                    page=page,
                    press_name=press_name,
                    oid=oid,
                    target_date=target_date,
                )

                time.sleep(1.5)

            except Exception as e:
                print(f"[ERROR] {press_name} 수집 실패: {e}")
                final_data["presses"][press_name] = {
                    "summary": {
                        "error": str(e),
                        "total_articles": 0,
                        "front_page_count": 0,
                        "editorial_count": 0,
                        "opinion_column_count": 0,
                        "page_summary": {},
                    },
                    "data": {
                        "1면": [],
                        "사설": [],
                        "칼럼_오피니언": [],
                        "전체지면": [],
                    },
                }

        browser.close()

    output_path = output_dir / f"naver_newspaper_{target_date}.json"

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)

    print(f"\n저장 완료: {output_path}")


if __name__ == "__main__":
    main()