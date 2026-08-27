### Scheduled Actions

Schedule a document's **Submit**, **Cancel**, or a single **field change**
for a future date and time - directly from any document's form, no custom
code required.

"Cancel this quote if it's not accepted by Friday." "Flip this task to
Closed next Monday morning." "Submit this leave application the moment it
becomes effective." Say the word, and Scheduled Actions handles the rest:
it fires shortly after the scheduled time (see **Timing** below), as the
user who scheduled it, and re-checks that everything's still valid right
before it runs.

#### Highlights

- **Works on any doctype** - open the "Schedule..." action in the form
  sidebar on whichever document you're already on, no per-doctype setup. (A
  small, sensible denylist keeps this off security-critical doctypes like
  User, Role, and System Settings, and off Single doctypes, which don't
  fit the model.)
- **Real, typed value controls** - picking a field to change shows a
  proper Select dropdown, Link/Dynamic Link picker, checkbox, date/time/
  color/duration control, and so on, prefilled with that field's current
  value - never an open text box guessing at the right format.
- **Runs as the scheduling user, never as Administrator** - and re-checks
  that user's doctype- and field-level permissions again right before
  firing, since they can change between scheduling and execution.
- **Edits win over a stale schedule** - the document stays fully editable
  while an action is pending; the form shows a heads-up, and actually
  changing the document cancels the pending action (notifying whoever
  scheduled it) rather than letting something unexpected fire later.
- **Automated changes are marked** - the target document's timeline shows
  the change "via Scheduled Action" plus a note that it ran automatically,
  so it never looks like someone edited it by hand.
- **Never fires twice, and recovers from a lost worker** - an atomic claim
  guards against overlapping scheduler ticks; an action left mid-execution
  by a killed worker (OOM, restart, deploy) is failed and flagged for
  retry rather than stuck forever.
- **Doesn't block the scheduler** - each scheduler pass only looks up
  what's due and hands it to a background worker; the action itself runs
  off the scheduler's critical path.
- **Failures explain themselves** - a failed action shows a
  plain-language reason ("Document is not in Draft state"), not just a
  traceback, and the notification says why. The full log is kept too, for
  whoever wants it.
- **Manual retry** on a failed action, right from its own form; once an
  action has run, its record is read-only.
- **Cleans up after itself** - finished actions (Executed/Failed/
  Cancelled) older than 90 days are cleared automatically; anything still
  Pending is never touched, regardless of age.
- **Full per-user timezone handling** - courtesy of Frappe's own Datetime
  field, what you type and see is always in your own timezone.

#### How it works

1. Open any document, click **Schedule...** in the form sidebar, and pick
   **Submit**, **Cancel**, or **Set Field**.
2. For a field change, pick the field - the value control adapts to that
   field's type automatically.
3. Pick a date and time, and confirm.
4. On the first scheduler pass at or after that time, the action runs as
   you (not as Administrator), and you get a notification either way.

Every scheduled action is also its own document (**Scheduled Action**),
listed and manageable like any other record - cancel a pending one, retry
a failed one, or just see what's coming up.

#### Timing

`scheduled_for` is a "not before" time, not an alarm clock. Actions run on
the next scheduler pass at or after it - so the lag is however often the
Frappe scheduler ticks, which defaults to **every 4 minutes**. To tighten
that up, set a shorter tick in `common_site_config.json`:

```json
{ "scheduler_tick_interval": 60 }
```

That brings it to about a minute. (This is the same mechanism behind
Frappe's own Reminders and time-based Notifications, which poll every 15
and 5 minutes respectively - sub-minute precision isn't something the
Frappe scheduler offers.) If you schedule a lot of actions on a busy
bench, consider giving them a dedicated worker queue so they don't wait
behind other background jobs.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO
bench install-app scheduled_actions
```

### Testing

This app ships with an automated test suite. Tests need to be enabled on
the site once:

```bash
bench --site $SITE_NAME set-config allow_tests true
bench --site $SITE_NAME run-tests --app scheduled_actions
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/scheduled_actions
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
