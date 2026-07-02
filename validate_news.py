from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import sys


KST = timezone(timedelta(hours=9))

MIN_TOTAL_ARTICLES = 20
MIN_FRONT_PAGE = 1
MIN_OPINION_OR_EDITORIAL = 1

# 일부 언론사는 사설 표기가 불규칙할 수 있으므로 warning 처리
STRICT_EDITORIAL_CHECK = False


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


def validate_press(press_name: str, press_payload: dict):
    warnings = []
    errors = []

    summary = press_payload.get("summary", {})
    data = press_payload.get("data", {})

    total_articles = summary.get("total_articles", 0)
    front_page_count = summary.get("front_page_count", 0)
    editorial_count = summary.get("editorial_count", 0)
    opinion_column_count = summary.get("opinion_column_count", 0)

    front_items = data.get("1면", [])
    editorial_items = data.get("사설", [])
    opinion_items = data.get("칼럼_오피니언", [])

    if "error" in summary:
        errors.append(f"수집 오류: {summary.get('error')}")

    if total_articles < MIN_TOTAL_ARTICLES:
        errors.append(f"전체 지면 기사 수가 너무 적습니다: {total_articles}개")

    if front_page_count < MIN_FRONT_PAGE:
        errors.append("1면 기사 수가 0개입니다.")

    if editorial_count == 0:
        message = "사설로 확정 분류된 기사가 0개입니다. 언론사별 사설 표기 방식 확인 필요."

        if STRICT_EDITORIAL_CHECK:
            errors.append(message)
        else:
            warnings.append(message)

    if editorial_count + opinion_column_count < MIN_OPINION_OR_EDITORIAL:
        warnings.append(
            f"사설+칼럼/오피니언 분류 기사가 너무 적습니다: "
            f"사설 {editorial_count}개, 칼럼/오피니언 {opinion_column_count}개"
        )

    for item in front_items:
        if not item.get("title"):
            warnings.append("1면 기사 중 제목이 비어 있는 항목이 있습니다.")
        if not item.get("url"):
            warnings.append(f"1면 기사 URL 누락: {item.get('title')}")
        if not item.get("article_key"):
            warnings.append(f"1면 기사 article_key 누락: {item.get('title')}")

    for group_name, items in [
        ("사설", editorial_items),
        ("칼럼/오피니언", opinion_items),
    ]:
        for item in items:
            if not item.get("title"):
                warnings.append(f"{group_name} 기사 중 제목이 비어 있는 항목이 있습니다.")
            if not item.get("url"):
                warnings.append(f"{group_name} 기사 URL 누락: {item.get('title')}")
            if not item.get("article_key"):
                warnings.append(f"{group_name} 기사 article_key 누락: {item.get('title')}")

    status = "OK"

    if warnings:
        status = "WARNING"

    if errors:
        status = "ERROR"

    return {
        "press": press_name,
        "status": status,
        "counts": {
            "total_articles": total_articles,
            "front_page_count": front_page_count,
            "editorial_count": editorial_count,
            "opinion_column_count": opinion_column_count,
        },
        "warnings": warnings,
        "errors": errors,
    }


def validate_all(input_path: Path):
    raw = load_json(input_path)

    target_date = raw.get("target_date")
    presses = raw.get("presses", {})

    results = []
    status_counts = {
        "OK": 0,
        "WARNING": 0,
        "ERROR": 0,
    }

    for press_name, press_payload in presses.items():
        result = validate_press(press_name, press_payload)
        results.append(result)
        status_counts[result["status"]] += 1

    overall_status = "OK"

    if status_counts["WARNING"] > 0:
        overall_status = "WARNING"

    if status_counts["ERROR"] > 0:
        overall_status = "ERROR"

    return {
        "target_date": target_date,
        "validated_at": datetime.now(KST).isoformat(),
        "overall_status": overall_status,
        "status_counts": status_counts,
        "results": results,
    }


def print_validation_report(report: dict):
    print("\n==============================")
    print("수집 결과 검증 리포트")
    print("==============================")
    print(f"날짜: {report.get('target_date')}")
    print(f"전체 상태: {report.get('overall_status')}")
    print(f"상태 집계: {report.get('status_counts')}")
    print()

    for item in report["results"]:
        press = item["press"]
        status = item["status"]
        counts = item["counts"]

        print(f"[{press}] {status}")
        print(
            f"  전체 {counts['total_articles']}개 / "
            f"1면 {counts['front_page_count']}개 / "
            f"사설 {counts['editorial_count']}개 / "
            f"칼럼·오피니언 {counts['opinion_column_count']}개"
        )

        for warning in item["warnings"]:
            print(f"  ⚠ WARNING: {warning}")

        for error in item["errors"]:
            print(f"  ❌ ERROR: {error}")

        print()


def main():
    target_date = get_target_date()
    date_dir = get_date_dir(target_date)

    input_path = date_dir / f"naver_newspaper_{target_date}.json"
    output_path = date_dir / f"validation_{target_date}.json"

    report = validate_all(input_path)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print_validation_report(report)
    print(f"검증 결과 저장 완료: {output_path}")

    if report["overall_status"] == "ERROR":
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()