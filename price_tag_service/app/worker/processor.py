from __future__ import annotations

import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app import PIPELINE_VERSION
from app.clients.aism import AismClient
from app.clients.models import AismCallError, CallLog
from app.clients.oss import OssClient
from app.clients.sku import SampleSkuClient
from app.core.config import Settings
from app.models.entities import (
    AICallLog,
    OssUploadCache,
    PhotoOutcome,
    PhotoStatus,
    PriceResult,
    PriceTagDetail,
    ProcessStage,
    QcResult,
    RecognitionPhoto,
    RecognitionRun,
    RecognitionTask,
)
from app.processing.postprocess import parse_amount, postprocess_price_payload
from app.processing.quality import normalize_quality_payload
from app.processing.sku_match import match_sku_tags, normalize_sku
from app.services.task_service import refresh_task_counters, utc_now
from app.worker.image_size import read_image_size
from app.worker.queue import claim_next_photo, recover_stale_photos

logger = logging.getLogger(__name__)


class PhotoProcessor:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.qc_client = AismClient(settings.qc_config, settings.prompt_dir)
        self.price_client = AismClient(settings.price_tag_config, settings.prompt_dir)
        self.oss_client = OssClient(settings.oss_config)
        self.sku_client = SampleSkuClient(settings.sku_sample_dir)

    def _agent_enabled(self, session: Session, photo: RecognitionPhoto, name: str) -> bool:
        task = session.get(RecognitionTask, photo.task_id)
        agents = ((task.task_config if task else {}).get("agent_config") or {})
        return bool((agents.get(name) or {}).get("enabled", False))

    def _effective_limits(
        self, session: Session, photo: RecognitionPhoto
    ) -> tuple[float | None, float | None]:
        task = session.get(RecognitionTask, photo.task_id)
        config = task.task_config if task is not None else {}
        return (
            config.get("min_price", self.settings.min_price),
            config.get("max_price", self.settings.max_price),
        )

    def _quality_check_enabled(
        self, session: Session, photo: RecognitionPhoto
    ) -> bool:
        task = session.get(RecognitionTask, photo.task_id)
        config = task.task_config if task is not None else {}
        agent_config = config.get("agent_config") or {}
        quality_config = agent_config.get("quality_check") or {}
        return bool(quality_config.get("enabled", True))

    def process_photo_id(self, photo_id: str) -> None:
        lease_stop = threading.Event()
        lease_thread = None
        try:
            with self.session_factory() as session:
                photo = session.get(RecognitionPhoto, photo_id)
                if photo is None or photo.status != PhotoStatus.RUNNING:
                    return
                if photo.locked_by:
                    lease_thread = threading.Thread(
                        target=self._renew_lease, args=(photo_id, photo.locked_by, lease_stop), daemon=True
                    )
                    lease_thread.start()
                try:
                    self._process(session, photo)
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    self._handle_failure(photo_id, exc)
        finally:
            lease_stop.set()
            if lease_thread is not None:
                lease_thread.join(timeout=1)

    def _renew_lease(self, photo_id: str, worker_id: str, stop: threading.Event) -> None:
        while not stop.wait(max(1, self.settings.worker_lease_sec // 3)):
            try:
                with self.session_factory() as session:
                    session.execute(update(RecognitionPhoto).where(
                        RecognitionPhoto.id == photo_id,
                        RecognitionPhoto.status == PhotoStatus.RUNNING,
                        RecognitionPhoto.locked_by == worker_id,
                        RecognitionPhoto.locked_until.is_not(None),
                    ).values(locked_until=utc_now() + timedelta(seconds=self.settings.worker_lease_sec)))
                    session.commit()
            except Exception:
                logger.exception("Failed to renew photo lease %s", photo_id)

    def _process(self, session: Session, photo: RecognitionPhoto) -> None:
        run = self._get_or_create_run(session, photo)
        if photo.current_stage == ProcessStage.UPLOAD:
            self._stage_upload(session, photo)
        if photo.current_stage == ProcessStage.QUALITY_CHECK:
            if not self._stage_quality_check(session, photo, run):
                return
        if photo.current_stage == ProcessStage.PRICE_RECOGNITION:
            if self._agent_enabled(session, photo, "sku"):
                self._stage_parallel(session, photo, run)
            else:
                self._stage_price(session, photo, run)
        if photo.current_stage == ProcessStage.POSTPROCESS:
            self._stage_postprocess(session, photo, run)
        if self._agent_enabled(session, photo, "sku_price_match"):
            self._stage_match(session, photo, run)
        self._complete(session, photo, run, PhotoOutcome.PROCESSED, None)

    def _stage_parallel(self, session: Session, photo: RecognitionPhoto, run: RecognitionRun) -> None:
        existing = session.scalar(select(PriceResult).where(PriceResult.run_id == run.id))
        sku_result = (run.config_snapshot or {}).get("sku_result")
        pending = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            if existing is None:
                pending["price"] = pool.submit(self.price_client.price_tag_detect, photo.oss_url or "")
            if sku_result is None:
                pending["sku"] = pool.submit(self.sku_client.recognize, photo.oss_url or "")
            failures = {}
            for branch, future in pending.items():
                try:
                    result = future.result()
                    if branch == "price":
                        self._stage_price(session, photo, run, result=result)
                        photo.current_stage = ProcessStage.PRICE_RECOGNITION
                        session.commit()
                    else:
                        normalized = normalize_sku(result)
                        run.config_snapshot = {**run.config_snapshot, "sku_result": normalized}
                        session.commit()
                except Exception as exc:
                    failures[branch] = exc
        if failures:
            branch, exc = next(iter(failures.items()))
            run.config_snapshot = {**run.config_snapshot, "branch_errors": {
                name: str(error) for name, error in failures.items()}}
            session.commit()
            if isinstance(exc, FileNotFoundError):
                raise AismCallError(str(exc), error_type="CONFIG_ERROR") from exc
            raise exc
        if (run.config_snapshot or {}).get("branch_errors"):
            run.config_snapshot = {key: value for key, value in run.config_snapshot.items()
                                   if key != "branch_errors"}
        photo.current_stage = ProcessStage.POSTPROCESS
        session.commit()

    def _stage_match(self, session: Session, photo: RecognitionPhoto, run: RecognitionRun) -> None:
        if (run.config_snapshot or {}).get("match_result") is not None:
            return
        sku = (run.config_snapshot or {}).get("sku_result")
        price = session.scalar(select(PriceResult).where(PriceResult.run_id == run.id))
        if sku is None or price is None:
            raise AismCallError("Both recognition branches are required for matching", error_type="CONFIG_ERROR")
        session.flush()
        details = session.scalars(select(PriceTagDetail).where(PriceTagDetail.price_result_id == price.id)).all()
        tags = [{"id": detail.tag_id,
                 "bbox": [detail.bbox_xmin, detail.bbox_ymin, detail.bbox_xmax, detail.bbox_ymax],
                 "price": str(detail.price) if detail.price is not None else None,
                 "score": min(value for value in (detail.confidence, detail.price_confidence) if value is not None)
                 if detail.confidence is not None or detail.price_confidence is not None else None}
                for detail in details]
        run.config_snapshot = {**run.config_snapshot, "match_result": {
            "status": "SUCCEEDED", "method_version": "SPATIAL_RULE_V0",
            "pairs": match_sku_tags(sku["items"], tags)}}
        session.commit()

    def _get_or_create_run(self, session: Session, photo: RecognitionPhoto) -> RecognitionRun:
        run = session.scalar(
            select(RecognitionRun)
            .where(RecognitionRun.photo_id == photo.id)
            .order_by(RecognitionRun.run_no.desc())
            .limit(1)
        )
        if run is not None and run.status == "RUNNING":
            return run
        next_run_no = session.scalar(
            select(func.max(RecognitionRun.run_no)).where(RecognitionRun.photo_id == photo.id)
        )
        run = RecognitionRun(
            photo_id=photo.id,
            run_no=(next_run_no or 0) + 1,
            status="RUNNING",
            outcome=PhotoOutcome.PENDING,
            pipeline_version=PIPELINE_VERSION,
            model_version=self.settings.price_tag_config.model_name,
            prompt_version=self.settings.price_tag_config.prompt_version,
            config_snapshot={
                "min_price": self._effective_limits(session, photo)[0],
                "max_price": self._effective_limits(session, photo)[1],
                "photo_sources": self.settings.photo_sources,
                "quality_check_enabled": self._quality_check_enabled(session, photo),
            },
            started_at=utc_now(),
        )
        session.add(run)
        session.flush()
        return run

    def _stage_upload(self, session: Session, photo: RecognitionPhoto) -> None:
        if photo.image_url:
            photo.oss_url = photo.image_url
            photo.current_stage = ProcessStage.QUALITY_CHECK
            photo.updated_at = utc_now()
            session.commit()
            return
        if not photo.local_path:
            raise AismCallError("Photo has neither image_url nor local_path", error_type="CONFIG_ERROR")

        local_path = Path(photo.local_path)
        file_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
        photo.file_sha256 = file_hash
        cached = session.scalar(select(OssUploadCache).where(OssUploadCache.file_sha256 == file_hash))
        if cached is not None:
            photo.oss_url = cached.oss_url
        else:
            oss_url = self.oss_client.upload(local_path)
            photo.oss_url = oss_url
            session.add(
                OssUploadCache(file_sha256=file_hash, file_name=local_path.name, oss_url=oss_url)
            )
        photo.current_stage = ProcessStage.QUALITY_CHECK
        photo.updated_at = utc_now()
        session.commit()

    def _stage_quality_check(
        self, session: Session, photo: RecognitionPhoto, run: RecognitionRun
    ) -> bool:
        photo.current_stage = ProcessStage.QUALITY_CHECK
        photo.updated_at = utc_now()
        session.commit()

        if not self._quality_check_enabled(session, photo):
            session.execute(delete(QcResult).where(QcResult.run_id == run.id))
            session.add(
                QcResult(
                    photo_id=photo.id,
                    run_id=run.id,
                    qc_status="SKIPPED",
                    is_valid=False,
                    should_continue=True,
                    qc_blur=None,
                    qc_over_exposure=None,
                    qc_low_light=None,
                    qc_file_corrupted=None,
                    invalid_reason=None,
                    invalid_reasons=[],
                    scene_type=None,
                    scene_group=None,
                    has_price_tag=False,
                    is_quality_pass=False,
                    is_target_scene=False,
                    can_proceed_to_price=True,
                    rejection_reasons=[],
                    model_name=None,
                    prompt_version=None,
                    model_cost_sec=0.0,
                    raw_payload={
                        "status": "SKIPPED",
                        "reason": "quality_check_disabled",
                    },
                )
            )
            photo.current_stage = ProcessStage.PRICE_RECOGNITION
            photo.updated_at = utc_now()
            session.commit()
            return True

        result = self.qc_client.quality_check(photo.oss_url or "")
        self._save_call_logs(session, photo, run, result.call_logs)
        run.model_version = result.model_name
        run.prompt_version = result.prompt_version
        normalized = normalize_quality_payload(result.parsed_payload)
        session.execute(delete(QcResult).where(QcResult.run_id == run.id))
        session.add(
                QcResult(
                    photo_id=photo.id,
                    run_id=run.id,
                    qc_status="PASSED" if normalized.can_proceed_to_price else "BLOCKED",
                is_valid=normalized.is_valid,
                should_continue=normalized.should_continue,
                qc_blur=normalized.qc_blur,
                qc_over_exposure=normalized.qc_over_exposure,
                qc_low_light=normalized.qc_low_light,
                qc_file_corrupted=normalized.qc_file_corrupted,
                invalid_reason=normalized.invalid_reason,
                invalid_reasons=normalized.invalid_reasons,
                scene_type=normalized.scene_type,
                scene_group=normalized.scene_group,
                has_price_tag=normalized.has_price_tag,
                is_quality_pass=normalized.is_quality_pass,
                is_target_scene=normalized.is_target_scene,
                can_proceed_to_price=normalized.can_proceed_to_price,
                confidence=normalized.confidence,
                rejection_reasons=normalized.rejection_reasons,
                model_name=result.model_name,
                prompt_version=result.prompt_version,
                model_cost_sec=result.cost_sec,
                raw_payload={
                    "parsed": result.parsed_payload,
                    "raw_response": result.raw_response,
                },
            )
        )
        photo.model_version = result.model_name
        photo.prompt_version = result.prompt_version
        photo.pipeline_version = PIPELINE_VERSION
        if not normalized.can_proceed_to_price:
            self._complete(
                session,
                photo,
                run,
                PhotoOutcome.BLOCKED,
                normalized.stop_reason,
                normalized.rejection_reasons,
            )
            return False
        photo.current_stage = ProcessStage.PRICE_RECOGNITION
        photo.updated_at = utc_now()
        session.commit()
        return True

    def _stage_price(
        self, session: Session, photo: RecognitionPhoto, run: RecognitionRun, result=None
    ) -> None:
        result = result or self.price_client.price_tag_detect(photo.oss_url or "")
        self._save_call_logs(session, photo, run, result.call_logs)
        run.model_version = result.model_name
        run.prompt_version = result.prompt_version
        session.execute(delete(PriceResult).where(PriceResult.run_id == run.id))
        photo.current_stage = ProcessStage.POSTPROCESS
        photo.model_version = result.model_name
        photo.prompt_version = result.prompt_version
        photo.pipeline_version = PIPELINE_VERSION
        photo.updated_at = utc_now()
        session.add(
            PriceResult(
                photo_id=photo.id,
                run_id=run.id,
                recognition_mode="WHOLE_IMAGE_VLM",
                model_name=result.model_name,
                prompt_version=result.prompt_version,
                model_cost_sec=result.cost_sec,
                raw_payload={
                    "parsed": result.parsed_payload,
                    "raw_response": result.raw_response,
                },
            )
        )
        session.commit()

    def _stage_postprocess(
        self, session: Session, photo: RecognitionPhoto, run: RecognitionRun
    ) -> None:
        price_result = session.scalar(
            select(PriceResult)
            .where(PriceResult.run_id == run.id)
            .order_by(PriceResult.created_at.desc())
            .limit(1)
        )
        if price_result is None:
            photo.current_stage = ProcessStage.PRICE_RECOGNITION
            raise AismCallError("Price result missing before post-process", error_type="STATE_ERROR")

        parsed = price_result.raw_payload.get("parsed", {})
        image_size = read_image_size(photo.local_path or photo.oss_url or "")
        min_price, max_price = self._effective_limits(session, photo)
        postprocessed = postprocess_price_payload(
            parsed,
            image_width=image_size[0] if image_size else None,
            image_height=image_size[1] if image_size else None,
            min_price=min_price,
            max_price=max_price,
        )
        session.execute(delete(PriceTagDetail).where(PriceTagDetail.price_result_id == price_result.id))
        detail_tags = list(postprocessed.price_tags)
        for tag in detail_tags:
            bbox = tag.get("bbox") or [None, None, None, None]
            amount = parse_amount(tag.get("price"))
            bundle_amount = parse_amount(tag.get("bundle_price"))
            second_amount = parse_amount(tag.get("second_item_price"))
            session.add(
                PriceTagDetail(
                    price_result_id=price_result.id,
                    photo_id=photo.id,
                    tag_id=int(tag.get("id") or 0),
                    shelf_layer=tag.get("shelf_layer"),
                    bbox_xmin=bbox[0],
                    bbox_ymin=bbox[1],
                    bbox_xmax=bbox[2],
                    bbox_ymax=bbox[3],
                    coordinate_scale=1000,
                    price=float(amount) if amount is not None else None,
                    raw_price_text=tag.get("raw_price_text"),
                    tag_type=tag.get("tag_type"),
                    promotion_type=tag.get("promotion_type"),
                    bundle_quantity=tag.get("bundle_quantity"),
                    bundle_price=float(bundle_amount) if bundle_amount is not None else None,
                    second_item_price=float(second_amount) if second_amount is not None else None,
                    unit=tag.get("unit"),
                    confidence=tag.get("confidence"),
                    price_confidence=tag.get("price_confidence"),
                    confidence_reason=tag.get("confidence_reason"),
                    is_promotion=False,
                )
            )
        price_result.total_raw_tags = (
            len(parsed.get("price_tags", [])) if isinstance(parsed, dict) else len(parsed)
        ) + (len(parsed.get("promotion_tags", [])) if isinstance(parsed, dict) else 0)
        price_result.total_tags = len(postprocessed.price_tags)
        price_result.total_promotion_tags = len(postprocessed.promotion_tags)
        price_result.image_width = postprocessed.image_width
        price_result.image_height = postprocessed.image_height
        price_result.postprocess_version = postprocessed.postprocess_version
        price_result.filter_events = postprocessed.filter_events

    def _complete(
        self,
        session: Session,
        photo: RecognitionPhoto,
        run: RecognitionRun,
        outcome: PhotoOutcome,
        stop_reason: str | None,
        rejection_reasons: list[str] | None = None,
    ) -> None:
        now = utc_now()
        photo.status = PhotoStatus.COMPLETED
        photo.outcome = outcome
        photo.stop_reason = stop_reason
        photo.current_stage = ProcessStage.DONE
        photo.locked_by = None
        photo.locked_until = None
        photo.updated_at = now
        run.status = "COMPLETED"
        run.outcome = outcome
        run.stop_reason = stop_reason
        run.completed_at = now
        if rejection_reasons:
            run.config_snapshot = {**run.config_snapshot, "rejection_reasons": rejection_reasons}
        refresh_task_counters(session, photo.task_id)
        session.commit()

    def _save_call_logs(
        self,
        session: Session,
        photo: RecognitionPhoto,
        run: RecognitionRun,
        call_logs: list[CallLog],
    ) -> None:
        for log in call_logs:
            session.add(
                AICallLog(
                    photo_id=photo.id,
                    run_id=run.id,
                    ability=log.ability,
                    attempt=log.attempt,
                    success=log.success,
                    http_status=log.http_status,
                    business_status=log.business_status,
                    request_payload=log.request_payload,
                    response_payload=log.response_payload,
                    model_name=log.model_name,
                    prompt_version=log.prompt_version,
                    error_type=log.error_type,
                    error_message=log.error_message,
                    cost_sec=log.cost_sec,
                )
            )
        session.commit()

    def _handle_failure(self, photo_id: str, exc: Exception) -> None:
        with self.session_factory() as session:
            photo = session.get(RecognitionPhoto, photo_id)
            if photo is None:
                return
            run = session.scalar(
                select(RecognitionRun)
                .where(RecognitionRun.photo_id == photo.id)
                .order_by(RecognitionRun.run_no.desc())
                .limit(1)
            )
            if isinstance(exc, AismCallError):
                if run is not None:
                    self._save_call_logs(session, photo, run, exc.call_logs)
            error_type = getattr(exc, "error_type", "UNKNOWN_ERROR")
            message = str(exc)
            now = utc_now()
            photo.last_error_type = error_type
            photo.last_error_message = message[:4000]
            non_retryable = error_type in {
                "CONFIG_ERROR",
                "HTTP_NON_RETRYABLE",
                "FILE_NOT_FOUND",
                "STATE_ERROR",
            }
            if not non_retryable and photo.retry_count < self.settings.worker_max_retries:
                photo.retry_count += 1
                index = min(photo.retry_count - 1, len(self.settings.worker_retry_backoff_sec) - 1)
                photo.status = PhotoStatus.QUEUED
                photo.outcome = PhotoOutcome.PENDING
                photo.next_run_at = now + timedelta(seconds=self.settings.worker_retry_backoff_sec[index])
            else:
                photo.status = PhotoStatus.FAILED
                photo.outcome = PhotoOutcome.FAILED
                photo.stop_reason = error_type
                if run is not None:
                    run.status = "FAILED"
                    run.outcome = PhotoOutcome.FAILED
                    run.stop_reason = error_type
                    run.completed_at = now
            photo.locked_by = None
            photo.locked_until = None
            photo.updated_at = now
            refresh_task_counters(session, photo.task_id)
            session.commit()


class WorkerRunner:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]):
        self.settings = settings
        self.session_factory = session_factory
        self.stop_event = threading.Event()
        self.use_select_for_update = not settings.database_url.startswith("sqlite")

    def start(self) -> None:
        threads = [
            threading.Thread(target=self._worker_loop, name=f"price-worker-{index}", daemon=True)
            for index in range(self.settings.worker_concurrency)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def stop(self) -> None:
        self.stop_event.set()

    def _worker_loop(self) -> None:
        processor = PhotoProcessor(self.settings, self.session_factory)
        while not self.stop_event.is_set():
            photo_id = self._claim()
            if photo_id is None:
                self.stop_event.wait(self.settings.worker_poll_interval_sec)
                continue
            try:
                processor.process_photo_id(photo_id)
            except Exception:
                logger.exception("Failed to process photo %s", photo_id)

    def _claim(self) -> str | None:
        with self.session_factory() as session:
            recover_stale_photos(session, self.settings.worker_lease_sec)
            photo = claim_next_photo(
                session,
                worker_id=threading.current_thread().name,
                lease_sec=self.settings.worker_lease_sec,
                use_select_for_update=self.use_select_for_update,
            )
            if photo is None:
                session.commit()
                return None
            photo_id = photo.id
            session.commit()
            return photo_id
