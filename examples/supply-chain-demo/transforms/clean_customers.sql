select
  customer_id,
  customer_name,
  segment,
  region,
  cast(risk_score as double) as risk_score,
  cast(approved_order_count as integer) as approved_order_count
from {{ input('raw.crm_customers') }}