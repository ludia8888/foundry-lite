# Palantir Foundry Streaming 교차 분석

**Status:** Reference cross-analysis; current implementation claims remain governed by `docs/implementation-status.md`
**Audience:** Data Connection product owners, streaming engineers, operators
**기준일:** 2026-07-16
**범위:** Data Connection Kafka Streaming Sync, continuous ingest, checkpoint, health, failover

이 문서는 Palantir의 공개 문서를 제품 계약으로 삼아 현재 Foundry-lite 구현을 과장 없이 분류한다. Palantir 내부 구현을 복제했다는 뜻이 아니라, 공개된 운영 의미와 사용자 흐름을 기준으로 호환 가능한 동작을 좁혀 가는 문서다.

## 공식 계약과 현재 상태

| Palantir 공개 계약 | Foundry-lite 현재 상태 | 판정 |
|---|---|---|
| Kafka topic을 realtime Foundry stream으로 읽고, topic partition 수에 맞춰 consumer thread를 늘릴 수 있다. | `partitionMode=all`이 실행 시 topic metadata를 다시 읽고 모든 partition을 독립 cursor로 수집한다. 실제 처리는 아직 한 Sync 안에서 순차적이며 broker group의 병렬 consumer thread assignment는 아니다. | 부분 완료 |
| Stream은 여러 partition을 소비자에게 투명하게 제공하고 병렬 처리한다. | `streamCursors`가 partition별 offset을 보존하고, 두 partition 실제 Kafka 테스트가 재시작·무중복을 증명한다. 병렬 job graph는 없다. | 부분 완료 |
| Checkpoint는 처리 위치와 operator state를 저장하고, 재시작은 마지막 성공 checkpoint에서 복구한다. | Dataset commit이 성공한 partition만 cursor가 전진한다. worker lease가 만료되기 전 standby는 차단되고, 만료 후 마지막 committed cursor에서 takeover한다. | 현재 증거 있음 |
| Checkpoint liveness/duration/failure, total lag/throughput/utilization을 모니터링하고 경보를 낸다. | heartbeat, checkpoint liveness/duration, total lag, output throughput, consecutive failures를 상태 API·SDK·Data Connection UI에서 판정한다. utilization, durable incident scheduler, 이메일/PagerDuty 전달은 없다. | 부분 완료 |
| Source에 여러 Agent를 배정해 고가용성을 만들고 healthy Agent로 ingestion을 dispatch한다. | 동일 Sync의 복수 worker는 lease로 single-active를 보장하고 expiry 후 takeover한다. Agent pair 등록·source assignment·load balancing·maintenance window dispatch는 없다. | 부분 완료 |
| Streaming job graph와 checkpoint가 모든 sink에 도달했는지 Job Details에서 본다. | upstream → Kafka → Dataset → checkpoint 단계와 workflow/run ID는 보이지만 operator graph state와 multi-sink checkpoint barrier는 없다. | 부분 완료 |
| AT_LEAST_ONCE와 EXACTLY_ONCE pipeline mode를 지원하며 latency trade-off가 있다. Streaming source extract는 현재 AT_LEAST_ONCE다. | Kafka Source config는 `AT_LEAST_ONCE`만 허용하고 다른 값을 fail-closed한다. exactly-once pipeline visibility는 구현하지 않았다고 명시한다. | 공개 계약과 정직하게 정렬 |
| 동일 Dataset branch에 두 Sync를 동시에 실행할 수 없다. | 다른 active Streaming Sync가 같은 target Dataset을 소유하면 Start를 `ConflictDetected`로 거절한다. | 현재 증거 있음 |
| Stream은 hot buffer와 cold storage를 함께 제공한다. | append Dataset은 cold/durable archive 역할을 한다. 별도 hot live-view buffer/query surface는 없다. | 미구현 |

## 이번 ratchet의 코드 증거

- `dataset_transactions.metadata.streamCursors`: topic·consumer group·partition·schema strategy별 cursor map.
- `test_kafka_live_broker_all_partitions_keep_independent_checkpoints`: 실제 2-partition Kafka broker, 두 partition 최초 commit, 한 partition만 추가 입력 후 독립 resume, event-id 무중복.
- `test_streaming_standby_worker_takes_over_only_after_active_lease_expires`: single-active lease와 expiry takeover.
- `test_only_one_streaming_sync_can_own_a_dataset_branch`: Dataset branch 동시 실행 차단.
- `test_source_streaming_health.py`: liveness, duration, lag, throughput, failure rule 판정.
- Data Connection Sync detail: partition별 offset/lag와 production monitor 상태를 같은 workflow telemetry에서 표시.

## 아직 Palantir 수준이라고 부를 수 없는 항목

1. Kafka consumer group `subscribe`와 실제 rebalance callback 기반 revoke/assign fencing.
2. partition 수에 맞춘 병렬 consumer thread/worker pool과 capacity utilization 계산.
3. broker commit 결과가 불명확한 `commit-unknown` reconciliation.
4. durable monitoring incident, cooldown/dedupe, 이메일·PagerDuty 같은 notification delivery.
5. hot live view, multi-sink streaming job graph, end-to-end checkpoint barrier.
6. Agent pair를 Source에 배정하는 HA control plane과 non-overlapping maintenance window 운영.
7. exactly-once downstream visibility와 checkpoint interval/latency 설정.

## 공식 출처

- [Palantir Streams](https://www.palantir.com/docs/foundry/data-integration/streams)
- [Palantir Stream monitoring](https://www.palantir.com/docs/foundry/data-integration/stream-monitoring)
- [Palantir Kafka connector](https://www.palantir.com/docs/foundry/available-connectors/kafka)
- [Palantir Data Connection overview](https://www.palantir.com/docs/foundry/data-connection/overview)
- [Palantir Initial setup and high availability](https://www.palantir.com/docs/foundry/data-connection/initial-setup-overview)
- [Palantir Data Connection architecture](https://www.palantir.com/docs/foundry/data-connection/architecture)
- [Palantir Data Connection troubleshooting](https://www.palantir.com/docs/foundry/data-connection/troubleshooting)
