import frappe
from frappe import _
from frappe.model import get_permitted_fields

from scheduled_actions.utils import (
	BLOCKED_DOCTYPES,
	UNSETTABLE_FIELDTYPES,
	ensure_doctype_allowed,
	get_pending_action_name,
)


@frappe.whitelist()
def get_pending_action(reference_doctype, reference_name):
	"""Used by the client to decide whether to lock the form and to show
	what's pending, if anything."""
	name = get_pending_action_name(reference_doctype, reference_name)
	if not name:
		return None
	return frappe.db.get_value(
		"Scheduled Action",
		name,
		["name", "action_type", "field_name", "scheduled_for", "scheduled_by"],
		as_dict=True,
	)


@frappe.whitelist()
def create_scheduled_action(
	reference_doctype, reference_name, action_type, scheduled_for, field_name=None, field_value=None
):
	doc = frappe.new_doc("Scheduled Action")
	doc.reference_doctype = reference_doctype
	doc.reference_name = reference_name
	doc.action_type = action_type
	doc.scheduled_for = scheduled_for
	doc.field_name = field_name
	doc.field_value = field_value
	doc.scheduled_by = frappe.session.user
	doc.insert()
	return doc.name


@frappe.whitelist()
def get_blocked_doctypes():
	"""The doctypes a Scheduled Action can never target - used by the client
	to filter the Document Type picker up front. Not a security boundary by
	itself: ScheduledAction.validate_reference() enforces the same list
	server-side regardless of what the client sends."""
	return sorted(BLOCKED_DOCTYPES)


@frappe.whitelist()
def get_settable_fields(doctype):
	"""Fields on `doctype` that are reasonable - and permitted - to schedule
	a value change for: skips layout/table/attachment fields, and is scoped
	to fields the current user has permlevel-write access to (mirrors the
	server-side check in ScheduledAction.validate_action(), so the picker
	never offers a field that would just be rejected on save)."""
	ensure_doctype_allowed(doctype)

	meta = frappe.get_meta(doctype)
	permitted = set(get_permitted_fields(doctype, permission_type="write"))

	out = []
	for df in meta.fields:
		if df.fieldtype in UNSETTABLE_FIELDTYPES or df.read_only:
			continue
		if df.fieldname not in permitted:
			continue
		out.append({
			"fieldname": df.fieldname,
			"label": df.label or df.fieldname,
			"fieldtype": df.fieldtype,
			"options": df.options,
		})
	return out


@frappe.whitelist()
def get_field_current_value(doctype, name, fieldname):
	"""Current value of `fieldname` on the target document, so the "new
	value" control can be prefilled with a sensible starting point instead
	of opening blank. Goes through the same field-list get_settable_fields()
	would return, so this can't be used to read a field the caller isn't
	otherwise permitted to see."""
	ensure_doctype_allowed(doctype)

	permitted = set(get_permitted_fields(doctype, permission_type="write"))
	if fieldname not in permitted:
		frappe.throw(_("You do not have permission to read {0}").format(fieldname), frappe.PermissionError)

	if not frappe.has_permission(doctype, "read", doc=name):
		frappe.throw(_("You do not have permission to read {0} {1}").format(doctype, name), frappe.PermissionError)

	return frappe.db.get_value(doctype, name, fieldname)
