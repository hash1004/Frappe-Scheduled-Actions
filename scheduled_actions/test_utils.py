# Copyright (c) 2026, Abdul Hannan and contributors
# See license.txt
#
# Covers utils.py directly: get_pending_action_name (including that it
# covers "Running", not just "Pending"), cancel_pending_action_on_change
# (a real edit cancels the pending action; a no-op save and the executor's
# own bypass flag don't), ensure_doctype_allowed, and BLOCKED_DOCTYPES'
# composition.

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

	def test_real_edit_cancels_the_pending_action(self):
		target = make_test_doc(category="Alpha")
		name = _schedule(target)

		target.reload()
		target.category = "Beta"
		target.save(ignore_permissions=True)  # not blocked

		row = frappe.db.get_value("Scheduled Action", name, ["status", "error_log"], as_dict=True)
		self.assertEqual(row.status, "Cancelled")
		self.assertIn("was changed", row.error_log)

		# ...and a note is left on the document itself.
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": TEST_DOCTYPE, "reference_name": target.name, "comment_type": "Info"},
			)
		)

	def test_noop_save_leaves_the_pending_action_alone(self):
		target = make_test_doc()
		name = _schedule(target)

		target.reload()
		target.save(ignore_permissions=True)  # nothing changed

		self.assertEqual(frappe.db.get_value("Scheduled Action", name, "status"), "Pending")

	def test_bypass_flag_leaves_the_pending_action_alone(self):
		target = make_test_doc(category="Alpha")
		name = _schedule(target)

		target.reload()
		target.category = "Gamma"
		target.flags.ignore_scheduled_action_lock = True
		target.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("Scheduled Action", name, "status"), "Pending")
		self.assertEqual(frappe.db.get_value(TEST_DOCTYPE, target.name, "category"), "Gamma")

	def test_manual_submit_cancels_the_pending_action(self):
		target = make_test_doc()
		name = _schedule(target)  # scheduled Submit, but the user submits first

		target.reload()
		target.submit()

		self.assertEqual(frappe.db.get_value("Scheduled Action", name, "status"), "Cancelled")

	def test_deleting_the_target_deletes_all_its_scheduled_actions(self):
		# The Dynamic Link would otherwise block the target from ever being
		# deleted; on_trash clears the rows before Frappe's link check runs.
		target = make_test_doc()
		finished = _schedule(target)
		frappe.db.set_value("Scheduled Action", finished, "status", "Executed")
		frappe.db.commit()
		pending = _schedule(target)

		frappe.delete_doc(TEST_DOCTYPE, target.name, ignore_permissions=True)  # not blocked

		self.assertFalse(frappe.db.exists("Scheduled Action", finished))
		self.assertFalse(frappe.db.exists("Scheduled Action", pending))

	def test_ensure_doctype_allowed_raises_for_blocked(self):
		with self.assertRaises(frappe.PermissionError):
			ensure_doctype_allowed("User")

	def test_ensure_doctype_allowed_passes_for_ordinary_doctype(self):
		ensure_doctype_allowed(TEST_DOCTYPE)  # must not raise

	def test_blocked_doctypes_includes_core_list(self):
		from frappe.model import core_doctypes_list

		self.assertTrue(set(core_doctypes_list).issubset(BLOCKED_DOCTYPES))

	def test_blocked_doctypes_includes_security_addendum(self):
		for doctype in (
			"Scheduled Action",
			"Server Script",
			"System Settings",
			"Role Profile",
			"OAuth Client",
			"Webhook",
		):
			self.assertIn(doctype, BLOCKED_DOCTYPES)

	def test_scheduled_action_cannot_target_a_scheduled_action(self):
		with self.assertRaises(frappe.PermissionError):
			ensure_doctype_allowed("Scheduled Action")
