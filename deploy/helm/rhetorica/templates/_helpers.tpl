{{- define "rhetorica.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "rhetorica.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "rhetorica.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "rhetorica.labels" -}}
app.kubernetes.io/name: {{ include "rhetorica.name" . }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "rhetorica.tolerations" -}}
{{- $root := index . "root" -}}
{{- $component := index . "component" -}}
{{- $tolerations := $root.Values.global.tolerations -}}
{{- $componentTolerations := index $component "tolerations" -}}
{{- if kindIs "slice" $componentTolerations -}}
{{- $tolerations = $componentTolerations -}}
{{- end -}}
{{- with $tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end -}}
{{- end -}}
