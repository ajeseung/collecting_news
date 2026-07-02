from datetime import datetime, timezone, timedelta
from pathlib import Path
from email.message import EmailMessage
import smtplib
import os
import sys
import json


KST = timezone(timedelta(hours=9))


def get_target_date():
    if len(sys.argv) >= 2:
        return sys.argv[1]

    return datetime.now(KST).strftime("%Y%m%d")


def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"환경변수 {name}가 설정되어 있지 않습니다.")

    return value


def attach_file(msg: EmailMessage, file_path: Path):
    if not file_path.exists():
        raise FileNotFoundError(f"첨부 파일을 찾을 수 없습니다: {file_path}")

    data = file_path.read_bytes()

    msg.add_attachment(
        data,
        maintype="application",
        subtype="octet-stream",
        filename=file_path.name,
    )


def load_validation_status(validation_path: Path):
    if not validation_path.exists():
        return "UNKNOWN", {}

    with validation_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("overall_status", "UNKNOWN"), data.get("status_counts", {})


def load_enrich_summary(enriched_path: Path):
    if not enriched_path.exists():
        return {}

    with enriched_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("enrich_summary", {})


def build_email_body(target_date: str, validation_status: str, status_counts: dict, enrich_summary: dict):
    return f"""안녕하세요.

{target_date} 기준 주요 언론 지면 기사 수집 결과를 첨부드립니다.

[수집/검증 상태]
- 검증 상태: {validation_status}
- 검증 집계: {status_counts}

[본문 수집 요약]
- 분석 대상 기사 수: {enrich_summary.get("total_target_articles", "N/A")}
- 본문 수집 성공: {enrich_summary.get("success_count", "N/A")}
- 본문 수집 실패: {enrich_summary.get("fail_count", "N/A")}
- 이미지형/만평형 제외: {enrich_summary.get("excluded_count", "N/A")}

[첨부 파일]
1. enriched_news_{target_date}.json
   - GPT Pro 분석용 원본 파일
   - 1면, 사설, 칼럼/오피니언 기사 본문 포함

2. validation_{target_date}.json
   - 수집 상태 검증 파일

GPT Pro에 업로드할 때는 아래 경로를 기준으로 분석하시면 됩니다.

presses → 각 언론사명 → articles → content

content_fetch_success가 true이고 content가 비어 있지 않은 기사만 본문 분석 대상으로 사용하면 됩니다.
"""


def main():
    target_date = get_target_date()

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))

    smtp_user = get_required_env("SMTP_USER")
    smtp_password = get_required_env("SMTP_PASSWORD")

    mail_from = os.getenv("MAIL_FROM", smtp_user)
    mail_to = get_required_env("MAIL_TO")

    mail_cc = os.getenv("MAIL_CC", "")

    recipients = [x.strip() for x in mail_to.split(",") if x.strip()]

    if mail_cc:
        recipients += [x.strip() for x in mail_cc.split(",") if x.strip()]

    date_dir = Path("output") / target_date

    enriched_path = date_dir / f"enriched_news_{target_date}.json"
    validation_path = date_dir / f"validation_{target_date}.json"
    collected_path = date_dir / f"naver_newspaper_{target_date}.json"

    validation_status, status_counts = load_validation_status(validation_path)
    enrich_summary = load_enrich_summary(enriched_path)

    msg = EmailMessage()
    msg["Subject"] = f"[일일 언론 지면 수집] {target_date} 결과 파일"
    msg["From"] = mail_from
    msg["To"] = mail_to

    if mail_cc:
        msg["Cc"] = mail_cc

    msg.set_content(
        build_email_body(
            target_date=target_date,
            validation_status=validation_status,
            status_counts=status_counts,
            enrich_summary=enrich_summary,
        )
    )

    # 필수 첨부
    attach_file(msg, enriched_path)
    attach_file(msg, validation_path)

    # 원본 수집 파일도 같이 보내고 싶으면 주석 해제
    # attach_file(msg, collected_path)

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(msg, to_addrs=recipients)

    print(f"메일 발송 완료: {', '.join(recipients)}")


if __name__ == "__main__":
    main()