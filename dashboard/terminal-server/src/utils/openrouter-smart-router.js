/**
 * OpenRouter Smart Router — Free-first model cascade proxy.
 *
 * Spawns a lightweight HTTP server (default :4891) that speaks the
 * OpenAI-compatible /v1/chat/completions API.  Incoming requests are
 * forwarded to https://openrouter.ai/api/v1 with automatic retry
 * across a prioritised model cascade:
 *
 *   Tier 1 (free)  → try each free model in order
 *   Tier 2 (paid)  → fall back to cheap paid models
 *
 * When a model returns HTTP 429 (rate-limited) or 503 (overloaded)
 * the router immediately retries with the next model in the cascade.
 *
 * Usage:
 *   const { startSmartRouter, stopSmartRouter } = require('./openrouter-smart-router');
 *   const server = await startSmartRouter({ apiKey: 'sk-or-...', port: 4891 });
 *   // later …
 *   stopSmartRouter(server);
 */

const http = require('http');
const https = require('https');
const { URL } = require('url');

// ── Model cascade ────────────────────────────────────────────────
// Order matters: first model tried first.

const FREE_MODELS = [
  // Proven working with openclaude tool calling:
  'openai/gpt-oss-120b:free',
  'google/gemma-4-31b-it:free',
  // May have tool calling issues (400) but worth trying:
  'qwen/qwen3-coder-480b:free',
  'nvidia/nemotron-3-super-120b:free',
  'meta-llama/llama-3.3-70b-instruct:free',
  'minimax/minimax-m2.5:free',
  'glm-4-5-air:free',
];

const PAID_MODELS = [
  'google/gemini-2.5-flash',       // ~$0.15/M — best value
  'moonshotai/kimi-k2.6',          // ~$0.3/M — strong reasoning
  'anthropic/claude-sonnet-4',     // ~$3/M — highest quality fallback
];

const ALL_MODELS = [...FREE_MODELS, ...PAID_MODELS];

// ── Per-model rate-limit tracker ─────────────────────────────────
// Maps model → timestamp when rate-limit was last seen.
// A model is "cooled down" for COOLDOWN_MS after a 429.
const rateLimitMap = new Map();
const COOLDOWN_MS = 60_000; // 1 minute cooldown per model

function isModelAvailable(model) {
  const lastHit = rateLimitMap.get(model);
  if (!lastHit) return true;
  if (Date.now() - lastHit > COOLDOWN_MS) {
    rateLimitMap.delete(model);
    return true;
  }
  return false;
}

function markRateLimited(model) {
  rateLimitMap.set(model, Date.now());
  console.log(`[smart-router] ⚠ Model ${model} rate-limited, cooling down for ${COOLDOWN_MS / 1000}s`);
}

// ── Upstream proxy call ──────────────────────────────────────────

const OPENROUTER_BASE = 'https://openrouter.ai';

function proxyToOpenRouter(apiKey, model, bodyBuffer, reqHeaders) {
  return new Promise((resolve) => {
    const url = new URL('/api/v1/chat/completions', OPENROUTER_BASE);

    // Parse the body to inject/override the model field
    let parsed;
    try {
      parsed = JSON.parse(bodyBuffer.toString('utf8'));
    } catch {
      resolve({ status: 400, headers: {}, body: Buffer.from('{"error":"invalid JSON body"}') });
      return;
    }
    parsed.model = model;
    const payload = Buffer.from(JSON.stringify(parsed), 'utf8');

    const isStreaming = parsed.stream === true;

    const headers = {
      'Content-Type': 'application/json',
      'Content-Length': payload.length,
      'Authorization': `Bearer ${apiKey}`,
      'HTTP-Referer': 'https://evonexus.local',
      'X-Title': 'EvoNexus Smart Router',
    };

    const req = https.request(url, { method: 'POST', headers }, (res) => {
      if (isStreaming && res.statusCode >= 200 && res.statusCode < 300) {
        // For streaming responses, resolve immediately with the stream
        resolve({
          status: res.statusCode,
          headers: res.headers,
          stream: res,
          model,
        });
      } else {
        // Buffer non-streaming or error responses
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          resolve({
            status: res.statusCode,
            headers: res.headers,
            body: Buffer.concat(chunks),
            model,
          });
        });
      }
    });

    req.setTimeout(30_000, () => {
      req.destroy(new Error('timeout'));
    });

    req.on('error', (err) => {
      resolve({
        status: 502,
        headers: {},
        body: Buffer.from(JSON.stringify({ error: `upstream error: ${err.message}` })),
        model,
      });
    });

    req.write(payload);
    req.end();
  });
}

// ── Request handler ──────────────────────────────────────────────

async function handleChatCompletions(apiKey, bodyBuffer, reqHeaders, res) {
  // Determine if request is streaming
  let isStreaming = false;
  try {
    const parsed = JSON.parse(bodyBuffer.toString('utf8'));
    isStreaming = parsed.stream === true;
  } catch {}

  const availableModels = ALL_MODELS.filter(isModelAvailable);

  if (availableModels.length === 0) {
    console.log('[smart-router] ❌ All models are rate-limited! Waiting for cooldowns...');
    // Reset all cooldowns and try again with free models
    rateLimitMap.clear();
    availableModels.push(...FREE_MODELS);
  }

  const freeAvailable = availableModels.filter((m) => m.includes(':free'));
  const paidAvailable = availableModels.filter((m) => !m.includes(':free'));
  const tierLabel = freeAvailable.length > 0 ? 'FREE' : 'PAID';

  console.log(
    `[smart-router] 🔄 New request — ${freeAvailable.length} free + ${paidAvailable.length} paid models available (trying ${tierLabel} first)`
  );

  // Try models in cascade order
  const orderedModels = [...freeAvailable, ...paidAvailable];

  for (let i = 0; i < orderedModels.length; i++) {
    const model = orderedModels[i];
    const tierEmoji = model.includes(':free') ? '🆓 FREE' : '💰 PAID';
    const tierHeader = model.includes(':free') ? 'free' : 'paid';
    console.log(`[smart-router]   -> Trying [${i + 1}/${orderedModels.length}] ${tierEmoji}: ${model}`);

    const result = await proxyToOpenRouter(apiKey, model, bodyBuffer, reqHeaders);

    // Success — stream or buffer back to client
    if (result.status >= 200 && result.status < 300) {
      console.log(`[smart-router] ✅ Success with ${model}`);

      if (result.stream) {
        // Pipe streaming response
        res.writeHead(result.status, {
          'Content-Type': result.headers['content-type'] || 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
          'X-SmartRouter-Model': model,
          'X-SmartRouter-Tier': tierHeader,
        });
        result.stream.pipe(res);
      } else {
        // Inject router metadata into response
        let responseBody = result.body;
        try {
          const parsed = JSON.parse(result.body.toString('utf8'));
          parsed._smart_router = { model, tier: model.includes(':free') ? 'free' : 'paid' };
          responseBody = Buffer.from(JSON.stringify(parsed), 'utf8');
        } catch {}

        res.writeHead(result.status, {
          'Content-Type': 'application/json',
          'X-SmartRouter-Model': model,
          'X-SmartRouter-Tier': tierHeader,
        });
        res.end(responseBody);
      }
      return;
    }

    // Rate limited or overloaded — mark and try next
    if (result.status === 429 || result.status === 503) {
      markRateLimited(model);

      // Parse retry info from error body
      let retryInfo = '';
      try {
        const errBody = JSON.parse(result.body.toString('utf8'));
        retryInfo = errBody.error?.message || '';
      } catch {}
      console.log(`[smart-router]   ⚠ ${model} returned ${result.status}: ${retryInfo.slice(0, 120)}`);
      continue;
    }

    // Other error (400, 401, etc.) — stop cascade, return error
    if (result.status === 401 || result.status === 403) {
      console.log(`[smart-router] ❌ Auth error (${result.status}) — check API key`);
      res.writeHead(result.status, { 'Content-Type': 'application/json' });
      res.end(result.body);
      return;
    }

    // 4xx client errors — not model-specific, pass through
    if (result.status >= 400 && result.status < 500) {
      console.log(`[smart-router]   ⚠ ${model} returned ${result.status}, trying next...`);
      continue;
    }

    // 5xx server errors — try next model
    console.log(`[smart-router]   ⚠ ${model} returned ${result.status}, trying next...`);
    continue;
  }

  // All models exhausted
  console.log('[smart-router] ❌ All models exhausted');
  res.writeHead(503, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({
    error: {
      message: 'All models are currently rate-limited or unavailable. Please try again in 60 seconds.',
      type: 'smart_router_exhausted',
      models_tried: orderedModels.length,
    }
  }));
}

// ── Models list endpoint ─────────────────────────────────────────

function handleModelsList(res) {
  const models = ALL_MODELS.map((id) => ({
    id,
    object: 'model',
    owned_by: id.split('/')[0] || 'openrouter',
    available: isModelAvailable(id),
    tier: id.includes(':free') ? 'free' : 'paid',
  }));

  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ object: 'list', data: models }));
}

// ── Health / status endpoint ─────────────────────────────────────

function handleStatus(res) {
  const status = {
    status: 'running',
    models: ALL_MODELS.length,
    free_available: FREE_MODELS.filter(isModelAvailable).length,
    paid_available: PAID_MODELS.filter(isModelAvailable).length,
    cooldowns: Object.fromEntries(
      [...rateLimitMap.entries()].map(([model, ts]) => [
        model,
        { since: new Date(ts).toISOString(), remaining_s: Math.max(0, COOLDOWN_MS - (Date.now() - ts)) / 1000 },
      ])
    ),
  };

  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(status, null, 2));
}

// ── Server lifecycle ─────────────────────────────────────────────

function startSmartRouter(options = {}) {
  const {
    apiKey = process.env.OPENROUTER_API_KEY || process.env.OPENAI_API_KEY || '',
    port = parseInt(process.env.SMART_ROUTER_PORT || '4891', 10),
    host = '127.0.0.1',
  } = options;

  if (!apiKey) {
    console.error('[smart-router] ❌ No API key provided — set OPENROUTER_API_KEY or pass apiKey option');
    return null;
  }

  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      // CORS
      res.setHeader('Access-Control-Allow-Origin', '*');
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

      if (req.method === 'OPTIONS') {
        res.writeHead(204);
        res.end();
        return;
      }

      const url = new URL(req.url, `http://${host}:${port}`);
      const pathname = url.pathname.replace(/\/+$/, '');

      // Route: POST /v1/chat/completions
      if (req.method === 'POST' && (pathname === '/v1/chat/completions' || pathname === '/chat/completions')) {
        const chunks = [];
        req.on('data', (chunk) => chunks.push(chunk));
        req.on('end', () => {
          const bodyBuffer = Buffer.concat(chunks);
          handleChatCompletions(apiKey, bodyBuffer, req.headers, res).catch((err) => {
            console.error('[smart-router] Unhandled error:', err);
            if (!res.headersSent) {
              res.writeHead(500, { 'Content-Type': 'application/json' });
            }
            res.end(JSON.stringify({ error: err.message }));
          });
        });
        return;
      }

      // Route: GET /v1/models
      if (req.method === 'GET' && (pathname === '/v1/models' || pathname === '/models')) {
        handleModelsList(res);
        return;
      }

      // Route: GET /status
      if (req.method === 'GET' && pathname === '/status') {
        handleStatus(res);
        return;
      }

      // Route: GET /health
      if (req.method === 'GET' && (pathname === '/health' || pathname === '/')) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'ok', version: '1.0.0', name: 'EvoNexus Smart Router' }));
        return;
      }

      // 404
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Not found', path: pathname }));
    });

    server.listen(port, host, () => {
      console.log(`[smart-router] 🚀 Smart Router listening on http://${host}:${port}`);
      console.log(`[smart-router]    ${FREE_MODELS.length} free + ${PAID_MODELS.length} paid models in cascade`);
      console.log(`[smart-router]    Cooldown per model: ${COOLDOWN_MS / 1000}s after rate-limit`);
      resolve(server);
    });

    server.on('error', (err) => {
      if (err.code === 'EADDRINUSE') {
        console.warn(`[smart-router] ⚠ Port ${port} already in use — router may already be running`);
        resolve(null);
      } else {
        console.error(`[smart-router] ❌ Server error: ${err.message}`);
        resolve(null);
      }
    });
  });
}

function stopSmartRouter(server) {
  if (server) {
    server.close();
    console.log('[smart-router] 🛑 Smart Router stopped');
  }
}

// Allow standalone execution: node openrouter-smart-router.js
if (require.main === module) {
  startSmartRouter().then((server) => {
    if (!server) {
      process.exit(1);
    }
    process.on('SIGINT', () => { stopSmartRouter(server); process.exit(0); });
    process.on('SIGTERM', () => { stopSmartRouter(server); process.exit(0); });
  });
}

module.exports = { startSmartRouter, stopSmartRouter, FREE_MODELS, PAID_MODELS, ALL_MODELS };
