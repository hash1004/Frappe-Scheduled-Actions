# Copyright (c) 2026, Abdul Hannan and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ScheduledActionsTestDoc(Document):
	"""Test-only fixture doctype - see the doctype's own description. No
	custom behavior; a plain Document is exactly what the test suite needs
	(something real to Submit/Cancel/Set Field against)."""

	pass
