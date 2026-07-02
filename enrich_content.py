from datetime import datetime, timezone, timedelta
from pathlib import Path
from bs4 import BeautifulSoup
import requests
import json
import time
import sys


KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

TARGET_GROUPS = ["1면", "사설", "칼럼_오피니언"]

# 만평/카툰/이미지형 콘텐츠는 본문 텍스트가 없을 가능성이 높으므로 분석 대상에서 제외
SKIP_IMAGE_ONLY_ITEMS = True

IMAGE_ONLY_EXACT_TITLES = {
    "아이디",
    "카툰포커스",
}

IMAGE_ONLY_TITLE_KEYWORDS = [
    "만평",
    "카툰",
]


def get_target_date():
    if len(sys.argv) >= 2:
        return sys.argv[1]

    return datetime.now(KST).strftime("%Y%m%d")


def get_date_dir(target_date: str) -> Path:
    date_dir = Path("output") / target_date
    date_dir.mkdir(parents=True, exist_ok=True)
    return date_dir


def load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path: Path):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_likely_image_only_item(title: str) -> bool:
    title = title or ""

    if title in IMAGE_ONLY_EXACT_TITLES:
        return True

    return any(keyword in title for keyword in IMAGE_ONLY_TITLE_KEYWORDS)


def get_article_content(url: str):
    """
    네이버 기사 본문 수집.
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        content_tag = soup.select_one("#dic_area")
        if not content_tag:
            content_tag = soup.select_one("article")

        if not content_tag:
            return {
                "success": False,
                "content": "",
                "error": "본문 영역을 찾지 못했습니다.",
            }

        for tag in content_tag.select("script, style, iframe, button"):
            tag.decompose()

        content = content_tag.get_text(" ", strip=True)

        return {
            "success": bool(content),
            "content": content,
            "error": None if content else "본문 텍스트가 비어 있습니다.",
        }

    except Exception as e:
        return {
            "success": False,
            "content": "",
            "error": str(e),
        }


def collect_target_articles(press_payload: dict):
    """
    1면, 사설, 칼럼_오피니언에서 중복 제거한 분석 대상 기사 목록 생성.

    한 기사가 1면이면서 칼럼일 가능성도 있으므로
    article_key 기준으로 중복 제거한다.

    이미지형 콘텐츠는 excluded_articles로 따로 저장한다.
    """
    data = press_payload.get("data", {})
    collected = {}
    category_map = {}

    for group in TARGET_GROUPS:
        for item in data.get(group, []):
            key = item.get("article_key")
            if not key:
                continue

            if key not in collected:
                collected[key] = dict(item)
                category_map[key] = set()

            category_map[key].add(group)

    result = []
    excluded = []

    for key, item in collected.items():
        item["analysis_categories"] = sorted(list(category_map[key]))

        if SKIP_IMAGE_ONLY_ITEMS and is_likely_image_only_item(item.get("title", "")):
            item["excluded_from_content_fetch"] = True
            item["exclude_reason"] = "image_only_or_cartoon"
            item["content_fetch_success"] = False
            item["content_fetch_error"] = "이미지형/만평/카툰 콘텐츠로 본문 수집 제외"
            item["content"] = ""
            item["content_length"] = 0
            excluded.append(item)
            continue

        result.append(item)

    return result, excluded


def enrich_all(raw_data: dict):
    target_date = raw_data.get("target_date")
    presses = raw_data.get("presses", {})

    enriched = {
        "target_date": target_date,
        "created_at": raw_data.get("created_at"),
        "enriched_at": datetime.now(KST).isoformat(),
        "presses": {},
    }

    total_count = 0
    success_count = 0
    fail_count = 0
    excluded_count = 0

    for press_name, press_payload in presses.items():
        print(f"\n[{press_name}] 본문 수집 시작")

        target_articles, excluded_articles = collect_target_articles(press_payload)

        excluded_count += len(excluded_articles)

        enriched["presses"][press_name] = {
            "summary": press_payload.get("summary", {}),
            "articles": [],
            "excluded_articles": excluded_articles,
        }

        print(f"  분석 대상 기사 수: {len(target_articles)}")
        print(f"  제외 기사 수: {len(excluded_articles)}")

        for item in target_articles:
            total_count += 1

            title = item.get("title", "")
            url = item.get("url", "")

            print(f"  - {title[:60]}")

            result = get_article_content(url)

            item["content_fetch_success"] = result["success"]
            item["content_fetch_error"] = result["error"]
            item["content"] = result["content"]
            item["content_length"] = len(result["content"])

            if result["success"]:
                success_count += 1
            else:
                fail_count += 1

            enriched["presses"][press_name]["articles"].append(item)

            time.sleep(1.0)

    enriched["enrich_summary"] = {
        "total_target_articles": total_count,
        "success_count": success_count,
        "fail_count": fail_count,
        "excluded_count": excluded_count,
    }

    return enriched


def main():
    target_date = get_target_date()
    date_dir = get_date_dir(target_date)

    input_path = date_dir / f"naver_newspaper_{target_date}.json"
    validation_path = date_dir / f"validation_{target_date}.json"
    output_path = date_dir / f"enriched_news_{target_date}.json"

    raw_data = load_json(input_path)

    if validation_path.exists():
        validation = load_json(validation_path)
        print(f"검증 상태: {validation.get('overall_status')}")

        if validation.get("overall_status") == "ERROR":
            print("검증 결과 ERROR가 있어 본문 수집을 중단합니다.")
            sys.exit(1)
    else:
        print("검증 파일이 없습니다. validate_news.py를 먼저 실행하는 것을 권장합니다.")

    enriched = enrich_all(raw_data)
    save_json(enriched, output_path)

    print("\n본문 수집 완료")
    print(f"저장 파일: {output_path}")
    print(f"요약: {enriched['enrich_summary']}")


if __name__ == "__main__":
    main()