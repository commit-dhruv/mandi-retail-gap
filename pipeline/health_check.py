"""Read-only health check for the KrishiPulse database. Prints PASS or FAIL per check; exit code 1 if any fail."""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.sources.csv_import import VALID_BOUNDS_PER_KG  # noqa: E402

load_dotenv(ROOT / ".env")


def is_zero(value):
    return value == 0


def at_most(limit):
    return lambda value: value is not None and float(value) <= limit


def between(low, high):
    return lambda value: value is not None and low <= float(value) <= high


BOUNDS_SQL = " OR ".join(
    f"(c.crop_name = '{name}' AND (w.modal_price < {low} OR w.modal_price > {high}))"
    for name, (low, high) in VALID_BOUNDS_PER_KG.items()
)

CHECKS = [
    ("wholesale duplicate keys",
     "SELECT COUNT(*) FROM (SELECT 1 FROM wholesale_price GROUP BY crop_id, mandi_id, variety, price_date HAVING COUNT(*) > 1) d",
     is_zero, "0"),
    ("retail duplicate keys",
     "SELECT COUNT(*) FROM (SELECT 1 FROM retail_price GROUP BY crop_id, city, platform, price_date HAVING COUNT(*) > 1) d",
     is_zero, "0"),
    ("anomaly duplicate keys",
     "SELECT COUNT(*) FROM (SELECT 1 FROM price_anomaly GROUP BY crop_id, mandi_id, price_date HAVING COUNT(*) > 1) d",
     is_zero, "0"),
    ("wholesale rows with min > modal, modal > max or min <= 0",
     "SELECT COUNT(*) FROM wholesale_price WHERE min_price > modal_price OR modal_price > max_price OR min_price <= 0",
     is_zero, "0"),
    ("wholesale rows outside per-crop price bounds",
     f"SELECT COUNT(*) FROM wholesale_price w JOIN crop_master c USING (crop_id) WHERE {BOUNDS_SQL}",
     is_zero, "0"),
    ("mandis without a district",
     "SELECT COUNT(*) FROM mandi_master WHERE district IS NULL",
     is_zero, "0"),
    ("mandi count",
     "SELECT COUNT(*) FROM mandi_master",
     between(40, 50), "40 to 50"),
    ("stale provisional rows (older than 3 days)",
     "SELECT COUNT(*) FROM wholesale_price WHERE is_provisional AND price_date < CURRENT_DATE - 3",
     is_zero, "0"),
    ("wholesale data age in days",
     "SELECT CURRENT_DATE - MAX(price_date) FROM wholesale_price",
     at_most(5), "at most 5"),
    ("retail (logged) data age in days",
     "SELECT CURRENT_DATE - MAX(price_date) FROM retail_price WHERE data_type = 'scraped'",
     at_most(4), "at most 4"),
    ("missing wholesale days since 2026-06-20",
     "SELECT COUNT(*) FROM generate_series('2026-06-20'::date, (SELECT MAX(price_date) FROM wholesale_price), '1 day') d "
     "WHERE NOT EXISTS (SELECT 1 FROM wholesale_price w WHERE w.price_date = d::date)",
     is_zero, "0"),
    ("retail city names untrimmed or unexpected",
     "SELECT COUNT(*) FROM (SELECT DISTINCT city FROM retail_price) t "
     "WHERE city <> btrim(city) OR city NOT IN ('Ahmedabad','Surat','Vadodara','Rajkot','Gandhinagar','Bhavnagar')",
     is_zero, "0"),
    ("anomaly flag rate in percent",
     "SELECT ROUND(100.0 * SUM(anomaly_flag::int) / NULLIF(COUNT(*), 0), 1) FROM price_anomaly",
     between(3, 15), "3 to 15"),
    ("hours since last successful wholesale load",
     "SELECT ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(started_at))) / 3600) FROM ingestion_log "
     "WHERE status = 'success' AND source = 'datagov_variety'",
     at_most(26), "at most 26"),
]


def main():
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        print("DATABASE_URL is not set")
        return 2
    print("database host:", urlparse(url).hostname)
    conn = psycopg2.connect(url, connect_timeout=15)
    cur = conn.cursor()
    failures = 0
    for label, sql, ok, expected in CHECKS:
        cur.execute(sql)
        value = cur.fetchone()[0]
        passed = ok(value)
        failures += 0 if passed else 1
        print(f"{'PASS' if passed else 'FAIL'}  {label}: {value} (expected {expected})")
    conn.close()
    print(f"\n{len(CHECKS) - failures} of {len(CHECKS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
