/**
 * Chat Bridge — spawns OpenClaude CLI with structured streaming events.
 * Replaces the old Anthropic SDK to support all providers (OpenRouter, etc).
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawn } = require('child_process');
const readline = require('readline');

const WORKSPACE_ROOT = path.resolve(__dirname, '..', '..', '..');

function readTrustMode() {
  try {
    const yaml = fs.readFileSync(path.join(WORKSPACE_ROOT, 'config', 'workspace.yaml'), 'utf8');
    const m = yaml.match(/^chat:\s*\n(?:[ \t]+[^\n]*\n)*?[ \t]+trustMode:\s*(true|false)/m);
    return m ? m[1] === 'true' : false;
  } catch { }
  return false;
}

const AUTO_APPROVE = new Set([
  'Read', 'Glob', 'Grep', 'WebFetch', 'WebSearch', 'ToolSearch',
  'NotebookRead', 'Skill',
]);

function loadAgentFile(agentName, cwd) {
  const agentPath = path.join(cwd, '.claude', 'agents', `${agentName}.md`);
  if (!fs.existsSync(agentPath)) {
    return null;
  }
  const raw = fs.readFileSync(agentPath, 'utf8');
  const fmMatch = raw.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!fmMatch) return { description: agentName, prompt: raw.trim() };

  const fmText = fmMatch[1];
  const meta = {};
  for (const line of fmText.split('\n')) {
    const m = line.match(/^(\w+):\s*(.+)$/);
    if (m) {
      meta[m[1]] = m[2].trim().replace(/^["']|["']$/g, '');
    }
  }
  return {
    description: typeof meta.description === 'string' ? meta.description : agentName,
    prompt: fmMatch[2].trim(),
    model: meta.model
  };
}

const TICKET_STATUSES = new Set(['open', 'in_progress', 'blocked', 'review', 'resolved', 'closed']);
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function _looksLikeTicket(obj) {
  return obj && typeof obj.id === 'string' && UUID_RE.test(obj.id) && TICKET_STATUSES.has(obj.status);
}

function _extractJsonObjects(text) {
  const results = [];
  for (let i = 0; i < text.length; i++) {
    if (text[i] !== '{') continue;
    let depth = 0;
    let inStr = false;
    let esc = false;
    for (let j = i; j < text.length; j++) {
      const c = text[j];
      if (esc) { esc = false; continue; }
      if (c === '\\') { esc = true; continue; }
      if (c === '"') { inStr = !inStr; continue; }
      if (inStr) continue;
      if (c === '{') depth++;
      else if (c === '}') {
        depth--;
        if (depth === 0) {
          results.push(text.slice(i, j + 1));
          i = j;
          break;
        }
      }
    }
  }
  return results;
}

function detectCreatedTicketId(text) {
  if (!text || typeof text !== 'string') return null;
  for (const candidate of _extractJsonObjects(text)) {
    try {
      const obj = JSON.parse(candidate);
      if (_looksLikeTicket(obj)) return obj.id;
    } catch { }
  }
  const m = text.match(/["']id["']\s*:\s*["']([0-9a-f-]{36})["']/i);
  return m && UUID_RE.test(m[1]) ? m[1] : null;
}

function loadProviderConfig() {
  const ALLOWED_VARS = new Set([
    'ANTHROPIC_API_KEY', 'CLAUDE_CODE_USE_OPENAI', 'CLAUDE_CODE_USE_GEMINI',
    'OPENAI_BASE_URL', 'OPENAI_API_KEY', 'OPENAI_MODEL', 'CODEX_API_KEY',
    'GEMINI_API_KEY', 'GEMINI_MODEL'
  ]);
  try {
    const configPath = path.join(WORKSPACE_ROOT, 'config', 'providers.json');
    if (!fs.existsSync(configPath)) return { cli: 'openclaude', env: {} };
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    const active = config.active_provider || 'anthropic';
    const provider = config.providers?.[active] || {};
    const cli = provider.cli_command || 'openclaude';
    const env = Object.fromEntries(
      Object.entries(provider.env_vars || {}).filter(([k, v]) => v !== '' && ALLOWED_VARS.has(k))
    );
    // fallback for codex
    if (active === 'codex_auth' && !env['OPENAI_MODEL']) env['OPENAI_MODEL'] = 'codexplan';
    else if (active === 'openai' && !env['OPENAI_MODEL']) env['OPENAI_MODEL'] = 'gpt-4.1';

    // Add explicitly PATH to find openclaude globally
    const home = process.env.HOME || '/root';
    env['PATH'] = `${home}/.local/bin:/usr/local/bin:/usr/bin:${process.env.PATH || ''}`;

    return { cli, env, active };
  } catch (err) {
    return { cli: 'openclaude', env: {} };
  }
}

class ChatBridge {
  constructor() {
    this.sessions = new Map();
  }

  async startSession(sessionId, options = {}) {
    const {
      agentName,
      workingDir,
      prompt,
      files,
      sdkSessionId,
      onMessage,
      onError,
      onComplete,
    } = options;

    if (this.sessions.has(sessionId)) {
      await this.stopSession(sessionId);
    }

    const abortController = new AbortController();
    const cwd = workingDir || process.cwd();

    // Agent config
    let finalPrompt = prompt || '';
    let enforcePrompt = '';

    if (agentName) {
      const agentDef = loadAgentFile(agentName, cwd);
      if (agentDef) {
        const runtimeLines = [
          '## Runtime context',
          'You are running inside the EvoNexus dashboard.',
          `- Current agent slug: ${agentName}`,
          `- Current chat session id: ${sessionId}`,
          '',
          'When you create a ticket via `evo.post("/api/tickets", {...})`, include `source_agent: "' + agentName + '"` and `source_session_id: "' + sessionId + '"` in the payload.',
          '',
          '## Tool permission policy',
          'Read/Glob/Grep/WebFetch/ToolSearch/Skill run automatically.',
          'Write/Edit/Bash/Agent/NotebookEdit need user approval per call. Don\'t ask for permission in text; just call the tool.'
        ];
        enforcePrompt = agentDef.prompt + '\n\n' + runtimeLines.join('\n');
      }
    }

    if (files && files.length > 0) {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'evo-chat-'));
      const savedPaths = [];
      for (const f of files) {
        if (f.base64) {
          const safeName = f.name.replace(/[^a-zA-Z0-9._-]/g, '_');
          const filePath = path.join(tmpDir, safeName);
          fs.writeFileSync(filePath, Buffer.from(f.base64, 'base64'));
          savedPaths.push({ name: f.name, path: filePath });
        }
      }
      if (savedPaths.length > 0) {
        const fileList = savedPaths.map(f => `- ${f.name}: ${f.path}`).join('\n');
        finalPrompt += `\n\n[Attached files — use Read tool to view them]\n${fileList}`;
      }
    }

    const providerConfig = loadProviderConfig();
    // NOTE: openclaude requires --verbose when --print + --output-format=stream-json are combined.
    // Without --verbose it crashes: "Error: When using --print, --output-format=stream-json requires --verbose"
    // --verbose outputs non-JSON debug lines to stderr only (not stdout), so JSON parsing on stdout is safe.
    const args = [
      '--print',
      '--verbose',
      finalPrompt,
      '--output-format=stream-json',
      '--input-format=stream-json'
    ];

    if (enforcePrompt) {
      args.push('--system-prompt', enforcePrompt);
    }

    // Pass the session id to resume conversation
    if (sdkSessionId) {
      args.push('--resume', sdkSessionId);
    }

    console.log(`[chat-bridge] Spawning ${providerConfig.cli} with args:`, args.join(' '));

    const child = spawn(providerConfig.cli, args, {
      cwd,
      env: { ...process.env, ...providerConfig.env }
    });

    const session = {
      active: true,
      abortController,
      agentName,
      sdkSessionId: sdkSessionId || null,
      process: child,
      pendingApprovals: new Map()
    };
    this.sessions.set(sessionId, session);

    const rl = readline.createInterface({ input: child.stdout });

    rl.on('line', (line) => {
      if (!session.active) return;
      try {
        const msg = JSON.parse(line);

        // Intercept permission requests from openclaude JSON stream
        if (msg.type === 'permission_request' || (msg.type === 'system' && msg.subtype === 'permission_request')) {
          const toolName = msg.tool_name || msg.toolName;
          const toolInput = msg.tool_input || msg.input || {};
          const requestId = msg.toolUseID || msg.request_id || `req-${Date.now()}`;

          if (readTrustMode() || AUTO_APPROVE.has(toolName)) {
            child.stdin.write(JSON.stringify({ type: 'permission_response', decision: 'allow', toolUseID: requestId }) + '\n');
            return;
          }

          session.pendingApprovals.set(requestId, { toolInput, requestId });
          if (onMessage) {
            onMessage({
              type: 'permission_request',
              requestId,
              toolName,
              input: toolInput,
              agentId: null
            });
          }
          return;
        }

        // Capture session ID
        if (msg.session_id && !session.sdkSessionId) {
          session.sdkSessionId = msg.session_id;
          if (onMessage) onMessage({ type: 'session_id', sdkSessionId: msg.session_id });
        }

        // Detect tickets
        if (msg.type === 'user') {
          const content = msg.message?.content || msg.content;
          if (Array.isArray(content)) {
            for (const block of content) {
              if (block.type === 'tool_result') {
                const raw = typeof block.content === 'string' ? block.content : JSON.stringify(block.content);
                const ticketId = detectCreatedTicketId(raw);
                if (ticketId && onMessage) onMessage({ type: 'ticket_detected', ticketId });
              }
            }
          }
        }

        if (onMessage) {
          onMessage(this._transformMessage(msg));
        }

      } catch (e) {
        // Non-JSON line on stdout — log for debugging, do not crash
        console.error(`[chat-bridge] Non-JSON stdout line:`, line.slice(0, 200));
      }
    });

    child.stderr.on('data', (data) => {
      // stderr may contain --verbose output; suppress noisy lines, log errors
      const txt = data.toString();
      if (txt.includes('Error') || txt.includes('error')) {
        console.error(`[chat-bridge] STDERR error:`, txt.trim());
      }
    });

    child.on('close', (code, signal) => {
      console.log(`[chat-bridge] Child process exited code=${code} signal=${signal}`);
      session.active = false;
      this.sessions.delete(sessionId);
      // If crashed (non-zero exit without signal), notify error
      if ((code !== null && code !== 0) && !signal) {
        if (onError) onError(new Error(`Agent process exited unexpectedly (code ${code})`));
      }
      if (onComplete) onComplete({ sdkSessionId: session.sdkSessionId });
    });

    child.on('error', (err) => {
      console.error(`[chat-bridge] Child process spawn error:`, err.message);
      session.active = false;
      this.sessions.delete(sessionId);
      if (onError) onError(err);
      if (onComplete) onComplete({ sdkSessionId: session.sdkSessionId });
    });

    return { sessionId, sdkSessionId: session.sdkSessionId };
  }

  async stopSession(sessionId) {
    const session = this.sessions.get(sessionId);
    if (!session) return;

    const sdkSessionId = session.sdkSessionId;
    session.active = false;

    if (session.process) {
      try { session.process.kill('SIGKILL'); } catch (e) { }
    }

    try { session.abortController.abort(); } catch { }
    this.sessions.delete(sessionId);
    return { sdkSessionId };
  }

  respondToApproval(sessionId, requestId, approved) {
    const session = this.sessions.get(sessionId);
    if (!session?.pendingApprovals || !session.process) return false;

    const entry = session.pendingApprovals.get(requestId);
    if (!entry) return false;

    session.pendingApprovals.delete(requestId);

    // Send response back to openclaude process via stdin
    const decision = approved ? 'allow' : 'deny';
    session.process.stdin.write(JSON.stringify({
      type: 'permission_response',
      decision: decision,
      toolUseID: requestId
    }) + '\n');

    return true;
  }

  getSdkSessionId(sessionId) {
    const session = this.sessions.get(sessionId);
    return session?.sdkSessionId || null;
  }

  isActive(sessionId) {
    const session = this.sessions.get(sessionId);
    return session?.active ?? false;
  }

  _transformMessage(msg) {
    // Exact same transformation as before
    switch (msg.type) {
      case 'stream_event': {
        const event = msg.event;
        if (!event) return { type: 'unknown', raw: msg };

        switch (event.type) {
          case 'content_block_start': {
            const cb = event.content_block;
            if (cb?.type === 'tool_use') return { type: 'tool_use_start', toolName: cb.name, toolId: cb.id, input: {}, parentToolUseId: msg.parent_tool_use_id };
            if (cb?.type === 'text') return { type: 'text_start' };
            if (cb?.type === 'thinking') return { type: 'thinking_start' };
            return { type: 'block_start', blockType: cb?.type };
          }
          case 'content_block_delta': {
            const delta = event.delta;
            if (delta?.type === 'text_delta') return { type: 'text_delta', text: delta.text };
            if (delta?.type === 'input_json_delta') return { type: 'tool_input_delta', json: delta.partial_json, parentToolUseId: msg.parent_tool_use_id };
            if (delta?.type === 'thinking_delta') return { type: 'thinking_delta', text: delta.thinking };
            return { type: 'delta', deltaType: delta?.type };
          }
          case 'content_block_stop': return { type: 'block_stop', index: event.index, parentToolUseId: msg.parent_tool_use_id };
          case 'message_start': return { type: 'message_start' };
          case 'message_delta': return { type: 'message_delta', stopReason: event.delta?.stop_reason, usage: event.usage };
          case 'message_stop': return { type: 'message_stop' };
          default: return { type: 'stream_other', eventType: event.type };
        }
      }
      case 'assistant': {
        const content = msg.message?.content || [];
        const blocks = content.map(block => {
          if (block.type === 'text') return { type: 'text', text: block.text };
          if (block.type === 'tool_use') return { type: 'tool_use', toolName: block.name, toolId: block.id, input: block.input };
          if (block.type === 'tool_result') return { type: 'tool_result', toolId: block.tool_use_id, content: block.content };
          return { type: block.type };
        });
        return { type: 'assistant_message', blocks, uuid: msg.uuid, sessionId: msg.session_id };
      }
      case 'result': return { type: 'result', subtype: msg.subtype, isError: msg.is_error ?? msg.subtype !== 'success', durationMs: msg.duration_ms, totalCost: msg.total_cost_usd, numTurns: msg.num_turns, usage: msg.usage, errors: msg.errors, sessionId: msg.session_id };
      case 'system': {
        if (msg.subtype === 'task_started') return { type: 'task_started', taskId: msg.task_id, toolUseId: msg.tool_use_id, description: msg.description, prompt: msg.prompt };
        if (msg.subtype === 'task_progress') return { type: 'task_progress', taskId: msg.task_id, description: msg.description, summary: msg.summary };
        if (msg.subtype === 'task_notification') return { type: 'task_complete', taskId: msg.task_id, toolUseId: msg.tool_use_id, status: msg.status };
        return { type: 'system', subtype: msg.subtype, sessionId: msg.session_id };
      }
      case 'tool_use_summary': return { type: 'tool_use_summary', summary: msg.summary, toolUseIds: msg.preceding_tool_use_ids };
      default: return { type: msg.type || 'unknown', sessionId: msg.session_id };
    }
  }
}

module.exports = { ChatBridge };