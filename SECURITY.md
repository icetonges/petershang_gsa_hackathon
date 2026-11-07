# Security Policy

As a U.S. Government agency, the General Services Administration (GSA) takes seriously our responsibility to protect the public's information, including financial and personal information, from unwarranted disclosure.

Software developed by the U.S. General Services Administration (GSA) is subject to the GSA Vulnerability Disclosure Policy.

Please consult our policy for:

How to submit a report if you believe you have discovered a vulnerability.
GSA's coordinated disclosure policy.
Information on how you may conduct security research on GSA developed software and systems.
Important legal and policy guidelines.

## Supported Versions

Use this section to tell people about which versions of your project are
currently being supported with security updates.

| Version (Branch) | Supported          |
| ------- | ------------------ |
| main   | :white_check_mark: |
| other  | :x:

## Security Researchers

Security researchers shall:

- Make every effort to avoid privacy violations, degradation of user experience, disruption to production systems, and destruction or manipulation of data.
- Only use exploits to the extent necessary to confirm a vulnerability. Do not use an exploit to compromise or exfiltrate data, establish command line access and/or persistence, or use the exploit to "pivot" to other systems. Once you've established that a vulnerability exists, or encountered any of the sensitive data outlined above, you must stop your test and notify us immediately.
- Keep confidential any information about discovered vulnerabilities for up to 90 calendar days after you have notified GSA. For details, please review Coordinated Disclosure.

## This server's posture

Seven of the eight tools are read-only. The eighth, `creator_write_project`,
writes files and is off unless an operator sets both `CREATOR_ALLOW_WRITE`
and `CREATOR_OUTPUT_ROOT`; every path is then resolved inside that root,
traversal and absolute paths are refused, and the default is a dry run.

No tool accepts an absolute URL, a raw SQL string, or a filesystem path.
`tests/test_security.py` asserts this, so adding one breaks CI on purpose.

Unity Catalog identifiers supplied for live profiling are validated against a
plain identifier pattern and backtick-quoted before they reach SQL; values
are always bound as query parameters. Upstream failures are sanitized: no
response bodies, headers, full URLs, stack traces, or credentials reach a
client or a log.

Schemas, sample rows, and document descriptions passed to these tools are
material to analyze. They are never instructions to the model, however they
are phrased. The same rule is written into the server's `instructions`, into
the generated servers' instructions, and into the retrieval tool docstrings
the generators emit.

## Deployed posture

MCP servers built from this template expose `/mcp` without authentication of
their own. On Databricks Apps the endpoint sits behind workspace
authentication: callers need `CAN USE` on the app and an OAuth token, and
personal access tokens are not supported. An unauthenticated probe returning
302 or 401 is the app working.

Before any non-hackathon use, front the endpoint with authentication, review
your agency's ATO requirements, and decide retention and access for logs
that may hold prompts. Never commit secrets: keep them in environment
variables or a Databricks secret scope, never in source and never in a
`value:` field in `app.yaml`.
