// Copyright (c) 2026, Abdul Hannan and contributors
// For license information, please see license.txt

const STATUS_COLOR = {
	Pending: "blue",
	Running: "yellow",
	Executed: "green",
	Failed: "red",
	Cancelled: "gray",
	Skipped: "purple",
};

frappe.listview_settings["Scheduled Action"] = {
	add_fields: ["scheduled_for"],
	get_indicator(doc) {
		// A Pending action whose time has already passed usually means a
		// stuck queue or a worker that's down, not anything wrong with the
		// action itself - worth a visibly different color from an
		// on-schedule Pending row so it doesn't blend in.
		//
		// doc.scheduled_for here is the raw stored value (system
		// timezone, same as the server) - system_datetime(), not
		// now_datetime() (the *viewing user's* timezone), is the correct
		// "now" to compare it against.
		if (doc.status === "Pending" && doc.scheduled_for < frappe.datetime.system_datetime()) {
			return [__("Overdue"), "orange", "status,=,Pending"];
		}

		return [__(doc.status), STATUS_COLOR[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
