#!/usr/bin/env bash
# media-pipeline installer.
# Installs the skill into the agent's own skill directory, writes the MCP
# server manifest, creates the working layout, and enables the inbox trigger.
# Safe to re-run: every step is idempotent and existing files are backed up.
set -euo pipefail

PROJ="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SKILLS_DIR="${MP_SKILLS_DIR:-$HOME/.local/share/prime-agent/0.9.6/skills}"
USER_UNIT_DIR="$HOME/.config/systemd/user"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

say() { printf '%s\n' "$*"; }

say "media-pipeline installer"
say "project: $PROJ"

# 1) working layout
for d in inbox work outbox failures logs; do
  mkdir -p "$PROJ/$d"
done
say "layout ready"

# 2) skill into the agent's own skill directory
if [ -d "$SKILLS_DIR" ]; then
  mkdir -p "$SKILLS_DIR/media-pipeline/references"
  cp -f "$PROJ/skills/media-pipeline/SKILL.md" "$SKILLS_DIR/media-pipeline/SKILL.md"
  cp -f "$PROJ/skills/media-pipeline/references/"*.md "$SKILLS_DIR/media-pipeline/references/" 2>/dev/null || true
  say "skill installed -> $SKILLS_DIR/media-pipeline"
else
  say "WARN skill dir not found: $SKILLS_DIR (set MP_SKILLS_DIR)"
fi

# 3) MCP server manifest (stdio transport)
cat > "$PROJ/mcp-server.json" <<JSON
{
  "mcpServers": {
    "media-pipeline": {
      "command": "$PROJ/bin/mp-mcp",
      "args": [],
      "transport": "stdio"
    }
  }
}
JSON
say "mcp manifest -> $PROJ/mcp-server.json"

# 4) systemd user units: inbox path trigger
cat > "$USER_UNIT_DIR/mp-inbox.path" <<UNIT
[Unit]
Description=Watch the media-pipeline inbox for new media

[Path]
PathChanged=$PROJ/inbox
Unit=mp-inbox.service

[Install]
WantedBy=default.target
UNIT

cat > "$USER_UNIT_DIR/mp-inbox.service" <<UNIT
[Unit]
Description=Process media waiting in the media-pipeline inbox
After=default.target

[Service]
Type=oneshot
WorkingDirectory=$PROJ
ExecStart=$PROJ/bin/mp watch
TimeoutStartSec=1800
UNIT
say "systemd units written -> $USER_UNIT_DIR"

# 5) enable the trigger
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user enable --now mp-inbox.path || say "WARN could not enable mp-inbox.path"
  say "trigger enabled (mp-inbox.path)"
else
  say "WARN systemctl not available; enable mp-inbox.path manually"
fi

# 6) self test
"$PROJ/bin/mp" selftest || true
say "done"
