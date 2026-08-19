// Copyright (c) 2026, Abdul Hannan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Scheduled Action", {
	refresh(frm) {
		if (frm.doc.status === "Pending" && !frm.is_new()) {
			frm.add_custom_button(__("Cancel Action"), () => {
				frappe.confirm(__("Cancel this scheduled action?"), () => {
					frappe.db.set_value(frm.doctype, frm.docname, "status", "Cancelled").then(() => frm.reload_doc());
				});
			});
		}
		frm.set_df_property("field_name", "description",
			frm.doc.reference_doctype
				? __("Fieldname on {0}, e.g. status", [frm.doc.reference_doctype])
				: __("Pick a Document Type first")
		);
	},

	reference_doctype(frm) {
		frm.set_value("reference_name", "");
		frm.set_value("field_name", "");
	},
});
