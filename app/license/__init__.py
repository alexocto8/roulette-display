"""Hardware-bound license verification.

Deliberately isolated from `app/ui` and `app/services`: this package only ever reads (never
writes) hardware identifiers and a license file, and verifies a signature with a public key. It
has no dependency on pygame, sqlite, or anything else in this codebase — `main.py` calls it once,
before opening the database or the display, and either proceeds or shows the license screen.

See `app/license/hardware.py` (Device ID) and `app/license/verify.py` (signature/expiry checks).
The tool that *issues* licenses (`license-generator/`, holds the private key) lives outside this
package on purpose — see that directory's own README.
"""
