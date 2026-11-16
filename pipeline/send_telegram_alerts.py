import os
import sys
import json
import time
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

SENT_FILE = Path(__file__).resolve().parent.parent / "alerts_sent.json"
WINDOW_DAYS = 14
MAX_MESSAGES = 5

SQL = """
select a.crop_id, c.crop_name, a.mandi_id, m.mandi_name, a.district, a.city,
       a.price_date, a.wholesale_price, a.wholesale_baseline, a.wholesale_zscore,
       a.retail_price, a.retail_baseline, a.retail_zscore, a.wedge_pct
from price_anomaly a
join crop_master c on c.crop_id = a.crop_id
left join mandi_master m on m.mandi_id = a.mandi_id
where a.anomaly_flag and a.price_date >= current_date - %s
order by a.price_date desc,
         greatest(abs(coalesce(a.wholesale_zscore, 0)), abs(coalesce(a.retail_zscore, 0))) desc
"""


def num(v, nd=2):
    return "n/a" if v is None else f"{float(v):.{nd}f}"


def make_key(r):
    return f"{r['crop_id']}|{r['mandi_id'] or r['city']}|{r['price_date']}"


def make_text(r):
    place = r["mandi_name"] or r["city"] or "Gujarat"
    z = r["wholesale_zscore"]
    direction = "SPIKE" if (z is not None and float(z) > 0) else "DROP"
    lines = [
        f"KrishiPulse {direction}: {r['crop_name']} at {place}",
        f"District: {r['district'] or 'n/a'} | Date: {r['price_date']}",
        f"Wholesale: Rs {num(r['wholesale_price'])}/kg (baseline {num(r['wholesale_baseline'])}, z {num(r['wholesale_zscore'])})",
    ]
    if r["retail_price"] is not None:
        extra = ""
        if r["retail_baseline"] is not None:
            extra = f" (baseline {num(r['retail_baseline'])}, z {num(r['retail_zscore'])})"
        lines.append(f"Retail ({r['city']}): Rs {num(r['retail_price'])}/kg{extra}")
    lines.append(f"Wedge: {num(r['wedge_pct'], 1)}%")
    return "\n".join(lines)


def load_sent():
    if SENT_FILE.exists():
        return set(json.loads(SENT_FILE.read_text()))
    return set()


def send(token, chat_id, text):
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    return resp.ok and resp.json().get("ok", False)


def main():
    do_send = "--send" in sys.argv
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().strip("\"'")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if do_send and not (token and chat_id):
        print("ALERT: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return

    conn = psycopg2.connect(os.environ["DATABASE_URL"].strip())
    cur = conn.cursor()
    cur.execute(SQL, (WINDOW_DAYS,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, x)) for x in cur.fetchall()]
    conn.close()

    sent = load_sent()
    pending = [r for r in rows if make_key(r) not in sent]
    print(f"flagged in last {WINDOW_DAYS} days: {len(rows)} | not yet alerted: {len(pending)}")

    batch = pending[:MAX_MESSAGES]
    for r in batch:
        text = make_text(r)
        if not do_send:
            print("--- DRY RUN ---")
            print(text)
            continue
        if send(token, chat_id, text):
            sent.add(make_key(r))
            SENT_FILE.write_text(json.dumps(sorted(sent)))
            print("sent:", make_key(r))
            time.sleep(1)
        else:
            print("ALERT: send failed for", make_key(r))

    extra = len(pending) - len(batch)
    if extra > 0:
        note = f"KrishiPulse: +{extra} more flagged anomalies queued, see the dashboard."
        if do_send:
            send(token, chat_id, note)
        else:
            print(note)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ALERT: alert step failed:", type(e).__name__, e)
    sys.exit(0)
