# tf-mitigations

Sandbox-side PreToolUse mitigations delivered as a separate plugin.

## secret_scan
Refuses `Edit`/`MultiEdit`/`Write`/`Bash` whose payload matches a
conservative regex set: AWS access keys, GitHub PATs (classic +
fine-grained), SSH/OpenSSL private key headers, Anthropic keys. Defense
in depth alongside the egress proxy.

## Publication Trailer
Publication is not available in the sandbox tool surface. Trusted workflow
activities stamp the
`[autofix-bot]` trailer. Stable trailer = stable operator handle for
rolling back agent-authored commits.
