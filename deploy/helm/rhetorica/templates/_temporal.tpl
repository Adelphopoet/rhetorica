{{- define "rhetorica.temporalAddress" -}}
{{- if .Values.infra.temporal.enabled -}}
{{- if not .Values.infra.enabled -}}
{{- fail "infra.temporal.enabled requires infra.enabled" -}}
{{- end -}}
{{- if .Values.scenarioWorkflows.temporal.address -}}
{{- fail "Choose either embedded Temporal or scenarioWorkflows.temporal.address" -}}
{{- end -}}
{{- printf "%s-infra:7233" (include "rhetorica.fullname" .) -}}
{{- else -}}
{{- if and .Values.scenarioWorkflows.enabled (not .Values.scenarioWorkflows.temporal.address) -}}
{{- fail "Enabling scenario workflows requires a Temporal frontend" -}}
{{- end -}}
{{- .Values.scenarioWorkflows.temporal.address -}}
{{- end -}}
{{- end -}}

{{- define "rhetorica.embeddedTemporal" -}}
- name: temporal
  image: {{ .Values.infra.temporal.image | quote }}
  imagePullPolicy: {{ .Values.infra.imagePullPolicy }}
  command: ["/bin/sh", "-ec"]
  args:
    - |
      {{- .Files.Get "files/temporal-schema-init.sh" | nindent 6 }}
      exec /etc/temporal/entrypoint.sh autosetup
  ports:
    - name: temporal
      containerPort: 7233
  env:
    - name: POD_IP
      valueFrom:
        fieldRef:
          fieldPath: status.podIP
    - name: TEMPORAL_ADDRESS
      value: "$(POD_IP):7233"
    - name: DB
      value: postgres12
    - name: DB_PORT
      value: "5432"
    # PostgreSQL is in this pod; using its Service here would deadlock readiness.
    - name: POSTGRES_SEEDS
      value: "127.0.0.1"
    - name: POSTGRES_USER
      value: temporal_persistence
    - name: VISIBILITY_POSTGRES_USER
      value: temporal_visibility
    - name: POSTGRES_PWD
      valueFrom:
        secretKeyRef:
          name: {{ required "infra.temporal.existingSecret is required" .Values.infra.temporal.existingSecret }}
          key: {{ .Values.infra.temporal.passwordKey }}
    - name: DBNAME
      value: {{ .Values.infra.postgres.database | quote }}
    - name: VISIBILITY_DBNAME
      value: {{ .Values.infra.postgres.database | quote }}
    - name: SKIP_DB_CREATE
      value: "true"
    - name: SKIP_SCHEMA_SETUP
      value: "true"
    - name: SQL_MAX_CONNS
      value: "4"
    - name: SQL_MAX_IDLE_CONNS
      value: "2"
    - name: SQL_VIS_MAX_CONNS
      value: "2"
    - name: SQL_VIS_MAX_IDLE_CONNS
      value: "1"
  startupProbe:
    exec:
      command: ["/bin/sh", "-ec", 'temporal operator cluster health --address "$TEMPORAL_ADDRESS"']
    periodSeconds: 5
    timeoutSeconds: 5
    failureThreshold: 60
  readinessProbe:
    exec:
      command: ["/bin/sh", "-ec", 'temporal operator cluster health --address "$TEMPORAL_ADDRESS"']
    periodSeconds: 10
    timeoutSeconds: 5
  livenessProbe:
    exec:
      command: ["/bin/sh", "-ec", 'temporal operator cluster health --address "$TEMPORAL_ADDRESS"']
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 5
  resources:
    {{- toYaml .Values.infra.temporal.resources | nindent 4 }}
{{- end -}}
