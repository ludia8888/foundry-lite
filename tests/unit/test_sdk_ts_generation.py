from __future__ import annotations

from pathlib import Path

from scripts import generate_sdk_ts as sdk


def _type_block(source: str, type_name: str) -> str:
    prefix = f"export type {type_name} = {{"
    start = source.index(prefix)
    end = source.index("};", start)
    return source[start : end + 2]


def test_sdk_generator_emits_typed_order_and_action_contract() -> None:
    ontology = sdk.load_ontology(sdk.DEFAULT_ONTOLOGY)
    generated = sdk.render_typescript(ontology)

    approve_params = _type_block(generated, "ApproveOrderParams")
    assert 'export type Order = FoundryLiteObject<"Order", OrderProperties>;' in generated
    assert "get(id: string, options?: { explain?: boolean }): Promise<Order>;" in generated
    assert "query(payload?: ObjectQueryRequest): Promise<ObjectQueryResult<Order>>;" in generated
    assert "apply(payload: ApproveOrderApplyRequest): Promise<ActionApplyResponse>;" in generated
    assert "reason: string;" in approve_params
    assert "any" not in approve_params
    assert "expectedObjectVersion(object: { objectVersion: number }): number" in generated
    assert "idempotencyKey(actionName: string, objectId: string): string" in generated


def test_sdk_generator_check_detects_api_name_drift(tmp_path: Path) -> None:
    ontology_path = tmp_path / "ontology.yaml"
    ts_output = tmp_path / "generated.ts"
    web_output = tmp_path / "generated-sdk.js"
    ontology_text = sdk.DEFAULT_ONTOLOGY.read_text(encoding="utf-8")

    ontology_path.write_text(ontology_text, encoding="utf-8")
    assert (
        sdk.write_or_check_outputs(
            ontology_path=ontology_path,
            ts_output=ts_output,
            web_output=web_output,
            should_check=False,
        )
        == 0
    )

    ontology_path.write_text(ontology_text.replace("apiName: Order", "apiName: PurchaseOrder", 1), encoding="utf-8")
    assert (
        sdk.write_or_check_outputs(
            ontology_path=ontology_path,
            ts_output=ts_output,
            web_output=web_output,
            should_check=True,
        )
        == 1
    )


def test_generated_sdk_files_match_active_ontology() -> None:
    assert (
        sdk.write_or_check_outputs(
            ontology_path=sdk.DEFAULT_ONTOLOGY,
            ts_output=sdk.DEFAULT_TS_OUTPUT,
            web_output=sdk.DEFAULT_WEB_OUTPUT,
            should_check=True,
        )
        == 0
    )
