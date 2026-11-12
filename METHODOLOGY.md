# KrishiPulse - Methodology Note

This note explains the data approach, design decisions and known limitations of the KrishiPulse pipeline. It describes the system as it stands on 12 November 2026 and is written for evaluators, mentors and anyone reproducing or extending the project.

---

## 1. Data timeline at a glance

| Period | Wholesale source | Days with data | Rows |
|---|---|---|---|
| 20 Jun - 25 Sep 2026 | data.gov.in API, resource "Current Daily Price of Various Commodities from Various Markets (Mandi)" (ID 9ef84268-d588-465a-a308-a864a43d0070) | 85 | 4,125 |
| 26 Sep - 9 Nov 2026 | Manual import of Agmarknet 2.0 "Price and Arrivals Report" CSV files | 45 | 3,856 |

Total: 130 days and 7,981 rows of wholesale data across 45 Gujarat mandis. The live API supplied about 65% of the days (85 of 130). Thirteen calendar days between 20 Jun and 25 Sep have no wholesale records.

After 25 September 2026 the API feed stopped returning records for our Gujarat crops and the pipeline fell back to the CSV route. The cause has not been identified. The API loader still runs on every scheduled run and writes through the same upsert as the CSV importer, so ingestion resumes through the same code path if the feed returns Gujarat records for these crops. This has not been exercised since the outage, so it is described here as retained, not as re-verified.

---

## 2. Wholesale data layer

**Coverage:** Gujarat mandis, 5 crops (Onion, Potato, Tomato, Garlic, Green Chilli).

### 2a. Live API period (20 Jun - 25 Sep 2026)

**Source:** data.gov.in resource "Current Daily Price of Various Commodities from Various Markets (Mandi)", which republishes Agmarknet data.
**Frequency:** pulled twice a day (9:00 AM and 2:30 PM IST) by a GitHub Actions cron job.

Quirks found during the build:
- A browser User-Agent header is required; blank headers return 502.
- Filter keys use the .keyword suffix: filters[state.keyword], filters[commodity.keyword].
- The feed is a current-day snapshot with no historical archive, so history was built by daily accumulation.
- Mandis report throughout the day. Early-morning pulls returned zero records, and the cron times were moved from 6:00 AM IST after a 41% failure rate.
- Each API response was saved to pipeline/raw/ before any cleaning, so processing can be repeated without calling the API again.

### 2b. What happened after 25 September

Evidence kept in the project:
- Scheduled GitHub Actions runs failed from 26 Sep 2026; the last successful run was on 25 Sep.
- A local run on 5 Nov returned empty responses (five 2-byte raw files are kept as evidence).
- A GitHub Actions run on 10 Nov was refused at connection level by the API host. The cause is unknown.
- On 11 Nov, 15:22-16:22 IST, 24 timestamped readings were logged. Every call returned HTTP 200 in 0.3-0.6 seconds. The unfiltered query reported 10,548 records at 15:22 and 11,137 from 15:32, all dated 11 Nov. A full scan at about 15:27 found Gujarat contributing 5 of 10,548 records (Tamil Nadu 6,861). The Gujarat + Onion query returned 0 records at every reading.
- One reading at 15:30 IST returned zero records for every query, and the feed was populated again two minutes later. It followed a large scan run from the project machine, and the cause is unknown.

### 2c. Fallback: manual Agmarknet CSV import (26 Sep 2026 onward)

**Source:** agmarknet.gov.in (Agmarknet 2.0), Reports -> Price and Arrivals Report. One file per crop, State Gujarat, District All, Market All. The portal limits this report to dates on or after 7 November 2025 and to a one-year window.
**Importer:** pipeline/import_agmarknet_file.py with pipeline/sources/csv_import.py. It detects the header row, reads prices in Rs/Quintal, parses DD-MM-YYYY dates, averages grades of the same variety, and writes through the same idempotent upsert as the API loader.
**Routine:** a weekly import, documented step by step in docs/WEEKLY_IMPORT.md. Overlapping date ranges are safe because of the upsert.
**First import (9 Nov 2026):** 3,888 rows read, 3,856 accepted, 32 rejected (0.8%). All 32 failed the price bounds: 30 Green Chilli rows (17 from Vadhvan, which repeats the same Rs 5.00 / 7.50 / 10.00 price triple on 17 different days, and 11 from Talalagir at Rs 8-9.50/kg) and 2 Tomato rows. The bounds were kept and the rejections are disclosed as a limitation.

### 2d. Cleaning rules (both routes)

**Unit normalization:** Agmarknet reports rupees per quintal (100 kg). All prices are converted to Rs/kg by dividing by 100.

**Date parsing:** dates are parsed with explicit formats, never with pandas auto-inference, which silently swaps day and month for ambiguous dates.

**Per-crop price bounds:** a record outside these ranges is rejected and never inserted.

| Crop | Min (Rs/kg) | Max (Rs/kg) |
|---|---|---|
| Onion | 2 | 100 |
| Potato | 3 | 80 |
| Tomato | 5 | 150 |
| Garlic | 20 | 500 |
| Green Chilli | 10 | 300 |

**Provisional flag:** records dated within 3 days of today are marked provisional and excluded from anomaly detection, because late mandi reports arrive for a few days. The anomaly job clears the flag for older records on every run.

**Upsert, not insert:** a unique key on (crop, mandi, variety, price date) with INSERT ... ON CONFLICT DO UPDATE prevents duplicates when overlapping data is loaded again.

---

## 3. Retail data layer

The retail layer is a hybrid: real manually logged prices for one city (Ahmedabad), extended to five other Gujarat cities with a margin model. It is disclosed as such, not presented as scraped data.

### 3a. Logged prices (Ahmedabad)

**Platform:** Blinkit Ahmedabad.
**Method:** a teammate logs the price of one standard product per crop each day into a shared Google Sheet, using the first non-organic, non-specialty result, and the same product every day. Prices are normalized to Rs/kg before logging.
**Coverage:** 580 logged rows, 18 Jul - 10 Nov 2026, stored with data_type 'scraped'.
**Why not automated scraping?** Blinkit pages are rendered by JavaScript and use authenticated requests carrying location tokens. A feasibility test ruled automation out as fragile.

**Loader checks** (ingestion/load_retail_manual.py): each sheet row must have a known crop (case-insensitive), a valid YYYY-MM-DD date that is not in the future, city Ahmedabad, platform Blinkit, unit kg, and a price inside per-crop retail bounds. Bad rows are skipped and reported as warnings in the run log. A corrected price in the sheet updates the stored row. Conflicting duplicate rows are reported and the last row wins.

| Crop | Retail min (Rs/kg) | Retail max (Rs/kg) |
|---|---|---|
| Onion | 10 | 150 |
| Potato | 10 | 120 |
| Tomato | 10 | 200 |
| Garlic | 60 | 600 |
| Green Chilli | 30 | 400 |

**Estimated period:** values for 20 Jun - 17 Jul 2026 (140 rows) are estimates, not real Blinkit prices, because logging began on 18 Jul. They are stored with data_type 'modeled', and the sheet loader ignores sheet rows noted as backfill. The dashboard does not plot them as retail prices.

### 3b. Margin model (other cities)

**Formula:** modeled_price = Ahmedabad logged price x city margin factor, generated by ingestion/generate_retail_model.py for each date and stored with data_type 'modeled'.

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

1. **One series per crop and mandi.** Each mandi uses its most-reported variety, so prices of different varieties (for example Local and Nasik) are never mixed in one baseline.
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

**Recomputed on every run:** the price_anomaly table is cleared and rebuilt each run, so the result is repeatable.

**Result as of 11 Nov 2026:** 608 flagged rows out of 7,548 scored rows (8.1%). Among the six districts with the most flags, the flag rate is 5-9%, and no single district dominates.

---

## 5. Automation and monitoring

A GitHub Actions workflow runs twice a day (9:00 AM and 2:30 PM IST) with these steps:
1. Wholesale API loader (non-blocking: a failure here does not stop the run)
2. Retail sheet loader
3. Retail margin model
4. Anomaly detection
5. Data freshness check, which fails the run if the latest wholesale date is more than 8 days old or the latest logged retail date is more than 4 days old

Because wholesale data now depends on the weekly CSV import, the freshness check is what turns a missed import into a visible failure. The 10 Nov run took under a minute.

The dashboard reads directly from the database, caches query results for one hour, and does not cache database errors. A line under the title shows the date through which wholesale, retail and anomaly data run.

---

## 6. Known limitations

| Limitation | Impact | Status |
|---|---|---|
| Wholesale API feed stopped returning Gujarat records for our crops after 25 Sep 2026 | Daily automatic wholesale refresh is not currently working | Weekly manual CSV import in place; API loader retained |
| Wholesale data is refreshed weekly by hand | Data is up to a week plus 3 days behind | Freshness check fails the run if more than 8 days old |
| 13 calendar days (20 Jun - 25 Sep) have no wholesale records | Gaps in early baselines | Disclosed; no records were invented |
| Retail logging started on 18 Jul 2026 | 20 Jun - 17 Jul retail values are estimates | Labelled modeled and not plotted as retail |
| Modeled retail prices for five cities derive from Ahmedabad | Wedges outside Ahmedabad depend on the margin factors | Disclosed on the dashboard and here |
| Blinkit is a quick-commerce channel with convenience markups | The retail premium is an upper-end indicator, not the intermediary margin alone | Stated; wedge is not presented as pure overcharging |
| A flag can reflect a change in what a mandi reports, not only a real price move | Example: Rajkot sub yard potato jumped from about Rs 10 to Rs 16 for three days in October while the daily min-max range narrowed from about Rs 10 to about Rs 1.5 | Flags are indicators to investigate, not verdicts |
| Some Agmarknet rows carry implausible prices | 32 of 3,888 rows (0.8%) rejected in the CSV import | Bounds kept; rejections disclosed |
| Heat-map covers 23 of Gujarat's 34 districts | 11 districts have no reporting mandi for these crops | Expected; not a pipeline error |
| Variety aggregation | Each mandi uses its most-reported variety | Stated simplification |

---

## 7. What this project does not claim

- **Not real-time.** Wholesale data is weeks-level fresh at worst and weekly by design right now; it is not a live market feed.
- **Not predictive.** The system detects past and present anomalies and does not forecast prices.
- **Not a complete retail picture.** Retail data covers Ahmedabad (logged) and five modeled cities, not all of Gujarat.
- **Not a scraper.** Retail data is logged by hand, a documented design decision.
- **Not proof of overcharging.** A high retail premium reflects retail-channel pricing, including convenience, as well as intermediary margins.
