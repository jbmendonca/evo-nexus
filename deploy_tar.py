import os
import subprocess
import paramiko
import tarfile

host = '31.97.151.13'
port = 22
username = 'root'
password = 'B@tist@1983100940'

output2 = subprocess.check_output(['git', 'ls-files']).decode('utf-8')
files = [line for line in output2.splitlines() if line]

print(f'Tarring {len(files)} files...')
tar_name = 'deploy_all_files.tar.gz'
with tarfile.open(tar_name, 'w:gz') as tar:
    for f in files:
        local_p = os.path.join('d:/Devops/EvoNexusAgro/evo-nexus', f)
        if os.path.exists(local_p):
            # Adicionar e garantir barras normais no nome dentro do tar
            arcname = f.replace('\\\\', '/')
            tar.add(local_p, arcname=arcname)

print('Connecting to VPS...')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, port, username, password)
sftp = ssh.open_sftp()

print('Uploading tar...')
sftp.put(tar_name, '/opt/evo-nexus/' + tar_name)
sftp.close()

print('Extracting and Rebuilding docker...')
commands = [
    f'cd /opt/evo-nexus && tar -xzf {tar_name}',
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
