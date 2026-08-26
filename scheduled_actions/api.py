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
def retry_action(name, scheduled_for=None):
	"""Re-queues a Failed action: back to Pending with a fresh
	scheduled_for (now by default, so it's picked up on the very next
	scheduler tick) and the previous error_log cleared. Goes through the
	normal Document.save() lifecycle rather than a raw field update, so
	ScheduledAction.validate() - the ownership lock, the doctype/field
	permission re-checks, all of it - applies exactly as it would to any
	other edit of this document; nothing here bypasses that."""
	doc = frappe.get_doc("Scheduled Action", name)
	if doc.status != "Failed":
		frappe.throw(_("Only a Failed action can be retried"))

	doc.scheduled_for = scheduled_for or frappe.utils.now_datetime()
	doc.status = "Pending"
	doc.executed_on = None
	doc.error_log = ""
	doc.save()
	return doc.name


@frappe.whitelist()
def get_blocked_doctypes():
	"""The doctypes a Scheduled Action can never target - used by the client
	to decide whether to show the "Schedule..." menu/sidebar entry at all on
	a given doctype's own form. Not a security boundary by itself:
	ScheduledAction.validate_reference() enforces the same list server-side
	regardless of what the client sends."""
	return sorted(BLOCKED_DOCTYPES)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def reference_doctype_query(doctype, txt, searchfield, start, page_len, filters):
	"""Custom query for reference_doctype's Link field on the standalone
	Scheduled Action form - excludes Single doctypes and BLOCKED_DOCTYPES so
	a disallowed doctype is never offered in the picker in the first place,
	rather than typed/selected and only then rejected on save. Same standard
	shape frappe.core.doctype.user_permission's query controllers use
	(list-of-lists via as_list); ensure_doctype_allowed() still enforces
	this authoritatively server-side, this is a UX filter on top."""
	return frappe.get_all(
		"DocType",
		filters=[
			["issingle", "=", 0],
			["name", "not in", list(BLOCKED_DOCTYPES)],
			["name", "like", f"%{txt}%"],
		],
		fields=["name"],
		start=start,
		page_length=page_len,
		as_list=True,
	)


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


@frappe.whitelist()
def resolve_dynamic_link_doctype(doctype, name, fieldname):
	"""For a Dynamic Link target field, the doctype it actually links to
	isn't fixed on the field itself - df.options is instead the *fieldname*
	on the same document that holds it (e.g. Scheduled Action's own
	reference_name is a Dynamic Link whose options is "reference_doctype").
	Resolves that so the client can point the Value control's Link options
	at the right doctype. Only needs read access to the controlling field
	(unlike get_field_current_value, this isn't reading the field that's
	about to be scheduled for a write - just consulting a different field
	for context), plus read access to the document itself."""
	ensure_doctype_allowed(doctype)

	meta = frappe.get_meta(doctype)
	df = meta.get_field(fieldname)
	if not df or df.fieldtype != "Dynamic Link":
		frappe.throw(_("{0} is not a Dynamic Link field on {1}").format(fieldname, doctype))

	controlling_fieldname = df.options
	permitted = set(get_permitted_fields(doctype, permission_type="read"))
	if controlling_fieldname not in permitted:
		frappe.throw(
			_("You do not have permission to read {0}").format(controlling_fieldname), frappe.PermissionError
		)

	if not frappe.has_permission(doctype, "read", doc=name):
		frappe.throw(_("You do not have permission to read {0} {1}").format(doctype, name), frappe.PermissionError)

	return frappe.db.get_value(doctype, name, controlling_fieldname)
