import paramiko

host = '31.97.151.13'
port = 22
username = 'root'
password = 'B@tist@1983100940'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, port, username, password)

# Instala as dependencias diretamente no container em execucao
# e depois faz rebuild com o uv.lock atualizado
commands = [
    # Instala no container em execucao (solucao rapida)
    'docker exec evonexus-dashboard uv pip install openpyxl pdfplumber pdf2image 2>&1 || docker exec evonexus-dashboard uv run pip install openpyxl pdfplumber pdf2image 2>&1',
    # Instala o poppler para pdf2image
    'apt-get install -y poppler-utils 2>&1 || true',
    # Verifica
    'docker exec evonexus-dashboard uv run python -c "import openpyxl; import pdfplumber; print(\'ALL DEPS OK\')" 2>&1',
    # Reinicia o Flask dentro do container
    'docker exec evonexus-dashboard pkill -f "python.*app.py" 2>&1 || true',
]

for cmd in commands:
    print(f'>>> {cmd[:80]}...')
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    print(f'OUT: {out[:500]}')
    if err:
        print(f'ERR: {err[:300]}')
    print()

ssh.close()
print('Done!')
