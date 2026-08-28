// Adds Scheduled Actions to every form: a "Schedule" section in the sidebar
// next to Assign/Attach/Share - a "+" to schedule an action, the pending
// action listed below it - plus a non-blocking banner when one is pending.
// (A pending action no longer locks the form: utils.
// cancel_pending_action_on_change cancels it server-side the moment the
// document is actually changed.)
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
			callback: (r) => scheduled_actions.render_sidebar(frm, r.message || null),
		});
	});
};

// Non-blocking heads-up - the form stays fully editable (see the file
// header). Yellow, dismissable, re-shown on the next form-refresh.
scheduled_actions.notify_pending = function (frm, pending) {
	const when = frappe.datetime.str_to_user(pending.scheduled_for);
	const link = `<a href="/app/scheduled-action/${pending.name}">${__("view")}</a>`;
	const msg = __("{0} is scheduled for {1} - editing this document will cancel it. ({2})", [
		__(pending.action_type),
		when,
		link,
	]);
	frm.set_intro(msg, "yellow");
};

scheduled_actions.render_sidebar = function (frm, pending) {
	if (!frm.sidebar || !frm.sidebar.sidebar) return; // form sidebar disabled

	const can_write = !!(frm.perm && frm.perm[0] && frm.perm[0].write);
	const can_submit = !!(frm.perm && frm.perm[0] && frm.perm[0].submit);
	if (!can_write && !can_submit) return;

	// Rebuilt from scratch on every form-refresh (the pending action changes
	// as it's scheduled / cancelled / fired), so drop any prior copy rather
	// than the usual "bail if already present" guard.
	frm.sidebar.sidebar.find(".form-schedule").remove();

	// Markup mirrors form_sidebar.html's .form-shared section exactly - a
	// .form-sidebar-items row (flex, space-between) holding a .form-sidebar-
	// label and an .icon-btn "+" button, with a list container below - so
	// form_sidebar.scss lays the frame out like the built-in Assign /
	// Attachments / Share sections. schedule_sidebar.css covers only what
	// those built-in selectors don't reach (the "+" button, the rows).
	const section = $(`
		<div class="sidebar-section form-schedule">
			<div>
				<span class="form-sidebar-items">
					<span class="schedule-label form-sidebar-label">
						${frappe.utils.icon("clock")}
						<span class="ellipsis">${__("Schedule")}</span>
					</span>
					<button class="add-schedule-btn btn btn-link icon-btn" title="${__("Schedule an action")}">
						<svg class="es-icon icon-sm"><use href="#es-line-add"></use></svg>
					</button>
				</span>
				<div class="scheduled-actions"></div>
			</div>
		</div>
	`);

	const open = () => scheduled_actions.open_dialog(frm);
	section.find(".add-schedule-btn").on("click", open);

	if (pending) {
		scheduled_actions.render_pending_row(section.find(".scheduled-actions"), pending);
		// One pending action per document (server-side invariant), so
		// while one exists neither the "+" nor the label opens the dialog -
		// there's nothing to add. The label is just a section header now;
		// the row below links to the action to cancel it.
		section.addClass("has-pending");
		section.find(".add-schedule-btn").remove();
		scheduled_actions.notify_pending(frm, pending);
	} else {
		section.find(".schedule-label").on("click", open);
	}

	const share_section = frm.sidebar.sidebar.find(".form-shared");
	if (share_section.length) {
		section.insertBefore(share_section);
	} else {
		section.appendTo(frm.sidebar.sidebar);
	}
};

// One row - action type, plus the field name for a Set Field action -
// linking to the Scheduled Action itself. Built as a list (like
// Attachments) even though there's only ever one, so it reads as "what's
// scheduled on this document".
scheduled_actions.render_pending_row = function ($list, pending) {
	const runs = __("Runs {0}", [frappe.datetime.str_to_user(pending.scheduled_for)]);
	const $row = $('<a class="scheduled-action-row"></a>')
		.attr("href", `/app/scheduled-action/${pending.name}`)
		.attr("title", runs);

	$('<span class="scheduled-action-type"></span>').text(__(pending.action_type)).appendTo($row);
	if (pending.action_type === "Set Field" && pending.field_name) {
		$('<span class="scheduled-action-field"></span>').text(pending.field_name).appendTo($row);
	}
	$row.appendTo($list);
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
		},
		{
			fieldname: "advanced_section",
			fieldtype: "Section Break",
			label: __("Conditions (optional)"),
			collapsible: 1,
		},
		{
			fieldname: "condition",
			fieldtype: "Small Text",
			label: __("Only run if"),
			description: __(
				'A Python expression checked against the document just before it runs, e.g. doc.status == "Open". If it\'s false, the action is skipped.'
			),
		},
		{
			fieldname: "skip_if_late_by",
			fieldtype: "Int",
			label: __("Skip if late by (minutes)"),
			non_negative: 1,
			description: __(
				"If the scheduler is behind and picks this up more than this many minutes late, skip it instead of running it."
			),
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
					condition: values.condition,
					skip_if_late_by: values.skip_if_late_by,
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
			args: { doctype: frm.doctype, name: frm.docname },
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
