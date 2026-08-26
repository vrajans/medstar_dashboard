{#-
  SCD Type 2 on business entities — the idiomatic dbt replacement for the manual
  valid_from / valid_to / is_current logic in warehouse.py._scd2_entity().
  dbt tracks changes to `entity_code`; when it changes, the old version is closed
  (dbt_valid_to set) and a new current version opened. Full history is preserved.
-#}
{% snapshot entity_snapshot %}
{{
  config(
    target_schema=target.schema,
    unique_key='entity_id',
    strategy='check',
    check_cols=['entity_code']
  )
}}
select
    entity_id,
    tenant_id,
    entity_type,
    entity_name,
    entity_code
from {{ ref('stg_entities') }}
{% endsnapshot %}
