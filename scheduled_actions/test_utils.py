# Copyright (c) 2026, Abdul Hannan and contributors
# See license.txt
#
# Covers utils.py directly: the pending-action lock (block_edit_while_
# scheduled/get_pending_action_name - including that it covers "Running",
# not just "Pending", and that the executor's own bypass flag works),
# ensure_doctype_allowed, and BLOCKED_DOCTYPES' composition.

import frappe
from frappe.tests import IntegrationTestCase

from scheduled_actions.tests.fixtures import TEST_DOCTYPE, make_test_doc, near_future_datetime
from scheduled_actions.utils import BLOCKED_DOCTYPES, ensure_doctype_allowed, get_pending_action_name


def _schedule(target):
	doc = frappe.get_doc({
		"doctype": "Scheduled Action",
		"reference_doctype": TEST_DOCTYPE,
		"reference_name": target.name,
		"action_type": "Submit",
		"scheduled_for": near_future_datetime(),
	})
	doc.insert(ignore_permissions=True)
	return doc.name


class IntegrationTestScheduledActionsUtils(IntegrationTestCase):
	def test_get_pending_action_name_none_when_nothing_scheduled(self):
		target = make_test_doc()
		self.assertIsNone(get_pending_action_name(TEST_DOCTYPE, target.name))

	def test_get_pending_action_name_finds_pending(self):
		target = make_test_doc()
		name = _schedule(target)
		self.assertEqual(get_pending_action_name(TEST_DOCTYPE, target.name), name)

	def test_get_pending_action_name_finds_running_too(self):
		target = make_test_doc()
		name = _schedule(target)
		frappe.db.set_value("Scheduled Action", name, "status", "Running")
		frappe.db.commit()
		self.assertEqual(get_pending_action_name(TEST_DOCTYPE, target.name), name)

	def test_get_pending_action_name_ignores_finished_states(self):
		target = make_test_doc()
		name = _schedule(target)
		for status in ("Executed", "Failed", "Cancelled"):
			frappe.db.set_value("Scheduled Action", name, "status", status)
			frappe.db.commit()
			self.assertIsNone(get_pending_action_name(TEST_DOCTYPE, target.name), f"status={status}")

	def test_locked_document_cannot_be_saved(self):
		target = make_test_doc()
		_schedule(target)

		target.reload()
		target.title = "attempted edit while locked"
		with self.assertRaises(frappe.ValidationError):
			target.save(ignore_permissions=True)

	def test_lock_bypass_flag_allows_the_executor_through(self):
		target = make_test_doc()
		_schedule(target)

		target.reload()
		target.title = "edit via the executor's own bypass"
		target.flags.ignore_scheduled_action_lock = True
		target.save(ignore_permissions=True)  # must not raise

		self.assertEqual(frappe.db.get_value(TEST_DOCTYPE, target.name, "title"), target.title)

	def test_ensure_doctype_allowed_raises_for_blocked(self):
		with self.assertRaises(frappe.PermissionError):
			ensure_doctype_allowed("User")

	def test_ensure_doctype_allowed_passes_for_ordinary_doctype(self):
		ensure_doctype_allowed(TEST_DOCTYPE)  # must not raise

	def test_blocked_doctypes_includes_core_list(self):
		from frappe.model import core_doctypes_list

		self.assertTrue(set(core_doctypes_list).issubset(BLOCKED_DOCTYPES))

	def test_blocked_doctypes_includes_security_addendum(self):
		for doctype in ("Server Script", "System Settings", "Role Profile", "OAuth Client", "Webhook"):
			self.assertIn(doctype, BLOCKED_DOCTYPES)
