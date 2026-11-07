# Triggers

## Inbox watch

`install.sh` writes:

    ~/.config/systemd/user/mp-inbox.path
    ~/.config/systemd/user/mp-inbox.service

The path unit uses `PathChanged` on `inbox/` and starts `mp watch`. systemd path units use inotify. No inotify-tools package is required.

`systemd/mp-inbox.path` and `systemd/mp-inbox.service` in the repo are the same units with `/home/opc/work/prime/media-pipeline` filled in. The service `install.sh` writes also sets `After=default.target`. `ExecStart` is `bin/mp watch`. `TimeoutStartSec` is 1800.

## Manual

    bin/mp watch

## What watch does

- Non-hidden files in `inbox/` run in sorted filename order.
- A job directory is `outbox/<epoch>_<stem>/` and contains `manifest.json`.
- After a successful job, the input file moves to `outbox/`.
- After an error, the input file moves to `failures/`.
- A sha256 already listed in `work/processed.json` stays in `inbox/`.

## MCP

`bin/mp-mcp` tools: `media_engines`, `media_probe`, `media_process`, `media_translate`, `media_batch`.

When a `prime-agent` binary is installed, `install.sh` runs `prime-agent mcp add`. `mp doctor` then looks for that entry in `~/.prime/agent/settings.json`. It also looks for `$HOME/bin/prime-tg`. That program is not in this repository, and a missing file is a critical `doctor` failure.

    bin/mp doctor
