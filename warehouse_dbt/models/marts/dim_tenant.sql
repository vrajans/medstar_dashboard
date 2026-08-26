-- Tenant dimension (tenant_id / customer_code / domain).
select distinct
    tenant_id,
    customer_code,
    domain
from {{ ref('stg_transactions') }}
