# Session API

`POST /jobs` requires `session_id`. Session state is scoped to that identity; interleaved users
must not read or overwrite one another, and each user can retrieve their own saved value.
