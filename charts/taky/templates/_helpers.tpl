{{/*
Expand the name of the chart.
*/}}
{{- define "taky.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "taky.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "taky.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "taky.labels" -}}
helm.sh/chart: {{ include "taky.chart" . }}
{{ include "taky.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "taky.selectorLabels" -}}
app.kubernetes.io/name: {{ include "taky.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Fully qualified image reference, honoring global.image.registry.
*/}}
{{- define "taky.image" -}}
{{- $registry := .Values.image.registry | default .Values.global.image.registry | default "ghcr.io" -}}
{{- printf "%s/%s:%s" $registry .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end }}

{{/*
COT server port: 8087 plain, 8089 with TLS.
*/}}
{{- define "taky.cotPort" -}}
{{- ternary 8089 8087 .Values.ssl.enabled -}}
{{- end }}

{{/*
Data package server port: 8080 plain, 8443 with TLS.
*/}}
{{- define "taky.dpsPort" -}}
{{- ternary 8443 8080 .Values.ssl.enabled -}}
{{- end }}
