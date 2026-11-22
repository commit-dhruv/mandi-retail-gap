# 🌾 KrishiPulse - Agri-Mandi Price Anomaly Radar (Gujarat)

A data pipeline and dashboard that compares Gujarat's wholesale APMC mandi prices with retail prices and flags statistically unusual price movements, showing where retail prices sit far above what wholesale prices suggest.

**Live demo:** [mandi-retail-gap.streamlit.app](https://mandi-retail-gap.streamlit.app/)

---

## Problem Statement

Indian farmers typically receive only 30-40% of the final retail price of staple vegetables. The remaining 60-70% is absorbed by commission agents, transport intermediaries and regional distributors. Wholesale APMC mandi prices are publicly available, but consumers, kirana store owners and small food businesses have no easy way to compare them against what they are charged at the retail end.

KrishiPulse makes that gap visible.

---

## What It Does

- Ingests wholesale prices for 5 crops (Onion, Potato, Tomato, Garlic, Green Chilli) from 46 mandis in 23 Gujarat districts, using the data.gov.in "Variety-wise Daily Market Prices" archive, refreshed twice a day
- Compares them with Blinkit Ahmedabad prices logged by hand since 18 Jul 2026, extended to five other Gujarat cities with a margin model
- Detects statistically unusual price moves with SQL rolling z-scores (30-day baseline, 2 standard deviations, 20% minimum move, 7-record minimum)
- Shows the results in a Streamlit dashboard with a Gujarat district heat-map, price trend charts and a flagged anomaly table
- Sends flagged anomalies to Telegram (up to 5 per run, no repeats)
- Runs on two schedules: a timer on the project laptop (wholesale load, anomaly detection, Telegram alerts) and GitHub Actions (retail load, margin model, anomaly detection, data freshness check)

**Data state on 22 Nov 2026:** 13,012 wholesale rows over 155 consecutive days (20 Jun - 21 Nov 2026), 12,043 scored rows, 1,005 flagged (8.3%).

---

## Data Timeline

| Period | Wholesale source |
|---|---|
| 20 Jun - 25 Sep 2026 | data.gov.in API, "Current Daily Price of Various Commodities from Various Markets (Mandi)", a current-day feed accumulated daily |
| 26 Sep - 9 Nov 2026 | Manual import of Agmarknet 2.0 Price and Arrivals Report CSV files |
| 13 Nov 2026 onward | data.gov.in "Variety-wise Daily Market Prices Data of Commodity" archive, loaded by the laptop timer |

From 25 Sep 2026, scheduled GitHub-hosted runs could not connect to api.data.gov.in, while the same calls succeed from India. The cause has not been identified, so wholesale loading moved to a timer on the project laptop. On 13 Nov the Variety-wise archive was used to backfill 20 Jun - 25 Sep (8,283 rows, 13 missing days filled) and matched all 3,856 CSV-imported rows with 0 price differences. The CSV importer remains as a manual fallback. Details and evidence are in `METHODOLOGY.md`.

---

## Tech Stack

| Component | Tool |
|---|---|
| Data ingestion and cleaning | Python (requests, psycopg2, pandas) |
| Wholesale fallback | Agmarknet 2.0 CSV import (pandas) |
| Database | PostgreSQL on Neon (free tier) |
| Anomaly detection | SQL window functions (rolling AVG and STDDEV) |
| Dashboard | Streamlit |
| Mapping | Plotly choropleth with a free Gujarat GeoJSON |
| Alerts | Telegram Bot API (plain requests) |
| Automation | systemd user timer (laptop) and GitHub Actions |
| Deployment | Streamlit Community Cloud |

---

## Project Structure

    mandi-retail-gap/
    ├── ingestion/
    │   ├── load_retail_manual.py        # Reads and validates retail prices from the Google Sheet
    │   └── generate_retail_model.py     # Builds modeled retail prices for five cities
    ├── pipeline/
    │   ├── load_master_data.py          # Seeds crop_master and crop_alias_map
    │   ├── load_wholesale_variety.py    # Wholesale loader (Variety-wise archive), run by the laptop timer
    │   ├── load_wholesale_data.py       # Original loader for the current-day feed (20 Jun - 25 Sep), kept, not scheduled
    │   ├── import_agmarknet_file.py     # Manual Agmarknet CSV importer (fallback)
    │   ├── sources/                     # CSV parsing and the shared record format
    │   ├── anomaly_detection.sql        # Rolling z-score anomaly query
    │   ├── run_anomaly_detection.py     # Runs the anomaly SQL
    │   ├── send_telegram_alerts.py      # Sends flagged anomalies to Telegram
    │   ├── check_freshness.py           # Fails the run if data is stale
    │   ├── health_check.py              # 14 read-only data quality checks
    │   └── raw/                         # Sample raw API responses
    ├── deploy/systemd/                  # Laptop timer and service unit files
    ├── dashboard/app.py                 # Streamlit dashboard (4 tabs)
    ├── db/schema.sql                    # Six core tables (migration 002 adds ingestion_log)
    ├── migrations/002_multisource.sql   # Adds the source column and the ingestion_log table
    ├── data_inbox/                      # Drop zone for downloaded CSV files
    ├── docs/WEEKLY_IMPORT.md            # Manual CSV import checklist
    ├── .github/workflows/daily_ingestion.yml
    ├── .env.example
    ├── METHODOLOGY.md
    ├── requirements.txt
    └── README.md

---

## Data Sources

1. **data.gov.in, Variety-wise Daily Market Prices Data of Commodity** (ID 35985678-0d79-46b4-9ed6-6f13308a1d24): full archive, Gujarat mandis, 5 crops. Used from 13 Nov 2026 and for the backfill of 20 Jun - 25 Sep.
2. **data.gov.in, Current Daily Price of Various Commodities from Various Markets (Mandi)** (ID 9ef84268-d588-465a-a308-a864a43d0070): the original current-day feed, used for 20 Jun - 25 Sep 2026.
3. **Agmarknet 2.0 CSV reports** (agmarknet.gov.in, Price and Arrivals Report): manual fallback; supplied 26 Sep - 9 Nov 2026.
4. **Blinkit Ahmedabad:** retail prices logged by hand each day for all 5 crops since 18 Jul 2026, using the same product every day.
5. **Margin model:** Ahmedabad price multiplied by a city factor, Surat 1.05, Vadodara 1.03, Rajkot 1.08, Gandhinagar 1.02, Bhavnagar 1.10.
6. **Gujarat district GeoJSON:** open boundary data for the heat-map.

---

## Anomaly Detection

Each crop and mandi gets one price series, using the mandi's most-reported variety. For every day, a 30-day trailing average and standard deviation are computed with SQL window functions, excluding the current day. A wholesale price is flagged when its z-score is above 2 in absolute value and the price is at least 20% away from its baseline. At least 7 earlier records are required before a z-score is computed, and the latest 3 days of data are treated as provisional and excluded. Retail z-scores use only the logged Ahmedabad prices. Each mandi is compared with the retail price of its district's city: Ahmedabad uses logged Blinkit prices, five other cities use the modeled price, and all remaining districts use Ahmedabad as the Gujarat reference.

The `price_anomaly` table is rebuilt on every run. Result on 22 Nov 2026: 1,005 flagged rows out of 12,043 scored rows (8.3%), scored through 18 Nov. See `METHODOLOGY.md` for the full method and its limits.

---

## Telegram Alerts

`pipeline/send_telegram_alerts.py` sends flagged rows from the last 14 days, newest first, at most 5 per run, and never sends the same row twice (tracked in a local `alerts_sent.json`). Because the latest 3 days are provisional, an alert arrives about 3 days after the price date. It sends nothing unless run with `--send`, and a failure cannot stop the wholesale load.

---

## Key Findings

- **Retail premium by crop.** Over the 30 scored days from 20 Oct to 18 Nov 2026, the average retail premium over the mandi price was highest for Green Chilli (about 394%), then Tomato (239%), Garlic (199%), Potato (150%) and Onion (85%). The premium is the average of daily mandi-level values of (retail price - wholesale price) / wholesale price. Blinkit is a quick-commerce channel, so these figures include convenience markups and are an upper-end indicator, not the intermediary margin alone.
- **Flags are indicators, not verdicts.** Some flags reflect a change in what a mandi reports rather than a real price move; an example is described in `METHODOLOGY.md`.

---

## Operating the Pipeline

**Laptop timer (wholesale, anomalies, alerts).** Runs at 10:00 and 18:00 IST while the laptop is on and logged in. Install:

    cp deploy/systemd/krishipulse-wholesale.service deploy/systemd/krishipulse-wholesale.timer ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now krishipulse-wholesale.timer

Check the last run with `systemctl --user status krishipulse-wholesale.service --no-pager`.

**GitHub Actions.** Scheduled for 09:00 and 14:30 IST (GitHub may start runs hours late). Runs the retail loader, margin model, anomaly detection and the freshness check, which fails the run if the newest wholesale row is more than 5 days old or the newest logged retail row is more than 4 days old.

**Health check.** `python pipeline/health_check.py` runs 14 read-only checks and prints PASS or FAIL for each.

**Manual fallback.** If the timer stalls, import Agmarknet CSV files by following `docs/WEEKLY_IMPORT.md`.

---

## Local Setup

    # 1. Clone the repo
    git clone https://github.com/commit-dhruv/mandi-retail-gap.git
    cd mandi-retail-gap

    # 2. Create a virtual environment and install dependencies
    python -m venv .venv
    source .venv/bin/activate       # Linux/macOS (Fish shell: source .venv/bin/activate.fish)
    # .venv\Scripts\activate        # Windows
    pip install -r requirements.txt

    # 3. Set up environment variables
    cp .env.example .env
    # Edit .env: DATABASE_URL, RETAIL_SHEET_URL, DATA_GOV_API_KEY
    # Optional, for alerts: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

    # 4. Create the schema, then apply the migration
    # Run db/schema.sql and then migrations/002_multisource.sql against your PostgreSQL database

    # 5. Seed master data
    python pipeline/load_master_data.py

    # 6. Load wholesale data (last 7 days), or import a downloaded CSV (see docs/WEEKLY_IMPORT.md)
    python pipeline/load_wholesale_variety.py --days 7

    # 7. Load retail data, build the model and run anomaly detection
    python ingestion/load_retail_manual.py
    python ingestion/generate_retail_model.py
    python pipeline/run_anomaly_detection.py

    # 8. Optional: preview alerts (dry run), then run the dashboard
    python pipeline/send_telegram_alerts.py
    streamlit run dashboard/app.py

For Streamlit Cloud, add `DATABASE_URL` under Settings -> Secrets in TOML format. The dashboard only needs that one value.

---

## Design Decisions and Limitations

- **Retail data is logged by hand.** Blinkit uses JavaScript-rendered pages with authenticated requests, so automated scraping was ruled out after a feasibility test.
- **Estimated retail period.** Retail values from 20 Jun to 17 Jul 2026 are estimates, stored as modeled, not plotted on the dashboard, and still used as the retail reference for those dates.
- **Modeled cities.** Retail prices for five cities come from Ahmedabad with margin factors based on distance, not fitted to real data from those cities.
- **Wholesale loading runs on a laptop.** GitHub-hosted runners cannot reach the data.gov.in API, so a laptop timer does the load. It only runs while the laptop is on, and the GitHub freshness check is the backstop.
- **Alerts lag by about 3 days** and are sent only when the laptop timer runs.
- **One variety per mandi.** Each mandi uses its most-reported variety, a stated simplification.
- **No machine learning.** Rolling z-scores are explainable, auditable and need no training data.
- **Flags are indicators.** A flagged row is a prompt to investigate, not proof of overcharging.
- **Heat-map coverage.** 23 of Gujarat's 34 districts have a reporting mandi for these crops; the other 11 are blank.

---

## Disclaimer

Retail prices for cities other than Ahmedabad are model estimates, and retail values before 18 July 2026 are estimates. Wholesale data is daily-refreshed from Agmarknet, not real-time. All methodology decisions are documented in `METHODOLOGY.md`.
