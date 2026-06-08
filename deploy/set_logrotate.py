import paramiko, os
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('47.79.86.112', port=22, username='root', password='Lxk828221', timeout=15)

si, so, se = c.exec_command("""cat > /etc/logrotate.d/wangcai << 'EOF'
/opt/wangcai/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    maxsize 50M
    copytruncate
}
EOF
cat /etc/logrotate.d/wangcai
""", get_pty=True, timeout=15)

print(so.read().decode(errors='ignore'))
c.close()
print("Logrotate configured!")
