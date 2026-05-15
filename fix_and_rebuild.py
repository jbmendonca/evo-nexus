import paramiko

host = '31.97.151.13'
port = 22
username = 'root'
password = 'B@tist@1983100940'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, port, username, password)

# Fix CRLF em scripts shell
commands = [
    # Instala dos2unix ou usa sed para converter
    'cd /opt/evo-nexus && sed -i "s/\\r$//" entrypoint.sh start-dashboard.sh && echo "CRLF fixed"',
    # Rebuild com os scripts corrigidos
    'cd /opt/evo-nexus && docker compose build --no-cache dashboard 2>&1 | tail -20',
    'cd /opt/evo-nexus && docker compose up -d dashboard 2>&1',
]

for cmd in commands:
    print(f'>>> {cmd[:80]}')
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=900)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    print(f'OUT: {out.strip()[:500]}')
    if err.strip():
        print(f'ERR: {err.strip()[:300]}')
    print()

ssh.close()
print('Done!')
