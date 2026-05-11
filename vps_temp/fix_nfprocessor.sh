#!/bin/bash
set -e

echo "=== [FIX-2] Corrigir nf-processor ImportError ==="
cd /workspace/workspace/agro-roraima/nf-processor

/workspace/.venv/bin/python3 -c "
import re

with open('/workspace/workspace/agro-roraima/nf-processor/batch_runner.py', 'r') as f:
    content = f.read()

if 'sys.path.insert' not in content:
    old = 'from processor import NFProcessor'
    new = 'import sys, os\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nfrom processor import NFProcessor'
    content = content.replace(old, new, 1)
    
    with open('/workspace/workspace/agro-roraima/nf-processor/batch_runner.py', 'w') as f:
        f.write(content)
    print('Fixed batch_runner.py with sys.path fix')
else:
    print('Fix already applied')
"

echo ""
echo "Verificando fix..."
head -50 /workspace/workspace/agro-roraima/nf-processor/batch_runner.py | grep -n "sys.path\|from processor"

echo ""
echo "=== Re-rodando testes nf-processor ==="
cd /workspace/workspace/agro-roraima
/workspace/.venv/bin/python3 -m pytest nf-processor/tests/ -v --tb=short 2>&1 | tail -35

echo ""
echo "=== DONE ==="
