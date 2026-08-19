// Injects "Schedule Submit" / "Schedule Cancel" / "Schedule Field Change"
// into every form's menu, everywhere, without every doctype needing its
// own client script.
frappe.provide("scheduled_actions");

$(document).on("app_ready", () => {
	if (frappe.ui.form.Form.prototype.__scheduled_actions_patched) return;
	frappe.ui.form.Form.prototype.__scheduled_actions_patched = true;

	const original_refresh = frappe.ui.form.Form.prototype.refresh;
	frappe.ui.form.Form.prototype.refresh = function () {
		original_refresh.apply(this, arguments);
		try {
			scheduled_actions.add_menu_items(this);
		} catch (e) {
			console.error(e); // eslint-disable-line no-console
		}
	};
});

scheduled_actions.add_menu_items = function (frm) {
	if (!frm || !frm.doc || frm.is_new() || frm.doctype === "Scheduled Action") return;

	const can_write = frappe.perm.has_perm(frm.doctype, 0, "write");
	const can_submit = frappe.perm.has_perm(frm.doctype, 0, "submit");

	if (frm.meta.is_submittable && frm.doc.docstatus === 0 && can_submit) {
		frm.page.add_menu_item(__("Schedule Submit..."), () =>
			scheduled_actions.open_dialog(frm, "Submit")
		);
	}

	if (frm.meta.is_submittable && frm.doc.docstatus === 1 && can_submit) {
		frm.page.add_menu_item(__("Schedule Cancel..."), () =>
			scheduled_actions.open_dialog(frm, "Cancel")
		);
	}

	if (can_write) {
		frm.page.add_menu_item(__("Schedule Field Change..."), () =>
			scheduled_actions.open_dialog(frm, "Set Field")
		);
	}
};

scheduled_actions.open_dialog = function (frm, action_type) {
	const fields = [
		{
			fieldname: "scheduled_for",
			fieldtype: "Datetime",
			label: __("Run At"),
			reqd: 1,
		},
	];

	if (action_type === "Set Field") {
		fields.unshift({
			fieldname: "field_name",
			fieldtype: "Select",
			label: __("Field"),
			reqd: 1,
			options: [],
		});
		fields.push({
			fieldname: "field_value",
			fieldtype: "Data",
			label: __("New Value"),
			reqd: 1,
		});
	}

	const dialog = new frappe.ui.Dialog({
		title:
			action_type === "Set Field"
				? __("Schedule Field Change")
				: __("Schedule {0}", [action_type]),
		fields,
		primary_action_label: __("Schedule"),
		primary_action: (values) => {
			frappe.call({
				method: "scheduled_actions.api.create_scheduled_action",
				args: {
					reference_doctype: frm.doctype,
					reference_name: frm.docname,
					action_type,
					scheduled_for: values.scheduled_for,
					field_name: values.field_name,
					field_value: values.field_value,
				},
				freeze: true,
				callback: (r) => {
					if (r.message) {
						frappe.show_alert({
							message: __("Scheduled as {0}", [r.message]),
							indicator: "green",
						});
						dialog.hide();
					}
				},
			});
		},
	});

	if (action_type === "Set Field") {
		frappe.call({
			method: "scheduled_actions.api.get_settable_fields",
			args: { doctype: frm.doctype },
			callback: (r) => {
				const options = (r.message || []).map((df) => ({
					label: `${df.label} (${df.fieldname})`,
					value: df.fieldname,
				}));
				dialog.set_df_property("field_name", "options", options);
			},
		});
	}

	dialog.show();
};
