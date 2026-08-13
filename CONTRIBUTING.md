# Contributing

Paperclock is deliberately narrow. A contribution should make date detection,
explanation, file support, accessibility, or privacy measurably better without
turning the project into a document platform.

## Before opening a pull request

1. Run `npm test` and `npm run lint`.
2. Add a small fixture or unit test for parser changes.
3. Keep result scoring explainable. If a rule cannot produce a plain-language
   reason, it probably does not belong in the core.
4. Do not add network calls, telemetry, accounts, or hosted inference.

The Python path is intentionally dependency-light. New packages need a concrete
reason that the standard library cannot reasonably cover.
