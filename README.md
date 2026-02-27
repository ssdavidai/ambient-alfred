# Ambient Alfred

Always-on ambient transcript pipeline for [Omi](https://www.omi.me/) wearable devices. Captures audio, transcribes it, groups conversations, detects voice commands, and writes structured notes to your vault.

## Architecture

```
  Omi Device                    Ambient Alfred
  ──────────                    ──────────────
                                ┌─────────────────────────────────┐
  PCM16 audio ──HTTP POST──►    │  RECEIVER (FastAPI :8080)       │
                                │                                 │
                                │  /audio?uid=X&sample_rate=16000 │
                                │       │                         │
                                │       ▼                         │
                                │  Silero VAD (speech detection)  │
                                │       │                         │
                                │       ▼                         │
                                │  SmartChunker (state machine)   │
                                │  IDLE → SPEECH → SILENCE → fin  │
                                │       │                         │
                                │       ▼                         │
                                │  SegmentQueue (disk-backed)     │
                                │       │                         │
                                │       ▼                         │
                                │  Transcription Client           │
                                │  (AssemblyAI/Whisper/OpenAI)    │
                                │       │                         │
                                │       ▼                         │
                                │  TranscriptStorage              │
                                │  transcripts/YYYY-MM-DD/*.json  │
                                └──────────┬──────────────────────┘
                                           │
                                           │  filesystem events
                                           ▼
                                ┌─────────────────────────────────┐
                                │  PIPELINE (watcher)             │
                                │                                 │
                                │  Watchdog filesystem observer   │
                                │       │                         │
                                │       ▼                         │
                                │  Segment grouping (10min gap)   │
                                │  Debounce (2min wait)           │
                                │       │                         │
                                │       ▼                         │
                                │  Command Detection              │
                                │  (OpenRouter LLM classifier)    │
                                │       │                         │
                                │  ┌────┴────┐                    │
                                │  ▼         ▼                    │
                                │ Command   Conversation          │
                                │  │         │                    │
                                │  ▼         ▼                    │
                                │ Spawn     Write to vault inbox  │
                                │ SubAgent  (markdown + YAML)     │
                                │  │         │                    │
                                │  ▼         ▼                    │
                                │ Execute   Notify (Slack, etc.)  │
                                └─────────────────────────────────┘
```

## Prerequisites

- **Omi wearable device** — pushes audio via HTTP
- **Python 3.11+** — for the receiver and pipeline
- **API keys** (at least one):
  - AssemblyAI, OpenAI, or a Whisper-compatible server for transcription
  - OpenRouter API key for command detection (optional)
- **OpenClaw** — for plugin integration, subagent spawning, and notifications

## Quick Start

### Option A: Install via npm (recommended)

```bash
openclaw plugins install ambient-alfred
openclaw gateway restart
```

### Option B: Install from source

```bash
git clone https://github.com/ssdavidai/ambient-alfred.git
cd ambient-alfred
chmod +x install.sh
./install.sh
```

### 1. Configure

### 2. Configure

Set environment variables (or configure via OpenClaw plugin UI):

```bash
# Required: transcription
export ALFRED_TRANSCRIPTION_API_KEY="your-assemblyai-key"

# Optional: command detection
export OPENROUTER_API_KEY="your-openrouter-key"

# Optional: customize paths
export ALFRED_TRANSCRIPTS_DIR="./transcripts"
export ALFRED_VAULT_INBOX_DIR="~/vault/inbox"
```

### 3. Set Omi webhook

In the Omi app, set the webhook URL to:

```
http://<your-ip>:8080/audio?uid=omi&sample_rate=16000
```

### 4. Start

**Via OpenClaw (recommended):**

Restart the OpenClaw gateway — the plugin starts both services automatically.

**Standalone:**

```bash
# Terminal 1: Audio receiver
.venv/bin/python -m receiver.run

# Terminal 2: Pipeline watcher
.venv/bin/python -m pipeline.watcher
```

### 5. Verify

```bash
curl http://localhost:8080/health
# {"status": "ok"}

curl http://localhost:8080/status
# Shows chunker sessions and queue status
```

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ALFRED_RECEIVER_HOST` | `0.0.0.0` | Receiver bind address |
| `ALFRED_RECEIVER_PORT` | `8080` | Receiver port |
| `ALFRED_TRANSCRIPTION_PROVIDER` | `assemblyai` | Provider: `assemblyai`, `whisper_compatible`, `openai`, `passthrough` |
| `ALFRED_TRANSCRIPTION_API_KEY` | | API key for the transcription provider |
| `ALFRED_TRANSCRIPTION_URL` | | URL for Whisper-compatible server |
| `ALFRED_TRANSCRIPTION_MODEL` | | Model name override |
| `ALFRED_TRANSCRIPTION_LANGUAGE` | | Language hint |
| `ALFRED_CHUNKER_SILENCE_THRESHOLD` | `10.0` | Seconds of silence before finalizing segment |
| `ALFRED_CHUNKER_MAX_SEGMENT_DURATION` | `300.0` | Max segment length in seconds |
| `ALFRED_CHUNKER_MIN_SEGMENT_SPEECH` | `0.5` | Min speech to keep a segment |
| `ALFRED_TRANSCRIPTS_DIR` | `transcripts` | Where transcript JSONs are saved |
| `ALFRED_QUEUE_DIR` | `queue` | Disk-backed segment queue directory |
| `ALFRED_CONVERSATION_GAP_SECONDS` | `600` | Silence gap that starts a new conversation |
| `ALFRED_MIN_WORDS` | `30` | Min words to keep a conversation |
| `ALFRED_DEBOUNCE_SECONDS` | `120` | Wait time after last segment before processing |
| `ALFRED_COMMAND_DETECTION_ENABLED` | `true` | Enable LLM command detection |
| `ALFRED_AGENT_NAME` | `Alfred` | Name the agent responds to |
| `OPENROUTER_API_KEY` | | OpenRouter API key for command detection |
| `ALFRED_OPENROUTER_MODEL` | `google/gemini-2.0-flash-001` | Model for command classification |
| `ALFRED_SUBAGENT_ID` | `subalfred` | SubAgent ID for command execution |
| `ALFRED_VAULT_INBOX_DIR` | `~/vault/inbox` | Where conversation markdown is written |
| `ALFRED_NOTIFICATION_CHANNEL` | | Notification target (channel ID) |
| `ALFRED_NOTIFICATION_CHANNEL_TYPE` | `slack` | Notification channel type |
| `OPENCLAW_GATEWAY_URL` | `http://localhost:18789` | OpenClaw Gateway URL |
| `OPENCLAW_GATEWAY_TOKEN` | | OpenClaw Gateway bearer token |

### OpenClaw Plugin Config

Configure via `plugins.entries.ambient-alfred.config` in your OpenClaw config:

```json5
{
  plugins: {
    entries: {
      "ambient-alfred": {
        enabled: true,
        config: {
          receiver: { host: "0.0.0.0", port: 8080 },
          transcription: {
            provider: "assemblyai",
            apiKeyEnv: "ASSEMBLYAI_API_KEY"
          },
          commandDetection: {
            enabled: true,
            agentName: "Alfred",
            openrouterApiKey: "sk-or-...",
            openrouterModel: "google/gemini-2.0-flash-001"
          },
          storage: {
            transcriptsDir: "./transcripts",
            vaultInboxDir: "~/vault/inbox"
          }
        }
      }
    }
  }
}
```

## Architecture Deep Dive

### Receiver

The receiver is a FastAPI server that accepts raw PCM16 audio from Omi devices:

1. **POST /audio** receives ~5-second PCM16 chunks
2. **Silero VAD** detects speech vs silence in each chunk
3. **SmartChunker** uses a state machine (IDLE/SPEECH/TRAILING_SILENCE) to group chunks into segments based on silence gaps
4. **SegmentQueue** persists segments to disk (crash-resilient) and feeds them to the transcription worker
5. **Transcription worker** sends audio to the configured provider and saves results as JSON

### Pipeline

The pipeline watches for new transcript files and processes them:

1. **Watchdog observer** detects new JSON files in the transcripts directory
2. **Segment grouping** clusters transcripts by time (10-min gap = new conversation)
3. **Debounce** waits 2 minutes after the last segment to finalize
4. **Instant command detection** — if a segment mentions the agent name, it's classified immediately via OpenRouter
5. **Command routing** — commands spawn a SubAgent via OpenClaw Gateway
6. **Inbox writer** — non-command conversations become markdown files with YAML frontmatter
7. **Notifications** — sent via OpenClaw Gateway (Slack, etc.)

### Transcription Providers

| Provider | Use Case |
|---|---|
| `assemblyai` | Best quality, Universal-2, multilingual, cloud |
| `whisper_compatible` | Self-hosted (LocalAI, faster-whisper-server) |
| `openai` | OpenAI Whisper API |
| `passthrough` | Testing / pre-transcribed audio |

## Troubleshooting

**Receiver not starting:**
- Check port is available: `lsof -i :8080`
- Verify Python venv: `.venv/bin/python -c "import fastapi; print('ok')"`

**No transcripts appearing:**
- Check Omi webhook URL is correct and device is streaming
- Check receiver logs: `curl http://localhost:8080/status`
- Verify the Silero VAD model downloaded: `.venv/bin/python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)"`

**Conversations not being processed:**
- Check pipeline watcher logs
- Verify transcripts directory has JSON files
- Check state file for processed files list

**Command detection not working:**
- Verify `OPENROUTER_API_KEY` is set
- Check the agent name matches what you say (default: "Alfred")
- Test: the pipeline logs will show "INSTANT COMMAND detected" for recognized commands

## License

MIT — see [LICENSE](LICENSE).
