"""Durable per-campaign checkpoints for metered external calls.

Write-ahead record is committed BEFORE dispatch. On an ambiguous error/crash,
a call is NOT replayed automatically. A human must authorize that particular
step to run again (which could incur another provider charge).
"""
import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .models import StepCheckpoint

log = logging.getLogger(__name__)


class CheckpointNeedsReview(RuntimeError):
    """Unsafe to repeat: charge may already have occurred."""


def fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str,
    ).encode("utf-8")).hexdigest()


def get_checkpoint(db, campaign_id: str, step_key: str):
    return db.scalar(select(StepCheckpoint).where(
        StepCheckpoint.campaign_id == campaign_id,
        StepCheckpoint.step_key == step_key,
    ))


def checkpointed(db, campaign_id: str, step_key: str, inputs: dict, execute):
    """Persist an individual call; return cached output on a safe retry.

    Only JSON-serializable data can be saved. A fresh or human-authorized call
    is marked in_flight and committed before invoking the remote service.
    """
    request_hash = fingerprint(inputs)
    step = get_checkpoint(db, campaign_id, step_key)
    if step is not None:
        if step.input_hash != request_hash:
            raise CheckpointNeedsReview(
                f"{step_key}: request changed since checkpoint; create a new campaign "
                "or deliberately reset this step after inspecting the old result"
            )
        if step.status == "complete":
            log.info("Checkpoint hit campaign=%s step=%s", campaign_id, step_key)
            return step.result["value"]
        if step.status != "retry_authorized":
            raise CheckpointNeedsReview(
                f"{step_key}: prior call is {step.status}; it may have been billed. "
                "Inspect and explicitly authorize a retry in the campaign UI."
            )
    else:
        step = StepCheckpoint(campaign_id=campaign_id, step_key=step_key,
                              input_hash=request_hash)
        db.add(step)
    step.status = "in_flight"
    step.attempts = (step.attempts or 0) + 1
    step.last_error = ""
    try:
        db.commit()  # write ahead, before any potentially metered network request
    except IntegrityError as exc:
        db.rollback()
        raise CheckpointNeedsReview(
            f"{step_key}: another worker has claimed this step"
        ) from exc
    try:
        value = execute()
        # Verify serializability before marking complete.
        json.dumps(value)
    except Exception as exc:
        db.rollback()
        step = get_checkpoint(db, campaign_id, step_key)
        if step:
            step.status = "uncertain"
            step.last_error = type(exc).__name__  # no provider response bodies or secrets
            db.commit()
        raise

    step = get_checkpoint(db, campaign_id, step_key)
    step.result = {"value": value}
    step.status = "complete"
    db.commit()  # atomic persistence of result + completion state
    return value
