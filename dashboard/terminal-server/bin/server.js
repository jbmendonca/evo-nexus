#!/usr/bin/env node

const { startServer } = require('../src/server');
const { startSmartRouter } = require('../src/utils/openrouter-smart-router');
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const getFlag = (name) => {
  const idx = args.indexOf(name);
  if (idx === -1) return null;
  const next = args[idx + 1];
  return next && !next.startsWith('--') ? next : true;
};

const portArg = getFlag('--port');
const port = portArg && portArg !== true ? parseInt(portArg, 10) : 32352;
const dev = args.includes('--dev');

if (isNaN(port) || port < 1 || port > 65535) {
  console.error('Error: Port must be a number between 1 and 65535');
  process.exit(1);
}

/**
 * Read providers.json to check if Smart Router should be started.
 * The Smart Router is started when the active provider is 'openrouter-hub'
 * and the base URL points to localhost:4891.
 */
function shouldStartSmartRouter() {
  try {
    const workspaceRoot = path.resolve(__dirname, '..', '..', '..');
    const configPath = path.join(workspaceRoot, 'config', 'providers.json');
    if (!fs.existsSync(configPath)) return false;

    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    const active = config.active_provider || 'anthropic';

    // Start if active provider is openrouter-hub
    if (active === 'openrouter-hub') return true;

    // Or if any active provider points to localhost:4891
    const provider = config.providers?.[active];
    const baseUrl = provider?.env_vars?.OPENAI_BASE_URL || '';
    if (baseUrl.includes('127.0.0.1:4891') || baseUrl.includes('localhost:4891')) return true;

    return false;
  } catch {
    return false;
  }
}

/** 
 * Read the OpenRouter API key from the environment or providers.json. 
 */
function getOpenRouterApiKey() {
  // 1. Environment variable (preferred)
  if (process.env.OPENROUTER_API_KEY) return process.env.OPENROUTER_API_KEY;

  // 2. Fallback: read from providers.json -> openrouter provider
  try {
    const workspaceRoot = path.resolve(__dirname, '..', '..', '..');
    const configPath = path.join(workspaceRoot, 'config', 'providers.json');
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

    // Try the direct openrouter provider first
    const orProvider = config.providers?.openrouter;
    const key = orProvider?.env_vars?.OPENAI_API_KEY;
    if (key && key.startsWith('sk-or-')) return key;

    // Try gemini-openrouter as fallback
    const gemOR = config.providers?.['gemini-openrouter'];
    const gemKey = gemOR?.env_vars?.OPENAI_API_KEY;
    if (gemKey && gemKey.startsWith('sk-or-')) return gemKey;
  } catch { }

  return '';
}

async function main() {
  try {
    console.log('Starting EvoNexus terminal server...');
    console.log(`Port: ${port}`);

    // Start Smart Router if needed (before terminal server)
    if (shouldStartSmartRouter()) {
      const apiKey = getOpenRouterApiKey();
      if (apiKey) {
        console.log('\n📡 Starting Smart Router (free-first model cascade)...');
        const routerServer = await startSmartRouter({ apiKey, port: 4891, host: '127.0.0.1' });
        if (routerServer) {
          console.log('✅ Smart Router started successfully\n');
        } else {
          console.warn('⚠️  Smart Router failed to start (port may be in use)\n');
        }
      } else {
        console.warn('⚠️  Smart Router skipped — no OPENROUTER_API_KEY found\n');
      }
    }

    await startServer({ port, dev });

    console.log(`\n🚀 Terminal server running at http://localhost:${port}`);
    console.log('Press Ctrl+C to stop\n');
  } catch (error) {
    console.error('Error starting server:', error.message);
    process.exit(1);
  }
}

main();
