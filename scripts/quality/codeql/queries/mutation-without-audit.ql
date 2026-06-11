/**
 * @name Mutation in repository commits without an audit event
 * @description Enforces guideline §10.2 (no mutation without audit) by
 *              identifying SQLAlchemy insert/update/delete calls inside an
 *              application service that lack a sibling call to runtime_service
 *              ._audit (or audit_repository.insert_audit_event) in the same
 *              method body.
 * @kind problem
 * @problem.severity warning
 * @id foundry-lite/mutation-without-audit
 * @tags security
 *       maintainability
 */

import python
import semmle.python.ApiGraphs

predicate isMutationCall(Call call) {
  exists(string name |
    name = call.getFunc().(Attribute).getName() and
    (
      name = "insert"
      or name = "update"
      or name = "delete"
      or name = "merge"
    )
  )
}

predicate isAuditCall(Call call) {
  exists(string name |
    name = call.getFunc().(Attribute).getName() and
    (
      name = "_audit"
      or name = "_outbox"
      or name = "insert_audit_event"
      or name = "insert_outbox_event"
    )
  )
}

predicate enclosingFunction(Call call, Function f) { call.getScope+() = f }

from Function f, Call mutation
where
  isMutationCall(mutation) and
  enclosingFunction(mutation, f) and
  // The service file is in libs/foundry_lite/application/services
  f.getLocation().getFile().getRelativePath().matches("libs/foundry_lite/application/services/%") and
  not exists(Call audit |
    isAuditCall(audit) and
    enclosingFunction(audit, f)
  ) and
  // Exclude private helpers prefixed with double underscore.
  not f.getName().matches("__%")
select mutation,
  "§10.2 violation: mutation in service method '" + f.getName() +
    "' has no audit/outbox sibling call. Add self.runtime_service._audit(...) " +
    "or insert_audit_event(...) in the same transaction."
