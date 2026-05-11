#!/bin/bash
set -e

echo "=== Fix: Criar __init__.py nos módulos para resolver colisão de namespace ==="

# Criar __init__.py em nf-processor e sped para namespacing
touch /workspace/workspace/agro-roraima/nf-processor/__init__.py
touch /workspace/workspace/agro-roraima/nf-processor/tests/__init__.py
touch /workspace/workspace/agro-roraima/sped/__init__.py
touch /workspace/workspace/agro-roraima/sped/tests/__init__.py

echo "Criados __init__.py:"
find /workspace/workspace/agro-roraima/nf-processor -name __init__.py
find /workspace/workspace/agro-roraima/sped -name __init__.py

# Também adicionar em todos os módulos para consistência
for dir in atendimento biological-assets contabil controladoria dominio exec-dashboard fiscal hitl ml-anomaly orquestrador rh risk-assessor security societario thomson-reuters; do
    touch "/workspace/workspace/agro-roraima/$dir/__init__.py" 2>/dev/null
    if [ -d "/workspace/workspace/agro-roraima/$dir/tests" ]; then
        touch "/workspace/workspace/agro-roraima/$dir/tests/__init__.py" 2>/dev/null
    fi
done

# Atualizar conftest do nf-processor tests para usar import absoluto
cat > /workspace/workspace/agro-roraima/nf-processor/tests/conftest.py << 'PYEOF'
"""Ensure nf-processor modules are correctly importable."""
import sys
import os

# Force nf-processor directory into sys.path BEFORE any sped paths
_NF_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

# Remove sped from path to avoid collision
sys.path = [p for p in sys.path if os.path.join('agro-roraima', 'sped') not in p]

# Ensure nf-processor is first
if _NF_DIR in sys.path:
    sys.path.remove(_NF_DIR)
sys.path.insert(0, _NF_DIR)

# Force reimport of processor module from nf-processor
for mod_name in list(sys.modules.keys()):
    if mod_name in ('processor', 'validator', 'classifier', 'batch_runner', 'extractor', 'state_machine', 'ocr_engine'):
        del sys.modules[mod_name]
PYEOF

echo ""
echo "=== Re-rodando suite COMPLETA ==="
cd /workspace/workspace/agro-roraima
/workspace/.venv/bin/python3 -m pytest */tests/ --tb=line 2>&1 | tail -10

echo ""
echo "=== DONE ==="
