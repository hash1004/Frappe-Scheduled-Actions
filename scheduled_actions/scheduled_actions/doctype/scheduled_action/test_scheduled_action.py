# Copyright (c) 2026, Abdul Hannan and contributors
# See license.txt
#
# Covers ScheduledAction.validate() and its three sub-checks
# (validate_reference/validate_action/validate_schedule) plus cast_value()'s
# type coercion. tasks.py's execution engine (claim/execute_action) and
# api.py's whitelisted endpoints have their own test files - this one is
# scoped to "can this Scheduled Action even be saved".

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from scheduled_actions.scheduled_actions.doctype.scheduled_action.scheduled_action import cast_value
from scheduled_actions.tests.fixtures import (
	TEST_DOCTYPE,
	make_test_doc,
	make_test_user,
	near_future_datetime,
)

# validate_action's permlevel check imports get_permitted_fields at module
# scope (from frappe.model import get_permitted_fields), so it must be
# patched at the point of use, not at frappe.model itself.
PERMITTED_FIELDS_PATCH_TARGET = (
	"scheduled_actions.scheduled_actions.doctype.scheduled_action.scheduled_action.get_permitted_fields"
)


class IntegrationTestScheduledAction(IntegrationTestCase):
	def test_blocked_doctype_is_rejected(self):
		doc = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": "User",
			"reference_name": "Administrator",
			"action_type": "Set Field",
			"field_name": "full_name",
			"field_value": "Hacked",
			"scheduled_for": near_future_datetime(),
		})
		with self.assertRaises(frappe.PermissionError):
			doc.insert(ignore_permissions=True)

	def test_single_doctype_is_rejected(self):
		single = frappe.db.get_value("DocType", {"issingle": 1}, "name")
		if not single:
			self.skipTest("no Single doctype available on this site to test against")

		doc = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": single,
			"reference_name": single,
			"action_type": "Submit",
			"scheduled_for": near_future_datetime(),
		})
		with self.assertRaises(frappe.PermissionError):
			doc.insert(ignore_permissions=True)

	def test_nonexistent_document_is_rejected(self):
		doc = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": TEST_DOCTYPE,
			"reference_name": "does-not-exist-at-all",
			"action_type": "Submit",
			"scheduled_for": near_future_datetime(),
		})
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_duplicate_pending_action_is_rejected(self):
		target = make_test_doc()
		first = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": TEST_DOCTYPE,
			"reference_name": target.name,
			"action_type": "Submit",
			"scheduled_for": near_future_datetime(),
		})
		first.insert(ignore_permissions=True)

		second = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": TEST_DOCTYPE,
			"reference_name": target.name,
			"action_type": "Submit",
			"scheduled_for": near_future_datetime(60),
		})
		with self.assertRaises(frappe.ValidationError):
			second.insert(ignore_permissions=True)

	def test_permission_denied_without_write_access(self):
		target = make_test_doc()
		plain_user = make_test_user()  # "All" only - our test doctype grants "All" read, not write

		original_user = frappe.session.user
		frappe.set_user(plain_user)
		try:
			doc = frappe.get_doc({
				"doctype": "Scheduled Action",
				"reference_doctype": TEST_DOCTYPE,
				"reference_name": target.name,
				"action_type": "Set Field",
				"field_name": "category",
				"field_value": "Beta",
				"scheduled_for": near_future_datetime(),
			})
			with self.assertRaises(frappe.PermissionError):
				doc.insert()
		finally:
			frappe.set_user(original_user)

	def test_permission_denied_without_submit_access(self):
		target = make_test_doc()
		plain_user = make_test_user()

		original_user = frappe.session.user
		frappe.set_user(plain_user)
		try:
			doc = frappe.get_doc({
				"doctype": "Scheduled Action",
				"reference_doctype": TEST_DOCTYPE,
				"reference_name": target.name,
				"action_type": "Submit",
				"scheduled_for": near_future_datetime(),
			})
			with self.assertRaises(frappe.PermissionError):
				doc.insert()
		finally:
			frappe.set_user(original_user)

	def test_unknown_field_name_is_rejected(self):
		target = make_test_doc()
		doc = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": TEST_DOCTYPE,
			"reference_name": target.name,
			"action_type": "Set Field",
			"field_name": "this_field_does_not_exist",
			"field_value": "x",
			"scheduled_for": near_future_datetime(),
		})
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_field_name_required_for_set_field(self):
		target = make_test_doc()
		doc = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": TEST_DOCTYPE,
			"reference_name": target.name,
			"action_type": "Set Field",
			"scheduled_for": near_future_datetime(),
		})
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_empty_field_value_rejected_for_set_field(self):
		# A Set Field action with no value would just blank the target field
		# on execution - reject it at creation (this is also the failure mode
		# when a mirror-sync doesn't fire, see scheduled_action.js).
		target = make_test_doc()
		for empty in ("", None):
			doc = frappe.get_doc({
				"doctype": "Scheduled Action",
				"reference_doctype": TEST_DOCTYPE,
				"reference_name": target.name,
				"action_type": "Set Field",
				"field_name": "category",
				"field_value": empty,
				"scheduled_for": near_future_datetime(),
			})
			with self.assertRaises(frappe.ValidationError, msg=f"field_value={empty!r}"):
				doc.insert(ignore_permissions=True)

	def test_falsy_but_real_field_value_allowed_for_set_field(self):
		# "0" from a Check/number mirror is a real value, not "empty".
		target = make_test_doc()
		doc = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": TEST_DOCTYPE,
			"reference_name": target.name,
			"action_type": "Set Field",
			"field_name": "is_flagged",
			"field_value": "0",
			"scheduled_for": near_future_datetime(),
		})
		doc.insert(ignore_permissions=True)  # must not raise
		self.assertEqual(doc.field_value, "0")

	def test_hidden_or_read_only_field_rejected_for_set_field(self):
		# You can't see/verify a hidden field or edit a read-only one on the
		# form, so scheduling a change to it makes no sense.
		target = make_test_doc()
		for fieldname in ("internal_flag", "computed_score"):
			doc = frappe.get_doc({
				"doctype": "Scheduled Action",
				"reference_doctype": TEST_DOCTYPE,
				"reference_name": target.name,
				"action_type": "Set Field",
				"field_name": fieldname,
				"field_value": "1",
				"scheduled_for": near_future_datetime(),
			})
			with self.assertRaises(frappe.ValidationError, msg=fieldname):
				doc.insert(ignore_permissions=True)

	def test_permlevel_write_check_blocks_field_not_in_permitted_list(self):
		# Doctype-level write already passes (Administrator/System Manager),
		# so this isolates the field-level (permlevel) check on its own -
		# mocked rather than set up via a real Role Permission Manager entry,
		# since get_permitted_fields' own correctness is Frappe core's to
		# test; ours is just "do we actually call it and honor the result".
		target = make_test_doc()
		with patch(PERMITTED_FIELDS_PATCH_TARGET, return_value=["title"]):
			doc = frappe.get_doc({
				"doctype": "Scheduled Action",
				"reference_doctype": TEST_DOCTYPE,
				"reference_name": target.name,
				"action_type": "Set Field",
				"field_name": "amount",  # not in the mocked permitted list
				"field_value": "5",
				"scheduled_for": near_future_datetime(),
			})
			with self.assertRaises(frappe.PermissionError):
				doc.insert(ignore_permissions=True)

	def test_permlevel_write_check_allows_field_in_permitted_list(self):
		target = make_test_doc()
		with patch(PERMITTED_FIELDS_PATCH_TARGET, return_value=["title", "amount"]):
			doc = frappe.get_doc({
				"doctype": "Scheduled Action",
				"reference_doctype": TEST_DOCTYPE,
				"reference_name": target.name,
				"action_type": "Set Field",
				"field_name": "amount",
				"field_value": "5",
				"scheduled_for": near_future_datetime(),
			})
			doc.insert(ignore_permissions=True)  # must not raise
			self.assertEqual(doc.status, "Pending")

	def test_past_schedule_is_rejected_on_create(self):
		target = make_test_doc()
		doc = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": TEST_DOCTYPE,
			"reference_name": target.name,
			"action_type": "Submit",
			"scheduled_for": frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=-30),
		})
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_only_scheduler_or_system_manager_can_edit(self):
		# Scheduled Action's own doctype permissions already restrict "All"
		# role writes to if_owner - a non-owner with only that role gets
		# rejected at the framework permission layer before validate() ever
		# runs, which is a real but *different* protection from the one
		# this test is after. This check exists for a role that has write
		# without if_owner and isn't System Manager - not configured by
		# default, but something a site's Role Permission Manager could
		# introduce - so it's exercised here with ignore_permissions=True to
		# isolate it from the (already-effective) permission layer, same as
		# how validate() itself always runs regardless of that flag.
		target = make_test_doc()
		scheduler = make_test_user(roles=("Scheduled Actions Test Writer",), key="scheduler")
		other_user = make_test_user(roles=("Scheduled Actions Test Writer",), key="other")

		original_user = frappe.session.user
		frappe.set_user(scheduler)
		try:
			doc = frappe.get_doc({
				"doctype": "Scheduled Action",
				"reference_doctype": TEST_DOCTYPE,
				"reference_name": target.name,
				"action_type": "Submit",
				"scheduled_for": near_future_datetime(),
			})
			doc.insert()
			self.assertEqual(doc.scheduled_by, scheduler)
		finally:
			frappe.set_user(original_user)

		frappe.set_user(other_user)
		try:
			doc.reload()
			doc.scheduled_for = near_future_datetime(120)
			with self.assertRaises(frappe.ValidationError):
				doc.save(ignore_permissions=True)
		finally:
			frappe.set_user(original_user)

	def test_a_cancelled_action_cannot_be_edited(self):
		target = make_test_doc()
		doc = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": TEST_DOCTYPE,
			"reference_name": target.name,
			"action_type": "Submit",
			"scheduled_for": near_future_datetime(),
		})
		doc.insert(ignore_permissions=True)
		frappe.db.set_value("Scheduled Action", doc.name, "status", "Cancelled")  # the manual-cancel path
		frappe.db.commit()

		doc.reload()
		doc.scheduled_for = near_future_datetime(300)
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_cast_value_int_and_check(self):
		self.assertEqual(cast_value(TEST_DOCTYPE, "is_flagged", "1"), 1)
		self.assertIsInstance(cast_value(TEST_DOCTYPE, "is_flagged", "1"), int)

	def test_cast_value_numeric_fieldtypes(self):
		self.assertEqual(cast_value(TEST_DOCTYPE, "amount", "3.5"), 3.5)
		self.assertIsInstance(cast_value(TEST_DOCTYPE, "amount", "3.5"), float)

	def test_cast_value_default_passthrough_for_text_like_fields(self):
		self.assertEqual(cast_value(TEST_DOCTYPE, "title", "hello"), "hello")
		self.assertEqual(cast_value(TEST_DOCTYPE, "category", "Beta"), "Beta")

	def test_cast_value_empty_value_passthrough(self):
		self.assertEqual(cast_value(TEST_DOCTYPE, "amount", ""), "")
		self.assertIsNone(cast_value(TEST_DOCTYPE, "amount", None))

	def test_scheduled_for_keeps_frappes_automatic_timezone_conversion(self):
		# frappe.ui.form.ControlDatetime converts a Datetime field's value
		# to/from the viewing user's own timezone automatically - and shows
		# it in the field's description - for every Datetime field, unless
		# hide_timezone is set on it (a client-side-only df key, not a real
		# DocField schema property, hence the dict-style .get() rather than
		# attribute access). scheduled_for (and the field_value_datetime
		# mirror) rely entirely on that framework behavior rather than any
		# custom logic of ours; this just guards against a future edit
		# accidentally opting either of them out of it.
		meta = frappe.get_meta("Scheduled Action")
		for fieldname in ("scheduled_for", "field_value_datetime"):
			df = meta.get_field(fieldname)
			self.assertEqual(df.fieldtype, "Datetime")
			self.assertFalse(df.get("hide_timezone"))
