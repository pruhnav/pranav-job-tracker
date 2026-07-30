import csv
import json
import os
import sys
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


FILES_TO_UPLOAD = {
    "All Matches 48h": "pranav_matches_48h.csv",
    "New Jobs 48h": "todays_new_jobs_48h.csv",
}


def read_csv(file_path: str) -> list[list[str]]:
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {file_path}")

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.reader(csv_file))

    if not rows:
        return [["No jobs found"]]

    return rows


def get_existing_sheets(service, spreadsheet_id: str) -> set[str]:
    spreadsheet = (
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties.title",
        )
        .execute()
    )

    return {
        sheet["properties"]["title"]
        for sheet in spreadsheet.get("sheets", [])
    }


def create_missing_sheets(
    service,
    spreadsheet_id: str,
    required_sheets: set[str],
) -> None:
    existing_sheets = get_existing_sheets(service, spreadsheet_id)
    missing_sheets = required_sheets - existing_sheets

    if not missing_sheets:
        return

    requests = [
        {
            "addSheet": {
                "properties": {
                    "title": sheet_name,
                    "frozenRowCount": 1,
                }
            }
        }
        for sheet_name in sorted(missing_sheets)
    ]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


def upload_sheet(
    service,
    spreadsheet_id: str,
    sheet_name: str,
    values: list[list[str]],
) -> None:
    escaped_sheet_name = sheet_name.replace("'", "''")
    sheet_range = f"'{escaped_sheet_name}'"

    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=sheet_range,
        body={},
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_range}!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute(num_retries=5)

    print(f"Uploaded {max(len(values) - 1, 0)} jobs to '{sheet_name}'.")


def main() -> None:
    spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID")
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not spreadsheet_id:
        raise RuntimeError("GOOGLE_SPREADSHEET_ID is missing.")

    if not service_account_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is missing.")

    credentials = Credentials.from_service_account_info(
        json.loads(service_account_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    service = build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False,
    )

    create_missing_sheets(
        service,
        spreadsheet_id,
        set(FILES_TO_UPLOAD.keys()),
    )

    for sheet_name, file_path in FILES_TO_UPLOAD.items():
        upload_sheet(
            service,
            spreadsheet_id,
            sheet_name,
            read_csv(file_path),
        )

    print("Google Sheet updated successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise