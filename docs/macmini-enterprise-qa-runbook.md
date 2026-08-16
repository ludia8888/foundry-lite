# Mac mini Enterprise QA Runbook

**Status:** 실행 절차와 합격 기준. 저장소 패키지가 있다는 사실과 실제 원격 실행 결과를 구분한다.

**Target:** `sean1234@ongleam-macmini`, `/Users/sean1234/foundry-qa`, Colima profile `foundry-qa`, Kubernetes namespace `foundry-qa`와 `foundry-qa-recovery`만 사용한다.

## 1. 증명하려는 것과 증명하지 못하는 것

이 run은 단일 Mac mini에서 production-shaped Kubernetes 배포, 실제 제품 폐루프, Pod/process/전용 Colima VM 장애 복구, 백업 복원을 검증한다. 다른 macOS 사용자, 다른 Colima profile, Docker Desktop, 다른 Kubernetes namespace는 변경하지 않는다.

단일 물리 장비이므로 아래 항목은 성공하더라도 `notProven`이다.

- 물리 호스트 장애 중 무중단
- 여러 node 또는 availability zone 사이의 failover
- 상용 24/7 SLA와 실제 production edge 용량
- Tailscale Funnel의 production edge 적합성

## 2. 소스와 이미지 고정

원격 설치를 시작하기 전에 다음이 모두 있어야 한다.

1. 대상 commit은 원격 `main`의 정확한 40자리 SHA다.
2. GitHub Actions가 API, Web, controller, Python code execution, Node code execution, trained model의 `linux/arm64` 이미지를 `sha-<commit>`으로 발행했다.
3. 각 이미지는 mutable tag가 아니라 `repository@sha256:<digest>`이고 OCI revision, SBOM, cosign 검증을 통과했다.
4. 여섯 좌표와 revision을 담은 image manifest를 `/Users/sean1234/foundry-qa/state`에 `0600`으로 둔다.

이미지나 chart를 개발 worktree에서 즉석 수정하여 배포하지 않는다. required CI와 release gate가 끝난 동일 commit만 사용한다.

## 3. 안전 preflight와 k3s 준비

원격에서 먼저 hostname, macOS principal, home, Tailscale identity를 읽기 전용으로 확인한다. 다음 명령은 `assert_host_boundary()`를 통과한 `sean1234`만 실행할 수 있고, 전용 profile만 stop/start한다.

```bash
cd /Users/sean1234/foundry-qa/repo
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/prepare_macmini_qa.py \
  --run-id "$RUN_ID" --profile foundry-qa --restart
```

합격 조건은 `6 CPU / 16 GiB / 120 GiB`, aarch64, k3s, secrets encryption enabled이며 host reboot와 다른 profile mutation은 모두 false다. kubectl, Helm, age, cosign, kubeconform 같은 도구는 `/Users/sean1234/foundry-qa/bin`에 digest/hash 검증 후 설치한다.

## 4. 최초 설치

application secret, QA dependency secret, backup age recipient, pull secret은 Git이나 Helm values에 기록하지 않는다. 부분적으로만 생성된 secret은 자동 덮어쓰지 않고 사람이 원인을 확인하도록 실패한다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/deploy_macmini_qa.py \
  --run-id "$RUN_ID" \
  --kubeconfig /Users/sean1234/.colima/foundry-qa/kubeconfig \
  --chart /Users/sean1234/foundry-qa/repo/deploy/helm/foundry-lite \
  --values /Users/sean1234/foundry-qa/repo/deploy/helm/foundry-lite/values.macmini-qa.yaml \
  --initial-auth-values /Users/sean1234/foundry-qa/repo/deploy/helm/foundry-lite/values.embedded-oauth-smoke.yaml \
  --image-manifest /Users/sean1234/foundry-qa/state/images.json \
  --age-recipient-file /Users/sean1234/foundry-qa/state/age-recipient.txt
```

도구는 foundation과 runtime을 두 단계로 설치한다. foundation에서는 stateful dependency만 준비하고 API/Web/worker를 0 또는 disabled로 둔다. 초기 runtime은 `values.embedded-oauth-smoke.yaml`을 반드시 검증·적용해 `identity.invalid` 외부 OIDC로 잘못 부팅되는 것을 막고, tailnet 내부 폐루프를 위한 내장 OAuth 시험 모드로 시작한다. final atomic upgrade의 pre-upgrade migration Job은 migration을 실제로 두 번 실행한다. 완료 영수증은 Helm revision, 적용한 두 values 파일의 합성 hash, `initialAuthMode=embedded_oauth_smoke`, Pod inventory, 실제 migration marker와 raw log가 아닌 log SHA-256을 기록한다. 기존 Helm release가 있으면 초기 설치 도구는 애플리케이션을 0 replica로 내리지 않고 실패한다.

## 5. 내부 tailnet 폐루프

Funnel을 열기 전에 tailnet에서 다음을 모두 실제 배포에 대해 실행한다.

- `/healthz`, API가 DB 연결까지 확인하는 `/readyz`, Web UI
- generated OSDK object query와 Action
- seed → Dataset commit → Transform → Ontology activation → Object query → Action → audit/outbox → materialization → downstream transform
- 식당 예약 고객 OSDK와 영수증/media 처리
- Builder, Ontology, Governed Release MCP의 read/write 흐름
- Python Function, Node Function, trained-model Kubernetes Job과 결과 hash

HTTP 200만으로 합격시키지 않는다. DB transaction, dataset/object version, action run, audit/outbox, downstream 결과 hash를 함께 대조한다.

## 6. Keycloak과 hosted ChatGPT

1차 내장 OAuth 검증 뒤 2차 Keycloak issuer로 바꾼다. Keycloak QA realm은 Authorization Code, PKCE S256, consent, public DCR, HTTPS ChatGPT/OpenAI redirect host, client ceiling과 protocol-mapper 제한을 적용한다.

Keycloak 26.7은 OAuth `resource` parameter를 직접 audience로 해석하지 못하므로 QA profile은 `mcp-audience:<exact-resource-uri>` parameterized scope를 exact token `aud`로 매핑한다. 이 보완은 opt-in이며 다른 IdP 계약을 완화하지 않는다.

ChatGPT DCR 뒤에는 생성된 public client의 실제 id를 확인하여 `external.oidc.allowedClientIdsJson`에 exact allowlist로 넣고 atomic upgrade한다. 그 전까지 hosted 호출은 의도적으로 `blocked`다. token은 같은 issuer/tenant, exact MCP resource audience와 scope, `azp`, `sid`, `human_grant=true`, `authorization_grant_type=authorization_code`를 가져야 한다. 로그인과 위젯 확인 gesture는 사용자가 직접 수행한다.

Tailscale owner와 DNS가 `sean1234` 대상임을 확인한 뒤에만 443을 Web/API/MCP, 8443을 Keycloak에 제한적으로 연다. 공개 단계가 끝나면 두 Funnel을 끄고 tailnet 내부 가동만 남긴다.

## 7. 장애 주입

각 장애는 `scripts/operations/inject_macmini_fault.py`로 한 번에 하나만 실행하고, 원래 replica와 NetworkPolicy selector를 `finally`에서 복구한다. 추가 deny policy는 기존 allow policy를 무효화하지 못하므로 network partition은 기존 internal policy의 selector를 bounded하게 patch한 뒤 원본으로 되돌린다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/inject_macmini_fault.py \
  --run-id "$RUN_ID" \
  --kubeconfig /Users/sean1234/.colima/foundry-qa/kubeconfig \
  --fault api-pod-delete
```

검증 대상은 API Pod, worker, PostgreSQL, MinIO, Redpanda, Temporal, Elasticsearch, ClamAV, invalid image, migration failure, network partition, disposable PVC disk pressure, Colima stop/start, Funnel/Keycloak 중단이다. 장애가 끝나도 replica, selector, PVC, current image digest가 원래 값과 다르면 실패다.

## 8. 백업과 recovery namespace 복원

백업은 restore mode로 write/outbox를 잠근 뒤 PostgreSQL dump, MinIO version manifest, schema revision, high-watermark, image digest와 checksum을 하나의 commit point로 묶고 age recipient로 암호화한다. raw token과 secret은 evidence에 넣지 않는다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/backup_macmini_qa.py \
  --run-id "$RUN_ID" \
  --kubeconfig /Users/sean1234/.colima/foundry-qa/kubeconfig \
  --bearer-token-file /Users/sean1234/foundry-qa/state/operator-token \
  --age-recipient-file /Users/sean1234/foundry-qa/state/age-recipient.txt
```

복원은 원본을 덮지 않고 `foundry-qa-recovery` namespace에 수행한다. Dataset inventory, active object index, action/materialization run, row/object/hash가 원본 백업 시점과 일치해야 resume approval을 허용한다. 목표는 RTO 30분 이내, backup commit point 기준 RPO 0이다.

## 9. 24시간 소크

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/run_macmini_soak.py \
  --run-id "$RUN_ID" \
  --kubeconfig /Users/sean1234/.colima/foundry-qa/kubeconfig \
  --duration-seconds 86400 --interval-seconds 60 \
  --probe healthz=https://FOUNDRY_TAILNET_HOST/healthz \
  --probe readyz=https://FOUNDRY_TAILNET_HOST/readyz \
  --probe osdk=https://FOUNDRY_TAILNET_HOST/api/objects/health-probe
```

실제 endpoint와 authenticated OSDK business probe는 배포 영수증에서 정한 값으로 치환한다. probe별 availability를 따로 계산하며 하나의 성공 probe가 다른 실패를 숨길 수 없다. 선언된 fault window는 baseline을 재설정하지만 그 밖의 restart 증가나 장기 Pod replacement는 실패다.

Acceptance는 다음과 같다.

- 선언된 장애 구간 밖 각 API/OSDK probe availability 99.9% 이상
- OOM 0, 예상 밖 restart/replacement 0
- committed 데이터 손실·중복 0
- node memory 85% 미만, disk 80% 미만
- warm-up 이후 지속적 memory/disk 증가 없음
- 모든 fault와 restore 결과가 `passed`, `failed`, `blocked`, `notProven` 중 하나로 분류됨
- 미해결 P0/P1 0

## 10. 증거와 종료

`/Users/sean1234/foundry-qa/evidence/<run-id>`에 Git SHA, image digest, Helm revision, redacted values hash, migration receipt, Kubernetes events, fault timeline, RTO/RPO, audit/outbox와 복구 checksum을 둔다. 최종 JSON/Markdown과 SHA-256 manifest의 암호화 사본을 현재 Mac의 `/Users/isihyeon/Foundry-QA-Evidence/<run-id>`로 복제한다.

GitHub/Anthropic 임시 secret은 제거하고 token을 폐기한다. Funnel 443/8443을 끈 뒤 tailnet 내부 health/readiness를 다시 확인한다. 실제 24시간이 끝나기 전, 또는 P0/P1이 남아 있는 상태에서는 Enterprise QA 완료라고 선언하지 않는다.
