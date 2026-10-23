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

## Ordering and failure handling

- Files are processed oldest first.
- A file that fails goes to `failures/` and the reason is in the manifest.
- Output files are named `<epoch>_<stem>` so repeated runs never collide.
- Nothing is deleted from the inbox until the result exists in the outbox.
