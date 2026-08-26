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


def block_edit_while_scheduled(doc, method=None):
	"""doc_events['*']['validate'] - refuses to save/submit/cancel any document
	that has a Pending Scheduled Action against it, so the action can't be
	invalidated (or produce a surprising result) by an edit that happens
	between scheduling and firing. The scheduler's own executor bypasses this
	via doc.flags.ignore_scheduled_action_lock."""

	if doc.doctype == "Scheduled Action" or doc.is_new():
		return
	if doc.flags.get("ignore_scheduled_action_lock"):
		return

	pending = get_pending_action_name(doc.doctype, doc.name)
	if pending:
		frappe.throw(
			_(
				"This document is locked because {0} is scheduled on it. Cancel the "
				"scheduled action first if you need to make changes."
			).format(frappe.utils.get_link_to_form("Scheduled Action", pending)),
			title=_("Locked by Scheduled Action"),
		)


def get_pending_action_name(reference_doctype, reference_name):
	# "Running" counts as locked too: a background worker has claimed the
	# action and is between the claim and actually writing its result, so
	# the target must stay locked for that window too, not just while
	# formally "Pending" - otherwise a concurrent edit could race the
	# worker's own save().
	return frappe.db.exists(
		"Scheduled Action",
		{
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"status": ["in", ("Pending", "Running")],
		},
	)
