// Shared by schedule_menu.js's Dialog and the Scheduled Action doctype's own
// form script (scheduled_action.js) - both need the same "pick a field, get
// a natively-typed Value control constrained to that field's own rules"
// behavior, and both are built on frappe.ui.form.Layout (Dialog is a
// FieldGroup, which extends Layout directly) so the same field defs and
// sync logic work unmodified in either place. Kept as one implementation so
// the two entry points can't quietly drift apart.
frappe.provide("scheduled_actions.value_control");

// Target fieldtypes that get a dedicated, natively-typed mirror field;
// anything else (Data, Text, ...) falls back to the plain text one - the
// "text" category. Dynamic Link shares the "link" category/mirror with
// plain Link - same control, the only difference is *how* its target
// doctype is known (see apply_field_pick's link_doctype_override param).
scheduled_actions.value_control.CATEGORY_BY_FIELDTYPE = {
	Select: "select",
	Link: "link",
	"Dynamic Link": "link",
	Check: "check",
	Date: "date",
	Datetime: "datetime",
	Time: "time",
	Color: "color",
	Duration: "duration",
	Int: "number",
	Float: "number",
	Currency: "number",
	Percent: "number",
};

scheduled_actions.value_control.MIRROR_FIELD_BY_CATEGORY = {
	select: "field_value_select",
	link: "field_value_link",
	check: "field_value_check",
	date: "field_value_date",
	datetime: "field_value_datetime",
	time: "field_value_time",
	color: "field_value_color",
	duration: "field_value_duration",
	number: "field_value_number",
};

scheduled_actions.value_control.category_for_fieldtype = function (fieldtype) {
	return scheduled_actions.value_control.CATEGORY_BY_FIELDTYPE[fieldtype] || "text";
};

scheduled_actions.value_control.cast_for_mirror = function (category, raw) {
	if (category === "check") return cint(raw);
	if (category === "number" || category === "duration") return flt(raw);
	return raw;
};

// Field defs for every mirror plus the hidden category field that drives
// their depends_on - splice these into a Form's field_order/Dialog's fields
// right after field_name. field_value (the plain-text fallback / the actual
// persisted column) is assumed to already exist wherever this is used.
//
// depends_on gates on *both* action_type and target_fieldtype, not just the
// latter: without the action_type half, switching the action away from
// "Set Field" after already picking e.g. a Select field would leave that
// mirror visible (its target_fieldtype doesn't get reset just because
// action_type changed). depends_on and mandatory_depends_on are
// deliberately identical - a mirror is only ever relevant, at all, exactly
// when it's also required - except Check, which is always a valid value
// (0) and so is never "mandatory".
scheduled_actions.value_control.mirror_field_defs = function () {
	const visible_when = (category) =>
		`eval:doc.action_type=="Set Field" && doc.target_fieldtype=="${category}"`;

	return [
		{
			fieldname: "target_fieldtype",
			fieldtype: "Data",
			hidden: 1,
			default: "text",
		},
		{
			fieldname: "field_value_select",
			fieldtype: "Select",
			label: __("New Value"),
			depends_on: visible_when("select"),
			mandatory_depends_on: visible_when("select"),
		},
		{
			fieldname: "field_value_link",
			fieldtype: "Link",
			label: __("New Value"),
			options: "[Select]",
			depends_on: visible_when("link"),
			mandatory_depends_on: visible_when("link"),
		},
		{
			fieldname: "field_value_check",
			fieldtype: "Check",
			label: __("New Value"),
			depends_on: visible_when("check"),
		},
		{
			fieldname: "field_value_date",
			fieldtype: "Date",
			label: __("New Value"),
			depends_on: visible_when("date"),
			mandatory_depends_on: visible_when("date"),
		},
		{
			fieldname: "field_value_datetime",
			fieldtype: "Datetime",
			label: __("New Value"),
			depends_on: visible_when("datetime"),
			mandatory_depends_on: visible_when("datetime"),
		},
		{
			fieldname: "field_value_number",
			fieldtype: "Float",
			label: __("New Value"),
			depends_on: visible_when("number"),
			mandatory_depends_on: visible_when("number"),
		},
		{
			fieldname: "field_value_time",
			fieldtype: "Time",
			label: __("New Value"),
			depends_on: visible_when("time"),
			mandatory_depends_on: visible_when("time"),
		},
		{
			fieldname: "field_value_color",
			fieldtype: "Color",
			label: __("New Value"),
			depends_on: visible_when("color"),
			mandatory_depends_on: visible_when("color"),
		},
		{
			fieldname: "field_value_duration",
			fieldtype: "Duration",
			label: __("New Value"),
			depends_on: visible_when("duration"),
			mandatory_depends_on: visible_when("duration"),
		},
	];
};

// `host` is anything with the FieldGroup surface Form and Dialog both share
// (get_field/set_value/set_df_property - Dialog extends FieldGroup, which
// Layout provides the same three for). Deliberately read current values via
// get_field(...).get_value() rather than host.doc: Form keeps a live `.doc`,
// but a bare Dialog doesn't, so get_field() is the one lookup that actually
// works on both.
function current_value_of(host, fieldname) {
	const field = host.get_field(fieldname);
	return field ? field.get_value() : undefined;
}

// `df` is the picked field's metadata (from get_settable_fields:
// fieldname/label/fieldtype/options). `current_value` (optional) prefills
// the matching mirror - the picker's whole point is to default to "what's
// there now" so the common case is a small edit, not a blank form.
// `link_doctype_override` is for Dynamic Link fields only: unlike a plain
// Link, df.options isn't the target doctype itself but the *fieldname*
// that holds it - the caller resolves that (see
// api.resolve_dynamic_link_doctype) and passes the actual doctype name in.
scheduled_actions.value_control.apply_field_pick = function (host, df, current_value, link_doctype_override) {
	const category = df ? scheduled_actions.value_control.category_for_fieldtype(df.fieldtype) : "text";

	if (category === "select") {
		host.set_df_property("field_value_select", "options", df.options || "");
	}
	if (category === "link") {
		const link_doctype = df.fieldtype === "Dynamic Link" ? link_doctype_override : df.options;
		host.set_df_property("field_value_link", "options", link_doctype || "[Select]");
	}

	const switching = current_value_of(host, "target_fieldtype") !== category;
	host.set_value("target_fieldtype", category);

	if (switching) {
		Object.values(scheduled_actions.value_control.MIRROR_FIELD_BY_CATEGORY).forEach((f) =>
			host.set_value(f, "")
		);
		host.set_value("field_value", "");
	}

	const mirror = scheduled_actions.value_control.MIRROR_FIELD_BY_CATEGORY[category];
	if (mirror && current_value !== null && current_value !== undefined && current_value !== "") {
		host.set_value(mirror, scheduled_actions.value_control.cast_for_mirror(category, current_value));
	}
};

// Call from each mirror field's own change handler - keeps the single
// persisted field_value column in lockstep with whichever mirror is
// currently the active/visible one.
scheduled_actions.value_control.sync_mirror_to_field_value = function (host, category) {
	if (current_value_of(host, "target_fieldtype") !== category) return;
	const mirror = scheduled_actions.value_control.MIRROR_FIELD_BY_CATEGORY[category];
	const value = current_value_of(host, mirror);
	host.set_value("field_value", value === 0 || value === false ? String(value) : value || "");
};
