import frappe
from frappe import _


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
	return frappe.db.exists(
		"Scheduled Action",
		{
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"status": "Pending",
		},
	)
