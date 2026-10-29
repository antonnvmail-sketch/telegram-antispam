"""Independent health checks. No polling, deletion, training, or automatic repair."""
import asyncio
try:
    import fcntl
except ImportError:
    fcntl=None
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from runtime_ops import atomic_json

def local_issues(service,runtime,now):
    issues=[]
    if service.get('ActiveState')!='active':issues.append('Бот сейчас не запущен службой автозапуска.')
    if not runtime or str(runtime.get('pid'))!=service.get('MainPID'):
        issues.append('Нет актуального контрольного сигнала от рабочего процесса.')
    elif now-runtime.get('started_at',0)>45:
        if now-runtime.get('loop_at',0)>45:issues.append('Рабочий процесс перестал обновлять контрольный сигнал.')
        poll=runtime.get('last_poll_success')
        if not poll or now-poll>150:issues.append('Бот давно не подтвердил успешное получение событий Telegram.')
        if runtime.get('inflight',0) and now-max(runtime.get('last_update_completed') or 0,runtime.get('last_update_received') or 0)>150:
            issues.append('Обработка полученного события затянулась; требуется проверка.')
    return issues

def notification(previous,issues,restarts):
    old=previous.get('issues')
    restart_changed='restarts' in previous and restarts>previous['restarts']
    if old is None and not issues:return None
    if old==issues and not restart_changed:return None
    if issues:text='Антиспам: нужна проверка.\n'+'\n'.join('• '+s for s in issues)
    else:text='Антиспам: работоспособность восстановлена. Telegram отвечает, контрольный сигнал актуален.'
    if restart_changed:text+='\nСлужба автоматически перезапустила бота. Проверьте незавершённые действия, если они были.'
    return text

async def run(directory=None,bot=None,config=None):
    if fcntl is None:raise RuntimeError('Health service requires systemd on a POSIX host')
    root=Path.cwd();directory=Path(directory or root/'.operations')
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    lock=(directory/'health.lock').open('a+')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return {'status':'already_running'}
    if config is None:import config as config
    from aiogram import Bot
    from aiogram.exceptions import TelegramBadRequest,TelegramForbiddenError
    own=bot is None
    if own:bot=Bot(config.TELEGRAM_TOKEN)
    path=directory/'health.json'
    try:previous=json.loads(path.read_text())
    except (FileNotFoundError,ValueError):previous={}
    try:runtime=json.loads((directory/'runtime.json').read_text())
    except (FileNotFoundError,ValueError):runtime={}
    r=subprocess.run(['systemctl','--user','show','antispam.service','-p','ActiveState','-p','MainPID','-p','NRestarts'],capture_output=True,text=True,timeout=5)
    service=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
    now=time.time();issues=local_issues(service,runtime,now)
    permissions=previous.get('permissions',{})
    checked=previous.get('permissions_checked_at',0)
    api_ok=False
    async def collect():
        nonlocal checked,permissions,api_ok
        try:me=await bot.get_me(request_timeout=5);api_ok=True
        except Exception:
            issues.append('Проверка связи с Telegram сейчас не прошла.');return
        if now-checked>=300:
            results={}
            for chat in config.CHAT_IDS:
                try:
                    m=await bot.get_chat_member(chat,me.id,request_timeout=2)
                    results[str(chat)]='ok' if m.status=='creator' or bool(getattr(m,'can_delete_messages',False)) else 'no_delete_rights'
                except (TelegramBadRequest,TelegramForbiddenError):results[str(chat)]='inaccessible'
                except Exception:results[str(chat)]='unknown'
            permissions=results;checked=now
    try:
        try:await asyncio.wait_for(collect(),timeout=35)
        except asyncio.TimeoutError:issues.append('Проверка Telegram не завершилась вовремя.')
        for chat,result in permissions.items():
            if result in {'no_delete_rights','inaccessible'}:issues.append(f'Чат {chat}: нет доступа или права удаления.')
            elif result=='unknown':issues.append(f'Чат {chat}: не удалось проверить права; результат неизвестен.')
        restarts=int(service.get('NRestarts','0'))
        text=notification(previous,issues,restarts)
        pending=text or previous.get('pending_notification')
        if text is None and previous.get('issues')!=issues:pending=None
        notified=previous.get('notified',{})
        report=dict(checked_at=now,issues=issues,restarts=restarts,api_ok=api_ok,service=service,
                    permissions=permissions,permissions_checked_at=checked,pending_notification=pending,notified=notified)
        if pending:
            fingerprint=hashlib.sha256(pending.encode()).hexdigest()
            for admin in config.ADMIN_IDS:
                key=str(admin)
                if notified.get(key,{}).get('fingerprint')==fingerprint:continue
                # Persist before transmission: an ambiguous timeout must not cause repeated alerts.
                notified[key]=dict(fingerprint=fingerprint,status='uncertain');atomic_json(path,report)
                try:await bot.send_message(admin,pending,parse_mode=None,request_timeout=5)
                except (TelegramBadRequest,TelegramForbiddenError):notified.pop(key,None)
                except Exception:pass
                else:notified[key]['status']='sent'
                atomic_json(path,report)
        atomic_json(path,report)
        return dict(status='healthy' if not issues else 'attention',issues=issues,api_ok=api_ok,
                    permissions_ok=sum(v=='ok' for v in permissions.values()),permissions_total=len(permissions),
                    checked_at=now,restarts=restarts)
    finally:
        if own:await bot.session.close()
        lock.close()

if __name__=='__main__':print(json.dumps(asyncio.run(run()),ensure_ascii=False))
