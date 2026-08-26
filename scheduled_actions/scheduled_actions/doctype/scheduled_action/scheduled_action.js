// Copyright (c) 2026, Abdul Hannan and contributors
// For license information, please see license.txt

// Target fieldtypes that don't have a dedicated "Value" mirror field fall
// back to the plain text one ("text" category).
const TARGET_FIELDTYPE_CATEGORY = {
	Select: "select",
	Link: "link",
	Check: "check",
	Date: "date",
	Datetime: "datetime",
	Int: "number",
	Float: "number",
	Currency: "number",
	Percent: "number",
};

// One real, natively-typed control per category, kept in sync onto the
// single `field_value` (Data) column that's actually persisted and read
// by the executor - see scheduled_actions/tasks.py.
const VALUE_MIRROR_FIELDS = {
	select: "field_value_select",
	link: "field_value_link",
	check: "field_value_check",
	date: "field_value_date",
	datetime: "field_value_datetime",
	number: "field_value_number",
};

// Fieldtypes a Scheduled Action can't meaningfully target - mirrors the
// server-side check in ScheduledAction.validate_action().
const UNSETTABLE_FIELDTYPES = [
	"Section Break", "Column Break", "Tab Break", "HTML", "Table",
	"Table MultiSelect", "Attach", "Attach Image", "Button", "Fold",
	"Heading", "Image",
];

frappe.ui.form.on("Scheduled Action", {
	refresh(frm) {
		if (frm.doc.status === "Pending" && !frm.is_new()) {
			frm.add_custom_button(__("Cancel Action"), () => {
				frappe.confirm(__("Cancel this scheduled action?"), () => {
					frappe.db.set_value(frm.doctype, frm.docname, "status", "Cancelled").then(() => frm.reload_doc());
				});
			});
		}

		gate_action_type(frm);
		build_field_name_options(frm);
		sync_value_field(frm, { load_existing_value: true });
	},

	reference_doctype(frm) {
		frm.set_value("reference_name", "");
		frm.set_value("field_name", "");
		gate_action_type(frm);
		build_field_name_options(frm);
	},

	action_type(frm) {
		gate_action_type(frm);
	},

	field_name(frm) {
		sync_value_field(frm, { load_existing_value: false });
	},

	field_value_select(frm) { mirror_to_field_value(frm, "select"); },
	field_value_link(frm) { mirror_to_field_value(frm, "link"); },
	field_value_check(frm) { mirror_to_field_value(frm, "check"); },
	field_value_date(frm) { mirror_to_field_value(frm, "date"); },
	field_value_datetime(frm) { mirror_to_field_value(frm, "datetime"); },
	field_value_number(frm) { mirror_to_field_value(frm, "number"); },
});

// A doctype that can't be submitted has no meaningful Submit/Cancel action,
// so lock Action Type to "Set Field" instead of letting the user pick a
// no-op that would only fail at execution time.
function gate_action_type(frm) {
	if (!frm.doc.reference_doctype) {
		frm.set_df_property("action_type", "read_only", 0);
		return;
	}

	frappe.model.with_doctype(frm.doc.reference_doctype, () => {
		const meta = frappe.get_meta(frm.doc.reference_doctype);
		const submittable = !!(meta && meta.is_submittable);

		frm.set_df_property("action_type", "read_only", submittable ? 0 : 1);
		if (!submittable && frm.doc.action_type !== "Set Field") {
			frm.set_value("action_type", "Set Field");
		}
	});
}

// field_name becomes a real picker over the target doctype's own fields,
// instead of free text the user has to get exactly right.
function build_field_name_options(frm) {
	if (!frm.doc.reference_doctype) {
		frm.set_df_property("field_name", "options", []);
		return;
	}

	frappe.model.with_doctype(frm.doc.reference_doctype, () => {
		const meta = frappe.get_meta(frm.doc.reference_doctype);
		const options = meta.fields
			.filter((df) => df.fieldname && !UNSETTABLE_FIELDTYPES.includes(df.fieldtype))
			.map((df) => df.fieldname)
			.sort();

		frm.set_df_property("field_name", "options", [""].concat(options));
		frm.refresh_field("field_name");
	});
}

// Pulls the selected field's metadata and points the matching Value mirror
// (Select/Link/Check/Date/Datetime/Number) at its constraints - options list,
// linked doctype, etc - so the input can only produce a valid value.
function sync_value_field(frm, { load_existing_value }) {
	if (!frm.doc.reference_doctype || !frm.doc.field_name) {
		set_active_category(frm, "text", { load_existing_value });
		return;
	}

	frappe.model.with_doctype(frm.doc.reference_doctype, () => {
		const meta = frappe.get_meta(frm.doc.reference_doctype);
		const df = meta.get_field(frm.doc.field_name);

		frm.set_df_property("field_name", "description",
			df ? __("{0} ({1})", [df.label, df.fieldtype]) : __("Pick a Document Type first"));

		const category = df ? (TARGET_FIELDTYPE_CATEGORY[df.fieldtype] || "text") : "text";

		if (category === "select") {
			frm.set_df_property("field_value_select", "options", df.options || "");
		}
		if (category === "link") {
			frm.set_df_property("field_value_link", "options", df.options || "");
		}

		set_active_category(frm, category, { load_existing_value });
	});
}

function set_active_category(frm, category, { load_existing_value }) {
	const switching_target = frm.doc.target_fieldtype !== category;
	frm.set_value("target_fieldtype", category);

	if (load_existing_value) {
		// Form just loaded/refreshed: the persisted value only lives in
		// field_value, so push it into the mirror that's now showing.
		const mirror = VALUE_MIRROR_FIELDS[category];
		if (mirror && frm.doc.field_value !== "" && frm.doc.field_value !== null && frm.doc.field_value !== undefined) {
			frm.set_value(mirror, cast_for_mirror(category, frm.doc.field_value));
		}
	} else if (switching_target) {
		// The target field changed underneath the user - stale values in
		// any mirror (including the one they can no longer see) would be
		// misleading, so clear them all.
		Object.values(VALUE_MIRROR_FIELDS).forEach((f) => frm.set_value(f, ""));
		frm.set_value("field_value", "");
	}
}

function cast_for_mirror(category, raw) {
	if (category === "check") return cint(raw);
	if (category === "number") return flt(raw);
	return raw;
}

// Whichever mirror is currently active is the user's actual input; keep the
// real, persisted field_value in lockstep with it.
function mirror_to_field_value(frm, category) {
	if (frm.doc.target_fieldtype !== category) return;
	const value = frm.doc[VALUE_MIRROR_FIELDS[category]];
	frm.set_value("field_value", value === 0 || value === false ? String(value) : (value || ""));
}
