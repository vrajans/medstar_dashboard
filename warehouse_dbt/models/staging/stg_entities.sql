-- Distinct business entities (the grain the SCD2 snapshot tracks over time).
-- entity_id is the stable natural key; entity_code is the tracked attribute.
select distinct
    {{ md5_of(['tenant_id', 'entity_type', 'entity_name']) }} as entity_id,
    tenant_id,
    entity_type,
    entity_name,
    entity_code
from {{ ref('stg_transactions') }}
