"""Optional compressed state persistence on Supabase Storage."""
import os
import tarfile
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


STATE_FILES = (
    "latest.json",
    "opportunities-latest.csv",
    "opportunities-legs.csv",
    "opportunities-sizes.csv",
    "matches.csv",
    "opportunity-ledger.json",
    "opportunity-tracking.jsonl",
    "opportunity-tracking.csv",
    "tracking.json",
    "tracking.csv",
    "tracking-history.jsonl",
    "tracking-history.json",
    "tracking-history.csv",
    "summary.csv",
)


def settings():
    url=os.getenv("SUPABASE_URL","").rstrip("/")
    key=os.getenv("SUPABASE_SERVICE_KEY","")
    bucket=os.getenv("SUPABASE_BUCKET","arbitraggio")
    return url,key,bucket


def request(url,key,method="GET",data=None,content_type="application/gzip"):
    headers={"Authorization":f"Bearer {key}","apikey":key}
    if data is not None:
        headers.update({"Content-Type":content_type,"x-upsert":"true"})
    return urlopen(Request(url,data=data,headers=headers,method=method),timeout=30)


def ensure_bucket(url,key,bucket):
    try:
        request(f"{url}/storage/v1/bucket",key).read()
    except HTTPError as error:
        if error.code not in (404,400):
            raise
        body=('{"name":"'+bucket+'","public":false}').encode()
        try:
            request(f"{url}/storage/v1/bucket",key,"POST",body,"application/json").read()
        except (HTTPError,URLError):
            pass


def archive(output):
    path=output/"arbitraggio-state.tar.gz"
    with tarfile.open(path,"w:gz") as handle:
        for name in STATE_FILES:
            source=output/name
            if source.exists() and source.stat().st_size:
                handle.add(source,arcname=name)
    return path


def hydrate(output):
    url,key,bucket=settings()
    if not url or not key:
        return False
    ensure_bucket(url,key,bucket)
    target=f"{url}/storage/v1/object/{bucket}/arbitraggio-state.tar.gz"
    try:
        with request(target,key) as response:
            payload=response.read()
    except HTTPError as error:
        if error.code==404:
            return False
        raise
    except URLError:
        return False
    temp=output/"state-download.tar.gz"
    temp.write_bytes(payload)
    try:
        with tarfile.open(temp,"r:gz") as handle:
            for member in handle.getmembers():
                name=Path(member.name)
                if name.is_absolute() or ".." in name.parts or not member.isfile():
                    continue
                handle.extract(member,output)
    finally:
        temp.unlink(missing_ok=True)
    return True


def save(output):
    url,key,bucket=settings()
    if not url or not key:
        return False
    ensure_bucket(url,key,bucket)
    path=archive(output)
    target=f"{url}/storage/v1/object/{bucket}/arbitraggio-state.tar.gz"
    request(target,key,"POST",path.read_bytes()).read()
    path.unlink(missing_ok=True)
    return True


def start_sync_loop(output,interval=None):
    _,key,_=settings()
    if not key:
        return None
    interval=float(interval or os.getenv("SUPABASE_SYNC_SECONDS","300"))
    def loop():
        while True:
            try:
                save(output)
                print("Supabase: stato salvato",flush=True)
            except Exception as error:
                print(f"Supabase sync: {type(error).__name__}: {error}",flush=True)
            time.sleep(max(60,interval))
    thread=threading.Thread(target=loop,name="supabase-sync",daemon=True)
    thread.start()
    return thread
