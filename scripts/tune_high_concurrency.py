#!/usr/bin/env python3
import re, shutil, subprocess
from pathlib import Path
CONF=Path("/etc/nginx/nginx.conf")
BACKUP=Path("/etc/nginx/nginx.conf.streamforge-pre-high-concurrency")
DROPIN=Path("/etc/systemd/system/nginx.service.d/streamforge-high-concurrency.conf")
SYSCTL=Path("/etc/sysctl.d/99-streamforge-high-concurrency.conf")
def main():
    if CONF.exists():
        original=CONF.read_text(encoding="utf-8",errors="ignore"); text=original
        text=re.sub(r"(?m)^\s*worker_processes\s+[^;]+;","worker_processes auto;",text,count=1)
        if "worker_rlimit_nofile" in text:
            text=re.sub(r"(?m)^\s*worker_rlimit_nofile\s+[^;]+;","worker_rlimit_nofile 1048576;",text,count=1)
        else:
            m=re.search(r"(?m)^worker_processes\s+auto;\s*$",text)
            text=text[:m.end()]+"\nworker_rlimit_nofile 1048576;"+text[m.end():] if m else "worker_processes auto;\nworker_rlimit_nofile 1048576;\n"+text
        ev=re.search(r"events\s*\{(?P<body>.*?)\}",text,re.S)
        if not ev: raise RuntimeError("nginx.conf has no events block")
        body=ev.group("body")
        body=re.sub(r"\bworker_connections\s+\d+\s*;","worker_connections 65535;",body,count=1) if "worker_connections" in body else body+"\n    worker_connections 65535;\n"
        body=re.sub(r"\bmulti_accept\s+(?:on|off)\s*;","multi_accept on;",body,count=1) if "multi_accept" in body else body+"    multi_accept on;\n"
        text=text[:ev.start("body")]+body+text[ev.end("body"):]
        if text!=original:
            if not BACKUP.exists(): shutil.copy2(CONF,BACKUP)
            CONF.write_text(text,encoding="utf-8")
    DROPIN.parent.mkdir(parents=True,exist_ok=True)
    DROPIN.write_text("[Service]\nLimitNOFILE=1048576\nTasksMax=infinity\n")
    SYSCTL.write_text("""# StreamForge high-concurrency baseline
fs.file-max = 2097152
net.core.somaxconn = 65535
net.core.netdev_max_backlog = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 10240 65535
net.ipv4.tcp_fin_timeout = 15
""")
    subprocess.run(["nginx","-t"],check=True)
    subprocess.run(["systemctl","daemon-reload"],check=True)
    subprocess.run(["sysctl","--system"],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return 0
if __name__=="__main__": raise SystemExit(main())
