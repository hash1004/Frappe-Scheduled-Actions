app_name = "scheduled_actions"
app_title = "Scheduled Actions"
app_publisher = "Abdul Hannan"
app_description = "Schedule a document field change or submit/cancel for a future date and time"
app_email = "sahannan96@gmail.com"
app_license = "MIT"

# Desk assets
# -----------
# value_control.js must load before schedule_menu.js - the latter calls into
# the scheduled_actions.value_control namespace the former defines.
app_include_css = "/assets/scheduled_actions/css/schedule_sidebar.css"
app_include_js = [
	"/assets/scheduled_actions/js/value_control.js",
	"/assets/scheduled_actions/js/schedule_menu.js",
]

# Document events
# ---------------
# Fired on every doctype (the schedule UI works anywhere):
#  - on_update / on_cancel: a real change to a document with a Pending
#    Scheduled Action cancels that action rather than blocking the edit.
#  - on_trash: drop a deleted document's Scheduled Actions so its
#    reference_name Dynamic Link doesn't block the delete.
doc_events = {
	"*": {
		"on_update": "scheduled_actions.utils.cancel_pending_action_on_change",
		"on_cancel": "scheduled_actions.utils.cancel_pending_action_on_change",
		"on_trash": "scheduled_actions.utils.clear_actions_on_target_delete",
	}
}

# Scheduled tasks
# ---------------
# run_due_actions runs every tick (it only looks up what's due and hands each
# off to a background worker); cleanup_old_actions clears finished rows daily.
scheduler_events = {
	"cron": {"* * * * *": ["scheduled_actions.tasks.run_due_actions"]},
	"daily": ["scheduled_actions.tasks.cleanup_old_actions"],
}
