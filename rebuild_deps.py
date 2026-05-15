import paramiko

host = '31.97.151.13'
port = 22
username = 'root'
password = 'B@tist@1983100940'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, port, username, password)

commands = [
    'cd /opt/evo-nexus && docker compose build --no-cache dashboard 2>&1',
    'cd /opt/evo-nexus && docker compose up -d dashboard',
    'docker exec evonexus-dashboard uv run python -c "import openpyxl; import pdfplumber; print(\'DEPS OK\')"',
]

for cmd in commands:
    print(f'>>> {cmd}')
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=600)
    for line in stdout:
        print(line, end='')
    for line in stderr:
        print('[ERR]', line, end='')
    print()

ssh.close()
print('Done!')
