"""Vulture allowlist for intentional contract-only API surface.

These names are Protocol method parameters on media ports whose first concrete
implementation lands in a later PR (content index = search projection, media policy
= security propagation). They are deliberate contract anchors, not dead code, so
vulture treats them as used. Each entry is removed when its implementation lands.

This file is intentionally excluded from ruff/mypy/pyright (it is not real code, it
is a vulture whitelist of bare names). See scripts/ci_gate.sh "Static: Vulture".
"""

source_version_id  # unused variable (libs/foundry_lite/application/ports/content_index.py:68)
expected_active  # unused variable (libs/foundry_lite/application/ports/content_index.py:76)
shadow  # unused variable (libs/foundry_lite/application/ports/content_index.py:76)
envelope  # unused variable (libs/foundry_lite/application/ports/media_policy.py:29)
principal_id  # unused variable (libs/foundry_lite/application/ports/media_policy.py:29)
