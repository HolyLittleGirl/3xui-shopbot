#!/usr/bin/env python3
"""
Добавляет RKN блокировку в routing rules Xray
"""
import json
import subprocess
import sys

XRAY_CONFIG = '/usr/local/x-ui/bin/config.json'

def get_blocked_ips():
    """Получить IP из ipset rkn_blocked"""
    result = subprocess.run(
        ['ipset', 'list', 'rkn_blocked'],
        capture_output=True, text=True, timeout=30
    )
    ips = []
    in_members = False
    for line in result.stdout.strip().split('\n'):
        if line.strip() == 'Members:':
            in_members = True
            continue
        if in_members and line.strip():
            # Формат: "1.2.3.0/24 timeout 0" или "1.2.3.4 timeout 0"
            ip = line.split()[0]
            if ip and not ip.startswith('#'):
                ips.append(ip)
    return ips

def main():
    # Получаем заблокированные IP
    blocked_ips = get_blocked_ips()
    if not blocked_ips:
        print("No blocked IPs found", file=sys.stderr)
        return 1
    
    print(f"Found {len(blocked_ips)} blocked IPs")
    
    # Читаем конфиг
    try:
        with open(XRAY_CONFIG, 'r') as f:
            config = json.load(f)
    except Exception as e:
        print(f"Error reading config: {e}", file=sys.stderr)
        return 1
    
    # Проверяем есть ли уже правило RKN
    routing = config.get('routing', {})
    rules = routing.get('rules', [])
    
    for i, rule in enumerate(rules):
        if rule.get('outboundTag') == 'rkn-blocked':
            print("RKN rule already exists, updating...", file=sys.stderr)
            # Обновляем существующее правило
            rule['ip'] = blocked_ips[:500]  # Лимит 500 IP
            break
    else:
        # Добавляем новое правило ПЕРЕД остальными
        rkn_rule = {
            'type': 'field',
            'ip': blocked_ips[:500],
            'network': 'TCP,UDP',
            'outboundTag': 'blocked'
        }
        rules.insert(0, rkn_rule)
        print("RKN rule added")
    
    routing['rules'] = rules
    config['routing'] = routing
    
    # Сохраняем конфиг
    try:
        with open(XRAY_CONFIG + '.bak', 'w') as f:
            json.dump(config, f, indent=2)
        
        with open(XRAY_CONFIG, 'w') as f:
            json.dump(config, f, indent=2)
        
        print("Config saved")
        
        # Перезагружаем x-ui
        subprocess.run(['systemctl', 'restart', 'x-ui'], check=True)
        print("Xray restarted")
        
    except Exception as e:
        print(f"Error saving config: {e}", file=sys.stderr)
        return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
