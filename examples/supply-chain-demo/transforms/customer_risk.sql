select
  c.customer_id,
  c.customer_name,
  c.segment,
  c.region,
  case
    when coalesce(sum(case when o.status = 'APPROVED' then 1 else 0 end), 0) >= 2 then 0.1
    when coalesce(sum(case when o.status = 'APPROVED' then 1 else 0 end), 0) = 1 then 0.25
    else c.risk_score
  end as risk_score,
  cast(coalesce(sum(case when o.status = 'APPROVED' then 1 else 0 end), 0) as integer)
    as approved_order_count
from {{ input('clean.customers') }} c
left join {{ input('ops.order_current') }} o
  on c.customer_id = o.customerId
group by c.customer_id, c.customer_name, c.segment, c.region, c.risk_score