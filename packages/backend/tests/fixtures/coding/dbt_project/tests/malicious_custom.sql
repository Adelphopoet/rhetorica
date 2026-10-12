{{ config(enabled=var('enable_malicious_test', false)) }}

{% if execute and var('enable_malicious_test', false) %}
  {% do run_query('insert into rhetorica_forbidden_catalog.schema.probe values (1)') %}
{% endif %}

select 1 where false
