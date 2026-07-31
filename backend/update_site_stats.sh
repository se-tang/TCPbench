#!/bin/bash
# 每月 1 号（北京时间）重新计算各网站延迟平均值
# 过滤 0ms（失败）结果
SQL=$(cat << 'SQL'
DELETE FROM site_stats WHERE avg_lat <= 0 OR avg_lat IS NULL;
INSERT INTO site_stats (name, avg_lat, min_lat, max_lat, sample_count, updated_at)
SELECT 
    elem->>'name',
    ROUND(AVG((elem->>'avg')::numeric)::numeric, 1),
    ROUND(MIN((elem->>'avg')::numeric)::numeric, 1),
    ROUND(MAX((elem->>'avg')::numeric)::numeric, 1),
    COUNT(*),
    NOW()
FROM reports r,
LATERAL jsonb_array_elements(r.raw::jsonb) AS elem
WHERE (elem->>'avg')::numeric > 0
GROUP BY elem->>'name'
ON CONFLICT (name) DO UPDATE SET
    avg_lat = EXCLUDED.avg_lat,
    min_lat = EXCLUDED.min_lat,
    max_lat = EXCLUDED.max_lat,
    sample_count = EXCLUDED.sample_count,
    updated_at = NOW();
SQL
)
echo "$SQL" | docker exec -i tcpbench-pg psql -U tcpbench -d tcpbench
echo "[$(date)] site_stats updated (0ms filtered)"
