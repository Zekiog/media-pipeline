# Triggers

## 1. Inbox watch (systemd path unit)

    /home/opc/.config/systemd/user/mp-inbox.path
    /home/opc/.config/systemd/user/mp-inbox.service

The path unit watches `inbox/` for `PathChanged` and runs `mp watch`, which
processes every file in the inbox and moves it to `outbox/` or `failures/`.
No inotify-tools package is needed: systemd path units use the kernel
inotify interface directly.

## 2. Telegram bridge

`prime-tg` receives Telegram updates on 127.0.0.1:8787 through the HAProxy
hop at 150.136.227.159. A photo, document, video or audio attachment is
downloaded with `getFile` and written into `inbox/`. The path unit above then
does the rest, so there is one processing path for every source.

## 3. Manual

    mp watch

## 4. Agent and MCP surface

The MCP server is registered in the user settings of the agent, not only in the
repository manifest:

    /home/opc/.prime/agent/settings.json
      "mcpServers": { "media-pipeline": { "type": "stdio", "command": ".../bin/mp-mcp" } }

Tools: `media_engines`, `media_probe`, `media_process`, `media_translate`,
`media_batch`. A changed registration is picked up by a new session; the live
server reports `connected` with `source: user`.

Check the whole surface at any time:

    mp doctor

## Ordering and failure handling

- Files are processed oldest first.
- A file that fails goes to `failures/` and the reason is in the manifest.
- Output files are named `<epoch>_<stem>` so repeated runs never collide.
- Nothing is deleted from the inbox until the result exists in the outbox.
