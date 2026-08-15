# Security policy

Paperclock processes private documents locally. The browser talks only to a
loopback Python service, and document contents are not sent to Paperclock or a
third-party API.

## Built-in safeguards

- The local API validates Host and Origin headers and accepts JSON mutations only.
- Requests and individual documents have size limits.
- Office XML uses an entity-safe parser; compressed entries have expansion limits.
- Hidden, developer, and unsupported files are rejected before parsing.
- The local SQLite index is restricted to the current operating-system account.
- Calendar exports escape user-controlled values and generate safe event IDs.

Paperclock should still be run as an ordinary user, not as root. A malicious
process already running as the same operating-system account is outside the
application's isolation boundary.

## Reporting a vulnerability

Please use the repository's private security-advisory channel when available.
Include the affected version, a minimal reproduction, and the expected impact.
Do not attach real policies, statements, identity documents, or other private
user files; construct a synthetic sample instead.

Avoid publishing exploit details until a fix is available.
