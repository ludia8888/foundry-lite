/**
 * @name Untrusted JSON body reaches dict[...] without Pydantic validation
 * @description Enforces guideline §5.2 (no dict[str, Any] passthrough) and
 *              §6.3 (API request schema clarity) by detecting paths where a
 *              raw request.json() value flows into a service call argument
 *              without passing through a Pydantic BaseModel boundary.
 * @kind path-problem
 * @problem.severity warning
 * @id foundry-lite/raw-json-flows-to-service
 * @tags maintainability
 *       reliability
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.TaintTracking
import semmle.python.ApiGraphs
import RawJsonToServiceFlow::PathGraph

module RawJsonToServiceConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    exists(API::Node n |
      n = API::moduleImport("fastapi").getMember("Request").getAnInstance().getMember("json") and
      source = n.getACall()
    )
  }

  predicate isSink(DataFlow::Node sink) {
    // Any application-layer call argument, except the argument to a Pydantic validation call.
    //
    // The exclusion belongs here and not in `isBarrier`. A barrier stops flow *at a node*, but
    // the sink is the argument, and taint reaches an argument before it reaches the call that
    // consumes it. Marking `model_validate` as a barrier therefore never prevented the finding:
    // the very call that satisfies the rule was reported as violating it, which is why adding an
    // envelope model to a router raised its count instead of clearing it.
    exists(DataFlow::CallCfgNode call |
      call.getLocation().getFile().getRelativePath().matches("apps/api/%") and
      exists(int i | sink = call.getArg(i)) and
      not isPydanticValidationCall(call)
    )
  }

  predicate isBarrier(DataFlow::Node node) {
    // Pydantic validation sanitises the value.
    //
    // `getASubclass*()` and not `BaseModel` alone: nobody calls
    // `pydantic.BaseModel.model_validate` directly, because a bare BaseModel declares no fields
    // and validates nothing. Validation is always `MyModel.model_validate(payload)`. Matching
    // only the base class made the barrier unreachable, so the rule flagged the very call that
    // satisfies it -- a router that added a model went from one finding to two.
    exists(API::Node model |
      model = API::moduleImport("pydantic").getMember("BaseModel").getASubclass*() and
      (
        node = model.getMember("model_validate").getACall()
        or
        node = model.getMember("parse_obj").getACall()
        or
        node = model.getMember("model_validate_json").getACall()
      )
    )
  }
}

/**
 * A Pydantic validation call. Matched by member name on any receiver rather than through the API
 * graph from `pydantic.BaseModel`: validation is always `MyModel.model_validate(...)` on a
 * subclass declared elsewhere, and `model_validate` / `parse_obj` / `model_validate_json` are not
 * names that appear outside Pydantic.
 */
predicate isPydanticValidationCall(DataFlow::CallCfgNode call) {
  exists(string name |
    name = call.getFunction().asCfgNode().(AttrNode).getName() and
    name in ["model_validate", "parse_obj", "model_validate_json"]
  )
}

module RawJsonToServiceFlow = TaintTracking::Global<RawJsonToServiceConfig>;

from RawJsonToServiceFlow::PathNode source, RawJsonToServiceFlow::PathNode sink
where RawJsonToServiceFlow::flowPath(source, sink)
select sink.getNode(), source, sink,
  "§5.2/§6.3 violation: raw request.json() value flows into service call $@ without Pydantic validation.",
  sink.getNode(), "this argument"
