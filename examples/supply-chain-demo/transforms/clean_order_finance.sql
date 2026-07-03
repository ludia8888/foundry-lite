select
  order_id,
  cast(margin as double) as margin
from {{ input('raw.erp_orders') }}
