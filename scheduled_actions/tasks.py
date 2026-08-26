import frappe
from frappe.utils import add_to_date, now_datetime

# How long a *finished* Scheduled Action (Executed/Failed/Cancelled) is kept
# before cleanup_old_actions() removes it. Pending/Running rows are never
# touched regardless of age - see that function's own docstring for why.
RETENTION_DAYS = 90


def run_due_actions():
	"""Called every minute by the scheduler. Only looks up what's due and
	hands each one to a background worker - this tick itself must stay fast,
	since a slow action (a heavy save() with its own hooks, or simply a
	backlog of many due actions) executing inline here would delay every
	other job sharing this scheduler tick."""

	due = frappe.get_all(
		"Scheduled Action",
		filters={"status": "Pending", "scheduled_for": ["<=", now_datetime()]},
		pluck="name",
	)

	for name in due:
		frappe.enqueue(
			"scheduled_actions.tasks.execute_action",
			queue="short",
			job_id=f"scheduled_action::{name}",
			deduplicate=True,
			name=name,
		)


def execute_action(name):
	if not _claim(name):
		# Already picked up by another worker (or no longer Pending for some
		# other reason) - nothing to do. See _claim()'s docstring for why
		# this is safe against two workers racing on the same action.
		return

	original_user = frappe.session.user
	try:
		action = frappe.get_doc("Scheduled Action", name)

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
		# This action's own row is Running at this point (see _claim()),
		# which counts as locked - it would otherwise trip the pending-action
		# lock on the very save/submit/cancel it's meant to perform.
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
			from frappe.model import get_permitted_fields

			from scheduled_actions.scheduled_actions.doctype.scheduled_action.scheduled_action import cast_value

			# Field-level (permlevel) permission can drift between scheduling
			# and execution too, same as the doctype-level check above.
			permitted = get_permitted_fields(action.reference_doctype, permission_type="write")
			if action.field_name not in permitted:
				_fail(action, f"{action.scheduled_by} no longer has permission to set {action.field_name}")
				return

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


def _claim(name):
	"""Atomically moves a due action from Pending to Running and reports
	whether *this* call was the one that made the move. `for_update` takes a
	row lock, so if two workers reach this at the same moment for the same
	action (an overlapping scheduler tick, or a job re-delivered by the queue
	after a crash) only one of them observes status still "Pending" - the
	other blocks on the lock, then reads back "Running" and returns False.
	This is what actually prevents double-execution; the enqueue-time
	deduplicate=True in run_due_actions() is only a cheap first line, since
	it stops re-queueing but not a job that's already been dequeued."""

	status = frappe.db.get_value("Scheduled Action", name, "status", for_update=True)
	if status != "Pending":
		frappe.db.commit()  # release the row lock
		return False

	frappe.db.set_value("Scheduled Action", name, "status", "Running", update_modified=False)
	frappe.db.commit()
	return True


def _fail(action, message):
	action.db_set("status", "Failed")
	action.db_set("executed_on", now_datetime())
	action.db_set("error_log", message[:9000])
	_notify(action, success=False)


def cleanup_old_actions(retention_days=RETENTION_DAYS):
	"""Deletes Scheduled Action rows that *finished* (Executed, Failed, or
	Cancelled) more than `retention_days` ago. Runs daily via
	scheduler_events.

	Deliberately not using Frappe's own default_log_clearing_doctypes hook
	for this: that clears purely by `creation` age, with no status
	awareness at all - it would delete a still-Pending action scheduled far
	in the future exactly as readily as a long-finished one, since it has
	no idea one hasn't happened yet. That's not just imprecise, it's
	actively dangerous for a doctype whose whole point is "this hasn't run
	yet" rows living for a while.

	Bulk delete (frappe.db.delete), not a per-row frappe.delete_doc() loop
	- this is routine log cleanup, not something that needs versioning,
	comments, or link-checks preserved for every row."""
	cutoff = add_to_date(now_datetime(), days=-retention_days)
	frappe.db.delete(
		"Scheduled Action",
		filters={
			"status": ["in", ("Executed", "Failed", "Cancelled")],
			"modified": ["<", cutoff],
		},
	)
	frappe.db.commit()


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
