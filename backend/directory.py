import json
from pathlib import Path

import httpx

COUNTRIES = {
    "United States": ("US", "美国"), "United Kingdom": ("GB", "英国"),
    "China": ("CN", "中国"), "India": ("IN", "印度"), "Germany": ("DE", "德国"),
    "France": ("FR", "法国"), "Spain": ("ES", "西班牙"), "Italy": ("IT", "意大利"),
    "Canada": ("CA", "加拿大"), "Australia": ("AU", "澳大利亚"), "Japan": ("JP", "日本"),
    "South Korea": ("KR", "韩国"), "Philippines": ("PH", "菲律宾"), "Oman": ("OM", "阿曼"),
    "Argentina": ("AR", "阿根廷"), "Brazil": ("BR", "巴西"), "Netherlands": ("NL", "荷兰"),
    "South Africa": ("ZA", "南非"), "Indonesia": ("ID", "印度尼西亚"), "Taiwan": ("TW", "中国台湾"),
    "Hong Kong": ("HK", "中国香港"), "Russia": ("RU", "俄罗斯"), "Finland": ("FI", "芬兰"),
    "Sweden": ("SE", "瑞典"), "Norway": ("NO", "挪威"), "Poland": ("PL", "波兰"),
    "Portugal": ("PT", "葡萄牙"), "Greece": ("GR", "希腊"), "Turkey": ("TR", "土耳其"),
}


class Directory:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.cache = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.cache = {}

    def get(self, source_id: int) -> dict:
        return self.cache.get(str(source_id), {"callsign": str(source_id), "name": "", "country": "待查询", "country_code": ""})

    async def lookup(self, source_id: int) -> dict:
        if str(source_id) in self.cache:
            return self.get(source_id)
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get("https://radioid.net/api/dmr/user/", params={"id": source_id})
            response.raise_for_status()
            rows = response.json().get("results", [])
        if not rows:
            entry = {"callsign": str(source_id), "name": "", "country": "未知", "country_code": ""}
        else:
            row = rows[0]
            country = str(row.get("country", "未知"))[:80]
            code, label = COUNTRIES.get(country, ("", country))
            entry = {"callsign": str(row.get("callsign", source_id))[:32], "name": str(row.get("fname") or row.get("name") or "")[:80], "country": label, "country_code": code}
        self.cache = {**self.cache, str(source_id): entry}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.cache, ensure_ascii=False))
        temp.replace(self.path)
        return entry
