import os
import sys

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

CITY_FACTORS = {
    "Surat": 1.05,
    "Vadodara": 1.03,
    "Rajkot": 1.08,
    "Gandhinagar": 1.02,
    "Bhavnagar": 1.10,
}

CONFIDENCE_NOTE = "Modeled: Ahmedabad Blinkit price × city margin factor (transport/distribution estimate)"

UPSERT_SQL = """
INSERT INTO retail_price
    (crop_id, city, platform, price_date, price, unit, data_type, confidence_note)
VALUES %s
ON CONFLICT (crop_id, city, platform, price_date) DO UPDATE
SET price = EXCLUDED.price,
    confidence_note = EXCLUDED.confidence_note
WHERE retail_price.data_type = 'modeled'
  AND retail_price.price IS DISTINCT FROM EXCLUDED.price
RETURNING (xmax = 0) AS inserted
"""


def fail(message):
    print(f"::error::{message}")
    sys.exit(1)


def main():
    if not DATABASE_URL:
        fail("DATABASE_URL not found - check .env file")

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT crop_id, price_date, price FROM retail_price "
            "WHERE city = 'Ahmedabad' AND data_type = 'scraped'"
        )
        source_rows = cur.fetchall()
        if not source_rows:
            fail("No Ahmedabad scraped data found - run load_retail_manual.py first")

        rows = {}
        for crop_id, price_date, base_price in source_rows:
            for city, factor in CITY_FACTORS.items():
                modeled_price = round(float(base_price) * factor, 2)
                rows[(crop_id, city, price_date)] = (
                    crop_id, city, "modeled", price_date, modeled_price, "kg", "modeled", CONFIDENCE_NOTE,
                )

        results = execute_values(cur, UPSERT_SQL, list(rows.values()), page_size=500, fetch=True)
        conn.commit()
        cur.close()
    except Exception as exc:
        conn.rollback()
        fail(f"Model generation failed and was rolled back: {exc}")
    finally:
        conn.close()

    inserted = sum(1 for r in results if r[0])
    updated = len(results) - inserted
    unchanged = len(rows) - len(results)
    print(f"Modeled rows inserted: {inserted}")
    print(f"Modeled rows updated: {updated}")
    print(f"Modeled rows unchanged: {unchanged}")


if __name__ == "__main__":
    main()
