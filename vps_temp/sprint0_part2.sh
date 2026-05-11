#!/bin/bash
set -e

echo "=========================================="
echo " SPRINT 0 — PARTE 2: SETUP & HEARTBEATS"
echo "=========================================="

echo ""
echo "=== [S0.3] Verificando Heartbeats YAML ==="
cd /workspace
.venv/bin/python3 -c "
import yaml
with open('/workspace/config/heartbeats.yaml', 'r') as f:
    data = yaml.safe_load(f)

agro_hbs = [h for h in data.get('heartbeats', []) if h.get('id','').startswith('agro-')]
print(f'Total heartbeats: {len(data.get(\"heartbeats\", []))}')
print(f'Agro heartbeats: {len(agro_hbs)}')
issues = []
for h in agro_hbs:
    status = '🟢 ENABLED' if h.get('enabled') else '⚪ DISABLED'
    interval = h.get('interval_seconds', 0)
    warn = ''
    if interval < 60:
        warn = ' ⚠️ ABAIXO DO MÍNIMO (60s)'
        issues.append(h['id'])
    print(f'  {h[\"id\"]}: {interval}s -> {h[\"agent\"]} {status}{warn}')
    max_turns = h.get('max_turns', 0)
    if max_turns < 1:
        print(f'    ⚠️ max_turns={max_turns} (mínimo 1)')
        issues.append(f'{h[\"id\"]}-turns')

if issues:
    print(f'\n⚠️ {len(issues)} issues encontradas nos heartbeats')
else:
    print('\n✅ Todos os heartbeats ok')
"

echo ""
echo "=== [S0.6] Executando Setup Scripts Faltantes ==="
cd /workspace/workspace/agro-roraima

# Verificar quais tabelas agro JÁ existem
existing=$(.venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('/workspace/dashboard/data/evonexus.db')
cursor = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'agro_%' ORDER BY name\")
for r in cursor:
    print(r[0])
conn.close()
" 2>/dev/null || echo "")

echo "Tabelas agro existentes:"
echo "$existing"

# Executar setups que podem criar tabelas adicionais
echo ""
echo "Tentando executar setup scripts (podem falhar se tabelas já existem)..."

for setup in atendimento/setup_atendimento.py societario/setup_societario.py hitl/setup_hitl.py dominio/setup_dominio.py sped/setup_sped.py; do
    echo "  → Executando $setup..."
    cd /workspace/workspace/agro-roraima
    /workspace/.venv/bin/python3 "$setup" 2>&1 | head -5 || echo "    ⚠️ Falhou (pode ser normal se tabela já existe)"
    echo ""
done

echo ""
echo "=== [S0.7] Tabelas Agro Após Setup ==="
/workspace/.venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('/workspace/dashboard/data/evonexus.db')
cursor = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'agro_%' ORDER BY name\")
tables = [r[0] for r in cursor]
print(f'Total agro tables: {len(tables)}')
for t in tables:
    count = conn.execute(f'SELECT COUNT(*) FROM [{t}]').fetchone()[0]
    print(f'  ✅ {t}: {count} rows')
conn.close()
"

echo ""
echo "=== [S0.8] Verificando Testes Unitários ==="
cd /workspace/workspace/agro-roraima
echo "Rodando testes com pytest..."
/workspace/.venv/bin/python3 -m pytest */tests/ -v --tb=short 2>&1 | tail -40 || echo "⚠️ Alguns testes falharam (analisar acima)"

echo ""
echo "=========================================="
echo " SPRINT 0 PARTE 2 — COMPLETO"
echo "=========================================="
