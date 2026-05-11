#!/bin/bash
set -e

echo "=========================================="
echo " SPRINT 0 — CORREÇÕES"
echo "=========================================="

echo ""
echo "=== [FIX-1] Corrigir heartbeat agro-orquestrador-30s: 30s → 60s ==="
cd /workspace
sed -i 's/interval_seconds: 30/interval_seconds: 60/' config/heartbeats.yaml
echo "Verificando:"
grep -A3 'agro-orquestrador' config/heartbeats.yaml | head -5

echo ""
echo "=== [FIX-2] Corrigir nf-processor ImportError (path collision) ==="
# O problema: nf-processor/batch_runner.py faz "from processor import NFProcessor"
# mas o cwd durante pytest é agro-roraima/ e encontra sped/processor.py primeiro
# Solução: usar import relativo ou sys.path fix

cd /workspace/workspace/agro-roraima/nf-processor

# Verificar a linha problemática
echo "Linha problemática em batch_runner.py:"
grep -n "from processor import" batch_runner.py

# Fix: trocar import absoluto por relativo com path manipulation
.venv/bin/python3 -c "
import re

with open('/workspace/workspace/agro-roraima/nf-processor/batch_runner.py', 'r') as f:
    content = f.read()

# Add sys.path fix at the top of the file if not already present
if 'sys.path.insert' not in content:
    # Find the first 'from processor' import
    old = 'from processor import NFProcessor'
    new = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from processor import NFProcessor'''
    content = content.replace(old, new, 1)
    
    with open('/workspace/workspace/agro-roraima/nf-processor/batch_runner.py', 'w') as f:
        f.write(content)
    print('✅ Fixed batch_runner.py with sys.path fix')
else:
    print('ℹ️ Fix already applied')
"

echo ""
echo "Verificando fix..."
grep -n "sys.path" /workspace/workspace/agro-roraima/nf-processor/batch_runner.py | head -3

echo ""
echo "=== [FIX-3] Re-rodando testes nf-processor ==="
cd /workspace/workspace/agro-roraima
/workspace/.venv/bin/python3 -m pytest nf-processor/tests/ -v --tb=short 2>&1 | tail -30

echo ""
echo "=========================================="
echo " CORREÇÕES COMPLETAS"
echo "=========================================="
