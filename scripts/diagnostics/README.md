# Runtime Diagnostics

이 폴더의 스크립트는 “데모가 실패했을 때 어디를 봐야 하는지”를 자동으로 남기는 장비다.

- `faulthandler`: Python이 멈추거나 크래시가 날 때 스택을 남긴다.
- `tracemalloc`: 어떤 코드가 메모리를 많이 잡는지 남긴다.
- `cProfile`/`pstats`: 어느 함수가 시간을 많이 쓰는지 남긴다.
- `gc`: 가비지 컬렉션 상태를 남긴다.
- `warnings`: 나중에 장애가 될 수 있는 경고를 남긴다.
- OpenTelemetry console exporter: 원하면 실행 span을 사람이 읽을 수 있게 출력한다.

실행:

```bash
uv run python scripts/diagnostics/run_runtime_diagnostics.py
```

콘솔에 span까지 전부 보고 싶을 때:

```bash
uv run python scripts/diagnostics/run_runtime_diagnostics.py --console-traces
```

결과는 `artifacts/diagnostics/`에 저장된다.
