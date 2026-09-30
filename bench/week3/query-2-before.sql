-- Future period report over seven business days.
SELECT business_date, provider, result_type,
       count(*) AS result_count,
       sum(system_amount_minor) AS system_amount_minor,
       sum(provider_amount_minor) AS provider_amount_minor
FROM recon_result_before
WHERE business_date >= DATE '2026-01-20'
  AND business_date < DATE '2026-01-27'
GROUP BY business_date, provider, result_type
ORDER BY business_date, provider, result_type;
