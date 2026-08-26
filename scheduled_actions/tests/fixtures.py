# Shared test fixtures for the scheduled_actions test suite. Deliberately
# not named test_*.py so Frappe's test runner doesn't try to collect it as
# a test module itself.

import uuid

import frappe
from frappe.utils import add_to_date, now_datetime

TEST_DOCTYPE = "Scheduled Actions Test Doc"


def make_test_doc(**kwargs):
	"""A Scheduled Actions Test Doc with a unique title (autoname is
	field:title, so titles must be unique) and sensible defaults, inserted
	as Administrator regardless of the current session user - tests that
	care about a specific user's permissions switch frappe.session.user
	*after* creating the fixture, not before."""
	original_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		doc = frappe.get_doc({
			"doctype": TEST_DOCTYPE,
			"title": kwargs.pop("title", f"SA Test {uuid.uuid4().hex[:10]}"),
			"category": kwargs.pop("category", "Alpha"),
			"is_flagged": kwargs.pop("is_flagged", 0),
			"amount": kwargs.pop("amount", 1.0),
			**kwargs,
		})
		doc.insert(ignore_permissions=True)
		return doc
	finally:
		frappe.set_user(original_user)


def make_test_user(roles=(), key=None):
	"""A throwaway user for permission-boundary tests - "All" only by
	default (no System Manager), plus whatever extra roles the test needs.
	Reused across calls with the same (roles, key) instead of creating a
	fresh one every time. Pass `key` when a test needs two *distinct* users
	with the same role set (e.g. "does this lock apply to someone other
	than the scheduler, even one with identical permissions") - without it,
	two calls with the same roles would resolve to the same cached user and
	the test wouldn't be testing what it looks like it's testing."""
	slug = key or ("-".join(sorted(roles)) or "plain")
	email = f"sa-test-{slug}@example.com".lower().replace(" ", "-")

	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
	else:
		user = frappe.get_doc({
			"doctype": "User",
			"email": email,
			"first_name": "SA Test User",
			"send_welcome_email": 0,
		})
		user.insert(ignore_permissions=True)

	existing_roles = {r.role for r in user.roles}
	for role in roles:
		if role not in existing_roles:
			user.append("roles", {"role": role})
	if roles:
		user.save(ignore_permissions=True)

	return user.name


def due_datetime(seconds_ago=5):
	"""A scheduled_for value that's already due - for tests that need to
	simulate an action whose time has come without waiting. Scheduled
	Action's validate_schedule() only rejects a past scheduled_for on
	*insert*, so the normal pattern is: insert with a near-future value,
	then frappe.db.set_value() it back into the past."""
	return add_to_date(now_datetime(), seconds=-abs(seconds_ago))


def near_future_datetime(seconds=30):
	return add_to_date(now_datetime(), seconds=seconds)
