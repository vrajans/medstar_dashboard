-- Conformed transaction grain (union of sales + purchases) with the same
-- idempotent business key + row hash that warehouse.py produces:
--   business key = coordinate (tenant, type, branch, party, date) + occurrence
--   row hash     = measures  (change detection)
with unioned as (
    select * from {{ ref('stg_sales') }}
    union all
    select * from {{ ref('stg_purchases') }}
),
keyed as (
    select
        *,
        row_number() over (
            partition by tenant_id, txn_type,
                         coalesce(branch_name, ''), coalesce(party_name, ''), date_key
            order by net_amount, gross_amount, cost_amount, tax_amount,
                     discount_amount, margin_pct, txn_count
        ) as occurrence
    from unioned
)
select
    {{ md5_of(['tenant_id', "txn_type", 'branch_name', 'party_name', 'date_key', 'occurrence']) }}
                                                        as business_key_hash,
    {{ md5_of(['net_amount', 'cost_amount', 'discount_amount', 'txn_count', 'margin_pct']) }}
                                                        as row_hash,
    tenant_id,
    'CUST' || coalesce(cast(tenant_id as varchar), '0') as customer_code,
    case when tenant_id is null then 'pharmacy' else 'generic' end as domain,
    txn_type,
    full_date,
    date_key,
    branch_name,
    party_name,
    entity_type,
    entity_name,
    entity_code,
    gross_amount,
    net_amount,
    cost_amount,
    tax_amount,
    discount_amount,
    margin_pct,
    txn_count,
    source_upload_id
from keyed
