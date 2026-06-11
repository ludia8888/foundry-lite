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

predicate keyHasRequestId(Expr key) {
  key.toString().matches("%request_id%")
  or
  key.toString().matches("%requestId%")
  or
  exists(StringLiteral text |
    key = text and
    (
      text.getText() = "request_id"
      or text.getText() = "requestId"
      or text.getS() = "request_id"
      or text.getS() = "requestId"
    )
  )
}

predicate detailHasRequestId(Call call) {
  exists(Keyword kw, Dict detail, Expr key |
    kw = call.getAKeyword() and
    kw.getArg() = "detail" and
    detail = kw.getValue() and
    key = detail.getAKey() and
    keyHasRequestId(key)
  )
  or
  detailText(call).matches("%request_id%")
  or
  detailText(call).matches("%requestId%")
}

from Call call, Function f
where
  isHttpException(call) and
  call.getScope+() = f and
  f.getLocation().getFile().getRelativePath().matches("apps/api/%") and
  not detailHasRequestId(call)
select call,
  "§8.3 violation: HTTPException in '" + f.getName() +
    "' raised without request_id in detail. Operators cannot correlate this " +
    "error with traces/audit unless the response carries the same request_id."
