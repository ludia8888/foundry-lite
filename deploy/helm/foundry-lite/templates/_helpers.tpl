{{- define "foundry-lite.name" -}}
foundry-lite
{{- end -}}

{{- define "foundry-lite.fullname" -}}
{{- if contains (include "foundry-lite.name" .) .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "foundry-lite.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "foundry-lite.labels" -}}
app.kubernetes.io/name: {{ include "foundry-lite.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "foundry-lite.selectorLabels" -}}
app.kubernetes.io/name: {{ include "foundry-lite.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "foundry-lite.image" -}}
{{- $image := index . 0 -}}
{{- $root := index . 1 -}}
{{- if and $root.Values.global.protectedProfile (or (empty $image.digest) (eq $image.digest "sha256:0000000000000000000000000000000000000000000000000000000000000000")) -}}
{{- fail "protectedProfile requires a non-placeholder sha256 image digest" -}}
{{- end -}}
{{- printf "%s@%s" $image.repository $image.digest -}}
{{- end -}}

{{- define "foundry-lite.podSecurityContext" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "foundry-lite.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
capabilities:
  drop: ["ALL"]
{{- end -}}

{{- define "foundry-lite.imagePullSecrets" -}}
{{- range .Values.global.imagePullSecrets }}
- name: {{ . }}
{{- end }}
{{- end -}}

{{- define "foundry-lite.validateProtectedProfile" -}}
{{- if .Values.global.protectedProfile -}}
{{- if eq .Values.secrets.applicationExistingSecret .Values.secrets.migrationExistingSecret -}}
{{- fail "protectedProfile requires distinct application and migration database secrets" -}}
{{- end -}}
{{- if and (eq .Values.global.runtimeProfile "production") (ne .Values.auth.profile "oidc") -}}
{{- fail "production protectedProfile requires auth.profile=oidc" -}}
{{- end -}}
{{- if and (eq .Values.auth.profile "oidc") (ne .Values.external.oidc.issuer .Values.mcp.authorizationServer) -}}
{{- fail "external.oidc.issuer and mcp.authorizationServer must match exactly" -}}
{{- end -}}
{{- if and (eq .Values.auth.profile "oidc") .Values.mcp.governedReleaseApplicationId -}}
{{- $releaseAudience := printf "%s/mcp/release/%s" (trimSuffix "/" .Values.mcp.publicBaseUrl) .Values.mcp.governedReleaseApplicationId -}}
{{- if ne $releaseAudience .Values.external.oidc.audience -}}
{{- fail "external.oidc.audience must equal the exact governed release resource URI" -}}
{{- end -}}
{{- end -}}
{{- if and .Values.qaDependencies.enabled (eq .Values.auth.profile "oidc") -}}
{{- $realmIssuer := printf "%s/realms/foundry-lite" (trimSuffix "/" .Values.qaDependencies.keycloak.publicBaseUrl) -}}
{{- if ne $realmIssuer .Values.external.oidc.issuer -}}
{{- fail "qaDependencies.keycloak.publicBaseUrl must be the origin of external.oidc.issuer" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
