#!/usr/bin/env bash
set -euo pipefail

# Ambient Alfred — Installation Script
# Sets up Python venv, installs dependencies, and registers the plugin.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
EXTENSIONS_DIR="${OPENCLAW_EXTENSIONS_DIR:-$HOME/.openclaw/extensions}"
PLUGIN_ID="ambient-alfred"

echo "========================================"
echo "  Ambient Alfred — Plugin Installer"
echo "========================================"
echo ""

# --- 1. Python venv ---
echo "[1/4] Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  Created venv at $VENV_DIR"
else
    echo "  Venv already exists at $VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

echo "  Installing receiver dependencies..."
pip install -q -r "$SCRIPT_DIR/receiver/requirements.txt"

echo "  Installing pipeline dependencies..."
pip install -q -r "$SCRIPT_DIR/pipeline/requirements.txt"

echo "  Done."
echo ""

# --- 2. Download Silero VAD model (so first run doesn't block) ---
echo "[2/4] Pre-downloading Silero VAD model..."
python3 -c "
import torch
torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)
print('  Silero VAD model cached.')
" 2>/dev/null || echo "  Warning: Could not pre-download VAD model (will download on first use)"
echo ""

# --- 3. Register with OpenClaw ---
echo "[3/4] Registering plugin with OpenClaw..."

# Option A: symlink into extensions dir
PLUGIN_DEST="$EXTENSIONS_DIR/$PLUGIN_ID"
if [ -L "$PLUGIN_DEST" ] || [ -d "$PLUGIN_DEST" ]; then
    echo "  Plugin already registered at $PLUGIN_DEST"
else
    mkdir -p "$EXTENSIONS_DIR"
    ln -s "$SCRIPT_DIR" "$PLUGIN_DEST"
    echo "  Symlinked $SCRIPT_DIR -> $PLUGIN_DEST"
fi

# Option B: use openclaw CLI if available
if command -v openclaw &>/dev/null; then
    echo "  Enabling plugin via openclaw CLI..."
    openclaw plugins enable "$PLUGIN_ID" 2>/dev/null || true
fi
echo ""

# --- 4. Launchd plist (macOS) or systemd unit (Linux) ---
echo "[4/4] Creating system service..."

if [[ "$(uname)" == "Darwin" ]]; then
    PLIST_NAME="com.ambient-alfred.receiver"
    PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

    if [ -f "$PLIST_PATH" ]; then
        echo "  Launchd plist already exists at $PLIST_PATH"
    else
        cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python</string>
        <string>-m</string>
        <string>receiver.run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/logs/receiver-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/logs/receiver-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST
        mkdir -p "$SCRIPT_DIR/logs"
        echo "  Created launchd plist: $PLIST_PATH"
        echo "  Load with: launchctl load $PLIST_PATH"
    fi

elif [[ "$(uname)" == "Linux" ]]; then
    UNIT_NAME="ambient-alfred-receiver"
    UNIT_PATH="$HOME/.config/systemd/user/$UNIT_NAME.service"

    if [ -f "$UNIT_PATH" ]; then
        echo "  Systemd unit already exists at $UNIT_PATH"
    else
        mkdir -p "$(dirname "$UNIT_PATH")"
        cat > "$UNIT_PATH" <<UNIT
[Unit]
Description=Ambient Alfred Audio Receiver
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_DIR/bin/python -m receiver.run
Restart=always
RestartSec=5
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
UNIT
        echo "  Created systemd unit: $UNIT_PATH"
        echo "  Enable with: systemctl --user enable --now $UNIT_NAME"
    fi
fi

echo ""
echo "========================================"
echo "  Installation complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo ""
echo "  1. Configure environment variables:"
echo "     - ALFRED_TRANSCRIPTION_API_KEY  (your AssemblyAI or OpenAI key)"
echo "     - OPENROUTER_API_KEY            (for command detection)"
echo "     - ALFRED_TRANSCRIPTS_DIR        (default: ./transcripts)"
echo "     - ALFRED_VAULT_INBOX_DIR        (default: ~/vault/inbox)"
echo ""
echo "  2. Set Omi webhook URL to:"
echo "     http://<your-ip>:8080/audio?uid=omi&sample_rate=16000"
echo ""
echo "  3. Start the services:"
echo "     - Via OpenClaw: restart the gateway (plugin starts automatically)"
echo "     - Standalone receiver: $VENV_DIR/bin/python -m receiver.run"
echo "     - Standalone pipeline: $VENV_DIR/bin/python -m pipeline.watcher"
echo ""
echo "  4. Test: curl http://localhost:8080/health"
echo ""
