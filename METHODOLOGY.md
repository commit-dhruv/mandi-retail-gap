# KrishiPulse - Methodology Note

This note explains the data approach, design decisions and known limitations of the KrishiPulse pipeline. It describes the system as it stands on 19 November 2026 and is written for evaluators, mentors and anyone reproducing or extending the project.

---

## 1. Data timeline at a glance

| Period | Wholesale source |
|---|---|
| 20 Jun - 25 Sep 2026 | data.gov.in API, resource "Current Daily Price of Various Commodities from Various Markets (Mandi)" (ID 9ef84268-d588-465a-a308-a864a43d0070), a current-day feed accumulated daily |
| 26 Sep - 9 Nov 2026 | Manual import of Agmarknet 2.0 "Price and Arrivals Report" CSV files (3,856 rows accepted) |
| 13 Nov 2026 onward | data.gov.in resource "Variety-wise Daily Market Prices Data of Commodity" (ID 35985678-0d79-46b4-9ed6-6f13308a1d24), the full archive, loaded by a scheduled job on the project laptop |

On 13 Nov 2026 the Variety-wise archive was used to backfill 20 Jun - 25 Sep. It wrote 8,283 rows and filled 13 calendar days that had no records. It was also compared with the 3,856 CSV-imported rows for 26 Sep - 9 Nov: all matched, with 0 price differences.

**Current state (19 Nov 2026):** 12,700 wholesale rows over 152 consecutive days (20 Jun - 18 Nov 2026, no missing days) across 46 Gujarat mandis.

The upsert overwrites each row's source label, so the exact split of rows between the API and CSV routes can no longer be reconstructed. It is not reported here.

---

## 2. Wholesale data layer

**Coverage:** Gujarat mandis, 5 crops (Onion, Potato, Tomato, Garlic, Green Chilli).

### 2a. Original API period (20 Jun - 25 Sep 2026)

**Source:** the current-day "Current Daily Price" resource, which republishes Agmarknet data.
**Frequency:** pulled twice a day by a GitHub Actions cron job.

Quirks found during the build:
- A browser User-Agent header is required; blank headers return 502.
- Filter keys use the .keyword suffix: filters[state.keyword], filters[commodity.keyword].
- The feed is a current-day snapshot with no historical archive, so history was built by daily accumulation.
- Mandis report throughout the day. Early-morning pulls returned zero records, and the cron times were moved from 6:00 AM IST after a 41% failure rate.
- The original API loader saved responses to pipeline/raw/ before cleaning. The folder now holds 16 sample files from three dates (20 Jun, 6 Jul and 22 Jul 2026), not every response. The current loader's raw-saving behaviour has not been checked, so no claim is made for it.

### 2b. What happened after 25 September

Evidence kept in the project:
- The first failed scheduled GitHub Actions run was on 25 Sep 2026, with a connect timeout to api.data.gov.in from the GitHub-hosted runner. Later runs were refused (10 Nov) or timed out on the new resource (13 Nov).
- Calls to the same API from the project laptop in India return HTTP 200. The cause of the GitHub-side failure has not been identified.
- Separately, the current-day feed returned very few Gujarat records on some days (for example 5 Gujarat records, none of our crops, on 11 Nov; 268 records including 9 Onion on 14 Nov at 19:57). One untested hypothesis is that Gujarat mandis report late in the day.

### 2c. Fallback: manual Agmarknet CSV import (kept as a backup route)

**Source:** agmarknet.gov.in (Agmarknet 2.0), Reports -> Price and Arrivals Report. One file per crop, State Gujarat, District All, Market All. The portal limits this report to a one-year window.
**Importer:** pipeline/import_agmarknet_file.py with pipeline/sources/csv_import.py. It detects the header row, reads prices in Rs/Quintal, parses DD-MM-YYYY dates, averages grades of the same variety, and writes through the same idempotent upsert as the API loader. Steps are in docs/WEEKLY_IMPORT.md.
**First import (9 Nov 2026):** 3,888 rows read, 3,856 accepted, 32 rejected (0.8%). All 32 failed the price bounds: 30 Green Chilli rows and 2 Tomato rows. The bounds were kept and the rejections are disclosed as a limitation.

### 2d. Current route: scheduled loader on the laptop

A systemd user timer on the project laptop is scheduled for 10:00 and 18:00 IST. Each run executes three steps in order:
1. pipeline/load_wholesale_variety.py --days 7 (re-pulls the last 7 days, so late reports are picked up)
2. pipeline/run_anomaly_detection.py
3. pipeline/send_telegram_alerts.py --send

Example run, 19 Nov 2026 10:30 IST: 470 rows parsed, 458 valid, 12 rejected, 458 written after averaging grades; 11,731 rows scored, 994 flagged.

Why a laptop and not the cloud: GitHub-hosted runners cannot reach the API (section 2b), and the same calls work from India. Limits of this choice: the timer only runs while the laptop is on and the user is logged in, and catch-up after downtime (Persistent=true) has not been tested. On 16 Nov the evening run started at 18:53, not 18:00; the reason is not established. The freshness check on GitHub Actions (section 6) is the backstop.

### 2e. Cleaning rules (all routes)

**Unit normalization:** Agmarknet reports rupees per quintal (100 kg). All prices are converted to Rs/kg by dividing by 100.

**Date parsing:** dates are parsed with explicit formats (DD/MM/YYYY or DD-MM-YYYY), never with pandas auto-inference, which silently swaps day and month for ambiguous dates.

**Per-crop price bounds:** a record outside these ranges is rejected and never stored.

| Crop | Min (Rs/kg) | Max (Rs/kg) |
|---|---|---|
| Onion | 2 | 100 |
| Potato | 3 | 80 |
| Tomato | 5 | 150 |
| Garlic | 20 | 500 |
| Green Chilli | 10 | 300 |

**Provisional flag:** records dated within 3 days of today are marked provisional and excluded from anomaly detection, because late mandi reports arrive for a few days. The anomaly job clears the flag for older records on every run.

**Upsert, not insert:** a unique key on (crop, mandi, variety, price date) with INSERT ... ON CONFLICT DO UPDATE prevents duplicates when overlapping data is loaded again.

**Mandi names:** near-duplicate mandi names (Nadiad, Rajkot, Gondal, Ahmedabad) are kept as separate yards.

---

## 3. Retail data layer

The retail layer is a hybrid: real manually logged prices for one city (Ahmedabad), extended to five other Gujarat cities with a margin model. It is disclosed as such, not presented as scraped data.

### 3a. Logged prices (Ahmedabad)

**Platform:** Blinkit Ahmedabad.
**Method:** a teammate logs the price of one standard product per crop each day into a shared Google Sheet, using the first non-organic, non-specialty result, and the same product every day. Prices are normalized to Rs/kg before logging.
**Coverage:** 620 logged rows, 18 Jul - 18 Nov 2026 (124 days x 5 crops, no gaps). The database stores these with data_type 'scraped'; they are manually logged, not scraped.
**Why not automated scraping?** Blinkit pages are rendered by JavaScript and use authenticated requests carrying location tokens. A feasibility test ruled automation out as fragile.
**Missed days are never backfilled with a later day's price.**

**Loader checks** (ingestion/load_retail_manual.py): each sheet row must have a known crop (case-insensitive), a valid YYYY-MM-DD date that is not in the future, city Ahmedabad, platform Blinkit, unit kg, and a price inside per-crop retail bounds. Bad rows are skipped and reported as warnings in the run log. A corrected price in the sheet updates the stored row. Conflicting duplicate rows are reported and the last row wins.

| Crop | Retail min (Rs/kg) | Retail max (Rs/kg) |
|---|---|---|
| Onion | 10 | 150 |
| Potato | 10 | 120 |
| Tomato | 10 | 200 |
| Garlic | 60 | 600 |
| Green Chilli | 30 | 400 |

**Estimated period:** values for 20 Jun - 17 Jul 2026 (140 rows) are estimates, not real Blinkit prices, because logging began on 18 Jul. They are stored with data_type 'modeled', and the sheet loader ignores sheet rows noted as backfill. The dashboard does not plot them as retail prices, but they are still the retail reference when wedge values are computed for those dates, and the heat-map caption says so.

### 3b. Margin model (other cities)

**Formula:** modeled_price = Ahmedabad logged price x city margin factor, generated by ingestion/generate_retail_model.py for each date and stored with data_type 'modeled' (760 rows per city).

| City | Margin factor | Rationale |
|---|---|---|
| Gandhinagar | 1.02 | Adjacent to Ahmedabad, minimal transport cost |
| Vadodara | 1.03 | Close and well connected |
| Surat | 1.05 | About 260 km, moderate distribution markup |
| Rajkot | 1.08 | About 220 km, smaller distribution network |
| Bhavnagar | 1.10 | Coastal location, higher last-mile cost |

The factors are estimates based on distance and distribution logic. They are not fitted to real price data from those cities. All five modeled cities move together whenever the Ahmedabad price changes.

---

## 4. Anomaly detection

**Method:** SQL window-function rolling z-scores, with no machine learning. The method is explainable, needs no training data, and every flagged row can be checked by hand from its price, baseline and z-score.

### Algorithm

1. **One series per crop and mandi.** Each mandi uses its most-reported variety, so prices of different varieties are never mixed in one baseline.
2. **Baseline.** A 30-day trailing average and standard deviation, excluding the current day:

        AVG(modal_price) OVER (
            PARTITION BY crop_id, mandi_id
            ORDER BY price_date
            ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING
        )

3. **Z-score:** (today's price - baseline) / standard deviation.
4. **Wholesale flag:** the absolute z-score is above 2 and the price is at least 20% away from its baseline. The 20% condition stops tiny moves in very flat price series from being flagged.
5. **Retail flag:** the absolute retail z-score is above 2. Retail z-scores use only logged Ahmedabad prices, never modeled prices.
6. **Retail reference and wedge.** Each mandi is compared with the retail price of its district's city: Ahmedabad uses the logged Blinkit price, Surat, Vadodara, Rajkot, Gandhinagar and Bhavnagar districts use that city's modeled price, and all other districts use the Ahmedabad logged price as a Gujarat-wide reference. wedge_pct = (retail price - wholesale price) / wholesale price x 100.

**Guards against false positives:**
- Provisional data (the latest 3 days) is excluded.
- At least 7 preceding records are required before a z-score is computed.
- A zero standard deviation gives no z-score instead of a division error.
- Per-crop bounds keep bad source values out of the baseline.
- Mandis with too few reports are never scored (for example Jasdan, Kalawad and Gondal APMC).

**Recomputed on every run:** the price_anomaly table is cleared and rebuilt each run, so the result is repeatable. anomaly_id is therefore not stable between runs.

**Result as of 19 Nov 2026:** 994 flagged rows out of 11,731 scored rows (8.5%).

---

## 5. Telegram alerts

pipeline/send_telegram_alerts.py sends flagged anomalies to a Telegram chat.

- **Selection:** rows with anomaly_flag true and a price date within the last 14 days, newest first, then by largest absolute z-score.
- **Why 14 days:** the anomaly job excludes the latest 3 days as provisional, so a flagged row is always at least about 3 days old. An alert therefore lags the price date by about 3 days.
- **Message:** SPIKE or DROP (from the sign of the wholesale z-score), crop, mandi or city, district, price date, wholesale price vs baseline and z-score, retail line when available, and wedge %.
- **Cap:** at most 5 messages per run; if more are pending, one extra line says how many are queued. The queue drains at 5 per run.
- **De-duplication:** alerts_sent.json (gitignored, exists only on the laptop) stores one key per crop, mandi or city, and price date. A key is saved only after Telegram accepts the message.
- **Safety:** dry run by default; messages are sent only with --send. The script catches all errors and exits cleanly, so a failed alert cannot stop the wholesale load.
- **Design change:** the script uses plain HTTP requests to the Telegram Bot API instead of python-telegram-bot (named in the project overview), because the bot only sends messages and the library adds nothing.
- **Limit:** alerts are sent only when the laptop timer runs. If the laptop is off or logged out, no alerts are sent.

---

## 6. Automation and monitoring

**Laptop timer (wholesale and alerts):** section 2d.

**GitHub Actions** (.github/workflows/daily_ingestion.yml) is scheduled for 09:00 and 14:30 IST. GitHub sometimes starts scheduled runs hours late (observed 11-14 Nov), so these are scheduled times, not guaranteed start times. Steps:
1. Retail sheet loader
2. Retail margin model
3. Anomaly detection
4. Data freshness check, which fails the run if the newest wholesale row is more than 5 days old or the newest logged retail row is more than 4 days old

A stalled laptop timer shows up as a failed freshness check, which is the only automatic warning. The alert step does not run on GitHub Actions.

**Health check:** pipeline/health_check.py runs 14 read-only checks (duplicate keys, bound violations, missing days, data age, flag rate and others). All 14 passed on 19 Nov 2026.

**Dashboard:** reads directly from the database, caches query results for one hour, and does not cache database errors. A line under the title shows the date through which wholesale, retail and anomaly data run.

---

## 7. Known limitations

| Limitation | Impact | Status |
|---|---|---|
| GitHub-hosted runners cannot connect to api.data.gov.in (since 25 Sep 2026); the same calls work from India | Wholesale loading runs on a laptop instead of the cloud | Cause not identified; freshness check is the backstop |
| Laptop timer runs only while the laptop is on and logged in | A stalled timer means stale data and no alerts | Freshness check fails the GitHub run; catch-up behaviour untested |
| Alerts lag the price date by about 3 days | Alerts are not same-day | By design (provisional window) |
| Alert de-duplication file exists only on the laptop | Losing it could repeat old alerts | Disclosed |
| Retail logging started on 18 Jul 2026 | 20 Jun - 17 Jul retail values are estimates and still feed wedge values for those dates | Labelled modeled, not plotted as retail, noted in the heat-map caption |
| Modeled retail prices for five cities derive from Ahmedabad | Wedges outside Ahmedabad depend on the margin factors | Disclosed on the dashboard and here |
| Blinkit is a quick-commerce channel with convenience markups | The retail premium is an upper-end indicator, not the intermediary margin alone | Stated; wedge is not presented as pure overcharging |
| A flag can reflect a change in what a mandi reports, not only a real price move | Example: Rajkot sub yard potato jumped from about Rs 10 to Rs 16 for three days in October while the daily min-max range narrowed from about Rs 10 to about Rs 1.5 | Flags are indicators to investigate, not verdicts |
| Some Agmarknet rows carry implausible prices | 32 of 3,888 rows (0.8%) rejected in the first CSV import | Bounds kept; rejections disclosed |
| Heat-map covers only districts with a reporting mandi for these crops | 23 of Gujarat's 34 districts have data; the other 11 are blank | Expected; not a pipeline error |
| Variety aggregation | Each mandi uses its most-reported variety | Stated simplification |

---

## 8. What this project does not claim

- **Not real-time.** Wholesale data is refreshed twice daily from Agmarknet's own daily batch, so it is daily-refreshed, not a live market feed.
- **Not predictive.** The system detects past and present anomalies and does not forecast prices.
- **Not a complete retail picture.** Retail data covers Ahmedabad (logged) and five modeled cities, not all of Gujarat.
- **Not a scraper.** Retail data is logged by hand, a documented design decision.
- **Not proof of overcharging.** A high retail premium reflects retail-channel pricing, including convenience, as well as intermediary margins.
