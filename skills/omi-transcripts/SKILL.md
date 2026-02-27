# Omi Ambient Transcripts

You have access to ambient audio transcripts captured by an Omi wearable device. These are continuously recorded, transcribed, and organized into conversations.

## Transcript Location

Transcripts are saved as JSON files in a date-organized directory:

```
$ALFRED_TRANSCRIPTS_DIR/
  2026-02-27/
    14-30-00_device1.json
    14-35-22_device1.json
    ...
```

Each JSON file contains:
- `uid`: device identifier
- `text`: transcribed text
- `language`: detected language code
- `timestamp`: ISO 8601 timestamp
- `audio_duration_seconds`: total audio length
- `speech_duration_seconds`: actual speech length

## Conversations

Segments are grouped into conversations by the pipeline:
- **10-minute silence gap** = new conversation
- Conversations with < 30 words are filtered as noise
- Each conversation is written to the vault inbox as markdown with YAML frontmatter

## Vault Inbox Files

Processed conversations appear in `$ALFRED_VAULT_INBOX_DIR/` as:

```
omi-2026-02-27-1430-1445.md
```

With YAML frontmatter containing: type, date, time, duration, segments, languages, words.

## Command Detection

If the agent's name (default: "Alfred") is mentioned in a conversation, the pipeline uses an LLM classifier to determine if it's a direct command. If so, a subagent is spawned to execute it.

## Working with Transcripts

When asked about conversations or what was discussed:
1. Check the vault inbox directory for recent markdown files
2. Read the transcript content to understand what was discussed
3. Transcripts may be in multiple languages — provide translations if needed
4. Pay attention to timestamps and duration for context
