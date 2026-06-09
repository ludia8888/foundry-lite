select
  order_id,
  customer_id,
  source_status,
  cast(amount as double) as amount,
  cast(margin as double) as margin,
  cast(order_ts as timestamp) as order_ts,
  region,
  case when cast(amount as double) >= 1000 then 0.8 else 0.3 end as risk_score
from {{ input('raw.erp_orders') }}