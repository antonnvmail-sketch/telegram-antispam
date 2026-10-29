"""Render optional user systemd units; --write writes files without starting services."""
import argparse
import os
from pathlib import Path
import sys

def quote(value):return '"'+str(value).replace('%','%%').replace('\\','\\\\').replace('"','\\"')+'"'

def units(root,python):
    root=Path(root).resolve();python=Path(python).resolve()
    common=f'WorkingDirectory={quote(root)}\nUMask=0077\nNoNewPrivileges=yes\n'
    return {
        'antispam.service':('[Unit]\nDescription=Telegram antispam\nStartLimitIntervalSec=300\nStartLimitBurst=6\n'
            '[Service]\nType=notify\n'+common+f'ExecStart={quote(python)} -B {quote(root/"bot_new.py")}\n'
            'Restart=always\nRestartSec=5\nWatchdogSec=60\nWatchdogSignal=SIGTERM\nNotifyAccess=main\n'
            'KillSignal=SIGINT\nTimeoutStartSec=60\nTimeoutStopSec=30\n[Install]\nWantedBy=default.target\n'),
        'antispam-health.service':('[Unit]\nDescription=Check antispam health\n[Service]\nType=oneshot\n'+common+
            f'ExecStart={quote(python)} -B {quote(root/"health_check.py")}\nTimeoutStartSec=55\n'),
        'antispam-health.timer':('[Unit]\nDescription=Check antispam every minute\n[Timer]\nOnBootSec=90\n'
            'OnUnitActiveSec=60\nAccuracySec=5\nUnit=antispam-health.service\n[Install]\nWantedBy=timers.target\n')}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true',help='Write user units; refuse to overwrite existing files')
    args=parser.parse_args();rendered=units(Path(__file__).parent,sys.executable)
    if args.write:
        if os.name!='posix':parser.error('User systemd units require a POSIX host with systemd')
        target=Path.home()/'.config/systemd/user';target.mkdir(parents=True,exist_ok=True)
        for name in rendered:
            if (target/name).exists():parser.error('Refusing to overwrite '+str(target/name))
        for name,text in rendered.items():
            with (target/name).open('x',encoding='utf-8') as f:f.write(text)
        print('Units written. Review them, stop any manually started bot, then enable the service as documented.')
    else:
        for name,text in rendered.items():print('# '+name+'\n'+text)
