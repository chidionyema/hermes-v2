# Vetted third-party skills

Nothing is installed from the ecosystem until it is listed here, and a row is
only added after someone has read the whole thing.

The rule: a skill is a prompt with shell commands in it. Installing one is
running someone else's code with your credentials. Read every line, or do not
install it.

## Reviewed and installed

_(none yet)_

## Reviewed and rejected

_(none yet)_

## How to review one

1. Read `SKILL.md` end to end. Every command.
2. Ask what it would do with the `.env` in this directory.
3. Reject anything that curls a URL and pipes it to a shell.
4. Reject anything that reads credentials it does not need for its stated job.
5. Record the commit hash you reviewed. An update is a new review.
