-- Normalizes raw purchases into the conformed transaction shape.
with src as (
    select * from {{ source('app', 'purchases') }}
)
select
    cast(tenant_id as integer)                             as tenant_id,
    cast('purchase' as varchar)                            as txn_type,
    {{ to_date('coalesce(grn_date, invoice_date)') }}      as full_date,
    {{ date_key('coalesce(grn_date, invoice_date)') }}     as date_key,
    cast(null as varchar)                                  as branch_name,
    supplier_name                                          as party_name,
    cast('supplier' as varchar)                            as entity_type,
    coalesce(supplier_name, '(unknown supplier)')         as entity_name,
    supplier_code                                          as entity_code,
    cast(coalesce(gross_amount, net_amount, 0) as double)  as gross_amount,
    cast(coalesce(net_amount, 0) as double)                as net_amount,
    cast(coalesce(net_amount, 0) as double)                as cost_amount,
    cast(coalesce(total_gst, 0) as double)                 as tax_amount,
    cast(coalesce(adjustment_value, 0) as double)          as discount_amount,
    cast(0 as double)                                      as margin_pct,
    cast(1 as double)                                      as txn_count,
    cast(upload_id as integer)                             as source_upload_id
from src
where coalesce(grn_date, invoice_date) is not null
