import os
import sys

import psycopg2

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MAX_WHOLESALE_AGE_DAYS = int(os.getenv("MAX_WHOLESALE_AGE_DAYS", "8"))
MAX_RETAIL_AGE_DAYS = int(os.getenv("MAX_RETAIL_AGE_DAYS", "4"))


def latest(cur, sql):
    cur.execute(sql)
    return cur.fetchone()


def main():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set")
        return 2
    conn = psycopg2.connect(url, connect_timeout=15)
    cur = conn.cursor()
    w_date, w_age = latest(
        cur, "SELECT MAX(price_date), CURRENT_DATE - MAX(price_date) FROM wholesale_price"
    )
    r_date, r_age = latest(
        cur,
        "SELECT MAX(price_date), CURRENT_DATE - MAX(price_date) FROM retail_price WHERE data_type = 'scraped'",
    )
    conn.close()

    print(f"Wholesale latest date: {w_date} ({w_age} days old, limit {MAX_WHOLESALE_AGE_DAYS})")
    print(f"Retail (scraped) latest date: {r_date} ({r_age} days old, limit {MAX_RETAIL_AGE_DAYS})")

    problems = []
    if w_date is None or w_age > MAX_WHOLESALE_AGE_DAYS:
        problems.append("Wholesale data is stale: run the weekly Agmarknet CSV import")
    if r_date is None or r_age > MAX_RETAIL_AGE_DAYS:
        problems.append("Retail data is stale: check the Google Sheet logging")
    for p in problems:
        print("STALE:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
