"""Wholesale loader for the data.gov.in "Variety-wise Daily Market Prices Data of Commodity" resource.

Pulls Gujarat rows for the 5 crops for each day in a lookback window and reuses the CSV importer's
validated parsing (bounds, units, provisional flag, grade averaging) and upsert.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import psycopg2
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.import_agmarknet_file import LOG_INGESTION_SQL, import_records
from pipeline.sources.csv_import import CsvImportAdapter

load_dotenv(ROOT / ".env")

API_URL = "https://api.data.gov.in/resource/35985678-0d79-46b4-9ed6-6f13308a1d24"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
CROPS = ["Onion", "Potato", "Tomato", "Garlic", "Green Chilli"]
SOURCE_NAME = "datagov_variety"
PAGE_SIZE = 500
RETRIES = 3
IST = timezone(timedelta(hours=5, minutes=30))


def text(value):
    return "" if value is None else str(value)


def fetch_day(api_key, crop, day):
    """Return (records, error_message). Retries transient failures; never includes the key in messages."""
    date_text = day.strftime("%d/%m/%Y")
    records = []
    offset = 0
    while True:
        params = {
            "api-key": api_key, "format": "json", "limit": PAGE_SIZE, "offset": offset,
            "filters[State]": "Gujarat", "filters[Commodity]": crop, "filters[Arrival_Date]": date_text,
        }
        body = None
        for attempt in range(1, RETRIES + 1):
            try:
                response = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
                if response.status_code == 200:
                    parsed = response.json()
                    if isinstance(parsed, dict) and "records" in parsed:
                        body = parsed
                        break
            except (requests.RequestException, ValueError):
                pass
            time.sleep(2 * attempt)
        if body is None:
            return records, f"{crop} {date_text}: request failed after {RETRIES} attempts"
        batch = body.get("records", [])
        records.extend(batch)
        total = int(body.get("total", 0) or 0)
        offset += PAGE_SIZE
        if not batch or offset >= total:
            return records, None
        time.sleep(0.3)


def to_dataframe(records):
    rows = []
    skipped_no_market = 0
    for r in records:
        if not text(r.get("Market")).strip():
            skipped_no_market += 1
            continue
        rows.append({
            "State": text(r.get("State")), "District": text(r.get("District")), "Market": text(r.get("Market")),
            "Commodity": text(r.get("Commodity")), "Variety": text(r.get("Variety")), "Grade": text(r.get("Grade")),
            "Arrival_Date": text(r.get("Arrival_Date")), "Min_Price": text(r.get("Min_Price")),
            "Max_Price": text(r.get("Max_Price")), "Modal_Price": text(r.get("Modal_Price")),
            "Price Unit": "Rs./Quintal",
        })
    return pd.DataFrame(rows), skipped_no_market


def compare_with_db(conn, aggregated, start, end):
    cur = conn.cursor()
    cur.execute(
        "SELECT c.crop_name, m.mandi_name, w.variety, w.price_date, w.modal_price "
        "FROM wholesale_price w JOIN crop_master c USING (crop_id) JOIN mandi_master m USING (mandi_id) "
        "WHERE w.price_date BETWEEN %s AND %s",
        (start, end),
    )
    db = {(crop, mandi, variety, d): float(price) for crop, mandi, variety, d, price in cur.fetchall()}
    cur.close()
    api = {(r.crop, r.market, r.variety, r.price_date): r.modal_price for r in aggregated}
    same = sum(1 for k, v in api.items() if k in db and abs(db[k] - v) < 0.01)
    different = [(k, db[k], v) for k, v in api.items() if k in db and abs(db[k] - v) >= 0.01]
    only_api = [k for k in api if k not in db]
    only_db = [k for k in db if k not in api]
    print(f"\nCOMPARISON {start} to {end}: API rows={len(api)}, DB rows={len(db)}")
    print(f"  same price: {same}")
    print(f"  different price: {len(different)}")
    for k, old, new in different[:10]:
        print(f"    {k}: DB {old} vs API {new}")
    print(f"  only in API: {len(only_api)}")
    for k in sorted(only_api, key=lambda x: str(x[3]))[:10]:
        print(f"    {k}")
    print(f"  only in DB: {len(only_db)}")
    for k in sorted(only_db, key=lambda x: str(x[3]))[:10]:
        print(f"    {k}")


def main():
    parser = argparse.ArgumentParser(description="Load Gujarat wholesale prices from the Variety-wise API.")
    parser.add_argument("--days", type=int, default=7, help="lookback window ending today (IST)")
    parser.add_argument("--from-date", help="YYYY-MM-DD (use with --to-date)")
    parser.add_argument("--to-date", help="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="fetch and compare with the database; write nothing")
    args = parser.parse_args()

    api_key = (os.getenv("DATA_GOV_API_KEY") or "").strip()
    db_url = (os.getenv("DATABASE_URL") or "").strip()
    if not api_key or not db_url:
        print("::error::DATA_GOV_API_KEY or DATABASE_URL is not set")
        return 2

    if args.from_date and args.to_date:
        start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
        end = datetime.strptime(args.to_date, "%Y-%m-%d").date()
    else:
        end = datetime.now(IST).date()
        start = end - timedelta(days=args.days - 1)

    started_at = datetime.now(timezone.utc)
    all_records, errors = [], []
    day = start
    while day <= end:
        for crop in CROPS:
            records, error = fetch_day(api_key, crop, day)
            all_records.extend(records)
            if error:
                errors.append(error)
                print(f"::warning::{error}")
            time.sleep(0.3)
        day += timedelta(days=1)
    print(f"Fetched {len(all_records)} API records for {start} to {end}; failed requests: {len(errors)}")

    df, skipped_no_market = to_dataframe(all_records)
    if skipped_no_market:
        print(f"::warning::{skipped_no_market} record(s) skipped because the market name was empty")

    adapter = CsvImportAdapter()
    aggregated, stats = [], {"total": 0, "valid": 0, "rejected": 0}
    if not df.empty:
        records, stats = adapter.parse_dataframe(df, "variety-api")
        aggregated = adapter.deduplicate_and_aggregate(records)
        for r in aggregated:
            r.source = SOURCE_NAME
    print(f"Parsed: {stats['total']} rows, {stats['valid']} valid, {stats['rejected']} rejected, {len(aggregated)} after averaging grades")

    conn = psycopg2.connect(db_url, connect_timeout=15)
    try:
        if args.dry_run:
            compare_with_db(conn, aggregated, start, end)
            return 1 if errors else 0
        written = 0
        if aggregated:
            written = import_records(conn, aggregated)["written"]
        status = "failure" if errors else ("success" if aggregated else "empty")
        cur = conn.cursor()
        cur.execute(LOG_INGESTION_SQL, (
            SOURCE_NAME, status, len(aggregated), written, 0, stats["rejected"],
            "; ".join(errors)[:1000] or None, started_at, datetime.now(timezone.utc),
        ))
        conn.commit()
        cur.close()
        print(f"Wrote {written} wholesale records. Status: {status}")
        return 1 if errors else 0
    except Exception as exc:
        conn.rollback()
        print(f"::error::Load failed and was rolled back ({type(exc).__name__})")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
