import frappe
from frappe.utils import now_datetime


def run_due_actions():
	"""Called every minute by the scheduler. Executes every Scheduled Action
	whose time has come, each isolated in its own try/except and its own
	permission context (the user who scheduled it) so one bad action or one
	user's revoked access can never affect another's."""

	due = frappe.get_all(
		"Scheduled Action",
		filters={"status": "Pending", "scheduled_for": ["<=", now_datetime()]},
		pluck="name",
	)

	for name in due:
		execute_action(name)


def execute_action(name):
	original_user = frappe.session.user
	try:
		action = frappe.get_doc("Scheduled Action", name)

		# Re-check everything at execution time - state may have moved
		# since this was scheduled (doc deleted, already submitted,
		# permission revoked, etc).
		if action.status != "Pending":
			return

		if not frappe.db.exists(action.reference_doctype, action.reference_name):
			_fail(action, f"{action.reference_doctype} {action.reference_name} no longer exists")
			return

		# Run as the user who scheduled it, never as Administrator, so
		# execution can't do anything that user couldn't do themselves
		# right now.
		frappe.set_user(action.scheduled_by or "Administrator")

		perm_type = "submit" if action.action_type in ("Submit", "Cancel") else "write"
		if not frappe.has_permission(action.reference_doctype, perm_type, doc=action.reference_name):
			_fail(action, f"{action.scheduled_by} no longer has {perm_type} permission")
			return

		target = frappe.get_doc(action.reference_doctype, action.reference_name)
		# This action's own row is still Pending at this point (only flips to
		# Executed below), which would otherwise trip the pending-action lock
		# on the very save/submit/cancel it's meant to perform.
		target.flags.ignore_scheduled_action_lock = True

		if action.action_type == "Submit":
			if target.docstatus != 0:
				_fail(action, "Document is not in Draft state, cannot submit")
				return
			target.submit()

		elif action.action_type == "Cancel":
			if target.docstatus != 1:
				_fail(action, "Document is not Submitted, cannot cancel")
				return
			target.cancel()

		elif action.action_type == "Set Field":
			from scheduled_actions.scheduled_actions.doctype.scheduled_action.scheduled_action import cast_value

			value = cast_value(action.reference_doctype, action.field_name, action.field_value)
			target.set(action.field_name, value)
			target.save()

		action.db_set("status", "Executed")
		action.db_set("executed_on", now_datetime())
		action.db_set("error_log", "")
		_notify(action, success=True)

	except Exception:
		frappe.db.rollback()
		try:
			action = frappe.get_doc("Scheduled Action", name)
			_fail(action, frappe.get_traceback())
		except Exception:
			frappe.log_error(title="Scheduled Action execution failed", message=frappe.get_traceback())
	finally:
		frappe.set_user(original_user)
		frappe.db.commit()


def _fail(action, message):
	action.db_set("status", "Failed")
	action.db_set("executed_on", now_datetime())
	action.db_set("error_log", message[:9000])
	_notify(action, success=False)


def _notify(action, success):
	if not action.scheduled_by:
		return
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": action.scheduled_by,
			"type": "Alert",
			"subject": frappe._(
				"Scheduled {0} on {1} {2} {3}"
			).format(
				action.action_type,
				action.reference_doctype,
				action.reference_name,
				"succeeded" if success else "failed",
			),
			"document_type": "Scheduled Action",
			"document_name": action.name,
		}
	).insert(ignore_permissions=True)
