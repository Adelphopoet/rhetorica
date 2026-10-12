{% macro coding_e_malicious_hook() %}
  {% do run_query('call system.rhetorica_forbidden_probe()') %}
{% endmacro %}
