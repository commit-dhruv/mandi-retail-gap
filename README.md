# 🌾 KrishiPulse — Agri-Mandi Price Anomaly Radar (Gujarat)

A data pipeline and dashboard that detects abnormal price gaps between Gujarat's wholesale APMC mandi prices and retail markets, surfacing where consumers are being overcharged by intermediaries.

**🔴 Live Demo:** [mandi-retail-gap.streamlit.app](https://mandi-retail-gap.streamlit.app/)

---

## Problem Statement

Indian farmers typically receive only 30–40% of the final retail price of staple vegetables. The remaining 60–70% is absorbed by commission agents, transport intermediaries, and regional distributors. Wholesale APMC mandi prices are publicly available, but consumers, kirana store owners, and small food businesses have no easy way to compare them against what they're actually being charged at the retail end.

KrishiPulse makes this gap visible in real time.

---

## What It Does

- Ingests daily wholesale prices for 5 crops (Onion, Potato, Tomato, Garlic, Green Chilli) across 22 Gujarat districts from the Government of India's Agmarknet API (data.gov.in)
- Compares against retail prices from Blinkit Ahmedabad (logged daily) extended to 5 other Gujarat cities via a calibrated margin model
- Detects statistically significant anomalies using SQL rolling z-score detection (30-day baseline, 2 standard deviation threshold, 7-record minimum)
- Surfaces findings on an interactive Streamlit dashboard with a Gujarat district heat-map, price trend charts, and a flagged anomaly table
- Runs fully automated via GitHub Actions twice-daily cron (9 AM IST + 2:30 PM IST)

---

## Tech Stack

| Component                 | Tool                                        |
| ------------------------- | ------------------------------------------- |
| Data ingestion & cleaning | Python (requests, psycopg2, pandas)         |
| Database                  | PostgreSQL on Neon (free tier)              |
| Anomaly detection         | SQL window functions (rolling AVG + STDDEV) |
| Dashboard                 | Streamlit                                   |
| Mapping                   | Plotly choropleth + free Gujarat GeoJSON    |
| Automation                | GitHub Actions (twice-daily cron)           |
| Deployment                | Streamlit Community Cloud                   |

---

## Project Structure

```
mandi-retail-gap/
├── ingestion/
│   ├── load_retail_manual.py     # Reads daily retail prices from Google Sheet
│   └── generate_retail_model.py  # Generates modeled retail prices for 5 cities
├── pipeline/
│   ├── load_master_data.py       # Seeds crop_master and crop_alias_map
│   ├── load_wholesale_data.py    # Daily Agmarknet API pull and cleaning
│   ├── anomaly_detection.sql     # Rolling z-score window function query
│   └── run_anomaly_detection.py  # Executes anomaly SQL programmatically
├── dashboard/
│   └── app.py                    # Streamlit dashboard (4 tabs)
├── db/
│   └── schema.sql                # Six-table PostgreSQL schema
├── .github/workflows/
│   └── daily_ingestion.yml       # GitHub Actions cron (twice daily)
├── .env.example                  # Environment variable template
├── requirements.txt
├── METHODOLOGY.md                # Hybrid retail data approach + limitations
└── README.md
```

---

## Data Sources

1. **Agmarknet API (data.gov.in)** — Variety-wise Daily Market Prices, Gujarat mandis, 5 crops, daily live pull. Resource ID: `9ef84268-d588-465a-a308-a864a43d0070`
2. **Blinkit Ahmedabad** — retail prices manually logged daily for all 5 crops (standard variety, first non-organic result)
3. **Calibrated retail model** — Ahmedabad price × city margin factor for Surat (1.05), Vadodara (1.03), Rajkot (1.08), Gandhinagar (1.02), Bhavnagar (1.10)
4. **Gujarat district GeoJSON** — open boundary data for the district heat-map

---

## Anomaly Detection Methodology

For each crop × mandi combination, a 30-day trailing average (baseline) and standard deviation are computed using SQL window functions, excluding the current day. A z-score is calculated as `(today's price − baseline) / stddev`. Any price more than 2 standard deviations from the baseline is flagged. A minimum of 7 preceding records is required before computing a z-score — this prevents low-data mandis from generating statistically unreliable flags. The same logic runs against retail prices. Both sides feed the `price_anomaly` table, which re-populates daily.

See `METHODOLOGY.md` for full details including known limitations.

---

## Key Findings

- **Amreli district** shows a consistent pattern of wholesale price drops not being passed to retail consumers — appearing in multiple anomaly flags for Tomato and Green Chilli across the observation period
- **Late July 2026 monsoon rains** in Gujarat caused statistically significant wholesale price spikes (Green Chilli Gandhinagar z-score: +4.68, Tomato Rajkot z-score: −4.29) detected automatically from mandi data with no manual tagging
- **Garlic and Green Chilli** consistently show the highest retail premiums (300–500%) across all districts, reflecting structural supply chain markups beyond transport costs alone

---

## Local Setup

```bash
# 1. Clone the repo
git clone https://github.com/commit-dhruv/mandi-retail-gap.git
cd mandi-retail-gap

# 2. Create virtual environment and install dependencies
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt

# 3. Set up environment variables
cp .env.example .env
# Edit .env — add DATABASE_URL and DATA_GOV_API_KEY

# 4. Create the database schema
# Run db/schema.sql against your PostgreSQL database

# 5. Seed master data
python pipeline/load_master_data.py

# 6. Pull first day of wholesale data
python pipeline/load_wholesale_data.py

# 7. Run the dashboard
streamlit run dashboard/app.py
```

For Streamlit Cloud deployment, add `DATABASE_URL` and `RETAIL_SHEET_URL` under Settings → Secrets in TOML format.

---

## Design Decisions and Limitations

- **Retail scraping ruled out** — Blinkit uses JS-rendered pages with authenticated POST requests. Manual daily logging chosen as a reliable, documented alternative.
- **No historical backfill** — Agmarknet API serves only the current day's snapshot; history is built by daily accumulation. Anomaly detection becomes more reliable as history grows.
- **Backfill data (before July 18, 2026)** — estimated retail prices, documented as backfill in the `notes` column of `retail_price`. Not real Blinkit prices.
- **Variety aggregation** — Agmarknet reports at variety level (e.g., Red vs White onion). This project maps all varieties to crop level via `crop_alias_map`. Stated explicitly as a simplification.
- **Data latency** — Mandi reports can arrive 1–2 days late. Records younger than 3 days are flagged `is_provisional = true` and excluded from anomaly calculations.
- **No ML** — Anomaly detection uses rolling z-scores, not machine learning. This is a deliberate choice: the method is fully explainable, auditable, and doesn't require training data.

---

## Disclaimer

Retail price data for cities other than Ahmedabad is model-estimated. Backfill retail data before July 18, 2026 is estimated, not real. All methodology decisions are documented in `METHODOLOGY.md`.
