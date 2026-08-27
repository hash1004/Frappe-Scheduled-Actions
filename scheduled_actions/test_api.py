# Copyright (c) 2026, Abdul Hannan and contributors
# See license.txt
#
# Covers api.py's whitelisted endpoints - the field picker's data source
# (get_settable_fields/get_field_current_value), the Document Type picker's
# filter (reference_doctype_query), get_blocked_doctypes, and
# create_scheduled_action's happy path (its validation is already covered
# thoroughly by test_scheduled_action.py, since it's the same Document
# lifecycle under the hood).

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from scheduled_actions.api import (
	create_scheduled_action,
	get_blocked_doctypes,
	get_field_current_value,
	get_pending_action,
	get_settable_fields,
	reference_doctype_query,
	resolve_dynamic_link_doctype,
	retry_action,
)
from scheduled_actions.tasks import execute_action
from scheduled_actions.tests.fixtures import TEST_DOCTYPE, due_datetime, make_test_doc, near_future_datetime
from scheduled_actions.utils import BLOCKED_DOCTYPES

GET_PERMITTED_FIELDS_TARGET = "scheduled_actions.api.get_permitted_fields"


class IntegrationTestScheduledActionsApi(IntegrationTestCase):
	def test_get_settable_fields_respects_permlevel(self):
		with patch(GET_PERMITTED_FIELDS_TARGET, return_value=["title", "amount"]):
			fields = get_settable_fields(TEST_DOCTYPE)
		fieldnames = {f["fieldname"] for f in fields}
		self.assertIn("title", fieldnames)
		self.assertIn("amount", fieldnames)
		self.assertNotIn("category", fieldnames)  # excluded by the mocked permitted list

	def test_get_settable_fields_carries_fieldtype_and_options(self):
		with patch(GET_PERMITTED_FIELDS_TARGET, return_value=["title", "category", "amount", "is_flagged"]):
			fields = get_settable_fields(TEST_DOCTYPE)
		by_name = {f["fieldname"]: f for f in fields}
		self.assertEqual(by_name["category"]["fieldtype"], "Select")
		self.assertEqual(by_name["category"]["options"], "Alpha\nBeta\nGamma")
		self.assertEqual(by_name["is_flagged"]["fieldtype"], "Check")

	def test_get_settable_fields_blocked_doctype_throws(self):
		with self.assertRaises(frappe.PermissionError):
			get_settable_fields("User")

	def test_get_field_current_value_returns_value(self):
		target = make_test_doc(category="Gamma")
		with patch(GET_PERMITTED_FIELDS_TARGET, return_value=["category"]):
			value = get_field_current_value(TEST_DOCTYPE, target.name, "category")
		self.assertEqual(value, "Gamma")

	def test_get_field_current_value_throws_for_field_outside_permitted_list(self):
		target = make_test_doc()
		with patch(GET_PERMITTED_FIELDS_TARGET, return_value=["title"]):
			with self.assertRaises(frappe.PermissionError):
				get_field_current_value(TEST_DOCTYPE, target.name, "amount")

	def test_get_field_current_value_throws_without_document_read_permission(self):
		target = make_test_doc()
		with patch(GET_PERMITTED_FIELDS_TARGET, return_value=["category"]), patch(
			"scheduled_actions.api.frappe.has_permission", return_value=False
		):
			with self.assertRaises(frappe.PermissionError):
				get_field_current_value(TEST_DOCTYPE, target.name, "category")

	def test_get_settable_fields_reports_dynamic_link_fieldtype(self):
		with patch(GET_PERMITTED_FIELDS_TARGET, return_value=["dynamic_ref"]):
			fields = get_settable_fields(TEST_DOCTYPE)
		by_name = {f["fieldname"]: f for f in fields}
		self.assertEqual(by_name["dynamic_ref"]["fieldtype"], "Dynamic Link")
		# options is the *fieldname* holding the target doctype, not a
		# doctype itself - resolve_dynamic_link_doctype is what turns that
		# into an actual doctype name.
		self.assertEqual(by_name["dynamic_ref"]["options"], "linked_doctype")

	def test_resolve_dynamic_link_doctype_returns_the_controlling_fields_value(self):
		target = make_test_doc(linked_doctype="DocType", dynamic_ref="DocType")
		with patch(GET_PERMITTED_FIELDS_TARGET, return_value=["linked_doctype"]):
			resolved = resolve_dynamic_link_doctype(TEST_DOCTYPE, target.name, "dynamic_ref")
		self.assertEqual(resolved, "DocType")

	def test_resolve_dynamic_link_doctype_rejects_non_dynamic_link_field(self):
		target = make_test_doc()
		with self.assertRaises(frappe.ValidationError):
			resolve_dynamic_link_doctype(TEST_DOCTYPE, target.name, "category")

	def test_resolve_dynamic_link_doctype_requires_read_on_controlling_field(self):
		target = make_test_doc(linked_doctype="DocType")
		with patch(GET_PERMITTED_FIELDS_TARGET, return_value=["dynamic_ref"]):  # linked_doctype excluded
			with self.assertRaises(frappe.PermissionError):
				resolve_dynamic_link_doctype(TEST_DOCTYPE, target.name, "dynamic_ref")

	def test_get_blocked_doctypes_matches_utils_constant(self):
		self.assertEqual(get_blocked_doctypes(), sorted(BLOCKED_DOCTYPES))

	def test_reference_doctype_query_excludes_blocked_doctypes(self):
		results = reference_doctype_query("DocType", "", "name", 0, 200, {})
		names = {r[0] for r in results}
		self.assertFalse(names & BLOCKED_DOCTYPES, "no blocked doctype should ever appear")

	def test_reference_doctype_query_excludes_singles(self):
		single = frappe.db.get_value(
			"DocType", {"issingle": 1, "name": ["not in", list(BLOCKED_DOCTYPES)]}, "name"
		)
		if not single:
			self.skipTest("no non-denylisted Single doctype available on this site to test against")

		results = reference_doctype_query("DocType", single, "name", 0, 20, {})
		self.assertNotIn(single, {r[0] for r in results})

	def test_reference_doctype_query_finds_ordinary_doctype(self):
		results = reference_doctype_query("DocType", TEST_DOCTYPE, "name", 0, 20, {})
		self.assertIn(TEST_DOCTYPE, {r[0] for r in results})

	def test_retry_action_requeues_a_failed_action(self):
		target = make_test_doc()
		doc = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": TEST_DOCTYPE,
			"reference_name": target.name,
			"action_type": "Submit",
			"scheduled_for": near_future_datetime(),
		})
		doc.insert(ignore_permissions=True)
		frappe.db.set_value("Scheduled Action", doc.name, "scheduled_for", due_datetime())
		frappe.db.commit()

		# Same "target deleted before firing" shape used elsewhere to get a
		# real Failed row without waiting on anything.
		frappe.delete_doc(TEST_DOCTYPE, target.name, force=True, ignore_permissions=True)
		frappe.db.commit()
		execute_action(doc.name)
		self.assertEqual(frappe.db.get_value("Scheduled Action", doc.name, "status"), "Failed")

		# Give it something real to point at again before retrying.
		frappe.get_doc({"doctype": TEST_DOCTYPE, "title": target.name}).insert(ignore_permissions=True)

		retry_action(doc.name)

		row = frappe.db.get_value("Scheduled Action", doc.name, ["status", "error_log"], as_dict=True)
		self.assertEqual(row.status, "Pending")
		self.assertEqual(row.error_log, "")

	def test_retry_action_rejects_a_non_failed_action(self):
		target = make_test_doc()
		doc = frappe.get_doc({
			"doctype": "Scheduled Action",
			"reference_doctype": TEST_DOCTYPE,
			"reference_name": target.name,
			"action_type": "Submit",
			"scheduled_for": near_future_datetime(),
		})
		doc.insert(ignore_permissions=True)  # still Pending

		with self.assertRaises(frappe.ValidationError):
			retry_action(doc.name)

	def test_create_scheduled_action_end_to_end(self):
		target = make_test_doc()
		name = create_scheduled_action(
			reference_doctype=TEST_DOCTYPE,
			reference_name=target.name,
			action_type="Submit",
			scheduled_for=near_future_datetime(),
		)
		doc = frappe.get_doc("Scheduled Action", name)
		self.assertEqual(doc.status, "Pending")
		self.assertEqual(doc.scheduled_by, frappe.session.user)

	def test_get_pending_action_returns_the_row_or_none(self):
		target = make_test_doc()
		self.assertIsNone(get_pending_action(TEST_DOCTYPE, target.name))

		name = create_scheduled_action(
			reference_doctype=TEST_DOCTYPE,
			reference_name=target.name,
			action_type="Set Field",
			field_name="category",
			field_value="Beta",
			scheduled_for=near_future_datetime(),
		)
		pending = get_pending_action(TEST_DOCTYPE, target.name)
		self.assertEqual(pending["name"], name)
		self.assertEqual(pending["action_type"], "Set Field")
		self.assertEqual(pending["field_name"], "category")
