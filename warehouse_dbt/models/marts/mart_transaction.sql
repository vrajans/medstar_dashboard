-- Serving mart: flattened one-big-table the analytics + AI layers read.
-- Mirrors warehouse.wh_mart_transaction.
select
    f.tenant_id,
    f.domain,
    f.txn_type,
    f.date_key,
    d.full_date,
    d.year,
    d.month,
    d.month_name,
    e.entity_type,
    e.entity_name,
    f.branch_name,
    f.party_name,
    cast(null as varchar)  as product_name,
    f.gross_amount,
    f.net_amount,
    f.cost_amount,
    f.tax_amount,
    f.margin_pct,
    f.txn_count
from {{ ref('fct_transaction') }} f
left join {{ ref('dim_date') }}   d on f.date_key = d.date_key
left join {{ ref('dim_entity') }} e on f.entity_key = e.entity_id and e.is_current = 1
