#!/bin/bash
set -e

echo "=== Fix: Adicionar conftest.py ao nf-processor/tests ==="

# Criar conftest.py para garantir que o path correto é usado
cat > /workspace/workspace/agro-roraima/nf-processor/tests/conftest.py << 'PYEOF'
"""Ensure nf-processor modules are importable from here."""
import sys
import os

_NF_DIR = os.path.join(os.path.dirname(__file__), os.pardir)
_NF_DIR = os.path.abspath(_NF_DIR)

# Remove any sped/ path entries and insert nf-processor/ first
sys.path = [p for p in sys.path if 'sped' not in p]
if _NF_DIR not in sys.path:
    sys.path.insert(0, _NF_DIR)
PYEOF

echo "conftest.py criado em nf-processor/tests/"
cat /workspace/workspace/agro-roraima/nf-processor/tests/conftest.py

echo ""
echo "=== Re-rodando suite COMPLETA ==="
cd /workspace/workspace/agro-roraima
/workspace/.venv/bin/python3 -m pytest */tests/ --tb=short 2>&1 | tail -15

echo ""
echo "=== DONE ==="
