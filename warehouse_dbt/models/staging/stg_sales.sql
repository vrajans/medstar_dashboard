-- Normalizes raw sales into the conformed transaction shape.
with src as (
    select * from {{ source('app', 'sales') }}
)
select
    cast(tenant_id as integer)                             as tenant_id,
    cast('sale' as varchar)                                as txn_type,
    {{ to_date('bill_date') }}                             as full_date,
    {{ date_key('bill_date') }}                            as date_key,
    branch                                                 as branch_name,
    supplier_name                                          as party_name,          -- client/customer for generic tenants
    case when supplier_name is not null then 'customer' else 'branch' end as entity_type,
    coalesce(supplier_name, branch, '(all)')              as entity_name,
    branch                                                 as entity_code,
    cast(coalesce(net_amount, 0) as double)
      + cast(coalesce(discount, 0) as double)              as gross_amount,
    cast(coalesce(net_amount, 0) as double)                as net_amount,
    cast(coalesce(cost_of_sales, 0) as double)             as cost_amount,
    cast(0 as double)                                      as tax_amount,
    cast(coalesce(discount, 0) as double)                  as discount_amount,
    cast(coalesce(margin_pct, 0) as double)                as margin_pct,
    cast(coalesce(total_bills, 1) as double)               as txn_count,
    cast(upload_id as integer)                             as source_upload_id
from src
where bill_date is not null
