// Pure-logic tests for value_control.js - the field-category map, the mirror
// field defs, and the mirror <-> field_value sync. No browser, no Frappe:
// `node --test` (built in, zero deps). Run: `npm test` from apps/scheduled_actions.
//
// This covers the two bugs this file has actually shipped: a fieldtype with
// no mirror category, and sync_mirror_to_field_value not reading
// target_fieldtype from a Form's `.doc` (Color/Time/Duration silently not
// persisting - see the [Form] cases below). The server side and the
// execution engine have their own Python suite; full browser behaviour is
// smoke-tested by hand.

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

// --- the handful of globals value_control.js leans on ---
global.frappe = {
	provide(namespace) {
		let obj = global;
		for (const part of namespace.split(".")) obj = obj[part] = obj[part] || {};
		return obj;
	},
};
global.__ = (s) => s;
global.cint = (v) => parseInt(v, 10) || 0;
global.flt = (v) => parseFloat(v) || 0;

// eslint-disable-next-line no-eval
eval(fs.readFileSync(path.join(__dirname, "value_control.js"), "utf8"));
const vc = global.scheduled_actions.value_control;

// A stand-in for the Form/Dialog surface. `asForm` mirrors a real Form:
// host.doc is the live model, and get_field() for a hidden control comes
// back with no value - which is exactly what broke the sync before.
function makeHost({ asForm = false } = {}) {
	const store = { target_fieldtype: "text", field_value: "" };
	return {
		doc: asForm ? store : undefined,
		get_field: (fn) => ({
			get_value: () => (asForm && fn === "target_fieldtype" ? undefined : store[fn]),
		}),
		set_value: (fn, v) => {
			store[fn] = v;
		},
		set_df_property: (fn, prop, v) => {
			(store.__df ??= {})[`${fn}.${prop}`] = v;
		},
		store,
	};
}

test("category_for_fieldtype maps known types, falls back to text", () => {
	assert.equal(vc.category_for_fieldtype("Color"), "color");
	assert.equal(vc.category_for_fieldtype("Dynamic Link"), "link");
	assert.equal(vc.category_for_fieldtype("Currency"), "number");
	assert.equal(vc.category_for_fieldtype("Data"), "text");
	assert.equal(vc.category_for_fieldtype("Text Editor"), "text");
});

test("every non-text category has a mirror field, and a def for it", () => {
	const categories = new Set(Object.values(vc.CATEGORY_BY_FIELDTYPE));
	const defNames = new Set(vc.mirror_field_defs().map((d) => d.fieldname));
	for (const category of categories) {
		const mirror = vc.MIRROR_FIELD_BY_CATEGORY[category];
		assert.ok(mirror, `no MIRROR_FIELD_BY_CATEGORY entry for "${category}"`);
		assert.ok(defNames.has(mirror), `mirror_field_defs() is missing "${mirror}"`);
	}
});

test("cast_for_mirror coerces check and numeric, passes the rest through", () => {
	assert.equal(vc.cast_for_mirror("check", "1"), 1);
	assert.equal(vc.cast_for_mirror("number", "3.5"), 3.5);
	assert.equal(vc.cast_for_mirror("duration", "90"), 90);
	assert.equal(vc.cast_for_mirror("color", "#ABCDEF"), "#ABCDEF");
});

for (const asForm of [false, true]) {
	const label = asForm ? "Form" : "Dialog";

	test(`[${label}] sync copies the active mirror into field_value`, () => {
		const host = makeHost({ asForm });
		host.set_value("target_fieldtype", "color");
		host.set_value("field_value_color", "#0055FF");

		vc.sync_mirror_to_field_value(host, "color");

		assert.equal(host.store.field_value, "#0055FF");
	});

	test(`[${label}] sync ignores a mirror that isn't the active category`, () => {
		const host = makeHost({ asForm });
		host.set_value("target_fieldtype", "select");
		host.set_value("field_value_select", "Open");
		host.set_value("field_value", "untouched");

		vc.sync_mirror_to_field_value(host, "color");

		assert.equal(host.store.field_value, "untouched");
	});

	test(`[${label}] sync stringifies a 0 / false check value`, () => {
		const host = makeHost({ asForm });
		host.set_value("target_fieldtype", "check");
		host.set_value("field_value_check", 0);

		vc.sync_mirror_to_field_value(host, "check");

		assert.equal(host.store.field_value, "0");
	});

	test(`[${label}] apply_field_pick sets target_fieldtype and prefills the mirror`, () => {
		const host = makeHost({ asForm });

		vc.apply_field_pick(host, { fieldtype: "Color" }, "#ABCDEF");

		assert.equal(host.store.target_fieldtype, "color");
		assert.equal(host.store.field_value_color, "#ABCDEF");
	});

	test(`[${label}] apply_field_pick clears the old mirror when the category changes`, () => {
		const host = makeHost({ asForm });
		vc.apply_field_pick(host, { fieldtype: "Select", options: "Open\nClosed" }, "Open");
		assert.equal(host.store.field_value_select, "Open");

		vc.apply_field_pick(host, { fieldtype: "Color" }, "#111111");

		assert.equal(host.store.field_value_select, "");
		assert.equal(host.store.target_fieldtype, "color");
		assert.equal(host.store.field_value_color, "#111111");
	});
}
