import json
import os
import sys
from datetime import datetime, timezone

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


def main() -> None:
    spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID")
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not spreadsheet_id:
        raise RuntimeError("GOOGLE_SPREADSHEET_ID is missing.")

    if not service_account_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is missing.")

    service_account_info = json.loads(service_account_json)

    credentials = Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    sheets_service = build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False,
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    values = [
        ["Status", "Timestamp"],
        ["Hello from GitHub Actions!", timestamp],
    ]

    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="Sheet1!A1:B2",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()

    print("Google Sheet updated successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise
