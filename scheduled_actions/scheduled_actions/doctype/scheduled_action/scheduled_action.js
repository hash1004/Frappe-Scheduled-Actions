// Copyright (c) 2026, Abdul Hannan and contributors
// For license information, please see license.txt

// field_name's picker and the field_value_* mirror controls (Select/Link/
// Check/Date/Datetime/Number/plain-text) are shared with the "Schedule..."
// Dialog in public/js/schedule_menu.js - see public/js/value_control.js,
// loaded globally via app_include_js so it's already available here.

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

		// A saved doc only ever persists field_value (the mirrors are a
		// pure UI convenience, not stored) - re-derive which mirror should
		// be showing from the current field_name and push the persisted
		// value into it, every refresh. Chained after the options call
		// (not fired in parallel) since it needs that call's field metadata
		// to know which mirror is even the right one.
		build_field_name_options(frm, () => apply_current_field(frm, { prefill_from: frm.doc.field_value }));
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
		if (!frm.doc.reference_doctype || !frm.doc.field_name) {
			apply_current_field(frm, { prefill_from: null });
			return;
		}

		// Picking a field defaults the new value to what's already on the
		// target document - the common case is nudging one field, not
		// starting from a blank slate.
		frappe.call({
			method: "scheduled_actions.api.get_field_current_value",
			args: {
				doctype: frm.doc.reference_doctype,
				name: frm.doc.reference_name,
				fieldname: frm.doc.field_name,
			},
			callback: (r) => apply_current_field(frm, { prefill_from: r.message }),
		});
	},

	field_value_select(frm) { scheduled_actions.value_control.sync_mirror_to_field_value(frm, "select"); },
	field_value_link(frm) { scheduled_actions.value_control.sync_mirror_to_field_value(frm, "link"); },
	field_value_check(frm) { scheduled_actions.value_control.sync_mirror_to_field_value(frm, "check"); },
	field_value_date(frm) { scheduled_actions.value_control.sync_mirror_to_field_value(frm, "date"); },
	field_value_datetime(frm) { scheduled_actions.value_control.sync_mirror_to_field_value(frm, "datetime"); },
	field_value_number(frm) { scheduled_actions.value_control.sync_mirror_to_field_value(frm, "number"); },
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

// field_name is a Select populated from the *server-filtered* field list -
// scheduled_actions.api.get_settable_fields() already excludes layout/table/
// attachment fields and anything the current user lacks permlevel-write
// access to, so this can't offer a field that would only be rejected on
// save. df's (fieldname/label/fieldtype/options) are cached on the form
// object for field_name's change handler to reuse without another call.
function build_field_name_options(frm, on_done) {
	if (!frm.doc.reference_doctype) {
		frm.set_df_property("field_name", "options", []);
		on_done && on_done();
		return;
	}

	frappe.call({
		method: "scheduled_actions.api.get_settable_fields",
		args: { doctype: frm.doc.reference_doctype },
		callback: (r) => {
			const fields_list = r.message || [];
			frm._settable_fields_by_name = {};
			fields_list.forEach((df) => (frm._settable_fields_by_name[df.fieldname] = df));

			frm.set_df_property("field_name", "options", [""].concat(fields_list.map((df) => df.fieldname)));
			frm.refresh_field("field_name");
			on_done && on_done();
		},
	});
}

function apply_current_field(frm, { prefill_from }) {
	const df = (frm._settable_fields_by_name || {})[frm.doc.field_name];

	frm.set_df_property("field_name", "description",
		df ? __("{0} ({1})", [df.label, df.fieldtype]) : __("Pick a Document Type first"));

	scheduled_actions.value_control.apply_field_pick(frm, df || null, prefill_from);
}
