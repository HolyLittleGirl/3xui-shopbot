#!/usr/bin/env python3
"""
Добавляет/удаляет RKN блокировку доменов в конфиге 3x-ui через базу данных

Использует локальный кэш доменов (/opt/rkn-blocker/rkn_domains.json).
При отсутствии кэша создаёт тестовый список из 3 доменов.

Usage:
    python3 update-3xui-rkn-domains.py enable   - Добавить RKN правила
    python3 update-3xui-rkn-domains.py disable  - Удалить RKN правила
    python3 update-3xui-rkn-domains.py download - Скачать домены из GitHub
"""
import json
import sqlite3
import subprocess
import sys
import time
import os

DB_PATH = '/etc/x-ui/x-ui.db'
DOMAINS_FILE = '/opt/rkn-blocker/rkn_domains.json'
GITHUB_URL = 'https://github.com/1andrevich/Re-filter-lists/releases/download/13062025/ruleset-domain-refilter_domains.json'
MAX_DOMAINS_PER_RULE = 1000  # Лимит Xray


def download_domains():
    """Скачать список доменов из GitHub"""
    print(f"Downloading domains from GitHub...")
    print(f"URL: {GITHUB_URL}")
    
    try:
        # Используем curl с таймаутом
        result = subprocess.run([
            'curl', '-sL', '--connect-timeout', '30', '--max-time', '300',
            '-o', DOMAINS_FILE, GITHUB_URL
        ], capture_output=True, text=True, timeout=300)
        
        if result.returncode != 0:
            print(f"Download failed: {result.stderr}")
            return False
        
        # Проверяем что файл не пустой
        if os.path.getsize(DOMAINS_FILE) < 1000:
            print("Downloaded file is too small, might be invalid")
            return False
        
        print(f"Downloaded to {DOMAINS_FILE}")
        return True
    
    except Exception as e:
        print(f"Error downloading: {e}")
        return False


def load_domains():
    """Загрузить домены из локального файла"""
    if not os.path.exists(DOMAINS_FILE):
        print(f"Domains file not found: {DOMAINS_FILE}")
        return []
    
    try:
        with open(DOMAINS_FILE, 'r') as f:
            data = json.load(f)
        
        domains = []
        for rule in data.get('rules', []):
            for domain in rule.get('domain', []):
                # Убираем префиксы если есть
                clean_domain = domain
                if domain.startswith('domain:'):
                    clean_domain = domain[7:]
                elif domain.startswith('regexp:'):
                    continue  # Пропускаем regexp
                
                if clean_domain and clean_domain not in domains:
                    domains.append(clean_domain)
        
        print(f"Loaded {len(domains)} domains from {DOMAINS_FILE}")
        return domains
    
    except Exception as e:
        print(f"Error loading domains: {e}")
        return []


def create_test_domains():
    """Создать тестовый файл с 3 доменами"""
    test_data = {
        "rules": [{
            "domain": [
                "facebook.com",
                "tiktok.com",
                "x.com"
            ]
        }]
    }
    
    with open(DOMAINS_FILE, 'w') as f:
        json.dump(test_data, f, indent=2)
    
    print(f"Created test domains file: {DOMAINS_FILE}")
    return ["facebook.com", "tiktok.com", "x.com"]


def split_domains(domains, max_per_rule=MAX_DOMAINS_PER_RULE):
    """Разбить домены на чанки"""
    chunks = []
    for i in range(0, len(domains), max_per_rule):
        chunks.append(domains[i:i + max_per_rule])
    return chunks


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 update-3xui-rkn-domains.py [enable|disable|download]")
        return 1
    
    action = sys.argv[1]
    
    # Обработка команды download
    if action == 'download':
        success = download_domains()
        if success:
            domains = load_domains()
            print(f"Total domains: {len(domains)}")
        return 0 if success else 1
    
    # Читаем конфиг из базы
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
    row = cursor.fetchone()
    
    if not row:
        print("xrayTemplateConfig not found in database")
        return 1
    
    try:
        config = json.loads(row[0])
    except Exception as e:
        print(f"Error parsing config: {e}")
        return 1
    
    routing = config.get('routing', {})
    rules = routing.get('rules', [])
    
    if action == 'enable':
        # Загружаем домены из локального файла
        domains = load_domains()
        
        # Если файла нет - создаём тестовый
        if not domains:
            print("No domains file found, creating test file...")
            domains = create_test_domains()
        
        # Разбиваем на чанки
        chunks = split_domains(domains)
        print(f"Split into {len(chunks)} rules ({MAX_DOMAINS_PER_RULE} domains each)")
        
        # Сначала удаляем старые RKN правила
        new_rules = []
        old_rkn_count = 0
        for rule in rules:
            domain_list = rule.get('domain', [])
            # Проверяем не RKN ли это правило
            is_rkn = ('facebook.com' in domain_list) or (len(domain_list) > 10 and rule.get('outboundTag') == 'blocked')
            if is_rkn:
                old_rkn_count += 1
                continue
            new_rules.append(rule)
        rules = new_rules
        print(f"Removed {old_rkn_count} old RKN rules")
        
        # Находим позицию для вставки (ПЕРЕД outbound правилами)
        insert_index = 0
        for i, rule in enumerate(rules):
            if 'inboundTag' not in rule and 'outboundTag' in rule:
                insert_index = i
                break
        print(f"Insert at index {insert_index}")
        
        # Добавляем правила
        for i, chunk in enumerate(chunks):
            rkn_rule = {
                'type': 'field',
                'domain': chunk,
                'network': 'TCP,UDP',
                'outboundTag': 'blocked'
            }
            rules.insert(insert_index + i, rkn_rule)
            print(f"Added RKN rule {i+1}/{len(chunks)} with {len(chunk)} domains")
        
        print(f"Total: {len(chunks)} RKN rules, {len(domains)} domains")
    
    elif action == 'disable':
        # Удаляем ВСЕ RKN правила
        original_count = len(rules)
        new_rules = []
        removed_count = 0
        for rule in rules:
            domain_list = rule.get('domain', [])
            # Проверяем не RKN ли это правило
            is_rkn = ('facebook.com' in domain_list) or (len(domain_list) > 10 and rule.get('outboundTag') == 'blocked')
            if is_rkn:
                removed_count += 1
                continue
            new_rules.append(rule)
        rules = new_rules
        print(f"Removed {removed_count} RKN domain rules")
    
    else:
        print(f"Unknown action: {action}")
        return 1
    
    routing['rules'] = rules
    config['routing'] = routing
    
    # Сохраняем в базу
    try:
        cursor.execute(
            "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
            (json.dumps(config),)
        )
        conn.commit()
        print("Config saved to database")
        
    except Exception as e:
        print(f"Error saving config: {e}")
        return 1
    finally:
        conn.close()
    
    # Перезагружаем x-ui (stop → start)
    try:
        print(f"Stopping x-ui...")
        subprocess.run(['systemctl', 'stop', 'x-ui'], check=True, timeout=30)
        print(f"x-ui stopped")
        time.sleep(2)
        print(f"Starting x-ui...")
        subprocess.run(['systemctl', 'start', 'x-ui'], check=True, timeout=30)
        print(f"x-ui started")
        
    except Exception as e:
        print(f"Error restarting x-ui: {e}")
        return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
