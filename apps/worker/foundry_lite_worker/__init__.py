"""Worker package composition roots.

The current local foundry still runs most jobs synchronously for deterministic MVP tests.
This package now hosts the Kafka/Redpanda stream archive worker entrypoint, and it is
where Temporal activities/workflows will be wired without changing application service contracts.
"""
