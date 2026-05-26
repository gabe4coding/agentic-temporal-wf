# tf-mitigations

Two PreToolUse hooks delivered as a separate plugin so each can be
turned on/off independently.

## secret_scan
Refuses `Edit`/`MultiEdit`/`Write`/`Bash` whose payload matches a
conservative regex set: AWS access keys, GitHub PATs (classic +
fine-grained), SSH/OpenSSL private key headers, Anthropic keys. Defense
in depth alongside the egress proxy.

## signed_trailer_verify
Refuses `mcp__repo__git_commit_and_push` whose `message` lacks the
`[autofix-bot]` trailer. Stable trailer = stable operator handle for
rolling back agent-authored commits.
