"""PostgreSQL-leased execution and verification work (ENG-015).

Reuses the proven local attempt pipeline (aieb_runner.lifecycle) and its
existing contracts; this package adds only the leasing, fencing, and
crash-recovery layer around it - no separate hosted scoring logic.
"""
