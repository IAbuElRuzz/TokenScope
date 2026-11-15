# Security policy

TokenScope is local-first. It reads only configured session roots and extracts usage metadata; it does not access auth files, send cloud telemetry, or collect prompt/response text. Source logs may contain sensitive data: parsed content is transiently in memory and must not be logged. Project basename and session/model identifiers remain personal metadata; review exports before sharing.

Default API binds localhost, requires a generated bearer token, rejects arbitrary Host/Origin headers, and authenticates WebSocket first frames. Token files and databases use restricted permissions where OS supports them. Do not expose the service to a public network; remote hosting/authentication/TLS is outside this release's supported threat model. Directory permissions on Windows depend on inherited ACLs; review user profile ACLs.

Report vulnerabilities privately through the repository's Security Advisory page: https://github.com/IAbuElRuzz/TokenScope/security/advisories/new . Do not include credentials or private logs in public issues. Only the latest 0.1.x source receives fixes. Dependencies must be reviewed before a production release; see validation limitations.
