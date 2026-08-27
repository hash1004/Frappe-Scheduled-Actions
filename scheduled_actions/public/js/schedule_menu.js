// Adds Scheduled Actions to every form: a "Schedule..." action in the
// sidebar next to Assign/Attach/Share, and a read-only lock (with banner)
// when a Scheduled Action is already Pending (or Running - see
// get_pending_action) against the open document.
frappe.provide("scheduled_actions");

// Fetched once and cached (not per-form-refresh) - this doesn't change
// within a session, and the "is this doctype even eligible" check needs to
// run on every form-refresh across the whole desk.
scheduled_actions._blocked_doctypes_promise = null;
scheduled_actions.get_blocked_doctypes = function () {
	if (!scheduled_actions._blocked_doctypes_promise) {
		scheduled_actions._blocked_doctypes_promise = frappe.call({
			method: "scheduled_actions.api.get_blocked_doctypes",
		}).then((r) => r.message || []);
	}
	return scheduled_actions._blocked_doctypes_promise;
};

// Frappe fires this document event from inside Form.render_form(), right
// after the page header/toolbar and sidebar have been rebuilt and before
// doctype-specific `refresh` client scripts run - the correct, public hook
// for exactly this. (Monkey-patching Form.prototype.refresh doesn't work
// here: its internals run through frappe.run_serially(), which is
// asynchronous, so code appended after calling the original refresh() races
// ahead of that rebuild and either finds frm.sidebar not ready yet or gets
// its own sidebar entry wiped when the sidebar is re-rendered.)
$(document).on("form-refresh", (event, frm) => {
	try {
		scheduled_actions.setup_form(frm);
	} catch (e) {
		console.error(e); // eslint-disable-line no-console
	}
});

// app_include_js is injected as a dynamically-created <script> tag *after*
// libs.bundle.js, not as a static synchronous tag - on a full page load
// (e.g. opening a document link directly, or a hard refresh) that script
// can finish loading and run this file *after* the very first form-refresh
// has already fired, so the listener above misses it. Cover that case by
// running setup once immediately for whatever form is already open when
// this file executes; every navigation/save after that goes through the
// event listener as normal.
if (window.cur_frm && cur_frm.doc) {
	try {
		scheduled_actions.setup_form(cur_frm);
	} catch (e) {
		console.error(e); // eslint-disable-line no-console
	}
}

scheduled_actions.setup_form = function (frm) {
	if (!frm || !frm.doc || frm.is_new() || frm.doctype === "Scheduled Action") return;

	scheduled_actions.get_blocked_doctypes().then((blocked) => {
		if (blocked.includes(frm.doctype)) return; // no Schedule UI at all on these

		frappe.call({
			method: "scheduled_actions.api.get_pending_action",
			args: { reference_doctype: frm.doctype, reference_name: frm.docname },
			callback: (r) => {
				if (r.message) {
					scheduled_actions.lock_form(frm, r.message);
				} else {
					scheduled_actions.add_sidebar_action(frm);
				}
			},
		});
	});
};

scheduled_actions.lock_form = function (frm, pending) {
	frm.disable_form();
	frm.set_intro(
		__("Locked: {0} is scheduled for {1} ({2})", [
			pending.action_type,
			frappe.datetime.str_to_user(pending.scheduled_for),
			`<a href="/app/scheduled-action/${pending.name}">${__("view")}</a>`,
		]),
		"orange"
	);
};

scheduled_actions.add_sidebar_action = function (frm) {
	if (!frm.sidebar || !frm.sidebar.sidebar) return; // form sidebar disabled
	if (frm.sidebar.sidebar.find(".form-schedule").length) return; // already added this render

	const can_write = !!(frm.perm && frm.perm[0] && frm.perm[0].write);
	const can_submit = !!(frm.perm && frm.perm[0] && frm.perm[0].submit);
	if (!can_write && !can_submit) return;

	// Deliberately not frm.sidebar.add_user_action() - that renders into a
	// separate "Links" section as a plain hyperlink, which is what looked
	// out of place. This instead matches the exact markup every other
	// sidebar action uses (Assign/Attachments/Tags/Share - see
	// form_sidebar.html: a .sidebar-section > .form-sidebar-items >
	// .form-sidebar-label with an icon), and sits in that same list of
	// actions, right before Share, instead of in its own section below it.
	const section = $(`
		<div class="sidebar-section form-schedule">
			<div>
				<span class="form-sidebar-items">
					<a class="form-sidebar-label">
						${frappe.utils.icon("clock")}
						<span class="ellipsis">${__("Schedule")}</span>
					</a>
				</span>
			</div>
		</div>
	`);

	const share_section = frm.sidebar.sidebar.find(".form-shared");
	if (share_section.length) {
		section.insertBefore(share_section);
	} else {
		section.appendTo(frm.sidebar.sidebar);
	}

	section.find(".form-sidebar-label").on("click", () => scheduled_actions.open_dialog(frm));
};

// One entry point (the sidebar action) -> the dialog always shows the
// Action picker, scoped to whatever this document/user actually allows.
scheduled_actions.open_dialog = function (frm) {
	// frm is already the target document's own form, so its submittability
	// is just frm.meta - no lookup needed the way the standalone Scheduled
	// Action form needs one (there, reference_doctype is a Link that can
	// change; here it's fixed to frm.doctype for the dialog's whole life).
	const submittable = !!frm.meta.is_submittable;
	const can_submit_now = submittable && frm.doc.docstatus === 0 && frm.perm[0].submit;
	const can_cancel_now = submittable && frm.doc.docstatus === 1 && frm.perm[0].submit;
	const can_set_field = !!(frm.perm && frm.perm[0] && frm.perm[0].write);

	const vc = scheduled_actions.value_control;
	const set_field_visible = 'eval:doc.action_type=="Set Field"';

	const fields = [];

	// Scoped to what this document/user can actually do right now: a
	// non-submittable doctype offers only "Set Field" (and the control is
	// read-only, since there's nothing to pick), a draft offers Submit, a
	// submitted doc offers Cancel. value_control.js's mirror fields read
	// doc.action_type in their depends_on, so this field must always exist
	// and hold a valid value even when there's only one option.
	const action_type_options = [];
	if (can_submit_now) action_type_options.push({ label: __("Submit"), value: "Submit" });
	if (can_cancel_now) action_type_options.push({ label: __("Cancel"), value: "Cancel" });
	if (can_set_field) action_type_options.push({ label: __("Set Field"), value: "Set Field" });

	fields.push({
		fieldname: "action_type",
		fieldtype: "Select",
		label: __("Action"),
		reqd: 1,
		read_only: action_type_options.length <= 1,
		options: action_type_options,
		default: action_type_options[0] && action_type_options[0].value,
	});

	fields.push({
		fieldname: "field_name",
		fieldtype: "Select",
		label: __("Field"),
		options: [],
		depends_on: set_field_visible,
		mandatory_depends_on: set_field_visible,
	});

	// Mirrored, natively-typed Value controls (Select/Link/Check/Date/
	// Datetime/Number/plain-text) - see value_control.js.
	fields.push(...vc.mirror_field_defs());

	fields.push(
		{
			fieldname: "field_value",
			fieldtype: "Data",
			label: __("New Value"),
			depends_on: 'eval:doc.action_type=="Set Field" && doc.target_fieldtype=="text"',
			mandatory_depends_on: 'eval:doc.action_type=="Set Field" && doc.target_fieldtype=="text"',
		},
		{
			fieldname: "scheduled_for",
			fieldtype: "Datetime",
			label: __("Run At"),
			// No manual timezone note needed - frappe.ui.form.ControlDatetime
			// already appends the viewing user's own timezone under every
			// Datetime field automatically (see set_description() in
			// frappe/public/js/frappe/form/controls/datetime.js), and
			// converts what's typed/shown to/from it transparently.
			reqd: 1,
		}
	);

	const dialog = new frappe.ui.Dialog({
		title: __("Schedule Action"),
		fields,
		primary_action_label: __("Schedule"),
		primary_action: (values) => {
			frappe.call({
				method: "scheduled_actions.api.create_scheduled_action",
				args: {
					reference_doctype: frm.doctype,
					reference_name: frm.docname,
					action_type: values.action_type,
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
						frm.reload_doc();
					}
				},
			});
		},
	});

	// fieldname -> its {fieldname, label, fieldtype, options} from
	// get_settable_fields(), fetched once below and reused on every pick so
	// choosing a field doesn't need a fresh round trip each time.
	let settable_fields_by_name = {};

	dialog.fields_dict.field_name.df.onchange = () => {
		const df = settable_fields_by_name[dialog.get_value("field_name")];
		if (!df) {
			vc.apply_field_pick(dialog, null, null);
			return;
		}

		// A Dynamic Link field's actual target doctype has to be resolved
		// (df.options is the *fieldname* holding it, not the doctype
		// itself) before the Link mirror's options can be set - every
		// other category can go straight to fetching the current value.
		const resolve_link_doctype =
			df.fieldtype === "Dynamic Link"
				? frappe
						.call({
							method: "scheduled_actions.api.resolve_dynamic_link_doctype",
							args: { doctype: frm.doctype, name: frm.docname, fieldname: df.fieldname },
						})
						.then((r) => r.message)
				: Promise.resolve(undefined);

		resolve_link_doctype.then((link_doctype) => {
			frappe.call({
				method: "scheduled_actions.api.get_field_current_value",
				args: { doctype: frm.doctype, name: frm.docname, fieldname: df.fieldname },
				callback: (r) => vc.apply_field_pick(dialog, df, r.message, link_doctype),
			});
		});
	};

	Object.keys(vc.MIRROR_FIELD_BY_CATEGORY).forEach((category) => {
		const fieldname = vc.MIRROR_FIELD_BY_CATEGORY[category];
		if (dialog.fields_dict[fieldname]) {
			dialog.fields_dict[fieldname].df.onchange = () =>
				vc.sync_mirror_to_field_value(dialog, category);
		}
	});

	if (can_set_field) {
		frappe.call({
			method: "scheduled_actions.api.get_settable_fields",
			args: { doctype: frm.doctype },
			callback: (r) => {
				const fields_list = r.message || [];
				settable_fields_by_name = {};
				fields_list.forEach((df) => (settable_fields_by_name[df.fieldname] = df));

				const options = fields_list.map((df) => ({
					label: `${df.label} (${df.fieldname})`,
					value: df.fieldname,
				}));
				dialog.set_df_property("field_name", "options", options);
			},
		});
	}

	dialog.show();
};
