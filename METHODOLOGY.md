# KrishiPulse — Methodology Note

This document explains the data approach, design decisions, and known limitations of the KrishiPulse pipeline. It is intended for evaluators, mentors, and anyone reproducing or extending this project.

---

## 1. Wholesale Data Layer

**Source:** Agmarknet API via data.gov.in (resource ID: `9ef84268-d588-465a-a308-a864a43d0070`)
**Coverage:** Gujarat mandis, 5 crops (Onion, Potato, Tomato, Garlic, Green Chilli)
**Frequency:** Pulled twice daily (9:00 AM IST and 2:30 PM IST) via GitHub Actions cron

### Key decisions

**Unit normalization:** Agmarknet reports prices in rupees per quintal (100 kg). All prices are converted to ₹/kg by dividing by 100 before storage. This is applied consistently so wholesale and retail prices are comparable.

**Date parsing:** Agmarknet returns dates as `DD/MM/YYYY` strings. These are parsed explicitly using `datetime.strptime(date_str, '%d/%m/%Y')` — never using pandas auto-inference, which silently swaps day and month for ambiguous dates.

**Per-crop price sanity bounds:** After unit conversion, any record outside the following ranges is rejected (skipped entirely, never inserted):

| Crop | Min (₹/kg) | Max (₹/kg) |
|---|---|---|
| Onion | 2 | 100 |
| Potato | 3 | 80 |
| Tomato | 5 | 150 |
| Garlic | 20 | 500 |
| Green Chilli | 10 | 300 |

This catches Agmarknet data entry errors (e.g., Mehsana Tomato reported at ₹0.80–₹2.30/kg, confirmed as clerk entry errors by cross-referencing real market rates).

**Provisional flag:** Records with `price_date` within 3 days of today are marked `is_provisional = true` and excluded from anomaly detection. This accounts for late-arriving mandi reports from rural areas. The flag is cleared daily for records older than 3 days.

**Upsert, not insert:** Daily cron re-pulls the same day's data (mandis report throughout the day). A unique constraint on `(crop_id, mandi_id, variety, price_date)` combined with `INSERT ... ON CONFLICT DO UPDATE` prevents duplicate rows.

**Raw JSON preservation:** Each API response is saved to `pipeline/raw/raw_wholesale_{crop}_{date}.json` before any cleaning. This allows reprocessing if cleaning logic changes, without re-hitting the API.

**API quirks discovered during build:**
- Requires a browser `User-Agent` header — blank headers return 502
- Filter keys use `.keyword` suffix: `filters[state.keyword]`, `filters[commodity.keyword]`
- The API is a current-day snapshot only — it has no historical archive. History is built by daily accumulation.
- Agmarknet populates throughout the day. Early-morning pulls (before 9 AM IST) frequently return zero records. Cron times were adjusted after observing a 41% failure rate at 6:00 AM IST.

---

## 2. Retail Data Layer

The retail data layer uses a hybrid approach: real manually-logged prices for one city (Ahmedabad), extended to five other Gujarat cities using a calibrated margin model. This is disclosed transparently rather than treated as scraped data.

### 2a. Real scraped data (Ahmedabad)

**Platform:** Blinkit Ahmedabad
**Method:** Manual daily logging — a teammate checks Blinkit each morning and logs the price of one standard product per crop into a shared Google Sheet
**Product selection rules:** First non-organic, non-specialty result per crop (e.g., "Onion (Dungdi) 1kg", "Potato (Bateta) 1kg"). Same product logged every day for time-series consistency.
**Unit conversion:** Prices normalized to ₹/kg before logging (e.g., ₹41/100g → ₹410/kg for Garlic)
**Storage:** Ingested daily by `ingestion/load_retail_manual.py`, stored as `data_type = 'scraped'` in `retail_price`

**Why not automated scraping?** Blinkit uses JS-rendered pages with authenticated POST requests carrying location tokens. Automated scraping was ruled out after a feasibility test — replicating the auth tokens is fragile and would break on any platform update. Manual logging was chosen as a reliable, auditable alternative.

**Backfill note:** Retail prices before July 18, 2026 (the date manual logging began) are estimated values, not real Blinkit prices. These are documented with `notes = 'estimated backfill'` in the `retail_price` table. They provide a baseline for the retail rolling average but should not be treated as ground truth.

### 2b. Calibrated model (other cities)

**Cities:** Surat, Vadodara, Rajkot, Gandhinagar, Bhavnagar
**Formula:** `modeled_price = ahmedabad_scraped_price × city_margin_factor`

| City | Margin Factor | Rationale |
|---|---|---|
| Gandhinagar | 1.02 | Adjacent to Ahmedabad, minimal transport cost |
| Vadodara | 1.03 | Close, well-connected |
| Surat | 1.05 | ~260 km, moderate distribution markup |
| Rajkot | 1.08 | ~220 km, smaller distribution network |
| Bhavnagar | 1.10 | Coastal location, higher last-mile cost |

**Storage:** Generated daily by `ingestion/generate_retail_model.py`, stored as `data_type = 'modeled'` in `retail_price`

**Stated limitations of this model:**
- Margin factors are estimates based on distance and distribution logic — not fitted to real price data from those cities
- The model assumes a linear relationship between Ahmedabad price and other cities, which may not hold during supply disruptions (e.g., floods, local crop failures)
- All 5 modeled cities move together when Ahmedabad price changes — this can cause mass anomaly flags on the retail z-score side when a single Ahmedabad data point is unusual. For this reason, anomalies driven purely by retail z-score (with null or low wholesale z-score) should be treated as indicative rather than definitive.

---

## 3. Anomaly Detection

**Method:** SQL window-function rolling z-score — no machine learning.

**Why not ML?** A rolling z-score is statistically sound, fully explainable, and doesn't require training data. Any flagged row can be manually verified by inspecting the price, baseline, and z-score directly. ML would introduce a black-box layer to a system whose value is transparency.

### Algorithm

For each crop × mandi combination (wholesale) and crop × city combination (retail):

1. Compute a 30-day trailing average (baseline) and standard deviation using SQL window functions, looking back 30 days and excluding today:
```sql
AVG(modal_price) OVER (
    PARTITION BY crop_id, mandi_id
    ORDER BY price_date
    ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING
)
```

2. Compute z-score:
```
z_score = (today's price − baseline) / standard_deviation
```

3. Flag if `|z_score| > 2` (price is more than 2 standard deviations from normal)

4. Also compute wedge: `wedge_pct = (retail_price − wholesale_price) / wholesale_price × 100`

**Guards against false positives:**
- `is_provisional = false` filter excludes data younger than 3 days
- Minimum 7 preceding records required before computing a z-score — prevents low-data mandis with tiny standard deviations from generating extreme z-scores from minor price moves
- `NULLIF(stddev, 0)` prevents division-by-zero when all preceding prices are identical
- Per-crop price bounds catch bad source data before it enters the baseline calculation

**Results are re-computed daily** — `price_anomaly` is fully cleared and repopulated on each run, making it idempotent.

---

## 4. Known Limitations

| Limitation | Impact | Status |
|---|---|---|
| Agmarknet is current-day only, no historical archive | 30-day baseline takes 30+ days to become meaningful | Accepted — daily accumulation strategy, documented |
| Retail backfill (before Jul 18, 2026) is estimated | Retail baseline for early dates is approximate | Documented in `notes` column |
| Modeled retail prices move in lockstep | Can cause mass retail z-score flags on unusual Ahmedabad days | Accepted — flagged rows filtered by wholesale z-score for presentation |
| Some Agmarknet district name spellings differ from GeoJSON | ~4 districts need name normalization | Fixed via `DISTRICT_NAME_FIXES` mapping |
| Agmarknet data entry errors exist in source | Bad prices can enter the pipeline | Mitigated by per-crop bounds — records outside bounds are rejected entirely |
| Grey districts on heat-map | 12 of 34 Gujarat districts have no Agmarknet data for these 5 crops | Expected — not a pipeline bug |
| Variety aggregation | Agmarknet reports at variety level; this project maps all varieties to crop level | Stated simplification — `variety` raw string kept for reference |

---

## 5. What This Project Does Not Claim

- **Not real-time** — Agmarknet updates on a daily batch basis. The pipeline reflects yesterday's mandi prices, not today's live market.
- **Not predictive** — no forecasting or price prediction. The system detects past and present anomalies only.
- **Not a complete retail picture** — retail data covers Ahmedabad (real) and 5 modeled cities. It does not represent all of Gujarat's retail markets.
- **Not a scraper** — the retail data collection is manual logging, not automated scraping. This is a deliberate, documented design decision.
