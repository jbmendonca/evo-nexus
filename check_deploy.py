import paramiko
import time

host = '31.97.151.13'
port = 22
username = 'root'
password = 'B@tist@1983100940'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, port, username, password)

print('Waiting 20s for container startup...')
time.sleep(20)

commands = [
    'docker ps --filter name=evonexus-dashboard --format "{{.Status}}"',
    'docker logs evonexus-dashboard --tail 10 2>&1',
    'docker exec evonexus-dashboard grep -rl "pdf-nf" /workspace/dashboard/frontend/dist/assets/ 2>&1 | head -3',
    'docker exec evonexus-dashboard curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/api/pdf-nf/upload -X POST',
    'docker exec evonexus-dashboard uv run python -c "import openpyxl; import pdfplumber; print(\'DEPS OK\')" 2>&1 | tail -1',
]

for cmd in commands:
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    print(f'{cmd[:70]}')
    print(f'  -> {out}')
    if err:
        print(f'  ERR: {err[:200]}')
    print()

ssh.close()
print('DONE')
