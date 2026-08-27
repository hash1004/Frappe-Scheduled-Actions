import frappe
from frappe import _
from frappe.model import NO_VALUE_FIELDS, core_doctypes_list

# Fieldtypes a "Set Field" action can never target - layout-only fieldtypes
# (Section Break, Table, ...) carry no scalar value at all, and attachments
# need an actual upload, not a scheduled string. Shared by the picker
# (api.get_settable_fields) and the server-side check
# (ScheduledAction.validate_action()) so they can't drift apart.
UNSETTABLE_FIELDTYPES = NO_VALUE_FIELDS | {"Attach", "Attach Image"}

# Doctypes a Scheduled Action must never be allowed to target. Frappe already
# maintains core_doctypes_list for exactly this purpose - Bulk Update and
# Data Import both block the same list to keep generic "pick any field, set
# any value" tools off the doctypes that define permissions/schema/code
# (User, Role, DocType, DocPerm, Custom Field, Client Script, ...). We reuse
# it rather than keep a second list in sync, plus a short addendum for
# doctypes not in that list but equally dangerous to schedule an unattended
# change against.
BLOCKED_DOCTYPES = frozenset(core_doctypes_list) | {
	"Server Script",
	"System Settings",
	"Role Profile",
	"OAuth Client",
	"Webhook",
}


def ensure_doctype_allowed(doctype):
	"""Raises if `doctype` is not a valid target for a Scheduled Action."""
	if doctype in BLOCKED_DOCTYPES:
		frappe.throw(
			_("Scheduling actions on {0} is not allowed").format(doctype), frappe.PermissionError
		)
	if frappe.get_meta(doctype).issingle:
		frappe.throw(
			_("Scheduling actions on single doctypes is not supported"), frappe.PermissionError
		)


def cancel_pending_action_on_change(doc, method=None):
	"""doc_events['*'] for on_update / on_cancel: when a document that has a
	Pending Scheduled Action against it changes, cancel that action rather
	than blocking the change, and tell whoever made it. A Scheduled Action
	captures an intent at a moment in time; once the document has moved on,
	firing it unattended is more surprising than quietly dropping it. The
	scheduler's own executor sets doc.flags.ignore_scheduled_action_lock
	while it performs the action, so the write it makes doesn't cancel the
	very action it's carrying out."""

	if doc.doctype == "Scheduled Action" or doc.is_new():
		return
	if doc.flags.get("ignore_scheduled_action_lock"):
		return

	# The indexed exists() check is the cheap filter - the overwhelming
	# majority of saves site-wide have no scheduled action and stop here,
	# before the more expensive change comparison below.
	pending = get_pending_action_name(doc.doctype, doc.name)
	if not pending:
		return

	# on_update also fires for a save that changed nothing (a form re-saved
	# untouched, a programmatic doc.save() from another app's hook). Only a
	# real change should cost someone their scheduled action; a manual
	# submit/cancel (on_cancel, or on_update with docstatus moved) always
	# counts.
	if method == "on_update" and not _document_meaningfully_changed(doc):
		return

	status, action_type, scheduled_by = frappe.db.get_value(
		"Scheduled Action", pending, ["status", "action_type", "scheduled_by"]
	)
	# "Running" means a worker has already claimed it and is mid-execution -
	# let that finish (its own write is exempted via the bypass flag); don't
	# race it to a Cancelled the executor would then overwrite.
	if status != "Pending":
		return

	reason = _("{0} {1} was changed on {2}").format(
		doc.doctype, doc.name, frappe.utils.format_datetime(frappe.utils.now_datetime())
	)
	frappe.db.set_value(
		"Scheduled Action",
		pending,
		{"status": "Cancelled", "error_log": _("Cancelled automatically: {0}.").format(reason)},
	)

	doc.add_comment(
		"Info",
		_("Scheduled action {0} ({1}) was cancelled because this document was changed.").format(
			pending, action_type
		),
	)
	frappe.msgprint(
		_("{0} was cancelled because you changed this document. Schedule it again if you still need it.").format(
			frappe.utils.get_link_to_form("Scheduled Action", pending)
		),
		title=_("Scheduled action cancelled"),
		indicator="orange",
	)

	# Whoever scheduled the action may not be whoever just changed the
	# document - let them know it won't run.
	if scheduled_by and scheduled_by != frappe.session.user:
		frappe.get_doc({
			"doctype": "Notification Log",
			"for_user": scheduled_by,
			"type": "Alert",
			"subject": _("Scheduled {0} on {1} {2} was cancelled (document changed)").format(
				action_type, doc.doctype, doc.name
			),
			"document_type": "Scheduled Action",
			"document_name": pending,
		}).insert(ignore_permissions=True)


def clear_actions_on_target_delete(doc, method=None):
	"""doc_events['*']['on_trash'] - a Scheduled Action points at its target
	through a Dynamic Link (reference_name), which otherwise blocks the
	target from ever being deleted ("Cannot delete ... is linked with
	Scheduled Action ..."). on_trash runs before Frappe's link check, so
	dropping the rows here clears the way. The actions are meaningless once
	their target is gone anyway - a Pending one would only fail, a finished
	one is history the daily cleanup drops regardless."""
	if doc.doctype == "Scheduled Action":
		return
	frappe.db.delete(
		"Scheduled Action",
		{"reference_doctype": doc.doctype, "reference_name": doc.name},
	)


def _document_meaningfully_changed(doc):
	"""True if `doc` differs from its pre-save state in a way worth acting on
	- any stored field or child-table row changed, or its docstatus moved (a
	manual submit/cancel of a doc that had an action scheduled counts). Uses
	the same diff Frappe itself uses to decide whether to record a Version,
	so "meaningful" here matches "meaningful" everywhere else."""
	before = doc.get_doc_before_save()
	if not before:
		return True
	if doc.docstatus != before.docstatus:
		return True

	from frappe.core.doctype.version.version import get_diff

	diff = get_diff(before, doc) or {}
	return bool(diff.get("changed") or diff.get("added") or diff.get("removed") or diff.get("row_changed"))


def get_pending_action_name(reference_doctype, reference_name):
	"""The Pending (or in-flight Running) Scheduled Action against a document,
	if any - the "already has one" check on the scheduling side (one at a
	time per document), what the form sidebar shows, and the trigger for
	cancel_pending_action_on_change. "Running" is included so a concurrent
	edit during the claim -> result window is still seen; the auto-cancel
	path itself re-checks and leaves a Running action alone."""
	return frappe.db.exists(
		"Scheduled Action",
		{
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"status": ["in", ("Pending", "Running")],
		},
	)
