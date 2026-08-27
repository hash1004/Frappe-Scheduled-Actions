import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

# How long a *finished* Scheduled Action (Executed/Failed/Cancelled/Skipped)
# is kept before cleanup_old_actions() removes it. Pending/Running rows are
# never touched regardless of age - see that function's own docstring.
RETENTION_DAYS = 90

# An action still "Running" this long after it was claimed had its worker
# killed mid-execution (OOM, restart, deploy) - _reclaim_stuck_running()
# fails it so it doesn't sit there forever. Generous: execute_action() does
# one save/submit; it should never legitimately take minutes.
STUCK_RUNNING_MINUTES = 15


def run_due_actions():
	"""Called every scheduler tick. Only looks up what's due and hands each
	one to a background worker - this tick itself must stay fast, since a
	slow action (a heavy save() with its own hooks, or simply a backlog of
	many due actions) executing inline here would delay every other job
	sharing this scheduler tick."""

	_reclaim_stuck_running()

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

		if _too_late(action):
			_skip(
				action,
				f"Picked up more than {action.skip_if_late_by} min after the scheduled time "
				f"(scheduled for {frappe.utils.format_datetime(action.scheduled_for)}).",
			)
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

		if action.condition and not _condition_met(action, target):
			return  # _condition_met has already marked it Skipped or Failed

		# This action's own row is Running at this point (see _claim()),
		# which counts as locked - it would otherwise trip the pending-action
		# lock on the very save/submit/cancel it's meant to perform.
		target.flags.ignore_scheduled_action_lock = True
		# Tags the resulting Version entry "via Scheduled Action <link>" in
		# the target's timeline (same mechanism Auto Repeat / Data Import
		# use) - see also _annotate_target() for the always-visible note.
		target.flags.updater_reference = {
			"doctype": "Scheduled Action",
			"docname": action.name,
			"label": frappe._("via Scheduled Action"),
		}

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

			# validate() rejects an empty field_value now, but rows created
			# before that check (a mirror-sync that never fired) could still
			# be Pending - fail them loudly rather than silently blanking the
			# target field.
			if action.field_value is None or action.field_value == "":
				_fail(action, f"No value was captured for {action.field_name} - re-create the action")
				return

			value = cast_value(action.reference_doctype, action.field_name, action.field_value)
			target.set(action.field_name, value)
			target.save()

		action.db_set("status", "Executed")
		action.db_set("executed_on", now_datetime())
		action.db_set("error_log", "")
		action.db_set("error_message", "")
		_annotate_target(action, target)
		_notify(action, "succeeded")

	except Exception as e:
		frappe.db.rollback()
		try:
			action = frappe.get_doc("Scheduled Action", name)
			_fail(action, _friendly_error(e), traceback=frappe.get_traceback())
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
	it stops re-queueing but not a job that's already been dequeued.

	`modified` is bumped (not update_modified=False) so it marks *when the
	action went Running* - _reclaim_stuck_running() measures staleness off
	it."""

	status = frappe.db.get_value("Scheduled Action", name, "status", for_update=True)
	if status != "Pending":
		frappe.db.commit()  # release the row lock
		return False

	frappe.db.set_value("Scheduled Action", name, "status", "Running")
	frappe.db.commit()
	return True


def _reclaim_stuck_running():
	"""An action stuck in Running - its worker was killed (OOM, restart,
	deploy) between claiming it and writing a result - would otherwise sit
	there forever: run_due_actions() only looks at Pending, cleanup only
	touches finished states. Fail it so it's visible and retryable. It's
	deliberately not just re-queued: single-attempt execution is the design
	(see README), and re-running a half-done action is risky - the target
	write may have landed before the worker died."""
	cutoff = add_to_date(now_datetime(), minutes=-STUCK_RUNNING_MINUTES)
	stuck = frappe.get_all(
		"Scheduled Action",
		filters={"status": "Running", "modified": ["<", cutoff]},
		pluck="name",
	)
	for name in stuck:
		_fail(
			frappe.get_doc("Scheduled Action", name),
			f"Execution did not complete within {STUCK_RUNNING_MINUTES} minutes - the worker "
			f"was likely interrupted. Use Retry to run it again.",
		)
	if stuck:
		frappe.db.commit()


def _fail(action, message, traceback=None):
	"""`message` is the human-readable "what went wrong" (shown as Message
	on the form, and in the failure notification); `traceback`, when there
	is one, is the full technical log (shown as Error Log). Every controlled
	failure path passes a plain message and no traceback - only the catch-
	all in execute_action() has one."""
	log = message if not traceback else f"{message}\n\n{traceback}"
	action.db_set("status", "Failed")
	action.db_set("executed_on", now_datetime())
	action.db_set("error_message", (message or "").strip()[:1000])
	action.db_set("error_log", (log or "").strip()[:9000])
	_notify(action, "failed")


def _skip(action, reason):
	"""The action's preconditions no longer hold (a false condition, or it's
	too late) - record it as Skipped with the reason, rather than running
	something that isn't wanted any more or firing hours off schedule."""
	action.db_set("status", "Skipped")
	action.db_set("executed_on", now_datetime())
	action.db_set("error_message", (reason or "").strip()[:1000])
	_notify(action, "skipped")


def _too_late(action):
	if not action.skip_if_late_by:
		return False
	deadline = add_to_date(get_datetime(action.scheduled_for), minutes=action.skip_if_late_by)
	return now_datetime() > deadline


def _condition_met(action, target):
	"""Evaluate the action's `condition` against the target document. A false
	condition -> Skipped; a condition that errors -> Failed (it's a broken
	expression, not an intentional skip). Returns True only when the action
	should proceed."""
	from frappe.email.doctype.notification.notification import get_context

	try:
		met = frappe.safe_eval(action.condition, None, get_context(target))
	except Exception as e:
		_fail(action, frappe._("The condition could not be evaluated: {0}").format(_friendly_error(e)))
		return False
	if not met:
		_skip(action, frappe._("Condition not met: {0}").format(action.condition))
		return False
	return True


def _friendly_error(exc):
	"""The readable part of an exception - the message a frappe.throw() put
	up, HTML stripped - for someone who isn't going to read a traceback."""
	msg = frappe.utils.strip_html(str(exc) or "").strip()
	return msg or exc.__class__.__name__


def _annotate_target(action, target):
	"""Leave a note on the target's own timeline so an automated change reads
	differently from a manual one. target.flags.updater_reference already
	tags the Version entry (see execute_action), but only on change-tracked
	doctypes and with nothing to attach to for a bare Submit/Cancel - this
	is the always-visible marker. Best-effort: the action has already run,
	a failure to comment must not undo that."""
	link = frappe.utils.get_link_to_form("Scheduled Action", action.name)
	by = action.scheduled_by or "Administrator"
	if action.action_type == "Submit":
		msg = frappe._("Submitted automatically by scheduled action {0} (scheduled by {1}).").format(link, by)
	elif action.action_type == "Cancel":
		msg = frappe._("Cancelled automatically by scheduled action {0} (scheduled by {1}).").format(link, by)
	else:
		field_label = frappe.get_meta(action.reference_doctype).get_label(action.field_name)
		msg = frappe._("{0} set automatically by scheduled action {1} (scheduled by {2}).").format(
			field_label, link, by
		)
	try:
		target.add_comment("Info", msg)
	except Exception:
		frappe.log_error(title="Scheduled Action: failed to annotate target timeline")


def cleanup_old_actions(retention_days=RETENTION_DAYS):
	"""Deletes Scheduled Action rows that *finished* (Executed, Failed,
	Cancelled, or Skipped) more than `retention_days` ago. Runs daily via
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
			"status": ["in", ("Executed", "Failed", "Cancelled", "Skipped")],
			"modified": ["<", cutoff],
		},
	)
	frappe.db.commit()


def _notify(action, outcome):
	"""outcome: "succeeded" | "failed" | "skipped" (already translatable
	words from the caller's point of view - passed through _())."""
	if not action.scheduled_by:
		return
	subject = frappe._("Scheduled {0} on {1} {2} {3}").format(
		action.action_type,
		action.reference_doctype,
		action.reference_name,
		frappe._(outcome),
	)
	# So the notification itself says *why*, not just "it failed / skipped".
	if outcome != "succeeded" and action.get("error_message"):
		subject = f"{subject}: {action.error_message}"
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": action.scheduled_by,
			"type": "Alert",
			"subject": subject[:140],
			"document_type": "Scheduled Action",
			"document_name": action.name,
		}
	).insert(ignore_permissions=True)
