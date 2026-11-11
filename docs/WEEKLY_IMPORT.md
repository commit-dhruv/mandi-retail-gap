# Weekly Wholesale Import (Agmarknet CSV)

The Agmarknet API no longer returns data, so wholesale prices are imported by hand once a week.
The cron job fails if wholesale data is more than 8 days old, so never skip a week.
Only the project owner runs the import. The teammate can download the CSV files and share them.

## 1. Find where the data currently ends
Run in the Neon SQL Editor (production branch):

    SELECT MAX(price_date) FROM wholesale_price;

Use that date minus 3 days as the "From" date below. The overlap is intentional: the last
3 days are provisional, and re-importing replaces them with the final values.

## 2. Download 5 CSV files from Agmarknet 2.0
Reports -> Price and Arrivals Report. One file per crop: Onion, Potato, Tomato, Garlic, Green Chilli.

- Price/Arrivals: Both
- Commodity Group: Vegetables
- Commodity: the crop
- Variety: All, Grade: All
- State: Gujarat, District: All, Market: All
- From: date from step 1, To: today
- Solve the captcha, click Go, download as CSV

## 3. Put the files in the inbox and check them
    cd ~/Projects/mandi-retail-gap
    source .venv/bin/activate.fish
    mv ~/Downloads/<each file>.csv data_inbox/
    head -n 1 data_inbox/*.csv

The first line of every file must show the date range you asked for.

## 4. Dry run (writes nothing)
    python pipeline/import_agmarknet_file.py --dry-run

Rejected rows = raw count minus valid count. If more than 3% are rejected, stop and investigate.

## 5. Confirm you are pointed at production
    set -e DATABASE_URL
    grep DATABASE_URL .env | string match -r '@[^/]+'

The host must start with ep-mute-smoke.

## 6. Import, re-run anomaly detection, check freshness
    python pipeline/import_agmarknet_file.py
    python pipeline/run_anomaly_detection.py
    python pipeline/check_freshness.py

The wholesale line from the freshness check must show 0-2 days old.

## 7. Check the dashboard
Open the live dashboard. The "Data through" line must show the new wholesale date.
The import moves processed files to data_inbox/processed/, which git ignores.
