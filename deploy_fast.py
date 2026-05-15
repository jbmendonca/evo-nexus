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

print('Unzipping and Rebuilding docker...')
commands = [
    f'cd /opt/evo-nexus && unzip -o {zip_name}',
    'cd /opt/evo-nexus && docker compose build dashboard',
    'cd /opt/evo-nexus && docker compose up -d dashboard'
]

for cmd in commands:
    print(f'Running: {cmd}')
    stdin, stdout, stderr = ssh.exec_command(cmd)
    for line in stdout:
        print(line, end='')
    for line in stderr:
        print(line, end='')

ssh.close()
print('Done!')
