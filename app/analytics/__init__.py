"""Operational/statistical analytics — a read-only layer over data already persisted in SQLite.
Never computes anything from the keyboard event path (see app/services/spin_service.py); the
heaviest computation here (chi-square over a 100k-row lifetime window) is still cheap enough to
run on demand from the admin panel, but is never called from the render loop.
"""
