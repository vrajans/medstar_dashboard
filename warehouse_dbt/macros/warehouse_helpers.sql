{#-
  Portable helpers so the same models run on DuckDB (dev) and Postgres (prod).
-#}

{# Convert a date/timestamp/text column into a YYYYMMDD integer surrogate key. #}
{% macro date_key(col) -%}
  {%- if target.type == 'duckdb' -%}
    cast(strftime(try_cast({{ col }} as timestamp), '%Y%m%d') as integer)
  {%- else -%}
    cast(to_char(cast({{ col }} as timestamp), 'YYYYMMDD') as integer)
  {%- endif -%}
{%- endmacro %}

{# Cast any date-ish value to a clean DATE. #}
{% macro to_date(col) -%}
  {%- if target.type == 'duckdb' -%}
    cast(try_cast({{ col }} as timestamp) as date)
  {%- else -%}
    cast(cast({{ col }} as timestamp) as date)
  {%- endif -%}
{%- endmacro %}

{# md5 over a pipe-joined list of expressions (both engines support md5 + concat_ws). #}
{% macro md5_of(cols) -%}
  md5(concat_ws('|',
    {%- for c in cols %}
      coalesce(cast({{ c }} as varchar), ''){{ "," if not loop.last }}
    {%- endfor %}
  ))
{%- endmacro %}

{# Portable month abbreviation from a month number. #}
{% macro month_name(month_num) -%}
  case {{ month_num }}
    when 1 then 'Jan' when 2 then 'Feb' when 3 then 'Mar' when 4 then 'Apr'
    when 5 then 'May' when 6 then 'Jun' when 7 then 'Jul' when 8 then 'Aug'
    when 9 then 'Sep' when 10 then 'Oct' when 11 then 'Nov' when 12 then 'Dec'
  end
{%- endmacro %}
