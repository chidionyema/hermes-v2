# Vetted third-party skills

Nothing is installed from the ecosystem until it is listed here, and a row is
only added after someone has read the whole thing.

The rule: a skill is a prompt with shell commands in it. Installing one is
running someone else's code with your credentials. Read every line, or do not
install it.

## Reviewed and installed

_(none yet)_

## Not installed - could not be found

Searched 2026-08-22 with `hermes skills search` and `hermes skills inspect`.
The hub is reachable and returns results for other queries, so this is not a
connectivity problem: these names do not resolve.

| skill | spec | result |
|---|---|---|
| oh-my-hermes | §4b, WORK | no match in any source |
| execplan-skill | §4b, WORK | no match |
| hermes-agent-acp-skill | §4b, WORK | no match |
| rtk-hermes | §4b, WORK and WATCH | no match |
| hermes-web-search-plus | §4b, WORK | no match |
| agenttrace | §4b, WORK | no match |
| lintlang | §4b, WORK | no match |
| drawio-skill | §4b, WORK | hub returns 25 results for "drawio", none named this |
| hermes-ai-infrastructure-monitoring-toolkit | §4b, WATCH | no match |

Installing something with a similar name would break the first rule of this
file. These stay uninstalled until the real identifier is known.

## Reviewed and rejected

_(none yet)_

## How to review one

1. Read `SKILL.md` end to end. Every command.
2. Ask what it would do with the `.env` in this directory.
3. Reject anything that curls a URL and pipes it to a shell.
4. Reject anything that reads credentials it does not need for its stated job.
5. Record the commit hash you reviewed. An update is a new review.
