#!/usr/bin/env python3
"""Stream njpvideo proxy media straight to R2 without staging it on disk.

The trusted VM has limited disk, so nothing may land on local disk. Each object
is piped source -> R2 multipart, so peak disk use is one part buffer (~16 MB)
regardless of file size.

Resumable: an object already present in R2 with a matching size is skipped, so
re-running fills only the gaps.

Use --fallback-proxy to cover video records which omit a low_proxy URL or whose
advertised low_proxy returns 404. In that mode one existing media object for a
record is sufficient, preventing duplicate proxy and low_proxy uploads.

Usage:  media_stream.py <catalogue.json> [--limit N] [--dry-run] [--fallback-proxy]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.request

UA = "performing-fire-corpus/1.0 (research; hello@ernie.sg)"
PART = 16 * 1024 * 1024
RATE_LIMIT = 1.0

ap = argparse.ArgumentParser()
ap.add_argument("catalogue")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--prefix", default="njpvideo/media")
ap.add_argument("--fallback-proxy", action="store_true")
args = ap.parse_args()

env = pathlib.Path.home() / ".config/rucksack/r2.env"
if env.exists():
    for line in env.read_text().splitlines():
        line = line.split("#")[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import boto3  # noqa: E402
from botocore.config import Config  # noqa: E402

s3 = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                  aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                  region_name="auto", config=Config(retries={"max_attempts": 5}))
BUCKET = os.environ.get("R2_BUCKET") or os.environ["ANVIL_R2_BUCKET"]

raw = json.loads(pathlib.Path(args.catalogue).read_text(encoding="utf-8"))


def strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)
    elif isinstance(o, str):
        yield o


urls = sorted({s for s in strings(raw)
               if s.lower().endswith(".mp4") and "low_proxy" in s.lower()})
proxy_by_record = {}
if args.fallback_proxy:
    for record in raw:
        proxy = record.get("proxyPath", "")
        if not proxy.lower().endswith(".mp4"):
            continue
        match = re.search(r"/storage/\d{4}/\d{2}/\d{2}/(\d+)/", proxy)
        if match:
            proxy_by_record[match.group(1)] = proxy
    low_by_record = {}
    for url in urls:
        match = re.search(r"/storage/\d{4}/\d{2}/\d{2}/(\d+)/", url)
        if match:
            low_by_record[match.group(1)] = url
    urls = [low_by_record.get(record, proxy)
            for record, proxy in sorted(proxy_by_record.items())]
if args.limit:
    urls = urls[:args.limit]
print(f"proxy candidates: {len(urls)}", flush=True)

existing = {}
paginator = s3.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket=BUCKET, Prefix=args.prefix + "/"):
    for o in page.get("Contents", []):
        existing[o["Key"]] = o["Size"]
print(f"already in R2 under {args.prefix}/: {len(existing)}", flush=True)
existing_records = {key.split("/")[2] for key in existing
                    if len(key.split("/")) >= 4}

done = skipped = failed = 0
bytes_moved = 0
started = time.time()

for i, url in enumerate(urls, 1):
    m = re.search(r"/storage/(\d{4})/(\d{2})/(\d{2})/(\d+)/", url)
    rec = m.group(4) if m else "unknown"
    if args.fallback_proxy and rec in existing_records:
        skipped += 1
        continue
    key = f"{args.prefix}/{rec}/{url.split('/')[-1]}"

    remote_size = None
    while True:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                remote_size = int(r.headers.get("Content-Length") or 0)
            break
        except Exception as exc:  # noqa: BLE001
            fallback = proxy_by_record.get(rec)
            if args.fallback_proxy and fallback and fallback != url:
                print(f"  {i}/{len(urls)} low_proxy unavailable {rec}; "
                      "trying canonical proxy", flush=True)
                url = fallback
                key = f"{args.prefix}/{rec}/{url.split('/')[-1]}"
                continue
            print(f"  {i}/{len(urls)} HEAD fail {rec}: {type(exc).__name__}", flush=True)
            failed += 1
            break
    if remote_size is None:
        continue
    if not remote_size:
        print(f"  {i}/{len(urls)} HEAD missing size {rec}", flush=True)
        failed += 1
        continue

    if existing.get(key) == remote_size and remote_size:
        skipped += 1
        continue
    if args.dry_run:
        print(f"  would stream {key} ({remote_size:,} B)", flush=True)
        continue

    upload_id = None
    try:
        mpu = s3.create_multipart_upload(Bucket=BUCKET, Key=key, ContentType="video/mp4",
                                         Metadata={"source": "njpvideo.ggcf.kr",
                                                   "record-id": rec, "authenticated": "false"})
        upload_id = mpu["UploadId"]
        parts, n = [], 0
        get = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(get, timeout=180) as resp:
            while True:
                buf = resp.read(PART)
                if not buf:
                    break
                n += 1
                p = s3.upload_part(Bucket=BUCKET, Key=key, PartNumber=n,
                                   UploadId=upload_id, Body=buf)
                parts.append({"ETag": p["ETag"], "PartNumber": n})
                bytes_moved += len(buf)
        s3.complete_multipart_upload(Bucket=BUCKET, Key=key, UploadId=upload_id,
                                     MultipartUpload={"Parts": parts})
        done += 1
        if done % 10 == 0 or i == len(urls):
            rate = bytes_moved / max(time.time() - started, 1) / 1e6
            print(f"  {i}/{len(urls)} done={done} skip={skipped} fail={failed} "
                  f"{bytes_moved/1e9:.2f} GB @ {rate:.2f} MB/s", flush=True)
    except Exception as exc:  # noqa: BLE001
        failed += 1
        if upload_id:
            try:
                s3.abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=upload_id)
            except Exception:  # noqa: BLE001
                pass
        print(f"  {i}/{len(urls)} FAIL {rec}: {type(exc).__name__} {str(exc)[:70]}", flush=True)
    time.sleep(RATE_LIMIT)

print(f"\nstreamed={done} skipped={skipped} failed={failed} "
      f"bytes={bytes_moved:,} elapsed={time.time()-started:.0f}s", flush=True)
