#!/usr/bin/env python3
"""
RKN Blocker - Блокировка запрещённых ресурсов РФ

Использует IP списки из antifilter.download для блокировки через iptables/ipset.

Usage:
    python3 update-3xui-rkn-blocker.py enable   - Включить блокировку
    python3 update-3xui-rkn-blocker.py disable  - Выключить блокировку
    python3 update-3xui-rkn-blocker.py update   - Обновить списки
"""
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

IP_LIST_URL = 'https://antifilter.download/list/allyouneed.lst'
IPSET_NAME = 'rkn_blocked'
STATE_FILE = '/var/log/rkn-blocker/state.json'


def log(message: str):
    print(message)


def run_command(cmd: list, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def fetch_iplist() -> list:
    """Скачать список IP"""
    log(f"Fetching IP list from {IP_LIST_URL}...")
    try:
        result = run_command(['curl', '-sL', '--connect-timeout', '30', IP_LIST_URL], timeout=120)
        if result.returncode != 0:
            log(f"Failed to fetch: {result.stderr}")
            return []
        
        ips = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
        log(f"Fetched {len(ips)} IPs")
        return ips
    except Exception as e:
        log(f"Error: {e}")
        return []


def ensure_ipset() -> bool:
    """Создать ipset"""
    result = run_command(['ipset', 'create', IPSET_NAME, 'hash:net', 'maxelem', '2097152'])
    if result.returncode == 0 or 'already exists' in result.stderr:
        return True
    log(f"Failed to create ipset: {result.stderr}")
    return False


def clear_ipset() -> bool:
    """Очистить ipset"""
    result = run_command(['ipset', 'flush', IPSET_NAME])
    return result.returncode == 0


def add_ips_to_ipset(ips: list) -> bool:
    """Добавить IP в ipset"""
    # Создаём batch файл
    batch_file = '/tmp/ipset_batch.txt'
    with open(batch_file, 'w') as f:
        for ip in ips:
            f.write(f'add {IPSET_NAME} {ip}\n')
    
    # Читаем файл и передаём в ipset restore
    try:
        with open(batch_file, 'r') as f:
            result = subprocess.run(
                ['ipset', 'restore'],
                stdin=f,
                capture_output=True,
                text=True,
                timeout=300
            )
        if result.returncode == 0:
            log(f"Added {len(ips)} IPs to ipset (batch mode)")
            return True
        else:
            log(f"Batch failed: {result.stderr}")
    except Exception as e:
        log(f"Batch error: {e}")
    
    # Fallback: добавляем по одному
    log(f"Trying individual adds...")
    count = 0
    for ip in ips:
        result = run_command(['ipset', 'add', IPSET_NAME, ip])
        if result.returncode == 0:
            count += 1
    
    log(f"Added {count}/{len(ips)} IPs to ipset")
    return count > 0


def ensure_iptables_rule() -> bool:
    """Добавить правило iptables"""
    # Проверяем наличие
    result = run_command(['iptables', '-C', 'OUTPUT', '-m', 'set', '--match-set', IPSET_NAME, 'dst', '-j', 'DROP'])
    if result.returncode == 0:
        log("iptables rule already exists")
        return True
    
    # Добавляем
    result = run_command(['iptables', '-I', 'OUTPUT', '1', '-m', 'set', '--match-set', IPSET_NAME, 'dst', '-j', 'DROP'])
    if result.returncode == 0:
        log("iptables rule added")
        return True
    log(f"Failed to add iptables rule: {result.stderr}")
    return False


def remove_iptables_rule() -> bool:
    """Удалить правило iptables"""
    result = run_command(['iptables', '-D', 'OUTPUT', '-m', 'set', '--match-set', IPSET_NAME, 'dst', '-j', 'DROP'])
    if result.returncode == 0:
        log("iptables rule removed")
        return True
    log(f"Failed to remove iptables rule: {result.stderr}")
    return False


def destroy_ipset() -> bool:
    """Удалить ipset"""
    result = run_command(['ipset', 'destroy', IPSET_NAME])
    if result.returncode == 0:
        log("ipset destroyed")
        return True
    log(f"Failed to destroy ipset: {result.stderr}")
    return False


def enable_blocking() -> dict:
    """Включить блокировку"""
    log("=== Enabling RKN blocking ===")
    
    ips = fetch_iplist()
    if not ips:
        return {"success": False, "error": "Failed to fetch IP list"}
    
    if not ensure_ipset():
        return {"success": False, "error": "Failed to create ipset"}
    
    clear_ipset()
    
    if not add_ips_to_ipset(ips):
        return {"success": False, "error": "Failed to add IPs"}
    
    if not ensure_iptables_rule():
        return {"success": False, "error": "Failed to add iptables rule"}
    
    log(f"Blocking enabled. {len(ips)} IPs blocked")
    return {"success": True, "blocked_count": len(ips)}


def disable_blocking() -> dict:
    """Выключить блокировку"""
    log("=== Disabling RKN blocking ===")
    
    remove_iptables_rule()
    destroy_ipset()
    
    log("Blocking disabled")
    return {"success": True}


def update_blocklist() -> dict:
    """Обновить списки"""
    log("=== Updating blocklist ===")
    
    ips = fetch_iplist()
    if not ips:
        return {"success": False, "error": "Failed to fetch IP list"}
    
    if not ensure_ipset():
        return {"success": False, "error": "ipset doesn't exist"}
    
    clear_ipset()
    
    if not add_ips_to_ipset(ips):
        return {"success": False, "error": "Failed to add IPs"}
    
    log(f"Blocklist updated. {len(ips)} IPs")
    return {"success": True, "blocked_count": len(ips)}


def get_status() -> dict:
    """Получить статус"""
    ipset_exists = run_command(['ipset', 'list', '-t', IPSET_NAME]).returncode == 0
    iptables_exists = run_command(['iptables', '-C', 'OUTPUT', '-m', 'set', '--match-set', IPSET_NAME, 'dst', '-j', 'DROP']).returncode == 0
    
    blocked_count = 0
    if ipset_exists:
        result = run_command(['ipset', 'list', IPSET_NAME])
        for line in result.stdout.split('\n'):
            if 'Number of entries:' in line:
                try:
                    blocked_count = int(line.split(':')[1].strip())
                except:
                    pass
    
    enabled = blocked_count > 0 and ipset_exists and iptables_exists
    
    return {
        "enabled": enabled,
        "blocked_count": blocked_count,
        "ipset_exists": ipset_exists,
        "iptables_exists": iptables_exists
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 update-3xui-rkn-blocker.py [enable|disable|update|status]")
        sys.exit(1)
    
    action = sys.argv[1]
    
    if action == 'enable':
        result = enable_blocking()
    elif action == 'disable':
        result = disable_blocking()
    elif action == 'update':
        result = update_blocklist()
    elif action == 'status':
        result = get_status()
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)
    
    print(json.dumps(result))
    sys.exit(0 if result.get('success') else 1)
