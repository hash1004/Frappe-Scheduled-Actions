# Copyright (c) 2026, Abdul Hannan and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model import get_permitted_fields
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from scheduled_actions.utils import UNSETTABLE_FIELDTYPES, ensure_doctype_allowed


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
		ensure_doctype_allowed(self.reference_doctype)

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
			# A "0"/"0.0" from a Check or number mirror is a real value; only
			# a genuinely empty field_value is rejected. Guards every path
			# into this doctype, including the raw create_scheduled_action
			# API and a mirror-sync that never fired (see scheduled_action.js).
			if self.field_value is None or self.field_value == "":
				frappe.throw(_("A value is required for a Set Field action"))
			meta = frappe.get_meta(self.reference_doctype)
			if not meta.has_field(self.field_name):
				frappe.throw(_("{0} has no field {1}").format(self.reference_doctype, self.field_name))
			df = meta.get_field(self.field_name)
			if df.fieldtype in UNSETTABLE_FIELDTYPES:
				frappe.throw(_("Cannot schedule a value for field type {0}").format(df.fieldtype))

			# Doctype-level write permission (checked in validate_reference) says
			# nothing about field-level (permlevel) restrictions - a field the
			# scheduling user can't otherwise see or write must not be settable
			# here either. Re-checked at execution time too, since this can
			# drift just like doctype-level permission can.
			permitted = get_permitted_fields(self.reference_doctype, permission_type="write")
			if self.field_name not in permitted:
				frappe.throw(
					_("You do not have permission to set {0} on {1}").format(
						self.field_name, self.reference_doctype
					),
					frappe.PermissionError,
				)

	def validate_schedule(self):
		# now_datetime() is system-timezone, not this user's - that's fine
		# to compare against directly: frappe.ui.form.ControlDatetime
		# already converts whatever the user typed/picked in *their* own
		# timezone into system time before it ever reaches self.scheduled_for
		# (and converts back for display), so by the time this runs, both
		# sides of the comparison are already in the same timezone. Same
		# reasoning applies to run_due_actions()'s scheduled_for <=
		# now_datetime() filter.
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
	if fieldtype in ("Float", "Currency", "Percent", "Duration"):
		return float(raw_value)

	return raw_value
