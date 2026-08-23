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

clean host에는 시스템 Python 3.9만 있을 수 있으므로 Python 설치 도구를 먼저 실행할 수 있다고 가정하지 않는다. public repository의 exact `main` SHA를 private QA root에 checkout한 다음, 시스템 Bash bootstrap이 고정된 uv 0.12.5 darwin-arm64 archive의 SHA-256과 Mach-O architecture를 확인해 `bin/uv`만 설치한다. 그 uv가 Python 3.12를 QA state 안에 설치한 뒤, tracked `deploy/macmini-tools-arm64.json`의 exact allowlist 전체를 Python installer로 검증·설치한다. GitHub release의 `latest`나 Homebrew 전역 설치를 실행 시점에 따라가지 않는다.

```bash
RELEASE_SHA="<원격 main의 정확한 40자리 SHA>"
/bin/mkdir -p /Users/sean1234/foundry-qa
/bin/chmod 700 /Users/sean1234/foundry-qa
/usr/bin/git clone https://github.com/ludia8888/foundry-lite.git /Users/sean1234/foundry-qa/repo
cd /Users/sean1234/foundry-qa/repo
/usr/bin/git checkout --detach "$RELEASE_SHA"
/bin/bash scripts/operations/bootstrap_macmini_qa_uv.sh
export PATH="/Users/sean1234/foundry-qa/bin:$PATH"
export UV_PYTHON_INSTALL_DIR="/Users/sean1234/foundry-qa/state/python"
export UV_CACHE_DIR="/Users/sean1234/foundry-qa/state/uv-cache"
/Users/sean1234/foundry-qa/bin/uv python install 3.12
PYTHONPATH=. /Users/sean1234/foundry-qa/bin/uv run --no-project --python 3.12 python \
  scripts/operations/install_macmini_qa_tool.py \
  --manifest /Users/sean1234/foundry-qa/repo/deploy/macmini-tools-arm64.json
/Users/sean1234/foundry-qa/bin/uv sync --all-groups --frozen
```

```bash
cd /Users/sean1234/foundry-qa/repo
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/prepare_macmini_qa.py \
  --run-id "$RUN_ID" --profile foundry-qa --restart
```

합격 조건은 `6 CPU / 16 GiB / 120 GiB`, aarch64, k3s, secrets encryption enabled이며 host reboot와 다른 profile mutation은 모두 false다. 준비 도구는 k3s `/readyz`를 bounded polling한 뒤 guest kubeconfig의 실제 loopback API port로 secrets encryption JSON 상태와 key hash 일치를 검증하고, 현재 `colima-foundry-qa` context를 `/Users/sean1234/foundry-qa/state/kubeconfig`에 mode `0600`으로 내보내 다시 `/readyz`를 확인한다. `uv`, kubectl, Helm, age, age-keygen, cosign, crane, kubeconform은 `/Users/sean1234/foundry-qa/bin`에 exact URL/archive member/SHA-256 검증 후 설치한다.
비대화형 SSH가 사용자 shell의 PATH를 로드하지 않아도 Colima의 Kubernetes dependency check가 QA 전용 kubectl을 찾을 수 있도록, 모든 준비 명령은 `/Users/sean1234/foundry-qa/bin`과 `/opt/homebrew/bin`을 상속 PATH 앞에 결정적으로 추가한다. 원문 PATH나 secret은 영수증에 기록하지 않는다.

## 4. 최초 설치

초기 smoke application secret, protected runtime application secret, migration secret, QA dependency secret, backup age recipient, pull secret은 Git이나 Helm values에 기록하지 않는다. 내장 OAuth smoke는 명시적인 비보호 `test` profile과 관리자 연결로만 짧게 검증한다. production OIDC 전환은 Pod rollout 전에 `applicationExistingSecret`을 별도 `foundry-lite-runtime-application` Secret으로 바꾸며, 이 최종 runtime URL은 `NOSUPERUSER`·`NOBYPASSRLS`인 `foundry_lite_app` 역할만 사용한다. Alembic migration은 별도 관리자 Secret을 사용한다. Helm의 bounded bootstrap Job은 runtime 역할이 이미 privileged이면 권한을 자동 축소해 숨기지 않고 실패하며, 현재·미래 테이블과 sequence에 필요한 DML 권한만 부여한다. `state/github-packages-token`은 `read:packages`만 가진 임시 token을 한 줄로 담은 `0600` 일반 파일이어야 하며, bootstrap은 이를 immutable `kubernetes.io/dockerconfigjson` Secret으로 변환한다. 설치기는 Helm을 시작하기 전에 이 token을 argv나 환경변수에 넣지 않고 표준입력으로만 Docker CLI에 전달해 전용 `/Users/sean1234/.colima/foundry-qa/docker.sock`에 여섯 exact digest를 모두 pre-pull한다. 각 cached image의 `RepoDigests`, `linux/arm64`, OCI revision을 다시 검사하고, mode `0700` 임시 Docker auth directory는 성공과 실패 모두에서 삭제한다. token 원문은 receipt, Helm values, 오류 문자열에 남기지 않는다. 부분적으로만 생성된 secret은 자동 덮어쓰지 않고 사람이 원인을 확인하도록 실패한다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/deploy_macmini_qa.py \
  --run-id "$RUN_ID" \
  --kubeconfig /Users/sean1234/foundry-qa/state/kubeconfig \
  --chart /Users/sean1234/foundry-qa/repo/deploy/helm/foundry-lite \
  --values /Users/sean1234/foundry-qa/repo/deploy/helm/foundry-lite/values.macmini-qa.yaml \
  --initial-auth-values /Users/sean1234/foundry-qa/repo/deploy/helm/foundry-lite/values.embedded-oauth-smoke.yaml \
  --image-manifest /Users/sean1234/foundry-qa/state/images.json \
  --age-recipient-file /Users/sean1234/foundry-qa/state/age-recipient.txt \
  --registry-token-file /Users/sean1234/foundry-qa/state/github-packages-token
```

도구는 image pre-pull 뒤 foundation과 runtime을 두 단계로 설치한다. foundation에서는 stateful dependency만 준비하고 API/Web/worker를 0 또는 disabled로 둔다. 설치·upgrade 도구는 `default/kubernetes` endpoint를 읽어 K3s API의 실제 backend CIDR/port를 private override에 고정한다. 따라서 NetworkPolicy가 Service DNAT 뒤에 적용되는 Colima에서도 release controller가 API server에 연결할 수 있고, 단순히 ClusterIP 443만 허용해 연결이 거절되는 상태를 피한다. controller는 OCI index에서 config를 읽을 때 `crane config --platform linux/arm64`를 명시해 검증 대상과 다른 amd64 기본 child를 찾지 않는다. immutable OAuth signing Secret 생성 Job과 그 제한 RBAC는 최초 `pre-install`에서만 실행한다. 이후 upgrade는 기존 Secret을 재사용하며, Secret이 사라졌으면 workload의 required Secret mount가 fail-closed한다. 이는 설치 후 default-deny NetworkPolicy가 활성화된 상태에서 bootstrap Pod가 Kubernetes API를 다시 호출하지 않도록 하는 one-time key 계약이다. 초기 runtime은 `values.embedded-oauth-smoke.yaml`을 반드시 검증·적용해 `identity.invalid` 외부 OIDC로 잘못 부팅되는 것을 막고, 비보호 `test` profile에서 tailnet 내부 폐루프를 위한 내장 OAuth만 짧게 점검한다. pre-upgrade role bootstrap은 최종 runtime DB principal의 비슈퍼유저·`NOBYPASSRLS` 상태를 보장하고, 이어지는 migration Job은 별도 관리자 Secret으로 migration을 실제로 두 번 실행한다. 이 초기 smoke는 production RLS 합격 증거로 계산하지 않는다. Keycloak OIDC atomic 전환이 production profile과 별도 runtime Secret을 함께 선택하고 rollout을 끝낸 뒤에만 protected runtime·RLS 검증을 시작한다. 완료 영수증은 image pre-pull count와 output hash, Helm revision, 적용한 두 values 파일의 합성 hash, `initialAuthMode=embedded_oauth_smoke`, Pod inventory, 실제 migration marker와 raw log가 아닌 log SHA-256을 기록한다. 기존 Helm release가 있으면 초기 설치 도구는 애플리케이션을 0 replica로 내리지 않고 실패한다.

## 4.1 이후 Helm upgrade

일반 `helm upgrade`를 직접 실행하지 않는다. `upgrade_macmini_qa.py`는 이미 `deployed`인 release만 대상으로 하고, Helm mutation 전에 동일한 전용 Colima Docker socket에서 여섯 immutable image digest를 pre-pull·ARM64·OCI revision까지 다시 검증한다. 따라서 처음 보는 큰 API image 때문에 pre-upgrade Job의 deadline이 먼저 만료되어 atomic rollback 되는 경로를 막는다. pre-pull 또는 cached-image 검증이 실패하면 Helm command는 실행되지 않는다. Helm 4 server-side apply에서는 chart가 release workload의 권위 있는 소유자이므로 upgrade에 `--force-conflicts`를 적용한다. 이는 앞선 bounded fault가 남긴 별도 field manager 때문에 검증된 chart image가 거절되는 상태를 회수하며, resource replacement를 허용하는 `--force-replace`와는 다르다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/upgrade_macmini_qa.py \
  --run-id "${RUN_ID}-upgrade" \
  --kubeconfig /Users/sean1234/foundry-qa/state/kubeconfig \
  --chart /Users/sean1234/foundry-qa/repo/deploy/helm/foundry-lite \
  --values /Users/sean1234/foundry-qa/repo/deploy/helm/foundry-lite/values.macmini-qa.yaml \
  --image-manifest /Users/sean1234/foundry-qa/state/images.json \
  --registry-token-file /Users/sean1234/foundry-qa/state/github-packages-token
```

성공 receipt는 image pre-pull evidence와 Helm revision을 함께 남긴다. 이미 없는 release, `deployed`가 아닌 release, pre-pull 실패는 모두 fail-closed한다.

## 5. 내부 tailnet 폐루프

Funnel을 열기 전에 tailnet에서 다음을 모두 실제 배포에 대해 실행한다.

- `/healthz`, API가 DB 연결까지 확인하는 `/readyz`, Web UI
- generated OSDK object query와 Action
- seed → Dataset commit → Transform → Ontology activation → Object query → Action → audit/outbox → materialization → downstream transform
- 식당 예약 고객 OSDK와 영수증/media 처리
- Builder, Ontology, Governed Release MCP의 read/write 흐름
- Python Function, Node Function, trained-model Kubernetes Job과 결과 hash
- Function API/생성 SDK의 `runtimeEvidence`와 Transform Dataset transaction metadata의 `runtimeEvidence`가 같은 exact image/result/network 증거를 보존하고 raw token·source·input·stdout·stderr를 포함하지 않는지 확인

HTTP 200만으로 합격시키지 않는다. DB transaction, dataset/object version, action run, audit/outbox, downstream 결과 hash를 함께 대조한다.

현재 실행처럼 Tailscale 장비 owner가 `sean1234`가 아니라면 Serve/Funnel을 변경하지 않는다. 이 경우 SSH 세션 안에서 Web과 `/api`·`/mcp`를 함께 라우팅하는 gateway NodePort `http://127.0.0.1:30443`을 사용해 내부 폐루프를 검증한다. `30444`는 Web이 아니라 Keycloak 전용 NodePort다. 공개 hosted 단계는 `blocked`로 기록한다. 이는 제품 실패가 아니라 다른 사용자의 네트워크 설정을 건드리지 않기 위한 안전 경계다.

24시간 검증용 전용 객체와 Action은 실제 배포 API를 통해 한 번 bootstrap한다. 이 명령은 CSV Source commit, Ontology activation, Object reindex, Object query, Action 실행과 동일 요청 replay, Action Log materialization과 Dataset preview를 수행하고 이후 replay에 필요한 정확한 초기 object version을 `0600` config에 고정한다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/run_macmini_business_probe.py \
  bootstrap --base-url http://127.0.0.1:30443 \
  --config /Users/sean1234/foundry-qa/state/business-probe.json
```

외부 OIDC 단계에서는 같은 명령에 `--bearer-token-file /Users/sean1234/foundry-qa/state/operator-token`을 추가한다. token 파일은 `0600`이어야 하며 token 원문은 stdout, config, 소크 sample, 최종 summary 어디에도 기록하지 않는다.

production OIDC 전환과 위 폐루프가 끝난 뒤에는 실제 API Pod의 현재 DB 연결로 PostgreSQL object-store를 검사한다. 이 검사는 `current_user=foundry_lite_app`, 모든 privilege flag false, JSONB 15개 컬럼, 운영 인덱스 10개(그중 `jsonb_path_ops` GIN 2개), `ENABLE/FORCE RLS`와 tenant policy가 있는 9개 테이블, tenant context가 없거나 다른 tenant일 때 0건, 교차 tenant insert 차단을 요구한다. 쓰기 검사는 transaction rollback 안에서 실행한다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python \
  scripts/operations/verify_macmini_postgres_object_store.py \
  --run-id "$RUN_ID" \
  --namespace foundry-qa \
  --kubeconfig /Users/sean1234/foundry-qa/state/kubeconfig
```

## 6. Keycloak과 hosted ChatGPT

1차 내장 OAuth 검증 뒤 2차 Keycloak issuer로 바꾼다. Keycloak QA realm은 Authorization Code, PKCE S256, consent, public DCR, HTTPS ChatGPT/OpenAI redirect host, client ceiling과 protocol-mapper 제한을 적용한다.

Keycloak 26.7은 OAuth `resource` parameter를 직접 audience로 해석하지 못하므로 QA profile은 `mcp-audience:<exact-resource-uri>` parameterized scope를 exact token `aud`로 매핑한다. 이 보완은 opt-in이며 다른 IdP 계약을 완화하지 않는다.

ChatGPT DCR 뒤에는 생성된 public client의 실제 id를 확인하여 `external.oidc.allowedClientIdsJson`에 exact allowlist로 넣고 atomic upgrade한다. 그 전까지 hosted 호출은 의도적으로 `blocked`다. token은 같은 issuer/tenant, exact MCP resource audience와 scope, `azp`, `sid`, `human_grant=true`, `authorization_grant_type=authorization_code`를 가져야 한다. 로그인과 위젯 확인 gesture는 사용자가 직접 수행한다.

secret bootstrap은 서로 다른 `sean1234-author`와 `sean1234-reviewer` 계정을 만들고 자격 증명은 mode `0600`인 `/Users/sean1234/foundry-qa/state/keycloak-qa-principals.txt`에만 둔다. 두 계정으로 각각 Authorization Code + PKCE 로그인을 완료한 뒤 raw access token은 `state/author-token`과 `state/reviewer-token`에 mode `0600`으로만 저장한다. 다음 두 명령은 Helm 값을 production OIDC로 atomic 전환하고, 실제 production auth adapter로 두 JWT의 issuer/audience/client/scope/human grant와 서로 다른 `sub`/`sid`를 검증한다. 영수증에는 hash만 남고 raw token은 기록하지 않는다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/switch_macmini_external_oidc.py \
  --run-id "$RUN_ID" \
  --kubeconfig /Users/sean1234/foundry-qa/state/kubeconfig \
  --chart /Users/sean1234/foundry-qa/repo/deploy/helm/foundry-lite \
  --public-base-url "https://<sean1234-owned-host>" \
  --identity-base-url "https://<sean1234-owned-idp-host>" \
  --application-id "<release-application-id>" \
  --allowed-client-id "<ChatGPT-DCR-client-id>"

PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/verify_macmini_external_oidc.py \
  --run-id "$RUN_ID" \
  --issuer "https://<sean1234-owned-idp-host>/realms/foundry-lite" \
  --discovery-url "https://<sean1234-owned-idp-host>/realms/foundry-lite/.well-known/openid-configuration" \
  --audience "https://<sean1234-owned-host>/mcp/release/<release-application-id>" \
  --allowed-client-id "<ChatGPT-DCR-client-id>" \
  --author-token-file /Users/sean1234/foundry-qa/state/author-token \
  --reviewer-token-file /Users/sean1234/foundry-qa/state/reviewer-token
```

이 검사는 서로 다른 IdP subject/session을 기술적으로 증명한다. 실제 물리적으로 서로 다른 두 사람이 계정을 공유하지 않았다는 조직적 통제는 IdP 계정 정책과 운영 절차로 별도 증명해야 한다.

Tailscale owner와 DNS가 `sean1234` 대상임을 확인한 뒤에만 443을 Web/API/MCP, 8443을 Keycloak에 제한적으로 연다. 공개 단계가 끝나면 두 Funnel을 끄고 tailnet 내부 가동만 남긴다.

## 7. 장애 주입

각 장애는 `scripts/operations/inject_macmini_fault.py`로 한 번에 하나만 실행하고, 원래 replica와 NetworkPolicy selector를 `finally`에서 복구한다. 추가 deny policy는 기존 allow policy를 무효화하지 못하므로 network partition은 기존 internal policy의 selector를 bounded하게 patch한 뒤 원본으로 되돌린다. signal fault는 더 이상 `kubectl exec` 안의 PID 1을 죽이지 않는다. Kubernetes status의 exact container ID를 Colima runtime `docker inspect`로 host PID까지 다시 대조한 뒤 같은 runtime container에 `docker kill --signal <SIGNAL>`을 보낸다. receipt는 kill acceptance timestamp, runtime container ID/PID 전후, restartCount, 새 container ID, 새 host PID, rollout recovery를 모두 남긴다. `SIGKILL`은 Kubernetes termination signal 또는 `128+signal` exit code를 반드시 요구한다. `SIGTERM`은 application이 신호를 정상 처리할 수 있으므로 같은 runtime kill·container 교체·restart 증가가 모두 증명되고 Kubernetes가 `reason=Completed`, `exitCode=0`, 종료 시각을 기록한 경우 `terminationMode=graceful`로도 통과한다. 이 완화는 `SIGKILL`에는 적용하지 않는다.

지원하는 추가 안전 장애 이름은 `invalid-image`, `bad-config`, `migration-failure`, `pvc-disk-pressure`다. `invalid-image`는 존재하지 않는 digest가 새 API replica로 승격되지 않는지 확인한 뒤 `rollout undo`가 아니라 관측한 원래 digest를 명시적으로 복원한다. 이 임시 변경과 복원은 모두 `--field-manager=helm`을 사용해 이후 Helm 4 upgrade를 막는 `kubectl-set` 소유권을 남기지 않는다. `bad-config`는 보호 프로필에서 application DB Secret 이름을 현재 migration DB Secret 이름과 같게 만들어 chart의 불변조건이 배포 전에 거절하는지 확인한다. 런타임 인증 프로필에 의존하지 않으며, 예상과 달리 Helm revision이나 values가 바뀌면 장애 전 revision으로 즉시 rollback하고 원래 values hash를 확인한다. `migration-failure`는 임시 immutable DB Secret과 `helm upgrade --atomic --reuse-values`로 실제 pre-upgrade migration hook을 연결 불가능한 loopback DB에 실행한다. 외부 프로세스 제한시간은 Helm의 3분 제한과 atomic rollback보다 길게 두고, 그래도 timeout 또는 `pending-rollback`이 관측되면 장애 전 revision으로 bounded rollback한다. 최종 release가 `deployed`이고 원래 values hash, live Deployment image, 가용 replica가 모두 복원되어야 통과한다. `pvc-disk-pressure`는 전용 128 MiB `local-path` PVC만 112 MiB(87.5%)까지 채워 임계 경보를 기록하고, 더 쓰지 않은 채 Job과 PVC를 삭제한다. 정리는 Kubernetes 리소스 부재뿐 아니라 PVC가 실제 저장되는 Colima 데이터 디스크의 `/var/lib/rancher/k3s/storage` 가용 공간 회복으로 확인한다. macOS host path나 기존 PVC는 채우지 않는다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/inject_macmini_fault.py \
  --run-id "$RUN_ID" \
  --kubeconfig /Users/sean1234/foundry-qa/state/kubeconfig \
  --fault api-pod
```

검증 대상은 API Pod, worker, PostgreSQL, MinIO, Redpanda, Temporal, Elasticsearch, ClamAV, invalid image, migration failure, network partition, disposable PVC disk pressure, Colima stop/start, Funnel/Keycloak 중단이다. 장애가 끝나도 replica, selector, PVC, current image digest가 원래 값과 다르면 실패다.

## 8. 백업과 recovery namespace 복원

백업은 restore mode로 write/outbox를 잠근 뒤 PostgreSQL dump, MinIO version manifest, schema revision, high-watermark, image digest와 checksum을 하나의 commit point로 묶고 age recipient로 암호화한다. loopback Operations 요청은 bearer token과 bounded QA operator identity header를 함께 보내며, OIDC profile은 검증된 bearer를, header-trust profile은 operator header를 각자의 인증 경계로 사용한다. 이때 Helm의 실제 merged release values도 commit point 앞뒤로 두 번 조회해 동일해야 하며, exact Git revision, 6개 GHCR repository/digest, image pull Secret 참조, 인증 profile, QA dependency profile을 검증한 뒤 암호화 archive에 포함한다. 로컬 chart 경로가 release Git SHA의 깨끗한 tracked tree인지 확인하고 exact chart package도 같은 archive에 고정한다. Helm values에는 Secret 내용이 없고 참조 이름만 있으며, raw token과 Secret 내용은 evidence나 archive에 넣지 않는다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/backup_macmini_qa.py \
  --run-id "$RUN_ID" \
  --kubeconfig /Users/sean1234/foundry-qa/state/kubeconfig \
  --bearer-token-file /Users/sean1234/foundry-qa/state/operator-token \
  --age-recipient-file /Users/sean1234/foundry-qa/state/age-recipient.txt
```

복원은 원본을 덮지 않고 `foundry-qa-recovery` namespace에 수행한다. source namespace에서 애플리케이션 설정, OAuth signing key, QA dependency credential, age recipient, GHCR pull credential Secret을 recovery namespace에 직접 복제하되, Secret 내용은 백업 archive나 영수증에 기록하지 않는다. 먼저 API/Web/worker/controller/broker를 끈 foundation phase로 PostgreSQL·MinIO와 기반 서비스만 준비하고 PostgreSQL을 복원한다. 그 뒤 recovery 내부에서만 API 하나를 잠시 켜 S3 version archive를 복원하고 DB inventory를 비교한다. 마지막 atomic Helm phase에서 백업에 고정된 exact chart package와 release values를 다시 적용한 다음 전체 deployment를 기다린다. 현재 checkout의 chart/default values나 mutable image tag로 대체하지 않는다.

단일 6 CPU/16 GiB 노드에서는 source와 recovery 전체 stack을 동시에 상주시킬 수 없으므로 restore mode 안에서 bounded capacity handoff를 수행한다. source PostgreSQL·MinIO와 API 1개는 검증용으로 유지하고 Web, execution broker, release controller, Temporal, 검색·관측·인증 보조 workload만 원래 replica를 기록한 뒤 일시 축소한다. recovery 전체 release와 post-restore validation을 통과하면 recovery workload를 0 replica로 hibernate하고 source의 기록된 replica와 worker를 복원한다. 모든 scale은 Helm 4 server-side apply와 충돌하지 않도록 `--field-manager=helm`을 사용하며, recovery PVC는 보존한다. 이는 두 stack을 축소된 resource 값으로 동시에 실행하는 검사가 아니라, exact release values를 순차적으로 검증하는 단일노드 cold-restore rehearsal이다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/restore_macmini_qa.py \
  --run-id "$RUN_ID" \
  --kubeconfig /Users/sean1234/foundry-qa/state/kubeconfig \
  --age-identity-file /Users/sean1234/foundry-qa/state/age-identity.txt \
  --bearer-token-file /Users/sean1234/foundry-qa/state/operator-token
```

Dataset inventory, active object index, action/materialization run, row/object/hash가 원본 백업 시점과 일치해야 recovery와 source의 resume approval을 허용한다. 목표는 RTO 30분 이내, backup commit point 기준 RPO 0이다.

## 9. 24시간 복합 장애 캠페인

최종 합격 소크는 health check만 반복하는 단순 소크가 아니다. `run_macmini_enterprise_campaign.py`가 시작 전에
절대 시각 기준 plan과 fault window를 먼저 고정하고, 내부 `run_macmini_soak.py` sampler를 5초 간격으로 실행한다.
모든 business closed loop는 Action idempotent replay, materialization, Dataset preview, Object query를 다시 확인한다.
운영 probe는 PostgreSQL connection 수, pending outbox 수와 최고 지연, dead-letter 증가를 분 단위로 남긴다.
최종 soak summary는 전체 집계 외에도 9개 단계별 sample 수, availability, HTTP/business p50·p95·p99,
memory/disk/DB connection p50·p95·p99, outbox 최고치와 최종치를 별도로 남긴다. quiet 마지막 10% 구간은
baseline p95 대비 memory `+10%p`, disk `+5%p`, DB connection `max(+5, +20%)` 안으로 복귀해야 하며,
business failure가 없어야 한다. 24시간 workload가 끝나면 business probe를 먼저 완전히 중단하고 operations-only
drain barrier를 시작한다. 2초 간격으로 pending outbox와 oldest lag가 모두 0인 관측이 3회 연속 나와야
`outboxDrain.status=passed`가 된다. 120초 안에 이 조건을 만족하지 못하면 실패한다. 따라서 workload가 새 이벤트를
만드는 순간에 찍힌 마지막 snapshot과 실제 drain 완료를 혼동하지 않는다. baseline 또는 quiet sample이 10개보다
적어도 fail-closed한다.

| 경과 시간 | 단계 | 실제 주입 및 검증 |
|---|---|---|
| 00~02h | baseline | closed loop, HTTP/business p50·p95·p99, memory, DB connection, outbox lag, disk |
| 02~05h | compute | API·worker·controller Pod delete, PID 1 SIGTERM/SIGKILL, worker `OOMKilled`와 원래 command 복원 |
| 05~08h | dependencies | PostgreSQL, MinIO, Temporal, Redpanda, Elasticsearch, ClamAV scale-down과 bounded recovery |
| 08~11h | network | 전용 Colima `cni0`의 latency·packet loss·TCP reset·DNS reject·full partition과 NetworkPolicy isolation |
| 11~14h | multi-tenant | Tenant A flood 중 Tenant B closed loop, 선행 cross-tenant 404, JSONB/index/FORCE RLS, durable MCP quota 분리 |
| 14~17h | security/time | deployed image 안에서 JWKS rotation grace/retirement, expired·revoked token, lease·cursor expiry와 key rotation |
| 17~20h | release | same-digest rolling restart, bad image/config/migration rejection, controller-verified signed digest rollback/forward |
| 20~22h | DR | encrypted checkpoint, `foundry-qa-recovery` 비파괴 restore, RTO/RPO와 semantic validation |
| 22~24h | quiet | 새 장애 중단, backlog drain, resource baseline 복귀, 최종 invariant scan |

실제 external issuer/JWKS network path는 승인된 public/tailnet owner와 issuer가 연결된 경우에만 별도 통과할 수 있다.
그 경로가 없으면 내부 deployed-image rotation proof와 혼동하지 않고 `blocked`로 남긴다. 단일 노드이므로
multi-node·multi-AZ는 항상 `notProven`이다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/run_macmini_enterprise_campaign.py \
  --run-id "$RUN_ID" \
  --kubeconfig /Users/sean1234/foundry-qa/state/kubeconfig \
  --business-probe-config /Users/sean1234/foundry-qa/state/business-probe.json \
  --operator-token-file /Users/sean1234/foundry-qa/state/operator-token \
  --age-recipient-file /Users/sean1234/foundry-qa/state/age-recipient.txt \
  --age-identity-file /Users/sean1234/foundry-qa/state/age-identity.txt \
  --current-commit "$CURRENT_SHA" \
  --rollback-commit "$PREVIOUS_VERIFIED_SHA"
```

Tailscale owner 검증을 통과한 별도 실행에서만 loopback URL을 승인된 tailnet URL로 치환한다. campaign은 각
이벤트 뒤 business/operations recovery probe가 둘 다 통과해야 다음 destructive fault를 허용한다. StatefulSet이나
container가 Ready여도 API connection pool이 기존 연결을 정리하는 짧은 구간이 있을 수 있으므로 recovery probe는
최대 120초 동안 5초 간격으로 bounded polling한다. receipt에는 첫 시도 상태, 총 시도 수, 재시도 후 복구 여부와
최종 business/operations 증거를 남긴다. 그 뒤 Outbox `pending=0`, `oldest=0`, 장애 전 대비 DLQ 증가 0을 2초 간격으로 세 번 연속 확인한 후에만 다음 장애를 시작한다. Publisher는 일시적 stream 실패를 최대 5회까지 pending으로 되돌려 재시도하며, 한도를 모두 소진한 경우에만 DLQ로 이동한다. fault execution
증거가 실패해도 recovery probe가 모두 통과하면 해당 이벤트 자체는 실패로 남기되 다음 장애는 계속 실행한다.
실제 복구가 실패할 때만 남은 mutation을 중지하고 quiet observation만 유지한다. 네트워크 fault는 다른 macOS 계정이나 Docker Desktop이
아니라 전용 `foundry-qa` Colima VM의 `cni0`에만 적용하며, 기존 qdisc가 있으면 덮어쓰지 않고 중단한다. 모든
qdisc/iptables/Deployment command 변경은 `finally`에서 exact 원상복구한다.

판정 오류나 중간 프로세스 종료 때문에 이전 campaign에서 `failed`, `skipped`, 또는 plan에는 있지만 journal에는 없는 이벤트를 즉시 다시 실행할 때는 같은 고정 event command와 recovery probe를 사용하는 remediation mode를 쓴다. 이 mode는 source journal SHA-256과 선택된 event ID, 개별 execution/recovery/outbox-drain receipt를 새 run 아래에 남긴다. `blocked`와 이미 `passed`인 이벤트는 다시 실행하지 않는다.

```bash
PYTHONPATH=.:libs:apps/api:apps/worker uv run python scripts/operations/run_macmini_enterprise_campaign.py \
  --run-id "$REMEDIATION_RUN_ID" \
  --rerun-failed-and-skipped-from-run-id "$SOURCE_RUN_ID" \
  --kubeconfig /Users/sean1234/foundry-qa/state/kubeconfig \
  --business-probe-config /Users/sean1234/foundry-qa/state/business-probe.json \
  --operator-token-file /Users/sean1234/foundry-qa/state/operator-token \
  --age-recipient-file /Users/sean1234/foundry-qa/state/age-recipient.txt \
  --age-identity-file /Users/sean1234/foundry-qa/state/age-identity.txt \
  --current-commit "$CURRENT_SHA" \
  --rollback-commit "$PREVIOUS_VERIFIED_SHA"
```

remediation summary가 `passed`여도 `full24HourCampaignStatus=notProven`, `p0P1Clear=false`를 유지한다. 이는 건너뛴
장애 복구 증거를 닫는 실행이지, 시간축·지속 부하까지 포함한 새 24시간 campaign을 대신하지 않는다.

sampler receipt에는 동일 `actionRunId`, `idempotentReplay=true`, materialization version/row count, Dataset preview 및
Object query 일치만 남기고 원문 데이터·parameter·token은 제외한다. probe별 availability와 p50·p95·p99를 따로
계산하며 하나의 성공 probe가 다른 실패를 숨길 수 없다. 선언된 fault window는 restart/OOM/replacement baseline을
재설정하지만 그 밖의 증가는 실패다. fault window 안의 메트릭 수집 실패는 허용하되, window 밖 resource metrics는
완전해야 한다.

Acceptance는 다음과 같다.

- 선언된 장애 구간 밖 각 API/OSDK probe availability 99.9% 이상
- business probe 실행 1회 이상, 실패 0, 마지막 Action replay·materialization·Object query receipt 모두 일치
- 선언 밖 OOM/restart/replacement 0; 선언된 worker OOM은 `OOMKilled` 실제 관측과 exact command 복원 필수
- committed 데이터 손실·중복 0
- workload 중단 후 pending outbox와 oldest lag 0을 3회 연속 관측, dead-letter 증가 0
- node memory 85% 미만, disk 80% 미만
- warm-up 이후 지속적 memory/disk 증가 없음
- 모든 fault와 restore 결과가 `passed`, `failed`, `blocked`, `notProven` 중 하나로 분류됨
- 미해결 P0/P1 0

## 10. 증거와 종료

`/Users/sean1234/foundry-qa/evidence/<run-id>`에 Git SHA, image digest, Helm revision, redacted values hash, migration receipt, Kubernetes events, fault timeline, RTO/RPO, audit/outbox와 복구 checksum을 둔다. 최종 JSON/Markdown과 SHA-256 manifest의 암호화 사본을 현재 Mac의 `/Users/isihyeon/Foundry-QA-Evidence/<run-id>`로 복제한다.

GitHub/Anthropic 임시 secret은 제거하고 token을 폐기한다. Funnel 443/8443을 끈 뒤 tailnet 내부 health/readiness를 다시 확인한다. 실제 24시간이 끝나기 전, 또는 P0/P1이 남아 있는 상태에서는 Enterprise QA 완료라고 선언하지 않는다.
