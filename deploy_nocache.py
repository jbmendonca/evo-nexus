import os
import subprocess
import paramiko
import zipfile

host = '31.97.151.13'
port = 22
username = 'root'
password = 'B@tist@1983100940'

# List tracked files
output2 = subprocess.check_output(['git', 'ls-files']).decode('utf-8')
files = [line for line in output2.splitlines() if line]

print(f'Zipping {len(files)} files...')
zip_name = 'deploy_all_files.zip'
with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as z:
    for f in files:
        local_p = os.path.join('d:/Devops/EvoNexusAgro/evo-nexus', f)
        if os.path.exists(local_p):
            z.write(local_p, f)

print('Connecting to VPS...')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, port, username, password)
sftp = ssh.open_sftp()

print('Uploading zip...')
sftp.put(zip_name, '/opt/evo-nexus/' + zip_name)
sftp.close()

print('Installing unzip + extracting + rebuilding (--no-cache)...')
commands = [
    f'apt-get update -qq && apt-get install -y -qq unzip > /dev/null 2>&1 && echo "unzip installed"',
    f'cd /opt/evo-nexus && unzip -o {zip_name} && echo "unzip done"',
    'grep -c "PdfNfExtractor" /opt/evo-nexus/dashboard/frontend/src/App.tsx',
    'grep -c "pdfNfExtractor" /opt/evo-nexus/dashboard/frontend/src/components/Sidebar.tsx',
    'ls -la /opt/evo-nexus/dashboard/backend/routes/pdf_nf.py',
    'grep "openpyxl" /opt/evo-nexus/pyproject.toml',
    'cd /opt/evo-nexus && docker compose build --no-cache dashboard',
    'cd /opt/evo-nexus && docker compose up -d dashboard',
]

for cmd in commands:
    print(f'\n>>> {cmd[:80]}')
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=900)
    for line in stdout:
        try:
            print(line, end='')
        except UnicodeEncodeError:
            print(line.encode('ascii', 'replace').decode(), end='')
    for line in stderr:
        try:
            print(line, end='')
        except UnicodeEncodeError:
            print(line.encode('ascii', 'replace').decode(), end='')

ssh.close()
print('\nDone!')
