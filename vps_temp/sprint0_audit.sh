#!/bin/bash
set -e

echo "=========================================="
echo " SPRINT 0 — FUNDAÇÃO & VALIDAÇÃO"
echo "=========================================="

echo ""
echo "=== [S0.1] Auditoria do Banco de Dados ==="
python3 -c "
import sqlite3, os
db_path = '/workspace/dashboard/data/evonexus.db'
print(f'DB Path: {db_path}')
print(f'DB Size: {os.path.getsize(db_path)} bytes')
conn = sqlite3.connect(db_path)
cursor = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")
tables = [r[0] for r in cursor]
print(f'Total tables: {len(tables)}')
print('Tables:')
for t in tables:
    count = conn.execute(f'SELECT COUNT(*) FROM [{t}]').fetchone()[0]
    print(f'  {t}: {count} rows')
agro_tables = [t for t in tables if 'agro' in t.lower()]
print(f'\nAgro-specific tables: {len(agro_tables)}')
for t in agro_tables:
    print(f'  ✅ {t}')
if not agro_tables:
    print('  ⚠️  NENHUMA tabela agro encontrada — setup_*.py precisa rodar')
conn.close()
"

echo ""
echo "=== [S0.2] Verificando Scripts Setup dos Módulos ==="
cd /workspace/workspace/agro-roraima
for setup in $(find . -name 'setup_*.py' -type f | sort); do
    echo "  📁 Found: $setup"
done

echo ""
echo "=== [S0.3] Verificando Heartbeats YAML ==="
python3 -c "
import yaml
with open('/workspace/config/heartbeats.yaml', 'r') as f:
    data = yaml.safe_load(f)

agro_hbs = [h for h in data.get('heartbeats', []) if h.get('id','').startswith('agro-')]
print(f'Total heartbeats: {len(data.get(\"heartbeats\", []))}')
print(f'Agro heartbeats: {len(agro_hbs)}')
for h in agro_hbs:
    status = '🟢 ENABLED' if h.get('enabled') else '⚪ DISABLED'
    interval = h.get('interval_seconds', 0)
    warn = ' ⚠️ ABAIXO DO MÍNIMO (60s)' if interval < 60 else ''
    print(f'  {h[\"id\"]}: {interval}s → {h[\"agent\"]} {status}{warn}')
"

echo ""
echo "=== [S0.4] Verificando Agentes Custom ==="
ls -la /workspace/.claude/agents/custom-*.md 2>/dev/null | while read line; do
    echo "  ✅ $line"
done

echo ""
echo "=== [S0.5] Verificando Testes Disponíveis ==="
cd /workspace/workspace/agro-roraima
test_count=0
for test_dir in $(find . -name 'tests' -type d | sort); do
    test_files=$(find "$test_dir" -name 'test_*.py' | wc -l)
    test_count=$((test_count + test_files))
    echo "  📁 $test_dir: $test_files test files"
done
echo "  Total test files: $test_count"

echo ""
echo "=========================================="
echo " SPRINT 0 — AUDITORIA COMPLETA"
echo "=========================================="
