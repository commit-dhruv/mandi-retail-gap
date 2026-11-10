UPDATE wholesale_price SET is_provisional = false
WHERE is_provisional = true AND price_date < CURRENT_DATE - 3;

DELETE FROM price_anomaly;

INSERT INTO price_anomaly (
    crop_id, mandi_id, district, city, price_date,
    wholesale_price, wholesale_baseline, wholesale_zscore,
    retail_price, retail_baseline, retail_zscore,
    wedge_pct, anomaly_flag
)
WITH dominant_variety AS (
    SELECT DISTINCT ON (crop_id, mandi_id) crop_id, mandi_id, variety
    FROM wholesale_price
    GROUP BY crop_id, mandi_id, variety
    ORDER BY crop_id, mandi_id, COUNT(*) DESC, variety
),
district_city AS (
    SELECT * FROM (VALUES
        ('Ahmedabad', 'Ahmedabad'),
        ('Surat', 'Surat'),
        ('Vadodara(Baroda)', 'Vadodara'),
        ('Rajkot', 'Rajkot'),
        ('Gandhinagar', 'Gandhinagar'),
        ('Bhavnagar', 'Bhavnagar')
    ) AS t(district, city)
),
wholesale_stats AS (
    SELECT
        w.crop_id,
        w.mandi_id,
        m.district,
        COALESCE(dc.city, 'Ahmedabad') AS ref_city,
        w.price_date,
        w.modal_price AS wholesale_price,
        AVG(w.modal_price) OVER (
            PARTITION BY w.crop_id, w.mandi_id
            ORDER BY w.price_date
            ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING
        ) AS wholesale_baseline,
        STDDEV(w.modal_price) OVER (
            PARTITION BY w.crop_id, w.mandi_id
            ORDER BY w.price_date
            ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING
        ) AS wholesale_stddev,
        COUNT(*) OVER (
            PARTITION BY w.crop_id, w.mandi_id
            ORDER BY w.price_date
            ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING
        ) AS preceding_count
    FROM wholesale_price w
    JOIN dominant_variety dv
        ON dv.crop_id = w.crop_id AND dv.mandi_id = w.mandi_id AND dv.variety = w.variety
    JOIN mandi_master m ON m.mandi_id = w.mandi_id
    LEFT JOIN district_city dc ON dc.district = m.district
    WHERE w.is_provisional = false
),
wholesale_scored AS (
    SELECT *,
        CASE
            WHEN wholesale_stddev IS NULL OR wholesale_stddev = 0 THEN NULL
            WHEN preceding_count < 7 THEN NULL
            ELSE ROUND(((wholesale_price - wholesale_baseline) / wholesale_stddev)::numeric, 2)
        END AS wholesale_zscore
    FROM wholesale_stats
    WHERE wholesale_baseline IS NOT NULL
),
retail_ref AS (
    SELECT crop_id, city, price_date, AVG(price) AS retail_price
    FROM retail_price
    GROUP BY crop_id, city, price_date
),
retail_stats AS (
    SELECT
        crop_id,
        city,
        price_date,
        price AS retail_price,
        AVG(price) OVER (
            PARTITION BY crop_id, city
            ORDER BY price_date
            ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING
        ) AS retail_baseline,
        STDDEV(price) OVER (
            PARTITION BY crop_id, city
            ORDER BY price_date
            ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING
        ) AS retail_stddev,
        COUNT(*) OVER (
            PARTITION BY crop_id, city
            ORDER BY price_date
            ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING
        ) AS preceding_count
    FROM retail_price
    WHERE data_type = 'scraped'
),
retail_scored AS (
    SELECT *,
        CASE
            WHEN retail_stddev IS NULL OR retail_stddev = 0 THEN NULL
            WHEN preceding_count < 7 THEN NULL
            ELSE ROUND(((retail_price - retail_baseline) / retail_stddev)::numeric, 2)
        END AS retail_zscore
    FROM retail_stats
    WHERE retail_baseline IS NOT NULL
)
SELECT
    ws.crop_id,
    ws.mandi_id,
    ws.district,
    ws.ref_city,
    ws.price_date,
    ws.wholesale_price,
    ROUND(ws.wholesale_baseline::numeric, 2),
    ws.wholesale_zscore,
    ROUND(rr.retail_price::numeric, 2),
    ROUND(rs.retail_baseline::numeric, 2),
    rs.retail_zscore,
    ROUND(((rr.retail_price - ws.wholesale_price)
        / NULLIF(ws.wholesale_price, 0) * 100)::numeric, 2) AS wedge_pct,
    CASE
        WHEN (ws.wholesale_zscore IS NOT NULL AND ABS(ws.wholesale_zscore) > 2
            AND ABS(ws.wholesale_price - ws.wholesale_baseline) >= 0.2 * ws.wholesale_baseline)
          OR (rs.retail_zscore IS NOT NULL AND ABS(rs.retail_zscore) > 2)
        THEN true
        ELSE false
    END AS anomaly_flag
FROM wholesale_scored ws
JOIN retail_ref rr
    ON rr.crop_id = ws.crop_id
    AND rr.city = ws.ref_city
    AND rr.price_date = ws.price_date
LEFT JOIN retail_scored rs
    ON rs.crop_id = rr.crop_id
    AND rs.city = rr.city
    AND rs.price_date = rr.price_date
    AND rs.city = ws.district;