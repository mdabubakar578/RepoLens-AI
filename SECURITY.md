# Security Policy

## Scope

RepoLens AI is an academic repository-analysis application. It accepts public repository URLs and text git logs. Its investigator tools read indexed evidence only; they do not execute or modify submitted repository code.

## Supported version

Security fixes are applied to the current main development version. No long-term support release is maintained.

## Reporting a vulnerability

Report a suspected vulnerability privately to the repository owner or project guide before publishing details. Include:

- affected route or component;
- reproduction steps;
- expected and observed behavior;
- potential impact;
- a minimal proof of concept with secrets removed.

Do not include API keys, private repository content, personal information, or destructive payloads.

## Implemented controls

- HTTP/HTTPS and repository-host allowlist;
- rejection of URL credentials and malformed repository paths;
- sanitized text/log uploads with size limits;
- bounded pasted input and question length;
- network and model timeouts;
- read-only agent tools and action limit;
- environment-based secrets;
- safe client error messages and server-side logging;
- security response headers;
- ignored local environment, database, and cache artifacts;
- stale background-task recovery.

## Known boundaries

- Public repositories only; no private OAuth permission model.
- SQLite and local indexes are designed for a single deployment instance.
- Git clone and GitHub API access still process untrusted public metadata and must retain timeouts and file limits.
- Generated prose can be incorrect even when evidence is supplied; citations and warnings must remain visible.
- The current content-security policy is intentionally minimal because templates contain inline scripts. Moving scripts to static files with nonces would permit a stricter policy.

Never commit the local environment file, Gemini key, GitHub token, application secret, database, or repository cache.
