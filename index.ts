/**
 * Ambient Alfred — OpenClaw plugin entry point.
 *
 * Registers a background service that manages:
 *   1. The audio receiver (FastAPI server receiving PCM16 from Omi devices)
 *   2. The conversation pipeline watcher (filesystem watcher + command detection + inbox writer)
 *
 * On first run, automatically creates a Python venv and installs all dependencies.
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

// ── Paths ────────────────────────────────────────────────────────
const DATA_DIR = join(homedir(), ".openclaw", "ambient-alfred");
const VENV_DIR = join(DATA_DIR, "venv");
const ZO_SECRETS_PATH = "/root/.zo_secrets";

// ── Zo Secrets ───────────────────────────────────────────────────

function isZoComputer(): boolean {
  return existsSync(ZO_SECRETS_PATH);
}

function loadZoSecrets(): Record<string, string> {
  if (!existsSync(ZO_SECRETS_PATH)) return {};
  try {
    const content = readFileSync(ZO_SECRETS_PATH, "utf-8");
    const secrets: Record<string, string> = {};
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eqIdx = trimmed.indexOf("=");
      if (eqIdx > 0) {
        const key = trimmed.slice(0, eqIdx).trim();
        let val = trimmed.slice(eqIdx + 1).trim();
        if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
          val = val.slice(1, -1);
        }
        secrets[key] = val;
      }
    }
    return secrets;
  } catch {
    return {};
  }
}

function saveZoSecret(key: string, value: string): void {
  if (!isZoComputer()) return;
  const secrets = loadZoSecrets();
  secrets[key] = value;
  const lines: string[] = [];
  for (const [k, v] of Object.entries(secrets)) {
    lines.push(`${k}="${v}"`);
  }
  writeFileSync(ZO_SECRETS_PATH, lines.join("\n") + "\n");
}

function ensureDirs() {
  if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true });
}

// ── Auto-setup: Python venv + dependencies ───────────────────────

function findVenvPython(): string | null {
  for (const p of [
    join(VENV_DIR, "bin", "python3"),
    join(VENV_DIR, "bin", "python"),
    join(VENV_DIR, "Scripts", "python.exe"),
  ]) {
    if (existsSync(p)) return p;
  }
  return null;
}

function findVenvPip(): string | null {
  for (const p of [
    join(VENV_DIR, "bin", "pip"),
    join(VENV_DIR, "bin", "pip3"),
    join(VENV_DIR, "Scripts", "pip.exe"),
  ]) {
    if (existsSync(p)) return p;
  }
  return null;
}

function ensurePythonEnv(pluginDir: string, logger: any): string {
  const existingPython = findVenvPython();

  // Verify existing venv works (check a key dependency)
  if (existingPython) {
    const testResult = spawnSync(existingPython, ["-c", "import fastapi; print('ok')"], {
      stdio: "pipe",
      timeout: 10_000,
    });
    if (testResult.status === 0) {
      return existingPython;
    }
    logger.info("[ambient-alfred] Venv exists but dependencies missing, reinstalling...");
  } else {
    logger.info("[ambient-alfred] First run — setting up Python environment...");
  }

  // Check python3 exists on system
  const pythonCheck = spawnSync("python3", ["--version"], { stdio: "pipe", timeout: 5_000 });
  if (pythonCheck.status !== 0) {
    const msg = "python3 not found. Install Python 3.10+ and try again.";
    logger.error(`[ambient-alfred] ${msg}`);
    throw new Error(msg);
  }
  logger.info(`[ambient-alfred] Found ${pythonCheck.stdout?.toString().trim()}`);

  // Nuke broken venv if it exists but is broken
  if (existingPython || existsSync(VENV_DIR)) {
    const rmResult = spawnSync("rm", ["-rf", VENV_DIR], { stdio: "pipe", timeout: 10_000 });
    logger.info("[ambient-alfred] Removed broken/incomplete venv");
  }

  // Create fresh venv
  const venvResult = spawnSync("python3", ["-m", "venv", VENV_DIR], {
    stdio: "pipe",
    timeout: 60_000,
  });
  if (venvResult.status !== 0) {
    const stderr = venvResult.stderr?.toString().trim() || "";
    const stdout = venvResult.stdout?.toString().trim() || "";
    const signal = venvResult.signal ? String(venvResult.signal) : "";
    const err = stderr || stdout || signal || `exit code ${venvResult.status}`;
    logger.error(`[ambient-alfred] Failed to create venv: ${err}`);
    if (err.includes("ensurepip") || err.includes("No module named")) {
      logger.error("[ambient-alfred] Fix: apt install python3-venv (Debian/Ubuntu) or dnf install python3-pip (Fedora)");
    }
    throw new Error(`venv creation failed: ${err}`);
  }

  // Find the python/pip in venv
  const pythonBin = findVenvPython();
  const pipBin = findVenvPip();
  if (!pythonBin || !pipBin) {
    logger.error("[ambient-alfred] venv directory created but no python binary inside.");
    logger.error("[ambient-alfred] This means python3-venv is not installed on your system.");
    logger.error("[ambient-alfred] Fix: apt install python3-venv (Debian/Ubuntu) or dnf install python3-devel (Fedora)");
    spawnSync("rm", ["-rf", VENV_DIR], { stdio: "pipe" });
    throw new Error("python3-venv not installed. Run: apt install python3-venv");
  }
  logger.info("[ambient-alfred] Python venv created");

  // Install all requirements
  const reqFiles = [
    join(pluginDir, "requirements.txt"),
    join(pluginDir, "receiver", "requirements.txt"),
    join(pluginDir, "pipeline", "requirements.txt"),
  ];

  for (const reqFile of reqFiles) {
    if (existsSync(reqFile)) {
      logger.info(`[ambient-alfred] Installing deps from ${reqFile}...`);
      const pipResult = spawnSync(pipBin, ["install", "-r", reqFile], {
        stdio: "pipe",
        timeout: 300_000,
      });
      if (pipResult.status !== 0) {
        const stderr = pipResult.stderr?.toString().trim() || "";
        const stdout = pipResult.stdout?.toString().trim() || "";
        const err = stderr || stdout || `exit code ${pipResult.status}`;
        logger.error(`[ambient-alfred] pip install failed for ${reqFile}: ${err}`);
        throw new Error(`pip install failed: ${err}`);
      }
    }
  }

  logger.info("[ambient-alfred] All Python dependencies installed");
  return pythonBin;
}

// ── Env builder ──────────────────────────────────────────────────

function buildEnv(config: any): Record<string, string> {
  const env: Record<string, string> = { ...(process.env as Record<string, string>) };

  // Receiver
  if (config?.receiver?.host) env.ALFRED_RECEIVER_HOST = config.receiver.host;
  if (config?.receiver?.port) env.ALFRED_RECEIVER_PORT = String(config.receiver.port);

  // Transcription
  if (config?.transcription?.provider) env.ALFRED_TRANSCRIPTION_PROVIDER = config.transcription.provider;
  // Direct API key from config (preferred)
  if (config?.transcription?.apiKey) {
    env.ALFRED_TRANSCRIPTION_API_KEY = config.transcription.apiKey;
  }
  // Fallback: env var name reference
  else if (config?.transcription?.apiKeyEnv) {
    const key = process.env[config.transcription.apiKeyEnv];
    if (key) env.ALFRED_TRANSCRIPTION_API_KEY = key;
  }
  // Fallback: Zo secrets
  if (!env.ALFRED_TRANSCRIPTION_API_KEY) {
    const zoSec = loadZoSecrets();
    const provider = config?.transcription?.provider || "assemblyai";
    const zoKey = provider === "assemblyai" ? zoSec.ASSEMBLYAI_API_KEY : zoSec.OPENAI_API_KEY;
    if (zoKey) env.ALFRED_TRANSCRIPTION_API_KEY = zoKey;
  }
  if (config?.transcription?.url) env.ALFRED_TRANSCRIPTION_URL = config.transcription.url;
  if (config?.transcription?.model) env.ALFRED_TRANSCRIPTION_MODEL = config.transcription.model;
  if (config?.transcription?.language) env.ALFRED_TRANSCRIPTION_LANGUAGE = config.transcription.language;

  // Chunker
  if (config?.chunker?.silenceThresholdSeconds) env.ALFRED_CHUNKER_SILENCE_THRESHOLD = String(config.chunker.silenceThresholdSeconds);
  if (config?.chunker?.maxSegmentDurationSeconds) env.ALFRED_CHUNKER_MAX_SEGMENT_DURATION = String(config.chunker.maxSegmentDurationSeconds);
  if (config?.chunker?.minSegmentSpeechSeconds) env.ALFRED_CHUNKER_MIN_SEGMENT_SPEECH = String(config.chunker.minSegmentSpeechSeconds);

  // Conversations
  if (config?.conversations?.gapSeconds) env.ALFRED_CONVERSATION_GAP_SECONDS = String(config.conversations.gapSeconds);
  if (config?.conversations?.minWords) env.ALFRED_MIN_WORDS = String(config.conversations.minWords);
  if (config?.conversations?.debounceSeconds) env.ALFRED_DEBOUNCE_SECONDS = String(config.conversations.debounceSeconds);

  // Command detection
  if (config?.commandDetection?.enabled !== undefined) env.ALFRED_COMMAND_DETECTION_ENABLED = String(config.commandDetection.enabled);
  if (config?.commandDetection?.agentName) env.ALFRED_AGENT_NAME = config.commandDetection.agentName;
  if (config?.commandDetection?.openrouterApiKey) {
    env.OPENROUTER_API_KEY = config.commandDetection.openrouterApiKey;
  } else {
    const zoSec = loadZoSecrets();
    if (zoSec.OPENROUTER_API_KEY) env.OPENROUTER_API_KEY = zoSec.OPENROUTER_API_KEY;
  }
  if (config?.commandDetection?.openrouterModel) env.ALFRED_OPENROUTER_MODEL = config.commandDetection.openrouterModel;
  if (config?.commandDetection?.subagentId) env.ALFRED_SUBAGENT_ID = config.commandDetection.subagentId;

  // Storage
  if (config?.storage?.transcriptsDir) env.ALFRED_TRANSCRIPTS_DIR = config.storage.transcriptsDir;
  if (config?.storage?.vaultInboxDir) env.ALFRED_VAULT_INBOX_DIR = config.storage.vaultInboxDir;
  if (config?.storage?.queueDir) env.ALFRED_QUEUE_DIR = config.storage.queueDir;

  // Notifications
  if (config?.notifications?.channel) env.ALFRED_NOTIFICATION_CHANNEL = config.notifications.channel;
  if (config?.notifications?.channelType) env.ALFRED_NOTIFICATION_CHANNEL_TYPE = config.notifications.channelType;

  return env;
}

// ── Plugin ───────────────────────────────────────────────────────

let receiverProcess: ChildProcess | null = null;
let pipelineProcess: ChildProcess | null = null;

export const id = "ambient-alfred";
export const name = "Ambient Alfred";

export default function register(api: any) {
  const cfg = () => api.config?.plugins?.entries?.["ambient-alfred"]?.config ?? {};

  ensureDirs();

  function startProcess(
    label: string,
    pythonBin: string,
    args: string[],
    pluginDir: string,
    env: Record<string, string>,
  ): ChildProcess {
    const proc = spawn(pythonBin, args, {
      cwd: pluginDir,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    proc.stdout?.on("data", (d: Buffer) => {
      for (const line of d.toString().split("\n").filter(Boolean)) {
        api.logger.info(`[${label}] ${line}`);
      }
    });

    proc.stderr?.on("data", (d: Buffer) => {
      for (const line of d.toString().split("\n").filter(Boolean)) {
        api.logger.warn(`[${label}] ${line}`);
      }
    });

    proc.on("exit", (code: number | null) => {
      api.logger.info(`[${label}] exited (code=${code})`);
    });

    return proc;
  }

  api.registerService({
    id: "ambient-alfred",
    start: () => {
      const config = cfg();
      const pluginDir = __dirname;

      // Auto-setup Python env on first run
      let pythonBin: string;
      try {
        pythonBin = ensurePythonEnv(pluginDir, api.logger);
      } catch (err: any) {
        api.logger.error(`[ambient-alfred] Python setup failed: ${err.message}. Plugin will not start.`);
        return;
      }

      const env = buildEnv(config);

      api.logger.info("[ambient-alfred] Starting audio receiver...");
      receiverProcess = startProcess("receiver", pythonBin, ["-m", "receiver.run"], pluginDir, env);

      api.logger.info("[ambient-alfred] Starting pipeline watcher...");
      pipelineProcess = startProcess("pipeline", pythonBin, ["-m", "pipeline.watcher"], pluginDir, env);

      api.logger.info("[ambient-alfred] All services started");
    },
    stop: () => {
      api.logger.info("[ambient-alfred] Stopping services...");
      if (receiverProcess) { receiverProcess.kill("SIGTERM"); receiverProcess = null; }
      if (pipelineProcess) { pipelineProcess.kill("SIGTERM"); pipelineProcess = null; }
      api.logger.info("[ambient-alfred] All services stopped");
    },
  });

  // ── CLI command ──────────────────────────────────────────────
  api.registerCli(
    ({ program }: any) => {
      const cmd = program
        .command("ambient-alfred")
        .description("Ambient Alfred (Omi) commands");

      cmd
        .command("status")
        .description("Show service status")
        .action(() => {
          const recvRunning = receiverProcess !== null && !receiverProcess.killed;
          const pipeRunning = pipelineProcess !== null && !pipelineProcess.killed;
          console.log(`Audio receiver:    ${recvRunning ? "running" : "stopped"}`);
          console.log(`Pipeline watcher:  ${pipeRunning ? "running" : "stopped"}`);
          console.log(`Venv:              ${existsSync(join(VENV_DIR, "bin", "python3")) ? "ready" : "not created"}`);
        });

      cmd
        .command("setup")
        .description("Interactive configuration for ambient-alfred")
        .action(async () => {
          const { createInterface } = await import("node:readline");
          const rl = createInterface({ input: process.stdin, output: process.stdout });
          const ask = (q: string, def?: string): Promise<string> =>
            new Promise((resolve) => {
              const suffix = def ? ` (${def})` : "";
              rl.question(`${q}${suffix}: `, (answer: string) => {
                resolve(answer.trim() || def || "");
              });
            });

          console.log("");
          console.log("╔══════════════════════════════════════════════╗");
          console.log("║     Ambient Alfred — Interactive Setup       ║");
          console.log("╚══════════════════════════════════════════════╝");
          console.log("");

          const configPath = join(homedir(), ".openclaw", "openclaw.json");
          let fullConfig: any = {};
          try {
            fullConfig = JSON.parse(readFileSync(configPath, "utf-8"));
          } catch {}

          const existing = fullConfig?.plugins?.entries?.["ambient-alfred"]?.config ?? {};

          const zoSecrets = loadZoSecrets();
          const isZo = isZoComputer();
          if (isZo) {
            console.log("  🖥️  Zo Computer detected — secrets will be saved to /root/.zo_secrets\n");
          }

          console.log("  Audio receiver\n");

          const receiverPort = await ask("  Receiver port", String(existing.receiver?.port ?? 8080));

          console.log("\n  Transcription\n");

          const provider = await ask(
            "  Provider (assemblyai / whisper / openai / passthrough)",
            existing.transcription?.provider || "assemblyai",
          );

          let transcriptionApiKey = "";
          let transcriptionUrl = "";
          if (provider === "assemblyai") {
            const existingKey = existing.transcription?.apiKey || zoSecrets.ASSEMBLYAI_API_KEY;
            transcriptionApiKey = await ask(
              "  AssemblyAI API key",
              existingKey ? "••••••" + existingKey.slice(-4) : undefined,
            );
          } else if (provider === "whisper") {
            transcriptionUrl = await ask("  Whisper API URL", existing.transcription?.url || "http://localhost:8090/v1/audio/transcriptions");
          } else if (provider === "openai") {
            const existingKey = existing.transcription?.apiKey || zoSecrets.OPENAI_API_KEY;
            transcriptionApiKey = await ask(
              "  OpenAI API key",
              existingKey ? "••••••" + existingKey.slice(-4) : undefined,
            );
          }

          const language = await ask("  Transcription language", existing.transcription?.language || "en");

          console.log("\n  Command detection (detects when you say your agent's name)\n");

          const agentName = await ask("  Agent name to listen for", existing.commandDetection?.agentName || "Alfred");
          const existingOrKey = existing.commandDetection?.openrouterApiKey || zoSecrets.OPENROUTER_API_KEY;
          const openrouterKey = await ask(
            "  OpenRouter API key",
            existingOrKey ? "••••••" + existingOrKey.slice(-4) : "skip",
          );
          const openrouterModel = await ask(
            "  Classification model",
            existing.commandDetection?.openrouterModel || "google/gemini-2.0-flash-001",
          );

          console.log("\n  Storage\n");

          const inboxDir = await ask("  Vault inbox directory", existing.storage?.vaultInboxDir || "~/vault/inbox");

          // Detect machine IP for webhook URL
          let machineIp = "localhost";
          try {
            // Try tailscale first
            const tsResult = spawnSync("tailscale", ["ip", "-4"], { stdio: "pipe", timeout: 5_000 });
            if (tsResult.status === 0) {
              machineIp = tsResult.stdout?.toString().trim().split("\n")[0] || machineIp;
            } else {
              // Fall back to hostname -I (Linux) or ifconfig (macOS)
              const hostResult = spawnSync("hostname", ["-I"], { stdio: "pipe", timeout: 5_000 });
              if (hostResult.status === 0) {
                machineIp = hostResult.stdout?.toString().trim().split(" ")[0] || machineIp;
              } else {
                const ifResult = spawnSync("sh", ["-c", "ifconfig | grep 'inet ' | grep -v 127.0.0.1 | head -1 | awk '{print $2}'"], { stdio: "pipe", timeout: 5_000 });
                if (ifResult.status === 0) {
                  machineIp = ifResult.stdout?.toString().trim() || machineIp;
                }
              }
            }
          } catch {}

          // Build config
          const pluginConfig: any = {
            receiver: { port: parseInt(receiverPort) || 8080 },
            transcription: {
              provider: provider,
              language: language,
            },
            commandDetection: {
              agentName: agentName,
              openrouterModel: openrouterModel,
            },
            storage: {
              vaultInboxDir: inboxDir,
            },
          };

          // Store API keys directly in config (not as env var names)
          if (transcriptionApiKey && !transcriptionApiKey.startsWith("••")) {
            pluginConfig.transcription.apiKey = transcriptionApiKey;
          } else if (existing.transcription?.apiKey) {
            pluginConfig.transcription.apiKey = existing.transcription.apiKey;
          }
          if (transcriptionUrl) pluginConfig.transcription.url = transcriptionUrl;

          if (openrouterKey && openrouterKey !== "skip" && !openrouterKey.startsWith("••")) {
            pluginConfig.commandDetection.openrouterApiKey = openrouterKey;
          } else if (existing.commandDetection?.openrouterApiKey) {
            pluginConfig.commandDetection.openrouterApiKey = existing.commandDetection.openrouterApiKey;
          }

          // Write to openclaw.json
          if (!fullConfig.plugins) fullConfig.plugins = {};
          if (!fullConfig.plugins.entries) fullConfig.plugins.entries = {};
          fullConfig.plugins.entries["ambient-alfred"] = {
            enabled: true,
            config: pluginConfig,
          };

          try {
            writeFileSync(configPath, JSON.stringify(fullConfig, null, 2) + "\n");
            console.log("\n  ✓ Configuration saved to ~/.openclaw/openclaw.json");
          } catch (err: any) {
            console.error(`\n  ✗ Failed to save config: ${err.message}`);
            console.log("\n  Config to add manually:\n");
            console.log(JSON.stringify({ "ambient-alfred": { enabled: true, config: pluginConfig } }, null, 2));
          }

          // Save secrets to Zo Computer if available
          if (isZo) {
            const txKey = pluginConfig.transcription?.apiKey;
            if (txKey) {
              const envName = provider === "assemblyai" ? "ASSEMBLYAI_API_KEY" : "OPENAI_API_KEY";
              saveZoSecret(envName, txKey);
            }
            const orKey = pluginConfig.commandDetection?.openrouterApiKey;
            if (orKey) saveZoSecret("OPENROUTER_API_KEY", orKey);
            console.log("  ✓ Secrets saved to /root/.zo_secrets");
          }

          const port = parseInt(receiverPort) || 8080;
          console.log("\n  Next steps:");
          console.log("  1. Restart gateway: openclaw gateway restart");
          console.log(`  2. Point Omi app webhook to: http://${machineIp}:${port}/audio`);
          if (machineIp === "localhost") {
            console.log("     (Could not detect IP — replace 'localhost' with your machine's IP)");
          }
          console.log("");

          rl.close();
        });
    },
    { commands: ["ambient-alfred"] },
  );

  api.logger.info("[ambient-alfred] Plugin registered");
}
