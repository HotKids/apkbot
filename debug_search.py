#!/usr/bin/env python3
"""调试脚本：dump APKMirror 搜索页面的实际 HTML，排查无结果原因。"""
import sys
sys.path.insert(0, "app")

from scraper import new_session, session_get, _parse_fontblack_apps, BASE_URL
from bs4 import BeautifulSoup

keyword = sys.argv[1] if len(sys.argv) > 1 else "taobao"
url = f"{BASE_URL}/?searchtype=app&s={keyword}"

print(f"[*] GET {url}")
s = new_session()
r = session_get(s, url)
print(f"[*] status : {r.status_code}")
print(f"[*] final url: {r.url}")
print(f"[*] no-results text present: {'No results found matching your query' in r.text}")

soup = BeautifulSoup(r.text, "lxml")
fontblack = soup.select("a.fontBlack")
print(f"[*] a.fontBlack count: {len(fontblack)}")
for a in fontblack[:10]:
    print(f"    href={a.get('href')!r:50s}  text={a.get_text(strip=True)!r}")

results = _parse_fontblack_apps(soup, 10)
print(f"[*] _parse_fontblack_apps results: {results}")

print("\n--- first 3000 chars of HTML ---")
print(r.text[:3000])
