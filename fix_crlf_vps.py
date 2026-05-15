import paramiko

host = '31.97.151.13'
port = 22
username = 'root'
password = 'B@tist@1983100940'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, port, username, password)

cmd = "cd /opt/evo-nexus && apt-get update && apt-get install -y dos2unix && find . -type f -name '*.sh' -exec dos2unix {} +"
stdin, stdout, stderr = ssh.exec_command(cmd)
status = stdout.channel.recv_exit_status()

print("Status:", status)
print("Out:", stdout.read().decode('utf-8', errors='ignore'))
print("Err:", stderr.read().decode('utf-8', errors='ignore'))

# After fixing the scripts on the host, we need to rebuild the docker image
print("Rebuilding dashboard...")
cmd2 = "cd /opt/evo-nexus && docker compose build dashboard && docker compose up -d dashboard"
stdin2, stdout2, stderr2 = ssh.exec_command(cmd2)
status2 = stdout2.channel.recv_exit_status()

print("Status 2:", status2)
print("Done!")
ssh.close()
