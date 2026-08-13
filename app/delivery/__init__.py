"""Report delivery: e-mail (SMTP + a persistent retry queue, since the Pi is often offline) and
thermal-printer receipts. Deliberately outside the render loop and the keyboard event path — see
scripts/send_pending_reports.py for how the queue actually gets drained (a separate, periodically
invoked process, matching this project's existing pattern for background work — e.g.
scripts/backup.sh — rather than an in-process thread inside the single-threaded pygame app).
"""
