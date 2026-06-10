/**
 * @name HTTP header value reaches SQL execution without parameter binding
 * @description Enforces guideline §10.2 (no raw SQL interpolation) by tracing
 *              data flow from FastAPI Request.headers / Header dependencies
 *              into SQLAlchemy execute() / DuckDB execute() calls. A header
 *              value reaching execute() as part of a string concatenation or
 *              f-string is a SQL injection root cause.
 * @kind path-problem
 * @problem.severity error
 * @id foundry-lite/header-flows-to-sql
 * @tags security
 *       external/cwe/cwe-089
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.TaintTracking
import semmle.python.ApiGraphs
import HeaderToSqlFlow::PathGraph

module HeaderToSqlConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    exists(API::Node n |
      // FastAPI Request.headers.get(...)
      n = API::moduleImport("fastapi").getMember("Request").getInstance().getMember("headers") and
      source = n.getMember("get").getACall()
      or
      // FastAPI Header() dependency
      n = API::moduleImport("fastapi").getMember("Header") and
      source = n.getACall()
    )
  }

  predicate isSink(DataFlow::Node sink) {
    exists(DataFlow::CallCfgNode call |
      // SQLAlchemy connection.execute(<sql>) or session.execute(<sql>)
      call.getFunction().asExpr().(Attribute).getName() = "execute" and
      sink = call.getArg(0)
    )
  }
}

module HeaderToSqlFlow = TaintTracking::Global<HeaderToSqlConfig>;

from HeaderToSqlFlow::PathNode source, HeaderToSqlFlow::PathNode sink
where HeaderToSqlFlow::flowPath(source, sink)
select sink.getNode(), source, sink,
  "§10.2 violation: HTTP header value flows into SQL execute() at $@.", sink.getNode(),
  "this call"
