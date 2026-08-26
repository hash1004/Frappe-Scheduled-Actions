// Copyright (c) 2026, Abdul Hannan and contributors
// For license information, please see license.txt

// field_name's picker and the field_value_* mirror controls (Select/Link/
// Check/Date/Datetime/Number/plain-text) are shared with the "Schedule..."
// Dialog in public/js/schedule_menu.js - see public/js/value_control.js,
// loaded globally via app_include_js so it's already available here.

frappe.ui.form.on("Scheduled Action", {
	setup(frm) {
		// Blocked doctypes (see utils.BLOCKED_DOCTYPES) and Singles should
		// never be offered here at all, not just rejected on save - a
		// server-side query controller, not a client-side "not in" filter,
		// so there's one source of truth and no cache/race to keep in sync.
		frm.set_query("reference_doctype", () => ({
			query: "scheduled_actions.api.reference_doctype_query",
		}));
	},

	refresh(frm) {
		if (frm.doc.status === "Pending" && !frm.is_new()) {
			frm.add_custom_button(__("Cancel Action"), () => {
				frappe.confirm(__("Cancel this scheduled action?"), () => {
					frappe.db.set_value(frm.doctype, frm.docname, "status", "Cancelled").then(() => frm.reload_doc());
				});
			});
		}

		if (frm.doc.status === "Failed" && !frm.is_new()) {
			// Single-attempt execution is deliberate (see tasks.py) - this
			// is a manual re-queue, not automatic retry-with-backoff. Goes
			// through api.retry_action (a real Document.save(), not a raw
			// field update) so the ownership lock and permission re-checks
			// still apply to who's allowed to do this.
			frm.add_custom_button(__("Retry"), () => {
				frappe.confirm(__("Re-queue this action to run again now?"), () => {
					frappe.call({
						method: "scheduled_actions.api.retry_action",
						args: { name: frm.docname },
						freeze: true,
						callback: () => frm.reload_doc(),
					});
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
		build_field_name_options(frm, () => apply_current_field(frm, frm.doc.field_value));
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
			apply_current_field(frm, null);
			return;
		}
		apply_current_field(frm, undefined); // fetches the current value itself
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

// `prefill_from` explicitly given (refresh, which already has the
// persisted field_value) -> used as-is. `undefined` (field_name just
// changed) -> fetched fresh via get_field_current_value. A Dynamic Link
// target field needs its actual doctype resolved first either way (see
// resolve_dynamic_link_doctype's docstring) - both calls run in parallel
// rather than the value-fetch waiting on the doctype-resolve for no reason.
function apply_current_field(frm, prefill_from) {
	const df = (frm._settable_fields_by_name || {})[frm.doc.field_name];

	frm.set_df_property("field_name", "description",
		df ? __("{0} ({1})", [df.label, df.fieldtype]) : __("Pick a Document Type first"));

	if (!df) {
		scheduled_actions.value_control.apply_field_pick(frm, null, prefill_from);
		return;
	}

	const resolve_link_doctype =
		df.fieldtype === "Dynamic Link"
			? frappe
					.call({
						method: "scheduled_actions.api.resolve_dynamic_link_doctype",
						args: {
							doctype: frm.doc.reference_doctype,
							name: frm.doc.reference_name,
							fieldname: df.fieldname,
						},
					})
					.then((r) => r.message)
			: Promise.resolve(undefined);

	const fetch_value =
		prefill_from !== undefined
			? Promise.resolve(prefill_from)
			: frappe
					.call({
						method: "scheduled_actions.api.get_field_current_value",
						args: {
							doctype: frm.doc.reference_doctype,
							name: frm.doc.reference_name,
							fieldname: df.fieldname,
						},
					})
					.then((r) => r.message);

	Promise.all([resolve_link_doctype, fetch_value]).then(([link_doctype, value]) => {
		scheduled_actions.value_control.apply_field_pick(frm, df, value, link_doctype);
	});
}
