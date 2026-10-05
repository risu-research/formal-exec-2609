#!/usr/bin/env python3
import json, requests, time
from pathlib import Path
PAIRS=[{"package":"apache-airflow-providers-apache-hive","predecessor":"6.1.0","secure_release":"6.1.1"},{"package":"apache-airflow-providers-jdbc","predecessor":"3.4.0","secure_release":"4.0.0"},{"package":"apache-airflow-providers-microsoft-mssql","predecessor":"3.4.0","secure_release":"3.4.1"},{"package":"apache-airflow-providers-odbc","predecessor":"3.3.0","secure_release":"4.0.0"},{"package":"apache-superset","predecessor":"2.1.1","secure_release":"3.0.0"},{"package":"apache-superset","predecessor":"4.1.2","secure_release":"5.0.0"},{"package":"bbot","predecessor":"2.8.4","secure_release":"2.8.5"},{"package":"django-unicorn","predecessor":"0.61.0","secure_release":"0.62.0"},{"package":"flask-unchained","predecessor":"0.8.1","secure_release":"0.9.0"},{"package":"guarddog","predecessor":"0.1.7","secure_release":"0.1.8"},{"package":"hermes","predecessor":"0.9.0","secure_release":"0.9.1"},{"package":"homeassistant-cli","predecessor":"0.9.6","secure_release":"1.0.0"},{"package":"intel-tensorflow","predecessor":"2.11.0","secure_release":"2.12.0"},{"package":"intel-tensorflow-avx512","predecessor":"2.11.0","secure_release":"2.12.0"},{"package":"ipython","predecessor":"5.3.0","secure_release":"6.0.0"},{"package":"ironic","predecessor":"37.0.0","secure_release":"38.0.0"},{"package":"kolla","predecessor":"14.8.0","secure_release":"15.0.0"},{"package":"label-studio","predecessor":"1.15.0","secure_release":"1.16.0"},{"package":"langflow","predecessor":"0.6.19","secure_release":"1.0.0"},{"package":"libusb","predecessor":"1.0.29.post7","secure_release":"1.0.30"},{"package":"lightning","predecessor":"2.3.3","secure_release":"2.4.0"},{"package":"mistral","predecessor":"20.1.0","secure_release":"21.0.0"},{"package":"mobsf","predecessor":"3.7.6","secure_release":"3.9.7"},{"package":"mobsf","predecessor":"4.4.0","secure_release":"4.4.2"},{"package":"numpy","predecessor":"1.18.5","secure_release":"1.19.0"},{"package":"pretalx","predecessor":"2025.2.2","secure_release":"2026.1.0"},{"package":"pyo","predecessor":"1.0.2","secure_release":"1.0.3"},{"package":"scipy","predecessor":"1.7.3","secure_release":"1.8.0"},{"package":"selenium","predecessor":"3.141.0","secure_release":"4.0.0"},{"package":"tensorflow-intel","predecessor":"2.11.1","secure_release":"2.12.0"},{"package":"torch","predecessor":"2.8.0","secure_release":"2.9.0"},{"package":"virtualbmc","predecessor":"2.2.2","secure_release":"3.0.0"},{"package":"vyper","predecessor":"0.2.16","secure_release":"0.3.0"},{"package":"vyper","predecessor":"0.3.7","secure_release":"0.3.8"},{"package":"weblate","predecessor":"5.14.3","secure_release":"5.15"}]
OUT=Path("job-out-a4"); OUT.mkdir(exist_ok=True)
UA="neutral-metadata-check/1.0"
def get(url):
    err=None
    for i in range(5):
        try:
            r=requests.get(url,headers={"User-Agent":UA},timeout=30)
            if r.status_code==200:return r.json(),None
            if r.status_code==404:return None,"404"
            err=f"HTTP {r.status_code}"
        except Exception as e: err=f"{type(e).__name__}:{e}"
        time.sleep(min(6,0.5*(2**i)))
    return None,err
def slim(d):
    if not d:return {}
    info=d.get("info") or {}; urls=d.get("urls") or []
    return {"requires_python":info.get("requires_python"),"classifiers":[x for x in (info.get("classifiers") or []) if x.startswith("Programming Language :: Python")],"summary":info.get("summary"),"home_page":info.get("home_page"),"project_urls":info.get("project_urls"),"upload_times":sorted(set((x.get("upload_time_iso_8601") or x.get("upload_time")) for x in urls if (x.get("upload_time_iso_8601") or x.get("upload_time")))),"yanked":all(bool(x.get("yanked")) for x in urls) if urls else None,"filenames":[x.get("filename") for x in urls]}
rows=[]
for i,p in enumerate(PAIRS,1):
    q={"package":p["package"],"predecessor":p["predecessor"],"secure_release":p["secure_release"]}
    for side,v in [("pre",p["predecessor"]),("fix",p["secure_release"])]:
        url=f"https://pypi.org/pypi/{requests.utils.quote(p['package'],safe='')}/{requests.utils.quote(str(v),safe='')}/json"
        d,e=get(url); q[side]=slim(d); q[side+"_error"]=e; q[side+"_url"]=url
    rows.append(q); print(f"{i}/{len(PAIRS)} {p['package']}",flush=True)
(OUT/"version_metadata.json").write_text(json.dumps(rows,indent=2,sort_keys=True))
