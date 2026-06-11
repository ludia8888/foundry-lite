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
    // The 'core' or any service in the application layer being called
    exists(DataFlow::CallCfgNode call |
      call.getLocation().getFile().getRelativePath().matches("apps/api/%") and
      exists(int i | sink = call.getArg(i))
    )
  }

  predicate isBarrier(DataFlow::Node node) {
    // Pydantic BaseModel.model_validate / parse_obj sanitises the value
    exists(API::Node bm |
      bm = API::moduleImport("pydantic").getMember("BaseModel") and
      (
        node = bm.getMember("model_validate").getACall()
        or
        node = bm.getMember("parse_obj").getACall()
        or
        node = bm.getMember("model_validate_json").getACall()
      )
    )
  }
}

module RawJsonToServiceFlow = TaintTracking::Global<RawJsonToServiceConfig>;

from RawJsonToServiceFlow::PathNode source, RawJsonToServiceFlow::PathNode sink
where RawJsonToServiceFlow::flowPath(source, sink)
select sink.getNode(), source, sink,
  "§5.2/§6.3 violation: raw request.json() value flows into service call $@ without Pydantic validation.",
  sink.getNode(), "this argument"
