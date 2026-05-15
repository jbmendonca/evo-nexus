import os
import subprocess
import paramiko

host = '31.97.151.13'
port = 22
username = 'root'
password = 'B@tist@1983100940'

try:
    output = subprocess.check_output(['git', 'status', '--porcelain']).decode('utf-8')
except Exception as e:
    print('Erro executando git status')
    exit(1)

files = []
for line in output.splitlines():
    if len(line) > 3:
        status = line[0:2]
        filepath = line[3:]
        filepath = filepath.strip('\"')
        if status != ' D':
            files.append(filepath)

# Also add the dashboard/frontend/dist directory if we want, but docker rebuilds it so no need.
# Wait, actually git status won't show all files modified between HEAD and v0.33.0 because I already committed them!
# To get files modified in the last commit (which has my merge resolution):
try:
    output2 = subprocess.check_output(['git', 'ls-files']).decode('utf-8')
    for line in output2.splitlines():
        if line and line not in files:
            files.append(line)
except:
    pass

print(f'Uploading {len(files)} files...')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, port, username, password)
sftp = ssh.open_sftp()

for f in files:
    local_p = os.path.join('d:/Devops/EvoNexusAgro/evo-nexus', f)
    remote_p = '/opt/evo-nexus/' + f.replace('\\', '/')
    print(f'Uploading {f} -> {remote_p}')
    try:
        dir_path = os.path.dirname(remote_p)
        ssh.exec_command('mkdir -p ' + dir_path)
        sftp.put(local_p, remote_p)
    except Exception as e:
        print('Erro no upload de', f, e)

sftp.close()

print('Rebuilding docker...')
stdin, stdout, stderr = ssh.exec_command('cd /opt/evo-nexus && docker compose build dashboard && docker compose up -d dashboard')
for line in stdout:
    print(line, end='')
for line in stderr:
    print(line, end='')

ssh.close()
