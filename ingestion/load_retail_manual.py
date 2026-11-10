import csv
import io
import os
import sys
from datetime import date, datetime, timedelta

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
RETAIL_SHEET_URL = (os.getenv("RETAIL_SHEET_URL") or "").strip()

REQUIRED_COLUMNS = ["entry_date", "city", "platform", "crop_name", "price_per_kg", "unit", "notes"]
ALLOWED_CITIES = {"ahmedabad": "Ahmedabad"}
ALLOWED_PLATFORMS = {"blinkit": "Blinkit"}
RETAIL_BOUNDS_PER_KG = {
    "Onion": (10.0, 150.0),
    "Potato": (10.0, 120.0),
    "Tomato": (10.0, 200.0),
    "Garlic": (60.0, 600.0),
    "Green Chilli": (30.0, 400.0),
}

UPSERT_SQL = """
INSERT INTO retail_price
    (crop_id, city, platform, price_date, price, unit, data_type, confidence_note)
VALUES (%s, %s, %s, %s, %s, 'kg', 'scraped', %s)
ON CONFLICT (crop_id, city, platform, price_date) DO UPDATE
SET price = EXCLUDED.price,
    confidence_note = EXCLUDED.confidence_note
WHERE retail_price.data_type = 'scraped'
RETURNING (xmax = 0) AS inserted
"""


def fail(message):
    print(f"::error::{message}")
    sys.exit(1)


def warn(row_num, message):
    print(f"::warning::Sheet row {row_num}: {message}")


def read_sheet_text():
    if os.path.exists(RETAIL_SHEET_URL):
        with open(RETAIL_SHEET_URL, encoding="utf-8") as f:
            return f.read()
    try:
        response = requests.get(RETAIL_SHEET_URL, timeout=30)
    except requests.RequestException as exc:
        fail(f"Could not reach the retail Google Sheet ({type(exc).__name__})")
    if response.status_code != 200:
        fail(f"Failed to fetch retail Google Sheet: HTTP {response.status_code}")
    response.encoding = "utf-8"
    return response.text


def load_rows():
    reader = csv.DictReader(io.StringIO(read_sheet_text()))
    columns = [(c or "").strip() for c in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        fail(f"Retail sheet is missing columns: {missing}. Found: {columns}")
    rows = []
    for raw in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k is not None}
        if any(row.values()):
            rows.append(row)
    return rows


def validate_row(row, crop_ids, latest_allowed):
    crop = crop_ids.get(row["crop_name"].lower())
    if crop is None:
        return None, f"unknown crop '{row['crop_name']}'"
    crop_name, crop_id = crop
    bounds = RETAIL_BOUNDS_PER_KG.get(crop_name)
    if bounds is None:
        return None, f"no retail price bounds configured for {crop_name}"
    try:
        price_date = datetime.strptime(row["entry_date"], "%Y-%m-%d").date()
    except ValueError:
        return None, f"bad date '{row['entry_date']}' (use YYYY-MM-DD)"
    if price_date > latest_allowed:
        return None, f"date {price_date} is in the future"
    city = ALLOWED_CITIES.get(row["city"].lower())
    if city is None:
        return None, f"unknown city '{row['city']}' (allowed: {', '.join(ALLOWED_CITIES.values())})"
    platform = ALLOWED_PLATFORMS.get(row["platform"].lower())
    if platform is None:
        return None, f"unknown platform '{row['platform']}' (allowed: {', '.join(ALLOWED_PLATFORMS.values())})"
    if row["unit"].lower() != "kg":
        return None, f"unit must be kg, got '{row['unit']}'"
    try:
        price = float(row["price_per_kg"].replace(",", ""))
    except ValueError:
        return None, f"bad price '{row['price_per_kg']}'"
    low, high = bounds
    if not (low <= price <= high):
        return None, f"{crop_name} price {price} is outside {low}-{high} Rs/kg"
    return (crop_id, city, platform, price_date, price, row["notes"] or None), None


def main():
    if not DATABASE_URL:
        fail("DATABASE_URL not found - check .env file")
    if not RETAIL_SHEET_URL:
        fail("RETAIL_SHEET_URL not found - check .env file")

    rows = load_rows()
    latest_allowed = date.today() + timedelta(days=1)
    stats = {"processed": 0, "inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0}

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        cur = conn.cursor()
        cur.execute("SELECT crop_name, crop_id FROM crop_master")
        crop_ids = {name.lower(): (name, cid) for name, cid in cur.fetchall()}
        crop_names = {cid: name for name, cid in crop_ids.values()}
        cur.execute(
            "SELECT crop_id, city, platform, price_date, price FROM retail_price WHERE data_type = 'scraped'"
        )
        existing = {(c, ci, p, d): float(pr) for c, ci, p, d, pr in cur.fetchall()}

        seen = {}
        for row_num, row in enumerate(rows, start=2):
            stats["processed"] += 1
            record, error = validate_row(row, crop_ids, latest_allowed)
            if error:
                stats["skipped"] += 1
                warn(row_num, error)
                continue
            crop_id, city, platform, price_date, price, note = record
            key = (crop_id, city, platform, price_date)
            if key in seen and seen[key] != price:
                warn(row_num, f"conflicting duplicate for {crop_names[crop_id]} {city} {price_date}: {seen[key]} vs {price}; last row wins")
            seen[key] = price
            if existing.get(key) == price:
                stats["unchanged"] += 1
                continue
            cur.execute(UPSERT_SQL, (crop_id, city, platform, price_date, price, note))
            result = cur.fetchone()
            if result is None:
                stats["skipped"] += 1
                warn(row_num, f"{city} {price_date} already exists as a non-scraped row; left untouched")
            elif result[0]:
                stats["inserted"] += 1
            else:
                stats["updated"] += 1
                print(f"::notice::Updated {crop_names[crop_id]} {city} {price_date}: {existing.get(key)} -> {price}")
            existing[key] = price
        conn.commit()
        cur.close()
    except Exception as exc:
        conn.rollback()
        fail(f"Retail load failed and was rolled back: {exc}")
    finally:
        conn.close()

    print(f"Total processed: {stats['processed']}")
    print(f"Total inserted: {stats['inserted']}")
    print(f"Total updated: {stats['updated']}")
    print(f"Total unchanged: {stats['unchanged']}")
    print(f"Total skipped: {stats['skipped']}")
    if stats["skipped"]:
        print(f"::warning::{stats['skipped']} sheet row(s) were skipped - fix them in the Google Sheet")
    if stats["inserted"] + stats["updated"] + stats["unchanged"] == 0:
        fail("No valid retail rows found in the sheet")


if __name__ == "__main__":
    main()
