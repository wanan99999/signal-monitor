# -*- coding: utf-8 -*-
"""读取云端（Supabase strategy-combos）里的组合配置；连不上时用上一次的缓存"""
import os, json, time
import requests
import data as D

CONF = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "supabase.json"), encoding="utf-8"))
VIEW = "https://wanan99999.github.io/quant-strategies/combo-view.html?id="


def combos():
    """[{id, name, note, config, jq, updated_at}]；失败时返回缓存并带 stale=True"""
    url = CONF["url"] + "/rest/v1/combos?select=id,name,note,config,jq,created_at,updated_at&order=created_at.asc"
    for i in range(8):
        try:
            r = requests.get(url, headers={"apikey": CONF["key"]}, timeout=20)
            r.raise_for_status()
            x = r.json()
            D.save("combos.pkl", x)
            return x, False
        except Exception as e:
            err = e
            time.sleep(1 + i)
    D.log("读取云端组合失败，用缓存：%s" % err)
    return D.load("combos.pkl", []) or [], True


def write_list(cl, out_dir):
    """给小程序用的组合清单（小程序连不了 Supabase，只能读 github.io）：data/combos/list.json"""
    rows = [dict(id=c["id"], name=c["name"], note=c.get("note") or "", jq=c.get("jq"),
                 created_at=c.get("created_at"), updated_at=c.get("updated_at"),
                 config={k: v for k, v in (c.get("config") or {}).items() if k != "ui"}) for c in cl]
    os.makedirs(os.path.join(out_dir, "combos"), exist_ok=True)
    json.dump(rows, open(os.path.join(out_dir, "combos", "list.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
