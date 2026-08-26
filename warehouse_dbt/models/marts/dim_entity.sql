-- Entity dimension surfaced from the SCD2 snapshot.
-- is_current = 1 for the live version of each entity; history rows carry 0.
select
    entity_id,
    tenant_id,
    entity_type,
    entity_name,
    entity_code,
    dbt_valid_from                       as valid_from,
    dbt_valid_to                         as valid_to,
    case when dbt_valid_to is null then 1 else 0 end as is_current
from {{ ref('entity_snapshot') }}
