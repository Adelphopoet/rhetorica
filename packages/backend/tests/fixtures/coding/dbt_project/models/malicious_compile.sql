{{ config(enabled=var('enable_malicious_compile', false)) }}

{% if execute and var('enable_malicious_compile', false) %}
  {% do run_query('create table rhetorica_forbidden_catalog.schema.probe (id bigint)') %}
{% endif %}

select 1 as id
