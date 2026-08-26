# Copyright (c) 2026, Abdul Hannan and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


class ScheduledAction(Document):
	def validate(self):
		self.validate_reference()
		self.validate_action()
		self.validate_schedule()

		if not self.scheduled_by:
			self.scheduled_by = frappe.session.user

		# Only the user who scheduled the action (or a System Manager) may
		# edit it later - prevents someone else's action being repointed
		# after the fact.
		if not self.is_new() and self.scheduled_by != frappe.session.user:
			if "System Manager" not in frappe.get_roles():
				frappe.throw(_("Only {0} can modify this Scheduled Action").format(self.scheduled_by))

	def validate_reference(self):
		if not frappe.db.exists(self.reference_doctype, self.reference_name):
			frappe.throw(_("{0} {1} does not exist").format(self.reference_doctype, self.reference_name))

		if self.is_new():
			from scheduled_actions.utils import get_pending_action_name

			existing = get_pending_action_name(self.reference_doctype, self.reference_name)
			if existing:
				frappe.throw(
					_("{0} already has a pending scheduled action ({1}). Cancel it first.").format(
						self.reference_name, existing
					)
				)

		# The scheduling user must actually be able to perform the action
		# they're scheduling - schedule time is not a way to gain access
		# you don't already have.
		perm_type = "submit" if self.action_type in ("Submit", "Cancel") else "write"
		if not frappe.has_permission(self.reference_doctype, perm_type, doc=self.reference_name):
			frappe.throw(
				_("You do not have {0} permission on {1} {2}").format(
					perm_type, self.reference_doctype, self.reference_name
				),
				frappe.PermissionError,
			)

	def validate_action(self):
		if self.action_type == "Set Field":
			if not self.field_name:
				frappe.throw(_("Field Name is required for a Set Field action"))
			meta = frappe.get_meta(self.reference_doctype)
			if not meta.has_field(self.field_name):
				frappe.throw(_("{0} has no field {1}").format(self.reference_doctype, self.field_name))
			df = meta.get_field(self.field_name)
			if df.fieldtype in ("Table", "Table MultiSelect", "Attach", "Attach Image"):
				frappe.throw(_("Cannot schedule a value for field type {0}").format(df.fieldtype))

	def validate_schedule(self):
		if self.is_new() and get_datetime(self.scheduled_for) <= now_datetime():
			frappe.throw(_("Scheduled For must be a future date and time"))


def cast_value(reference_doctype, field_name, raw_value):
	"""Cast a Scheduled Action's stored text value to the target field's fieldtype."""
	meta = frappe.get_meta(reference_doctype)
	df = meta.get_field(field_name)
	fieldtype = df.fieldtype if df else "Data"

	if raw_value in (None, ""):
		return raw_value

	if fieldtype in ("Int", "Check"):
		return int(raw_value)
	if fieldtype in ("Float", "Currency", "Percent"):
		return float(raw_value)

	return raw_value
