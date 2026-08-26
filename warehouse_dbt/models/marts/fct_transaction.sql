{{
  config(
    materialized='incremental',
    unique_key='business_key_hash',
    incremental_strategy='delete+insert'
  )
}}
-- Conformed fact. Incremental + delete+insert on business_key_hash makes re-runs
-- idempotent: unchanged rows are skipped, changed rows (new row_hash) are upserted,
-- new rows inserted. For hard-delete reconciliation of vanished source rows, run
-- with --full-refresh on a schedule (see README).
with s as (
    select * from {{ ref('stg_transactions') }}
    {% if is_incremental() %}
    where not exists (
        select 1 from {{ this }} x
        where x.business_key_hash = {{ ref('stg_transactions') }}.business_key_hash
          and x.row_hash          = {{ ref('stg_transactions') }}.row_hash
    )
    {% endif %}
),
ent as (
    select entity_id, tenant_id, entity_type, entity_name
    from {{ ref('dim_entity') }}
    where is_current = 1
)
select
    s.business_key_hash,
    s.row_hash,
    s.tenant_id,
    s.customer_code,
    s.domain,
    s.txn_type,
    s.date_key,
    e.entity_id            as entity_key,
    cast(null as varchar)  as product_key,
    s.branch_name,
    s.party_name,
    s.gross_amount,
    s.net_amount,
    s.cost_amount,
    s.tax_amount,
    s.discount_amount,
    s.margin_pct,
    s.txn_count,
    s.source_upload_id
from s
left join ent e
       on  e.tenant_id  = s.tenant_id
      and  e.entity_type = s.entity_type
      and  e.entity_name = s.entity_name
