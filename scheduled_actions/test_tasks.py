# Copyright (c) 2026, Abdul Hannan and contributors
# See license.txt
#
# Covers tasks.py's execution engine: the atomic claim (the actual
# double-execution guard), execute_action()'s three action types and their
# failure paths, and that Notification Log rows land on both success and
# failure. run_due_actions() itself is deliberately not exercised end to
# end here (it only enqueues via frappe.enqueue, which needs a live worker
# to actually run - not something a unit/integration test should depend
# on); _claim() and execute_action() are what run_due_actions() delegates
# the real work to, and those are covered directly.

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from scheduled_actions.tasks import (
	STUCK_RUNNING_MINUTES,
	_claim,
	_reclaim_stuck_running,
	cleanup_old_actions,
	execute_action,
)
from scheduled_actions.tests.fixtures import (
	TEST_DOCTYPE,
	due_datetime,
	make_test_doc,
	near_future_datetime,
)

PERMITTED_FIELDS_PATCH_TARGET = "frappe.model.get_permitted_fields"


def _schedule(target, action_type, **kwargs):
	"""A Scheduled Action already due (scheduled_for pushed into the past
	after insert, same pattern validate_schedule() forces everywhere else -
	see due_datetime()'s docstring)."""
	doc = frappe.get_doc({
		"doctype": "Scheduled Action",
		"reference_doctype": TEST_DOCTYPE,
		"reference_name": target.name,
		"action_type": action_type,
		"scheduled_for": near_future_datetime(),
		**kwargs,
	})
	doc.insert(ignore_permissions=True)
	frappe.db.set_value("Scheduled Action", doc.name, "scheduled_for", due_datetime())
	frappe.db.commit()
	return doc.name


class IntegrationTestScheduledActionTasks(IntegrationTestCase):
	def test_claim_is_atomic(self):
		target = make_test_doc()
		name = _schedule(target, "Submit")

		self.assertTrue(_claim(name), "first claim should succeed")
		self.assertEqual(frappe.db.get_value("Scheduled Action", name, "status"), "Running")
		self.assertFalse(_claim(name), "a second claim on an already-Running action must fail")

	def test_claim_fails_for_non_pending_status(self):
		target = make_test_doc()
		name = _schedule(target, "Submit")
		frappe.db.set_value("Scheduled Action", name, "status", "Cancelled")
		frappe.db.commit()

		self.assertFalse(_claim(name))

	def test_claim_fails_gracefully_for_missing_row(self):
		self.assertFalse(_claim("SA-DOES-NOT-EXIST"))

	def test_stuck_running_action_is_failed_by_the_reaper(self):
		target = make_test_doc()
		name = _schedule(target, "Submit")
		old = add_to_date(now_datetime(), minutes=-(STUCK_RUNNING_MINUTES + 5))
		frappe.db.set_value("Scheduled Action", name, "status", "Running", update_modified=False)
		frappe.db.set_value("Scheduled Action", name, "modified", old, update_modified=False)
		frappe.db.commit()

		_reclaim_stuck_running()

		row = frappe.db.get_value("Scheduled Action", name, ["status", "error_log"], as_dict=True)
		self.assertEqual(row.status, "Failed")
		self.assertIn("did not complete", row.error_log)

	def test_reaper_leaves_a_freshly_claimed_running_action_alone(self):
		target = make_test_doc()
		name = _schedule(target, "Submit")
		self.assertTrue(_claim(name))  # Running, modified = now

		_reclaim_stuck_running()

		self.assertEqual(frappe.db.get_value("Scheduled Action", name, "status"), "Running")

	def test_execute_set_field_success(self):
		target = make_test_doc(category="Alpha")
		name = _schedule(target, "Set Field", field_name="category", field_value="Beta")

		execute_action(name)

		self.assertEqual(frappe.db.get_value("Scheduled Action", name, "status"), "Executed")
		self.assertEqual(frappe.db.get_value(TEST_DOCTYPE, target.name, "category"), "Beta")

	def test_execute_leaves_an_automated_change_note_on_the_target(self):
		target = make_test_doc(category="Alpha")
		name = _schedule(target, "Set Field", field_name="category", field_value="Beta")

		execute_action(name)

		note = frappe.db.get_value(
			"Comment",
			{
				"reference_doctype": TEST_DOCTYPE,
				"reference_name": target.name,
				"comment_type": "Info",
			},
			"content",
		)
		self.assertTrue(note, "an Info comment should be left on the target")
		self.assertIn("automatically", note)
		self.assertIn(name, note)  # links to the Scheduled Action

	def test_execute_fails_gracefully_when_value_was_never_captured(self):
		# A row that predates validate()'s empty-value check (a mirror-sync
		# that never fired) - must Fail, not silently blank the field.
		target = make_test_doc(category="Alpha")
		name = _schedule(target, "Set Field", field_name="category", field_value="Beta")
		frappe.db.set_value("Scheduled Action", name, "field_value", "")
		frappe.db.commit()

		execute_action(name)

		row = frappe.db.get_value(
			"Scheduled Action", name, ["status", "error_message", "error_log"], as_dict=True
		)
		self.assertEqual(row.status, "Failed")
		# A controlled failure: the readable message lands in both fields,
		# there's no traceback dump.
		self.assertIn("No value was captured", row.error_message)
		self.assertIn("No value was captured", row.error_log)
		self.assertNotIn("Traceback", row.error_log)
		self.assertEqual(frappe.db.get_value(TEST_DOCTYPE, target.name, "category"), "Alpha")

	def test_unexpected_failure_records_a_readable_error_and_the_full_log(self):
		from unittest.mock import patch

		target = make_test_doc(category="Alpha")
		name = _schedule(target, "Set Field", field_name="category", field_value="Beta")

		CAST = "scheduled_actions.scheduled_actions.doctype.scheduled_action.scheduled_action.cast_value"
		with patch(CAST, side_effect=frappe.ValidationError("The category is not one we recognise")):
			execute_action(name)

		row = frappe.db.get_value(
			"Scheduled Action", name, ["status", "error_message", "error_log"], as_dict=True
		)
		self.assertEqual(row.status, "Failed")
		self.assertEqual(row.error_message, "The category is not one we recognise")
		self.assertIn("The category is not one we recognise", row.error_log)
		self.assertIn("Traceback", row.error_log)  # the full log is kept below it

		# ...and the failure notification says why, not just "it failed".
		subject = frappe.db.get_value(
			"Notification Log",
			{"document_type": "Scheduled Action", "document_name": name},
			"subject",
			order_by="creation desc",
		)
		self.assertIn("not one we recognise", subject)

	def test_execute_submit_success(self):
		target = make_test_doc()
		name = _schedule(target, "Submit")

		execute_action(name)

		self.assertEqual(frappe.db.get_value("Scheduled Action", name, "status"), "Executed")
		self.assertEqual(frappe.db.get_value(TEST_DOCTYPE, target.name, "docstatus"), 1)

	def test_an_executed_action_is_frozen(self):
		target = make_test_doc()
		name = _schedule(target, "Submit")
		execute_action(name)  # -> Executed

		doc = frappe.get_doc("Scheduled Action", name)
		doc.scheduled_for = near_future_datetime(600)
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_a_failed_action_is_frozen_except_for_a_retry(self):
		from scheduled_actions.api import retry_action

		target = make_test_doc(category="Alpha")
		name = _schedule(target, "Set Field", field_name="category", field_value="Beta")
		frappe.db.set_value("Scheduled Action", name, "field_value", "")  # will fail on execute
		frappe.db.commit()
		execute_action(name)  # -> Failed
		self.assertEqual(frappe.db.get_value("Scheduled Action", name, "status"), "Failed")

		# editing it any other way is blocked
		doc = frappe.get_doc("Scheduled Action", name)
		doc.field_value = "Gamma"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

		# but retrying it (Failed -> Pending) is allowed
		frappe.db.set_value("Scheduled Action", name, "field_value", "Beta")
		retry_action(name)
		self.assertEqual(frappe.db.get_value("Scheduled Action", name, "status"), "Pending")

	def test_execute_cancel_success(self):
		target = make_test_doc()
		target.submit()
		name = _schedule(target, "Cancel")

		execute_action(name)

		self.assertEqual(frappe.db.get_value("Scheduled Action", name, "status"), "Executed")
		self.assertEqual(frappe.db.get_value(TEST_DOCTYPE, target.name, "docstatus"), 2)

	def test_execute_fails_gracefully_when_target_deleted(self):
		# ignore_on_trash so the doc_events['*']['on_trash'] hook doesn't
		# clear the action - this test is the executor's own safety net for
		# a target that vanished some other way (raw SQL, a migration).
		target = make_test_doc()
		name = _schedule(target, "Submit")
		frappe.delete_doc(
			TEST_DOCTYPE, target.name, force=True, ignore_permissions=True, ignore_on_trash=True
		)
		frappe.db.commit()

		execute_action(name)  # must not raise

		row = frappe.db.get_value("Scheduled Action", name, ["status", "error_log"], as_dict=True)
		self.assertEqual(row.status, "Failed")
		self.assertIn("no longer exists", row.error_log)

	def test_execute_fails_gracefully_when_already_submitted(self):
		target = make_test_doc()
		target.submit()
		name = _schedule(target, "Submit")  # already Submitted, not Draft

		execute_action(name)

		row = frappe.db.get_value("Scheduled Action", name, ["status", "error_log"], as_dict=True)
		self.assertEqual(row.status, "Failed")
		self.assertIn("Draft", row.error_log)

	def test_execute_fails_gracefully_when_not_yet_submitted(self):
		target = make_test_doc()  # still Draft
		name = _schedule(target, "Cancel")

		execute_action(name)

		row = frappe.db.get_value("Scheduled Action", name, ["status", "error_log"], as_dict=True)
		self.assertEqual(row.status, "Failed")
		self.assertIn("Submitted", row.error_log)

	def test_execute_rechecks_field_permlevel_at_runtime(self):
		# Simulates permission having been revoked between scheduling and
		# firing (a Role Permission Manager change, say) - execute_action()
		# re-checks get_permitted_fields itself, not just at schedule time.
		target = make_test_doc()
		name = _schedule(target, "Set Field", field_name="amount", field_value="9")

		from unittest.mock import patch

		with patch(PERMITTED_FIELDS_PATCH_TARGET, return_value=["title"]):
			execute_action(name)

		row = frappe.db.get_value("Scheduled Action", name, ["status", "error_log"], as_dict=True)
		self.assertEqual(row.status, "Failed")
		self.assertIn("permission", row.error_log.lower())
		# And the target must be untouched - the whole point of the check.
		self.assertEqual(frappe.db.get_value(TEST_DOCTYPE, target.name, "amount"), 1.0)

	def test_notification_log_created_on_success_and_failure(self):
		before = now_datetime()

		success_target = make_test_doc()
		success_name = _schedule(success_target, "Submit")
		execute_action(success_name)

		# Same "target deleted before firing" shape as
		# test_execute_fails_gracefully_when_target_deleted, just reused
		# here to get a Failed row to check the notification for
		# (ignore_on_trash, same reason as there).
		failed_target = make_test_doc()
		fail_name = _schedule(failed_target, "Submit")
		frappe.delete_doc(
			TEST_DOCTYPE, failed_target.name, force=True, ignore_permissions=True, ignore_on_trash=True
		)
		frappe.db.commit()
		execute_action(fail_name)

		count = frappe.db.count(
			"Notification Log",
			{"document_type": "Scheduled Action", "creation": [">=", before]},
		)
		self.assertGreaterEqual(count, 2)

	def test_cleanup_deletes_old_finished_actions(self):
		target = make_test_doc()
		name = _schedule(target, "Submit")
		execute_action(name)  # Executed
		self.assertEqual(frappe.db.get_value("Scheduled Action", name, "status"), "Executed")

		frappe.db.set_value(
			"Scheduled Action", name, "modified", add_to_date(now_datetime(), days=-100), update_modified=False
		)
		frappe.db.commit()

		cleanup_old_actions(retention_days=90)

		self.assertFalse(frappe.db.exists("Scheduled Action", name))

	def test_cleanup_keeps_recently_finished_actions(self):
		target = make_test_doc()
		name = _schedule(target, "Submit")
		execute_action(name)  # Executed, modified = just now

		cleanup_old_actions(retention_days=90)

		self.assertTrue(frappe.db.exists("Scheduled Action", name))

	def test_cleanup_never_touches_pending_or_running_regardless_of_age(self):
		# The whole reason this isn't just frappe's generic
		# default_log_clearing_doctypes hook: that clears by creation age
		# alone, with no status awareness, and would happily delete a
		# still-Pending action scheduled far in the future. This is the
		# property that actually matters here.
		target = make_test_doc()
		pending_name = _schedule(target, "Submit")
		frappe.db.set_value(
			"Scheduled Action",
			pending_name,
			"modified",
			add_to_date(now_datetime(), days=-1000),
			update_modified=False,
		)

		running_target = make_test_doc()
		running_name = _schedule(running_target, "Submit")
		frappe.db.set_value("Scheduled Action", running_name, "status", "Running", update_modified=False)
		frappe.db.set_value(
			"Scheduled Action",
			running_name,
			"modified",
			add_to_date(now_datetime(), days=-1000),
			update_modified=False,
		)
		frappe.db.commit()

		cleanup_old_actions(retention_days=90)

		self.assertTrue(frappe.db.exists("Scheduled Action", pending_name))
		self.assertTrue(frappe.db.exists("Scheduled Action", running_name))
