#!/usr/bin/env python3
"""Fetch NJP Art Center YouTube transcripts via youtube_transcript_api, then upload to R2.

MUST run on the laptop. The trusted VM's datacenter IP is blocked by YouTube
("Sign in to confirm you're not a bot" / RequestBlocked), verified 2026-07-26.
This path also survives the HTTP 429 that yt-dlp's player API hits.

Everything written here is machine ASR, recorded as transcript_kind so it can
never be silently pooled with the 38 human-authored njpvideo .srt files.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import random
import sys
import time

from youtube_transcript_api import YouTubeTranscriptApi

LIST = pathlib.Path("/Users/erniesg/code/erniesg/performing-fire-corpus/.local/youtube/yt-videos.tsv")
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else
                   "/Users/erniesg/code/erniesg/performing-fire-corpus/.local/youtube")
TX = OUT / "transcripts"
TX.mkdir(parents=True, exist_ok=True)

RATE = 6.0   # slower on the timer: 59 videos still owed after IpBlocked throttling
videos = []
for line in LIST.read_text(encoding="utf-8", errors="replace").splitlines():
    # yt-dlp's --print emitted a literal backslash-t, not a real tab
    sep = "\t" if "\t" in line else "\\t"
    if sep in line:
        vid, _, title = line.partition(sep)
        if len(vid.strip()) == 11:
            videos.append((vid.strip(), title.strip()))
print(f"videos: {len(videos)}", flush=True)

api = YouTubeTranscriptApi()
manifest, failures = [], []
consecutive_blocked = 0

for i, (vid, title) in enumerate(videos, 1):
    dest = TX / f"{vid}.json"
    if dest.exists():                      # resumable
        manifest.append(json.loads(dest.read_text(encoding="utf-8"))["_meta"])
        continue
    try:
        listing = api.list(vid)
        tracks = [(t.language_code, t.is_generated) for t in listing]
        pick = None
        for code, gen in tracks:           # prefer the source track
            if code.startswith(("ko", "en")):
                pick = code
                break
        pick = pick or (tracks[0][0] if tracks else None)
        if pick is None:
            failures.append({"video_id": vid, "reason": "no_tracks"})
            continue
        fetched = api.fetch(vid, languages=[pick])
        segs = [{"start": round(s.start, 3), "duration": round(s.duration, 3), "text": s.text}
                for s in fetched]
        is_gen = dict((c, g) for c, g in tracks).get(pick, True)
        meta = {
            "video_id": vid, "title": title, "url": f"https://www.youtube.com/watch?v={vid}",
            "lang": pick, "segments": len(segs),
            "chars": sum(len(s["text"]) for s in segs),
            "transcript_kind": "machine_asr" if is_gen else "human_subtitle",
            "available_tracks": [{"lang": c, "generated": g} for c, g in tracks],
        }
        body = json.dumps({"_meta": meta, "segments": segs}, ensure_ascii=False, indent=1)
        dest.write_text(body, encoding="utf-8")
        meta["sha256"] = hashlib.sha256(body.encode()).hexdigest()
        meta["bytes"] = len(body.encode())
        manifest.append(meta)
        consecutive_blocked = 0
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        failures.append({"video_id": vid, "reason": f"{name}: {str(exc)[:90]}"})
        # An hourly timer must not prolong a block. IpBlocked/TooManyRequests means the
        # host is throttled, not that this video lacks a transcript, so stop the run and
        # let the next hour try. TranscriptsDisabled is permanent and never counts here.
        if name in ("IpBlocked", "RequestBlocked", "TooManyRequests"):
            consecutive_blocked += 1
            if consecutive_blocked >= 3:
                print(f"  aborting: {consecutive_blocked} consecutive block errors; "
                      f"host is throttled, retrying next run", flush=True)
                break
        else:
            consecutive_blocked = 0
    if i % 20 == 0 or i == len(videos):
        print(f"  {i}/{len(videos)}  ok={len(manifest)} fail={len(failures)}", flush=True)
    time.sleep(RATE + random.uniform(0, 1.0))

payload = {
    "source": "youtube.com/@NamJunePaikArtCenter", "source_id": "njp-youtube-official",
    "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "authenticated": False, "executed_on": "laptop",
    "why_laptop": "trusted VM datacenter IP is blocked by YouTube (RequestBlocked); verified 2026-07-26",
    "method": "youtube_transcript_api",
    "warning": "Predominantly machine ASR. Check transcript_kind per record; do not pool "
               "machine_asr with the 38 human njpvideo .srt transcripts.",
    "videos_seen": len(videos), "transcripts": len(manifest), "failures": failures,
    "files": manifest,
}
(OUT / "youtube-transcripts-manifest.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

kinds = {}
for m in manifest:
    kinds[m["transcript_kind"]] = kinds.get(m["transcript_kind"], 0) + 1
print(f"\ntranscripts={len(manifest)}/{len(videos)}  failures={len(failures)}  kinds={kinds}")
print(f"total chars: {sum(m.get('chars',0) for m in manifest):,}")

env = pathlib.Path("/Users/erniesg/code/erniesg/performing-fire-corpus/.env.live")
if env.exists():
    for line in env.read_text().splitlines():
        line = line.split("#")[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
try:
    import boto3
    s3 = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                      aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                      aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                      region_name="auto")
    bucket = os.environ.get("R2_BUCKET") or os.environ["ANVIL_R2_BUCKET"]
    n = 0
    for f in sorted(TX.glob("*.json")):
        s3.put_object(Bucket=bucket, Key=f"youtube/transcripts/{f.name}",
                      Body=f.read_bytes(), ContentType="application/json",
                      Metadata={"transcript-kind": "machine_asr", "source": "youtube",
                                "authenticated": "false"})
        n += 1
    body = (OUT / "youtube-transcripts-manifest.json").read_bytes()
    s3.put_object(Bucket=bucket, Key="youtube/transcripts/manifest.json",
                  Body=body, ContentType="application/json")
    print(f"uploaded {n} transcripts + manifest to r2://{bucket}/youtube/transcripts/")
except Exception as exc:  # noqa: BLE001
    print(f"R2 upload failed: {type(exc).__name__}: {exc}")
