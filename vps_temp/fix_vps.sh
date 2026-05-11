#!/bin/bash
set -e
cd /opt/evo-nexus

echo "=== [1/4] Fixing Sidebar max-h-96 -> max-h-[800px] ==="
sed -i 's/max-h-96 opacity-100/max-h-[800px] opacity-100/g' dashboard/frontend/src/components/Sidebar.tsx
grep -n 'max-h-' dashboard/frontend/src/components/Sidebar.tsx

echo ""
echo "=== [2/4] Fixing pt-BR i18n: Separador Fiscal -> Separador XML ==="
sed -i "s/nfeSeparator: 'Separador Fiscal'/nfeSeparator: 'Separador XML'/" dashboard/frontend/src/i18n/locales/pt-BR/index.ts
grep -n 'nfeSeparator' dashboard/frontend/src/i18n/locales/pt-BR/index.ts

echo ""
echo "=== [3/4] Adding nfeSeparator to en-US ==="
if ! grep -q 'nfeSeparator' dashboard/frontend/src/i18n/locales/en-US/index.ts; then
  sed -i "/updateAvailable:.*version/a\\      nfeSeparator: 'XML Separator'," dashboard/frontend/src/i18n/locales/en-US/index.ts
  echo "Added nfeSeparator to en-US"
else
  echo "nfeSeparator already exists in en-US"
fi
grep -n 'nfeSeparator' dashboard/frontend/src/i18n/locales/en-US/index.ts

echo ""
echo "=== [4/4] Adding nfeSeparator to es ==="
if ! grep -q 'nfeSeparator' dashboard/frontend/src/i18n/locales/es/index.ts; then
  sed -i "/updateAvailable:.*version/a\\      nfeSeparator: 'Separador XML'," dashboard/frontend/src/i18n/locales/es/index.ts
  echo "Added nfeSeparator to es"
else
  echo "nfeSeparator already exists in es"
fi
grep -n 'nfeSeparator' dashboard/frontend/src/i18n/locales/es/index.ts

echo ""
echo "=== ALL PATCHES APPLIED SUCCESSFULLY ==="
