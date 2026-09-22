from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path


def _request_json(url: str, api_key: str | None) -> dict:
    headers = {"X-API-Key": api_key} if api_key else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _download(url: str, path: Path, api_key: str | None) -> None:
    headers = {"User-Agent": "price-tag-service-export/1.0"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response, path.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--api-key", default="")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    output_dir = args.output_dir
    image_dir = output_dir / "images"
    result_dir = output_dir / "service_results"
    image_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)

    task = _request_json(f"{base_url}/v1/tasks/{args.task_id}", args.api_key)
    photos_response = _request_json(
        f"{base_url}/v1/tasks/{args.task_id}/photos", args.api_key
    )
    photos = photos_response["items"]
    (output_dir / "service_task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "service_photos.json").write_text(
        json.dumps(photos, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    downloaded = 0
    saved_results = 0
    errors: list[str] = []
    for index, photo in enumerate(photos, 1):
        image_name = photo.get("image_name") or f"{photo['id']}.jpg"
        stem = Path(image_name).stem
        suffix = Path(image_name).suffix or ".jpg"
        prefix = f"{index:02d}_{stem}"

        try:
            _download(photo["image_url"], image_dir / f"{prefix}{suffix}", args.api_key)
            downloaded += 1
        except Exception as exc:
            errors.append(f"download {image_name}: {exc}")

        record: dict = {"photo": photo}
        for key, endpoint in (("qc", "qc"), ("price", "tags")):
            try:
                record[key] = _request_json(
                    f"{base_url}/v1/photos/{photo['id']}/{endpoint}", args.api_key
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    errors.append(f"{endpoint} {image_name}: {exc}")
            except Exception as exc:
                errors.append(f"{endpoint} {image_name}: {exc}")

        (result_dir / f"{prefix}.service.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        saved_results += 1

    print(
        f"photos={len(photos)} downloaded={downloaded} results={saved_results} "
        f"task_status={task['status']} failed_photos={task['failed_photos']}"
    )
    for error in errors:
        print(f"ERROR: {error}")
    return 0 if task["status"] == "COMPLETED" and task["failed_photos"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
