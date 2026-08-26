-- Calendar dimension derived from the dates present in the data.
with dates as (
    select distinct date_key, full_date
    from {{ ref('stg_transactions') }}
    where date_key is not null
)
select
    date_key,
    full_date,
    cast(extract(year   from full_date) as integer) as year,
    cast((cast(extract(month from full_date) as integer) - 1) / 3 + 1 as integer) as quarter,
    cast(extract(month  from full_date) as integer) as month,
    {{ month_name('cast(extract(month from full_date) as integer)') }} as month_name,
    cast(extract(day    from full_date) as integer) as day,
    cast(extract(dow    from full_date) as integer) as weekday,
    case when cast(extract(dow from full_date) as integer) in (0, 6) then 1 else 0 end as is_weekend
from dates
