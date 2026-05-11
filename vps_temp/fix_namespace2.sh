#!/bin/bash
set -e

echo "=== Revertendo __init__.py desnecessários ==="
# Remover TODOS os __init__.py criados
find /workspace/workspace/agro-roraima -name __init__.py -delete
echo "Removidos todos __init__.py"

echo ""
echo "=== Abordagem: pytest com conftest na raiz usando importlib mode ==="

# Criar conftest.py na raiz de agro-roraima com import mode
cat > /workspace/workspace/agro-roraima/conftest.py << 'PYEOF'
"""Root conftest for agro-roraima test suite."""
import pytest

def pytest_configure(config):
    """Use importlib import mode to avoid module name collisions."""
    config.option.importmode = "importlib"
PYEOF

echo "conftest.py criado em agro-roraima/"

# Limpar conftest do nf-processor para evitar conflito
rm -f /workspace/workspace/agro-roraima/nf-processor/tests/conftest.py
echo "Removido conftest.py de nf-processor/tests/"

echo ""
echo "=== Re-rodando suite COMPLETA ==="
cd /workspace/workspace/agro-roraima
/workspace/.venv/bin/python3 -m pytest */tests/ --import-mode=importlib --tb=line 2>&1 | tail -10

echo ""
echo "=== DONE ==="
