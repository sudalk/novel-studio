import logging
import threading

from app.config import Settings
from app.database import utcnow
from app.models import Episode, Job
from app.services.pipeline import run_breakdown, run_illustrate, run_line_tts, run_pipeline, run_tts, run_video

logger = logging.getLogger(__name__)

_RUNNERS = {
    "breakdown": run_breakdown,
    "tts": run_tts,
    "illustrate": run_illustrate,
    "video": run_video,
    "pipeline": run_pipeline,
}


class JobRunner:
    def __init__(self, session_factory, settings: Settings):
        self.session_factory = session_factory
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="studio-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id = self._claim()
            if not job_id:
                self._stop.wait(0.4)
                continue
            try:
                execute_job(job_id, self.session_factory, self.settings)
            except Exception:
                logger.exception("任务执行器异常")

    def _claim(self) -> str | None:
        db = self.session_factory()
        try:
            job = db.query(Job).filter(Job.status == "queued").order_by(Job.created_at.asc()).first()
            if job is None:
                return None
            job.status = "running"
            job.message = "开始执行"
            job.updated_at = utcnow()
            db.commit()
            return job.id
        except Exception:
            db.rollback()
            logger.exception("领取任务失败")
            return None
        finally:
            db.close()


def execute_job(job_id: str, session_factory, settings: Settings) -> None:
    db = session_factory()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        kind = job.kind
        episode_id = job.episode_id
        line_id = job.line_id
        job.status = "running"
        job.updated_at = utcnow()
        db.commit()
    finally:
        db.close()

    def progress(percent: int, message: str) -> None:
        session = session_factory()
        try:
            current = session.get(Job, job_id)
            if current is None:
                return
            current.status = "running"
            current.progress = max(0, min(99, int(percent)))
            current.message = message[:200]
            current.updated_at = utcnow()
            session.commit()
        finally:
            session.close()

    try:
        if not episode_id:
            raise RuntimeError("任务没有绑定分集")
        if kind == "line-tts":
            if not line_id:
                raise RuntimeError("没有指定要重配的台词")
            run_line_tts(session_factory, settings, episode_id, line_id, progress)
        else:
            runner = _RUNNERS.get(kind)
            if runner is None:
                raise RuntimeError(f"未知任务：{kind}")
            runner(session_factory, settings, episode_id, progress)
    except Exception as exc:
        logger.exception("任务 %s 失败", job_id)
        session = session_factory()
        try:
            current = session.get(Job, job_id)
            if current is not None:
                current.status = "failed"
                current.error = str(exc)[:2000]
                current.message = "失败"
                current.updated_at = utcnow()
            if episode_id:
                episode = session.get(Episode, episode_id)
                if episode is not None:
                    episode.error = str(exc)[:2000]
                    episode.updated_at = utcnow()
            session.commit()
        finally:
            session.close()
        return

    session = session_factory()
    try:
        current = session.get(Job, job_id)
        if current is not None:
            current.status = "succeeded"
            current.progress = 100
            current.message = "完成"
            current.error = None
            current.updated_at = utcnow()
        if episode_id:
            episode = session.get(Episode, episode_id)
            if episode is not None:
                episode.error = None
        session.commit()
    finally:
        session.close()
