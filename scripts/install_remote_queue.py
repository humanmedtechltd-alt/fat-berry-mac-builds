#!/usr/bin/env python3
"""Install an event-driven systemd reporter over the existing admin SSH key."""
from __future__ import annotations
import base64
import json
from pathlib import Path
import subprocess

ROOT = "/home/ubuntu/fatberry-mac-builds/release-20261014"


def main() -> None:
    scripts = Path(__file__).parent
    service = f"""[Unit]
Description=Fat Berry installer queue report (event driven)
After=network.target
StartLimitIntervalSec=0

[Service]
Type=oneshot
User=ubuntu
UMask=0077
ExecStartPre=/usr/bin/sleep 2
ExecStart=/usr/bin/python3 {ROOT}/reconcile.py {ROOT}
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={ROOT}
MemoryMax=256M
TimeoutStartSec=300
"""
    watcher = f"""[Unit]
Description=React to Fat Berry runner status uploads

[Path]
PathModified={ROOT}/events.log
PathModified={ROOT}/input/index.json
PathModified={ROOT}/input/windows-index.json
Unit=fatberry-installer-report.service

[Install]
WantedBy=multi-user.target
"""
    files = {f"{ROOT}/transfer.py": (scripts / "transfer.py").read_text(encoding="utf-8"),
             f"{ROOT}/reconcile.py": (scripts / "reconcile.py").read_text(encoding="utf-8"),
             f"{ROOT}/compilation_identity.py": (scripts / "compilation_identity.py").read_text(encoding="utf-8"),
             "/etc/systemd/system/fatberry-installer-report.service": service,
             "/etc/systemd/system/fatberry-installer-report.path": watcher}
    program = """import json,sys,pathlib,os,subprocess
files=json.load(sys.stdin)
for name,content in files.items():
 p=pathlib.Path(name);tmp=p.with_suffix(p.suffix+'.next');tmp.write_text(content);tmp.chmod(0o600);os.replace(tmp,p)
 if name.startswith('/home/ubuntu/'):os.chown(p,1000,1000)
root=pathlib.Path('/home/ubuntu/fatberry-mac-builds/release-20261014')
(root/'events.log').touch(exist_ok=True);os.chown(root/'events.log',1000,1000)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','reset-failed','fatberry-installer-report.path','fatberry-installer-report.service'],check=True)
subprocess.run(['systemctl','enable','--now','fatberry-installer-report.path'],check=True)
subprocess.run(['systemctl','restart','fatberry-installer-report.path'],check=True)
subprocess.run(['systemctl','start','fatberry-installer-report.service'],check=True)
print('Event-driven private reporter installed and started')
"""
    encoded = base64.b64encode(program.encode()).decode()
    subprocess.run(["ssh", "-i", str(Path.home() / ".ssh/fatberry_vps"), "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes", "ubuntu@57.131.154.174",
                    f"sudo python3 -c \"import base64;exec(base64.b64decode('{encoded}'))\""],
                   input=json.dumps(files).encode(), check=True)


if __name__ == "__main__":
    main()
