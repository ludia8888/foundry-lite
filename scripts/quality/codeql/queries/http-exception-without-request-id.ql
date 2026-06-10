/**
 * @name Error raised in API handler lacks request_id in detail
 * @description Enforces guideline §8.3 (error response carries request_id) by
 *              finding HTTPException constructions in apps/api/* whose
 *              detail argument does not include 'request_id' as a key or
 *              substring. Operators rely on request_id to correlate the
 *              user-facing error with traces, audits, and run state.
 * @kind problem
 * @problem.severity warning
 * @id foundry-lite/http-exception-without-request-id
 * @tags maintainability
 *       reliability
 */

import python

predicate isHttpException(Call call) {
  call.getFunc().(Name).getId() = "HTTPException"
  or
  call.getFunc().(Attribute).getName() = "HTTPException"
}

string detailText(Call call) {
  // Try to extract a string representation of the detail keyword argument
  exists(Keyword kw | kw = call.getAKeyword() and kw.getArg() = "detail" |
    result = kw.getValue().toString()
  )
}

from Call call, Function f
where
  isHttpException(call) and
  call.getScope+() = f and
  f.getLocation().getFile().getRelativePath().matches("apps/api/%") and
  not detailText(call).matches("%request_id%") and
  not detailText(call).matches("%requestId%")
select call,
  "§8.3 violation: HTTPException in '" + f.getName() +
    "' raised without request_id in detail. Operators cannot correlate this " +
    "error with traces/audit unless the response carries the same request_id."
