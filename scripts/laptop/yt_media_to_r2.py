#!/usr/bin/env python3
"""Stream official NJP YouTube videos to R2 without local media staging.

The selected representation is one combined audio+video MP4 per upload,
preferring YouTube format 18 (360p). The worker is resumable and writes a
manifest after every record.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import time

import boto3
from botocore.config import Config

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
YT_DLP = pathlib.Path(os.environ.get("YT_DLP") or shutil.which("yt-dlp") or "yt-dlp")
FORMAT = "18/best[ext=mp4][height<=480][acodec!=none][vcodec!=none]"
PART_SIZE = 16 * 1024 * 1024
PREFIX = "youtube/media"
UA = os.environ.get(
    "CORPUS_USER_AGENT",
    "performing-fire-corpus/1.0 (research; +https://github.com/erniesg/performing-fire-corpus)",
)

parser = argparse.ArgumentParser()
parser.add_argument("--videos", required=True)
parser.add_argument("--manifest", required=True)
parser.add_argument("--limit", type=int, default=0)
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--rate-limit", type=float, default=3.0)
args = parser.parse_args()

env_path = REPO_ROOT / ".env.live"
for line in (env_path.read_text() if env_path.exists() else "").splitlines():
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["R2_ENDPOINT"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    region_name="auto",
    config=Config(retries={"max_attempts": 5, "mode": "standard"}),
)
bucket = os.environ.get("R2_BUCKET") or os.environ["ANVIL_R2_BUCKET"]

videos: list[dict[str, str]] = []
for raw_line in pathlib.Path(args.videos).read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line:
        continue
    if "\\t" in line:
        video_id, title = line.split("\\t", 1)
    elif "\t" in line:
        video_id, title = line.split("\t", 1)
    else:
        video_id, title = line, ""
    videos.append({"video_id": video_id.strip(), "catalogue_title": title.strip()})
if args.limit:
    videos = videos[: args.limit]

manifest_path = pathlib.Path(args.manifest)
if manifest_path.exists():
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
else:
    manifest = {
        "schema_version": 1,
        "source": "youtube.com/@NamJunePaikArtCenter",
        "source_id": "njp-youtube-official",
        "executed_on": "trusted-laptop",
        "authenticated": False,
        "selection": {
            "format_expression": FORMAT,
            "intent": "one combined playable audio-video representation per upload",
        },
        "videos_seen": len(videos),
        "results": [],
    }
manifest["videos_seen"] = len(videos)


def write_manifest() -> None:
    manifest["updated_utc"] = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)


known = {
    str(item["video_id"]): item
    for item in manifest.get("results", [])
    if isinstance(item, dict) and item.get("status") == "uploaded_verified"
}
existing: dict[str, int] = {}
for page in s3.get_paginator("list_objects_v2").paginate(
    Bucket=bucket, Prefix=PREFIX + "/"
):
    for obj in page.get("Contents", []):
        existing[str(obj["Key"])] = int(obj["Size"])

print(
    f"videos={len(videos)} verified_manifest={len(known)} "
    f"existing_r2={len(existing)}",
    flush=True,
)
uploaded = skipped = failed = 0
bytes_uploaded = 0
consecutive_block_errors = 0
started = time.time()

for index, row in enumerate(videos, 1):
    video_id = row["video_id"]
    prior = known.get(video_id)
    if prior is not None:
        key = str(prior["object_key"])
        size = int(prior["r2_size"])
        if existing.get(key) == size and size > 0:
            skipped += 1
            continue

    url = f"https://www.youtube.com/watch?v={video_id}"
    info_command = [
        str(YT_DLP),
        "--dump-single-json",
        "--simulate",
        "--no-warnings",
        "--no-playlist",
        "--socket-timeout",
        "30",
        "--retries",
        "3",
        "-f",
        FORMAT,
        url,
    ]
    try:
        info_result = subprocess.run(
            info_command,
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        info = json.loads(info_result.stdout)
    except Exception as exc:  # noqa: BLE001
        failed += 1
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or "")[-500:]
        blocked = any(
            marker in detail.lower()
            for marker in ("429", "too many requests", "confirm you", "sign in")
        )
        consecutive_block_errors = consecutive_block_errors + 1 if blocked else 0
        manifest["results"].append(
            {
                **row,
                "status": "metadata_failed",
                "error": type(exc).__name__,
                "blocked": blocked,
            }
        )
        write_manifest()
        print(
            f"{index}/{len(videos)} META_FAIL {video_id} "
            f"blocked={blocked} consecutive_blocks={consecutive_block_errors}",
            flush=True,
        )
        if consecutive_block_errors >= 3:
            print("aborting after 3 consecutive block errors", flush=True)
            break
        time.sleep(args.rate_limit)
        continue

    consecutive_block_errors = 0
    extension = str(info.get("ext") or "mp4").lower()
    if extension not in {"mp4", "m4v", "webm"}:
        failed += 1
        manifest["results"].append(
            {
                **row,
                "status": "unsupported_container",
                "container": extension,
            }
        )
        write_manifest()
        print(f"{index}/{len(videos)} FORMAT_FAIL {video_id} ext={extension}", flush=True)
        time.sleep(args.rate_limit)
        continue
    key = f"{PREFIX}/{video_id}/{video_id}.{extension}"
    if existing.get(key, 0) > 0:
        skipped += 1
        manifest["results"].append(
            {
                **row,
                "status": "uploaded_verified",
                "object_key": key,
                "r2_size": existing[key],
                "sha256": None,
                "verification": "existing_positive_size",
                "format_id": str(info.get("format_id") or ""),
                "container": extension,
                "duration_seconds": info.get("duration"),
                "width": info.get("width"),
                "height": info.get("height"),
            }
        )
        known[video_id] = manifest["results"][-1]
        write_manifest()
        continue

    if args.dry_run:
        print(
            f"{index}/{len(videos)} WOULD_UPLOAD {video_id} "
            f"format={info.get('format_id')} ext={extension} "
            f"duration={info.get('duration')}",
            flush=True,
        )
        continue

    upload_id = None
    process: subprocess.Popen[bytes] | None = None
    try:
        multipart = s3.create_multipart_upload(
            Bucket=bucket,
            Key=key,
            ContentType="video/mp4" if extension in {"mp4", "m4v"} else "video/webm",
            Metadata={
                "source": "youtube.com",
                "source-id": "njp-youtube-official",
                "video-id": video_id,
                "authenticated": "false",
                "format-id": str(info.get("format_id") or "unknown"),
            },
        )
        upload_id = multipart["UploadId"]
        download_command = [
            str(YT_DLP),
            "--quiet",
            "--no-warnings",
            "--no-playlist",
            "--socket-timeout",
            "30",
            "--retries",
            "3",
            "--fragment-retries",
            "3",
            "-f",
            FORMAT,
            "-o",
            "-",
            url,
        ]
        process = subprocess.Popen(
            download_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        parts: list[dict[str, object]] = []
        digest = hashlib.sha256()
        observed_size = 0
        part_number = 0
        while True:
            block = process.stdout.read(PART_SIZE)
            if not block:
                break
            part_number += 1
            observed_size += len(block)
            digest.update(block)
            result = s3.upload_part(
                Bucket=bucket,
                Key=key,
                PartNumber=part_number,
                UploadId=upload_id,
                Body=block,
            )
            parts.append({"ETag": result["ETag"], "PartNumber": part_number})
        stderr = (process.stderr.read() if process.stderr else b"").decode(
            "utf-8", "replace"
        )
        return_code = process.wait(timeout=30)
        if return_code != 0:
            raise RuntimeError(f"yt-dlp exit {return_code}: {stderr[-300:]}")
        if observed_size <= 0 or not parts:
            raise RuntimeError("yt-dlp produced no media bytes")
        s3.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        head = s3.head_object(Bucket=bucket, Key=key)
        r2_size = int(head["ContentLength"])
        if r2_size != observed_size:
            raise RuntimeError(
                f"R2 size mismatch: streamed={observed_size} r2={r2_size}"
            )
        uploaded += 1
        bytes_uploaded += observed_size
        item = {
            **row,
            "status": "uploaded_verified",
            "object_key": key,
            "r2_size": r2_size,
            "sha256": digest.hexdigest(),
            "verification": "stream_hash_and_exact_head",
            "format_id": str(info.get("format_id") or ""),
            "format_note": str(info.get("format_note") or ""),
            "container": extension,
            "duration_seconds": info.get("duration"),
            "width": info.get("width"),
            "height": info.get("height"),
            "language": info.get("language"),
            "upload_date": info.get("upload_date"),
        }
        manifest["results"].append(item)
        known[video_id] = item
        write_manifest()
        print(
            f"{index}/{len(videos)} OK {video_id} "
            f"uploaded={uploaded} skipped={skipped} failed={failed} "
            f"bytes={bytes_uploaded}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        failed += 1
        if process is not None and process.poll() is None:
            process.kill()
        if upload_id is not None:
            try:
                s3.abort_multipart_upload(
                    Bucket=bucket, Key=key, UploadId=upload_id
                )
            except Exception:  # noqa: BLE001
                pass
        text = str(exc)
        blocked = any(
            marker in text.lower()
            for marker in ("429", "too many requests", "confirm you", "sign in")
        )
        consecutive_block_errors = consecutive_block_errors + 1 if blocked else 0
        manifest["results"].append(
            {
                **row,
                "status": "upload_failed",
                "error": type(exc).__name__,
                "blocked": blocked,
            }
        )
        write_manifest()
        print(
            f"{index}/{len(videos)} UPLOAD_FAIL {video_id} "
            f"{type(exc).__name__} blocked={blocked}",
            flush=True,
        )
        if consecutive_block_errors >= 3:
            print("aborting after 3 consecutive block errors", flush=True)
            break
    time.sleep(args.rate_limit)

manifest["summary"] = {
    "selected": len(videos),
    "verified_total": len(known),
    "uploaded_this_run": uploaded,
    "skipped_this_run": skipped,
    "failed_this_run": failed,
    "bytes_uploaded_this_run": bytes_uploaded,
    "elapsed_seconds": round(time.time() - started, 3),
}
write_manifest()
print(json.dumps(manifest["summary"], sort_keys=True), flush=True)
