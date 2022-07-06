{{/*
Expand the name of the chart.
*/}}
{{- define "nifi.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "nifi.fullname" -}}
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
{{- define "nifi.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "nifi.labels" -}}
helm.sh/chart: {{ include "nifi.chart" . }}
{{ include "nifi.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "nifi.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nifi.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "nifi.image" -}}
{{ if .Values.global.containerRegistry }}
{{ (printf "%s/" .Values.global.containerRegistry) }}
{{ end }}
{{- .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end }}

{{- define "nifi.ingressNodeList" -}}
{{- range $i, $e := until (.Values.nodeCount | int) }}
{{ printf "- %s%d.%s" $.Values.ingress.nodeBaseHostName $i $.Values.ingress.hostName }}
{{- end }}
{{- end }}

{{- define "nifi.ingressNodes" -}}
{{- range $i, $e := until (.Values.nodeCount | int) }}
{{- printf "%s%d.%s," $.Values.ingress.nodeBaseHostName $i $.Values.ingress.hostName }}
{{- end }}
{{- end }}

{{/*
NiFi Registry FQDN
*/}}
{{- define "nifi.registryUrl" -}}
{{ .Release.Name }}-{{ include "nifi-registry.fullname" . }}.{{ .Release.Namespace }}.svc
{{- end }}

{{- define "nifi.zookeeperUrl" -}}
zookeeper-url
{{- end }}
