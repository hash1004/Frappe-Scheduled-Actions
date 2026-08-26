import frappe
from frappe import _

from scheduled_actions.utils import get_pending_action_name


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
def get_settable_fields(doctype):
	"""Fields on `doctype` that are reasonable to schedule a value change for -
	skips tables, attachments, and read-only/system fields."""
	meta = frappe.get_meta(doctype)
	skip_fieldtypes = {
		"Table",
		"Table MultiSelect",
		"Attach",
		"Attach Image",
		"Section Break",
		"Column Break",
		"Tab Break",
		"HTML",
		"Button",
		"Image",
		"Heading",
	}
	out = []
	for df in meta.fields:
		if df.fieldtype in skip_fieldtypes or df.read_only:
			continue
		out.append({"fieldname": df.fieldname, "label": df.label or df.fieldname, "fieldtype": df.fieldtype})
	return out
