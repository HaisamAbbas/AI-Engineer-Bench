# Corrected tool arguments API

`POST /jobs` accepts `draft`, `correct`, and `execute` actions with a session and target.
An execute action uses the most recent corrected target; the original target remains untouched.
