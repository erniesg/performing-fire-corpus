#!/usr/bin/env python3
"""Project the local synthetic ASR/OCR evaluation into Rucksack Evidence Lab."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPIRY = "2099-01-01T00:00:00Z"
JOB_ID = "pfc-synthetic-eval-20260727"
SUITE_ID = "pfc-synthetic-asr-ocr"


def sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def git_revision(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


def started_at(path: Path) -> str:
    value = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def artifact(
    trace_id: str,
    artifact_id: str,
    role: str,
    content: bytes,
    media_type: str,
) -> dict[str, object]:
    return {
        "id": artifact_id,
        "ref": f"pfc-synth:{trace_id}:{artifact_id}",
        "sha256": sha(content),
        "media_type": media_type,
        "role": role,
        "expires_at": EXPIRY,
        "redaction_state": "none",
    }


def put_artifacts(ledger: Any, objects: list[tuple[dict[str, object], bytes]]) -> None:
    for metadata, content in objects:
        ledger.artifacts.put(
            ref=str(metadata["ref"]),
            content=content,
            media_type=str(metadata["media_type"]),
            sha256=str(metadata["sha256"]),
            expires_at=str(metadata["expires_at"]),
        )


def base_envelope(
    *,
    trace_id: str,
    case_id: str,
    candidate_id: str,
    suite_digest: str,
    revision: str,
    model: dict[str, str],
    parameter_digest: str,
    prompt_digest: str,
    inputs: list[dict[str, object]],
    outputs: list[dict[str, object]],
    output_digest: str,
    started: str,
    duration_ms: int,
    uncertainty: float,
    uncertainty_basis: str,
    grader: dict[str, object],
    modality: dict[str, object],
    environment: dict[str, str],
) -> dict[str, object]:
    return {
        "schema_version": "eval-trace-envelope/v1",
        "trace_id": trace_id,
        "repository": {
            "slug": "erniesg/performing-fire-corpus",
            "revision": revision,
        },
        "job_id": JOB_ID,
        "case_id": case_id,
        "candidate_id": candidate_id,
        "suite": {"id": SUITE_ID, "version": 1, "digest": suite_digest},
        "code": {"revision": revision, "version": "local-eval-20260727"},
        "environment": {
            **environment,
            "digest": sha(canonical(environment)),
        },
        "model": model,
        "prompt_digest": prompt_digest,
        "parameter_digest": parameter_digest,
        "input_artifacts": inputs,
        "output_artifacts": outputs,
        "output_digest": output_digest,
        "timing": {"started_at": started, "duration_ms": duration_ms},
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "uncertainty": {
            "score": round(max(0.0, min(1.0, uncertainty)), 6),
            "basis": uncertainty_basis,
        },
        "evidence_class": "synthetic",
        "privacy": {
            "classification": "synthetic",
            "retention": "local-ephemeral",
            "redaction_state": "none",
            "expires_at": EXPIRY,
            "content_permitted": True,
            "unavailable_reason": "",
        },
        "graders": [grader],
        "modality": modality,
        "proposal_state": "non-gating-development",
    }


def prior_grader(row: dict[str, object] | None, trace_id: str) -> dict[str, object]:
    if row is None:
        finding = {
            "state": "unscored-followup",
            "note": "Later controlled prompt experiment; requires operator review.",
        }
        return {
            "id": "prior-followup-unscored",
            "version": "provisional-20260727",
            "result": "unknown",
            "result_digest": sha(canonical(finding)),
        }
    passed = bool(row["pass"])
    category = str(row["failure_category"] or "semantic-pass").replace("_", "-")
    finding = {
        "pass": passed,
        "failure_category": row["failure_category"],
        "observation": row["observation"],
        "wer": row["wer"],
        "cer": row["cer"],
        "token_recall": row["token_recall"],
    }
    return {
        "id": f"prior-{category}",
        "version": "provisional-20260727",
        "result": "pass" if passed else "fail",
        "result_digest": sha(canonical(finding)),
    }


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as source:
        return round(source.getnframes() * 1000 / source.getframerate())


def asr_model(
    profile: str,
    summary: dict[str, object],
    provenance: dict[str, object],
) -> dict[str, str]:
    base_profile = "large-v3-turbo" if "turbo" in profile else "large-v3"
    record = provenance[base_profile]
    assert isinstance(record, dict)
    return {
        "engine": f"{summary['engine']}-{summary['engine_version']}",
        "id": str(summary["model_repo"]),
        "revision": str(record["model_revision"]),
    }


def seed_asr(
    *,
    ledger: Any,
    eval_root: Path,
    rows: dict[tuple[str, str, str], dict[str, object]],
    provenance: dict[str, object],
    suite_digest: str,
    revision: str,
) -> int:
    count = 0
    output_root = eval_root / "asr" / "outputs"
    for profile_root in sorted(path for path in output_root.iterdir() if path.is_dir()):
        summary_path = profile_root / "run-summary.json"
        if not summary_path.exists():
            continue
        profile = profile_root.name
        summary = load_json(summary_path)
        records = {
            str(item["case_id"]): item for item in summary.get("records", [])
        }
        parameters = summary.get("parameters", {})
        prompt = str(parameters.get("initial_prompt") or "")
        for result_path in sorted(profile_root.glob("asr*.json")):
            case_id = result_path.stem
            row = rows.get(("asr", profile, case_id))
            result = load_json(result_path)
            raw_output = result_path.read_bytes()
            candidate_text = str(result.get("text") or "").strip().encode("utf-8")
            source_path = eval_root / "asr" / case_id / "input.wav"
            reference_path = eval_root / "asr" / case_id / "ground-truth.txt"
            source = source_path.read_bytes()
            reference = reference_path.read_bytes()
            duration_ms = (
                round(float(row["duration_seconds"]) * 1000)
                if row is not None
                else wav_duration_ms(source_path)
            )
            language = str(result.get("language") or "unknown")
            segments = []
            for segment in result.get("segments", []):
                start_ms = max(0, round(float(segment["start"]) * 1000))
                end_ms = min(duration_ms, round(float(segment["end"]) * 1000))
                if end_ms <= start_ms:
                    continue
                segments.append(
                    {
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "language": language,
                        "text_excerpt": str(segment.get("text") or "").strip(),
                    }
                )
            trace_id = f"pfc-asr-{profile}-{case_id}"
            source_artifact = artifact(
                trace_id, "source-audio", "input", source, "audio/wav"
            )
            reference_artifact = artifact(
                trace_id,
                "reference-transcript",
                "reference",
                reference,
                "text/plain",
            )
            candidate_artifact = artifact(
                trace_id,
                "candidate-transcript",
                "output",
                candidate_text,
                "text/plain",
            )
            structured_artifact = artifact(
                trace_id,
                "structured-output",
                "output",
                raw_output,
                "application/json",
            )
            run_record = records.get(case_id, {})
            elapsed = float(
                run_record.get(
                    "elapsed_seconds",
                    row["elapsed_seconds"] if row is not None else 0,
                )
            )
            uncertainty = (
                max(float(row["cer"]), 1.0 - float(row["token_recall"]))
                if row is not None
                else 0.5
            )
            envelope = base_envelope(
                trace_id=trace_id,
                case_id=case_id,
                candidate_id=profile,
                suite_digest=suite_digest,
                revision=revision,
                model=asr_model(profile, summary, provenance),
                parameter_digest=sha(canonical(parameters)),
                prompt_digest=sha(prompt),
                inputs=[source_artifact, reference_artifact],
                outputs=[candidate_artifact, structured_artifact],
                output_digest=sha(raw_output),
                started=started_at(summary_path),
                duration_ms=round(elapsed * 1000),
                uncertainty=uncertainty,
                uncertainty_basis=(
                    "synthetic-reference-diagnostic"
                    if row is not None
                    else "followup-requires-review"
                ),
                grader=prior_grader(row, trace_id),
                modality={
                    "kind": "audio",
                    "duration_ms": duration_ms,
                    "detected_language": language,
                    "segments": segments,
                },
                environment={
                    "runtime": f"mlx-whisper-{summary['engine_version']}",
                    "platform": "darwin-arm64-m2-max",
                },
            )
            put_artifacts(
                ledger,
                [
                    (source_artifact, source),
                    (reference_artifact, reference),
                    (candidate_artifact, candidate_text),
                    (structured_artifact, raw_output),
                ],
            )
            ledger.register(envelope)
            count += 1
    return count


def ocr_output(profile: str, profile_root: Path, case_id: str) -> tuple[bytes, bytes, Any]:
    if profile == "apple-vision":
        result_path = profile_root / f"{case_id}.json"
        raw = result_path.read_bytes()
        result = json.loads(raw)
        return raw, str(result["text"]).encode("utf-8"), result
    result_path = profile_root / f"{case_id}.txt"
    raw = result_path.read_bytes()
    return raw, raw, None


def ocr_model(profile: str, provenance: dict[str, object]) -> dict[str, str]:
    record = provenance[profile]
    assert isinstance(record, dict)
    if profile == "apple-vision":
        return {
            "engine": "apple-vision",
            "id": "VNRecognizeTextRequest-accurate",
            "revision": f"request-{record['engine_revision']}-macos-15.6.1",
        }
    return {
        "engine": f"tesseract-{record['engine_version']}",
        "id": str(record["model_repo"]),
        "revision": str(record["model_revision"]),
    }


def seed_ocr(
    *,
    ledger: Any,
    eval_root: Path,
    rows: dict[tuple[str, str, str], dict[str, object]],
    provenance: dict[str, object],
    suite_digest: str,
    revision: str,
) -> int:
    count = 0
    output_root = eval_root / "ocr" / "outputs"
    for profile_root in sorted(path for path in output_root.iterdir() if path.is_dir()):
        profile = profile_root.name
        for input_path in sorted((eval_root / "ocr").glob("ocr*/input.png")):
            case_id = input_path.parent.name
            row = rows[("ocr", profile, case_id)]
            raw_output, candidate_text, result = ocr_output(
                profile, profile_root, case_id
            )
            source = input_path.read_bytes()
            reference = (input_path.parent / "ground-truth.txt").read_bytes()
            trace_id = f"pfc-ocr-{profile}-{case_id}"
            source_artifact = artifact(
                trace_id, "source-image", "input", source, "image/png"
            )
            reference_artifact = artifact(
                trace_id, "reference-text", "reference", reference, "text/plain"
            )
            candidate_artifact = artifact(
                trace_id, "candidate-ocr", "output", candidate_text, "text/plain"
            )
            outputs = [candidate_artifact]
            objects = [
                (source_artifact, source),
                (reference_artifact, reference),
                (candidate_artifact, candidate_text),
            ]
            if profile == "apple-vision":
                structured_artifact = artifact(
                    trace_id,
                    "structured-output",
                    "output",
                    raw_output,
                    "application/json",
                )
                outputs.append(structured_artifact)
                objects.append((structured_artifact, raw_output))
            regions = []
            if isinstance(result, dict):
                for index, line in enumerate(result.get("lines", []), start=1):
                    x = max(0.0, min(1.0, float(line["x"])))
                    width = max(0.000001, min(1.0 - x, float(line["width"])))
                    height = max(0.000001, min(1.0, float(line["height"])))
                    y = max(
                        0.0,
                        min(
                            1.0 - height,
                            1.0 - float(line["y"]) - height,
                        ),
                    )
                    regions.append(
                        {
                            "bbox": [x, y, width, height],
                            "reading_order": index,
                            "label_ref": f"line-{index}",
                        }
                    )
            parameters = (
                {
                    "request_revision": result["engineRevision"],
                    "recognition_level": result["recognitionLevel"],
                    "languages": result["recognitionLanguages"],
                    "uses_language_correction": result["usesLanguageCorrection"],
                }
                if isinstance(result, dict)
                else {"oem": 1, "psm": 3, "languages": ["kor", "eng"]}
            )
            envelope = base_envelope(
                trace_id=trace_id,
                case_id=case_id,
                candidate_id=profile,
                suite_digest=suite_digest,
                revision=revision,
                model=ocr_model(profile, provenance),
                parameter_digest=sha(canonical(parameters)),
                prompt_digest=sha("no-prompt"),
                inputs=[source_artifact, reference_artifact],
                outputs=outputs,
                output_digest=sha(raw_output),
                started=started_at(profile_root),
                duration_ms=round(float(row["elapsed_seconds"]) * 1000),
                uncertainty=max(
                    float(row["cer"]), 1.0 - float(row["token_recall"])
                ),
                uncertainty_basis="synthetic-reference-diagnostic",
                grader=prior_grader(row, trace_id),
                modality={
                    "kind": "image",
                    "page": 1,
                    "orientation_degrees": (
                        90 if case_id == "ocr07-rotated" else 0
                    ),
                    "regions": regions,
                },
                environment={
                    "runtime": (
                        "apple-vision-request-3"
                        if profile == "apple-vision"
                        else "tesseract-5.5.1"
                    ),
                    "platform": "darwin-arm64-m2-max",
                },
            )
            put_artifacts(ledger, objects)
            ledger.register(envelope)
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--rucksack-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    args = parser.parse_args()
    corpus_root = args.corpus_root.expanduser().resolve()
    rucksack_root = args.rucksack_root.expanduser().resolve()
    state_root = args.state_root.expanduser().resolve()
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_root.chmod(0o700)
    eval_root = corpus_root / ".local" / "eval-local-20-20260727"
    sys.path.insert(0, str(rucksack_root / "src"))
    from rucksack.eval_evidence import EvalEvidenceLedger

    evaluation = load_json(eval_root / "evaluation.json")
    rows = {
        (str(row["modality"]), str(row["profile"]), str(row["case_id"])): row
        for row in evaluation["rows"]
    }
    provenance = evaluation["provenance"]
    suite_digest = sha(
        (eval_root / "asr" / "cases.json").read_bytes()
        + (eval_root / "ocr" / "cases.json").read_bytes()
        + (eval_root / "evaluation.json").read_bytes()
    )
    revision = git_revision(corpus_root)
    ledger = EvalEvidenceLedger(state_root / "eval-evidence")
    asr_count = seed_asr(
        ledger=ledger,
        eval_root=eval_root,
        rows=rows,
        provenance=provenance,
        suite_digest=suite_digest,
        revision=revision,
    )
    ocr_count = seed_ocr(
        ledger=ledger,
        eval_root=eval_root,
        rows=rows,
        provenance=provenance,
        suite_digest=suite_digest,
        revision=revision,
    )
    projection = ledger.list()
    for private_directory in (
        state_root / "eval-evidence",
        state_root / "eval-evidence" / "artifacts",
        state_root / "eval-evidence" / "artifacts" / "objects",
    ):
        private_directory.chmod(0o700)
    print(
        json.dumps(
            {
                "state_root": str(state_root),
                "asr_traces": asr_count,
                "ocr_traces": ocr_count,
                "total_traces": projection["counts"]["total"],
                "reviewed": projection["counts"]["reviewed"],
                "content": "synthetic-only",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
