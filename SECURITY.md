# Security Policy

## Supported versions

This project is developed on `main`. Fixes are applied there; there are no
maintained release branches.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Use GitHub's
private reporting instead: go to the repository's **Security** tab →
**Report a vulnerability**.

Please include what you were doing, what happened, and how to reproduce it.
You can expect an initial response within about a week.

## Scope notes

Things worth reporting:

* A way to get untrusted content from the pipeline to execute in the browser.
  Occupation titles and model rationales come from third-party data and a
  language model, and the site treats both as untrusted text — a path that
  bypasses that is a bug.
* A way to make the extract stage download from, or send data to, a host other
  than `jobsandskills.gov.au`.
* Anything that could leak `ANTHROPIC_API_KEY` into a file, log line or
  committed artefact.

Not in scope: the accuracy of the AI exposure scores, and vulnerabilities in
GitHub Pages or the Claude API themselves.

## Handling credentials

`ANTHROPIC_API_KEY` is read from the environment and is never written to any
output file. `.env` files are gitignored. If you believe a key has been exposed,
rotate it in the [Claude Console](https://platform.claude.com/) immediately.
