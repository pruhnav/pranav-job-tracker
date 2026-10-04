import csv
import os
import smtplib
import sqlite3
import sys
from email.message import EmailMessage
from pathlib import Path


CSV_FILE = Path("todays_new_jobs_48h.csv")
DB_FILE = Path("sponsorscan.db")


def count_new_jobs() -> int:
    if not CSV_FILE.exists():
        raise FileNotFoundError(f"Missing file: {CSV_FILE}")

    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))

    # Subtract the CSV header.
    return max(len(rows) - 1, 0)


def load_failures() -> list[tuple[str, str]]:
    """Boards the last fetch-jobs run could not read, as (board, error)."""
    if not DB_FILE.exists():
        return []
    con = sqlite3.connect(DB_FILE)
    try:
        return [tuple(row) for row in con.execute(
            "SELECT board, error FROM fetch_failures ORDER BY board")]
    except sqlite3.OperationalError:
        return []  # a database from before failures were recorded
    finally:
        con.close()


def failures_text(failures: list[tuple[str, str]]) -> str:
    if not failures:
        return ""
    lines = [
        f"{len(failures)} job board{'s' if len(failures) != 1 else ''} "
        "failed to fetch, so their postings are missing. "
        "A persistent failure usually means a wrong slug in companies.yaml:"
    ]
    lines.extend(f"  - {board}: {error}" for board, error in failures)
    return "\n" + "\n".join(lines) + "\n"


def main() -> None:
    sender_email = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient_email = os.environ.get("NOTIFICATION_EMAIL")

    if not sender_email:
        raise RuntimeError("GMAIL_ADDRESS is missing.")

    if not app_password:
        raise RuntimeError("GMAIL_APP_PASSWORD is missing.")

    if not recipient_email:
        raise RuntimeError("NOTIFICATION_EMAIL is missing.")

    new_job_count = count_new_jobs()

    if new_job_count == 0:
        print("No new jobs found. Email will not be sent.")
        return

    message = EmailMessage()
    message["From"] = sender_email
    message["To"] = recipient_email
    message["Subject"] = (
        f"SponsorScan found {new_job_count} new job"
        f"{'s' if new_job_count != 1 else ''}"
    )

    message.set_content(
        f"""SponsorScan found {new_job_count} new job{'s' if new_job_count != 1 else ''}.

Your Google Sheet has been updated.

The new-jobs CSV is attached to this email.
{failures_text(load_failures())}"""
    )

    with CSV_FILE.open("rb") as attachment:
        message.add_attachment(
            attachment.read(),
            maintype="text",
            subtype="csv",
            filename=CSV_FILE.name,
        )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender_email, app_password)
        smtp.send_message(message)

    print(f"Email sent to {recipient_email} with {new_job_count} new jobs.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise