from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from sqlalchemy import select

from app.api.deps import DbDep, Protected, SettingsDep
from app.api.serializers import import_job_result, photo_summary, task_summary
from app.importers.excel import run_import_job
from app.models.entities import ImportJob, RecognitionPhoto, RecognitionTask
from app.schemas.tasks import CreateTaskResponse, TaskCreate
from app.services.task_service import LocalPhoto, create_local_task, create_task

router = APIRouter(prefix="/v1/tasks", dependencies=[Protected])


@router.post("", response_model=CreateTaskResponse)
def create_recognition_task(payload: TaskCreate, db: DbDep, settings: SettingsDep):
    if len(payload.photos) > settings.max_photos_per_task:
        raise HTTPException(400, f"单任务最多 {settings.max_photos_per_task} 张照片")
    task, created = create_task(
        db,
        source_type="MANUAL",
        photos=payload.photos,
        task_config={
            "min_price": payload.min_price,
            "max_price": payload.max_price,
            "photo_sources": payload.photo_sources,
        },
        created_by=payload.created_by,
        remark=payload.remark,
    )
    return CreateTaskResponse(task_id=task.id, task_no=task.task_no, created_photos=created)


@router.get("")
def list_tasks(db: DbDep, page: int = 1, page_size: int = 20):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    tasks = db.scalars(
        select(RecognitionTask)
        .order_by(RecognitionTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"items": [task_summary(task) for task in tasks], "page": page, "page_size": page_size}


@router.get("/{task_id}")
def get_task(task_id: str, db: DbDep):
    task = db.get(RecognitionTask, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task_summary(task)


@router.get("/{task_id}/photos")
def list_task_photos(task_id: str, db: DbDep, page: int = 1, page_size: int = 50):
    task = db.get(RecognitionTask, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    photos = db.scalars(
        select(RecognitionPhoto)
        .where(RecognitionPhoto.task_id == task_id)
        .order_by(RecognitionPhoto.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"items": [photo_summary(photo) for photo in photos], "page": page, "page_size": page_size}


@router.post("/import-excel")
def import_excel(
    background_tasks: BackgroundTasks,
    db: DbDep,
    settings: SettingsDep,
    request: Request,
    file: UploadFile = File(...),
    max_photos: int | None = None,
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "只支持 .xlsx / .xlsm 巡店导出表")
    limited_max = min(max_photos or settings.max_photos_per_task, settings.max_photos_per_task)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename).suffix
    saved_path = settings.upload_dir / f"import-{uuid.uuid4().hex}{suffix}"
    size_limit = settings.max_upload_mb * 1024 * 1024
    digest = hashlib.sha256()
    size = 0
    with saved_path.open("wb") as output:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > size_limit:
                output.close()
                saved_path.unlink(missing_ok=True)
                raise HTTPException(413, f"文件超过 {settings.max_upload_mb}MB")
            digest.update(chunk)
            output.write(chunk)
    job = ImportJob(status="PENDING", source_file=str(saved_path))
    db.add(job)
    db.flush()
    db.commit()
    background_tasks.add_task(
        run_import_job,
        session_factory=request.app.state.db.session_factory,
        import_job_id=job.id,
        excel_path=saved_path,
        photo_sources=settings.photo_sources,
        max_photos=limited_max,
    )
    return import_job_result(job)


@router.post("/upload")
def upload_photos(
    db: DbDep,
    settings: SettingsDep,
    files: list[UploadFile] = File(...),
    created_by: str = "upload-api",
    remark: str | None = None,
):
    if len(files) > settings.max_photos_per_task:
        raise HTTPException(400, f"单任务最多 {settings.max_photos_per_task} 张照片")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    local_photos: list[LocalPhoto] = []
    size_limit = settings.max_upload_mb * 1024 * 1024
    for upload in files:
        if upload.content_type and not upload.content_type.startswith("image/"):
            raise HTTPException(400, f"仅支持图片文件: {upload.filename}")
        digest = hashlib.sha256()
        size = 0
        target = settings.upload_dir / f"upload-{uuid.uuid4().hex}-{Path(upload.filename or 'image').name}"
        with target.open("wb") as output:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > size_limit:
                    output.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(413, f"单张图片超过 {settings.max_upload_mb}MB")
                digest.update(chunk)
                output.write(chunk)
        local_photos.append(
            LocalPhoto(
                file_name=upload.filename or target.name,
                local_path=str(target),
                file_sha256=digest.hexdigest(),
                source_metadata={"content_type": upload.content_type},
            )
        )
    task, created = create_local_task(db, files=local_photos, created_by=created_by, remark=remark)
    return {"task_id": task.id, "task_no": task.task_no, "created_photos": created}
