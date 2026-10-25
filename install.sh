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

# 3) MCP server: manifest + agent kaydi (tanima = agent'in sunucuyu gormesi)
cat > "$PROJ/mcp-server.json" <<JSON
{
  "mcpServers": {
    "media-pipeline": {
      "type": "stdio",
      "command": "$PROJ/bin/mp-mcp",
      "cwd": "$PROJ"
    }
  }
}
JSON
say "mcp manifest -> $PROJ/mcp-server.json"

PRIME_AGENT_BIN="$(command -v prime-agent || true)"
if [ -z "$PRIME_AGENT_BIN" ]; then
  for cand in "$HOME/.local/share/prime-agent"/*/prime-agent; do
    [ -x "$cand" ] && PRIME_AGENT_BIN="$cand" && break
  done
fi
if [ -n "$PRIME_AGENT_BIN" ] && [ -x "$PRIME_AGENT_BIN" ]; then
  "$PRIME_AGENT_BIN" mcp add media-pipeline --force --cwd "$PROJ" -- "$PROJ/bin/mp-mcp" \
    && say "mcp registered -> \$HOME/.prime/agent/settings.json" \
    || say "WARN could not register the MCP server"
  "$PRIME_AGENT_BIN" mcp list | sed 's/^/  /'
else
  say "WARN prime-agent CLI not found; register manually:"
  say "  prime-agent mcp add media-pipeline --cwd $PROJ -- $PROJ/bin/mp-mcp"
fi

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

# 6) skill dosyalari tam mi?
for f in SKILL.md references/engines.md references/triggers.md references/recipes.md; do
  if [ -f "$SKILLS_DIR/media-pipeline/$f" ]; then
    say "  skill ok: $f"
  else
    say "  WARN skill missing: $f"
  fi
done

# 7) MCP sunucusu gercekten el koynuyor mu? (stdio handshake)
if "$PROJ/bin/mp-mcp" <<'JSON' 2>/dev/null | grep -q '"media_engines"'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
JSON
then
  say "mcp handshake ok (tools/list answered)"
else
  say "WARN mcp handshake failed"
fi

# 8) systemd durumu
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user is-enabled mp-inbox.path | sed 's/^/  mp-inbox.path: /' || true
  systemctl --user is-active mp-inbox.path | sed 's/^/  mp-inbox.path: /' || true
fi

# 9) self test
"$PROJ/bin/mp" selftest || true
say "done"
