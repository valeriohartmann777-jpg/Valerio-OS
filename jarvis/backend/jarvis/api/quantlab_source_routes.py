"""QuantLab Idea-to-Edge intake endpoints: text, links, file uploads, sources and evidence.

Uploads stream as the raw request body (``application/octet-stream``) with the
file name in ``X-Filename``; nothing is buffered in memory and the size is
capped while reading. No endpoint here fetches an arbitrary URL, spends money
or calls a model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from jarvis.quantlab.intake.detect import IntakeError
from jarvis.runtime import Runtime

router = APIRouter()


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(_runtime)]


def _error(exc: IntakeError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "message": exc.message})


class IntakeBody(BaseModel):
    text: str | None = Field(None, max_length=250_000)
    url: str | None = Field(None, max_length=2048)
    note: str | None = Field(None, max_length=4000)

    @model_validator(mode="after")
    def _one(self) -> IntakeBody:
        if bool(self.text and self.text.strip()) == bool(self.url and self.url.strip()):
            raise ValueError("send either text or a url")
        return self


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _header(request: Request, name: str, limit: int) -> str | None:
    raw = request.headers.get(name)
    if raw is None:
        return None
    value = unquote(raw)
    return value[:limit] if value.strip() else None


@router.get("/quantlab/sources")
async def sources(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.sources.sources()


@router.post("/quantlab/sources/intake")
async def intake(body: IntakeBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        if body.url:
            return await rt.sources.add_link(body.url, body.note)
        return await rt.sources.add_text(body.text or "", body.note)
    except IntakeError as exc:
        raise _error(exc) from exc


@router.post("/quantlab/sources/intake/file")
async def intake_file(request: Request, rt: RuntimeDep) -> dict[str, Any]:
    filename = _header(request, "x-filename", 255) or "upload"

    async def body() -> AsyncIterator[bytes]:
        async for chunk in request.stream():
            yield chunk

    try:
        return await rt.sources.receive_upload(
            body(),
            filename,
            note=_header(request, "x-note", 4000),
            language=_header(request, "x-language", 8),
            link_source_id=_header(request, "x-link-source", 40),
        )
    except IntakeError as exc:
        raise _error(exc) from exc


@router.get("/quantlab/sources/speech-model")
async def speech_model(rt: RuntimeDep) -> dict[str, Any]:
    return rt.sources.speech_model()


@router.post("/quantlab/sources/speech-model")
async def install_speech_model(rt: RuntimeDep) -> dict[str, Any]:
    return await rt.sources.install_speech_model()


@router.get("/quantlab/sources/audit")
async def sources_audit(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.sources.store.audit_log(None, 200)


@router.get("/quantlab/sources/{source_id}")
async def source(source_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.sources.source(source_id)
    except IntakeError as exc:
        raise _error(exc) from exc


@router.post("/quantlab/sources/{source_id}/extract")
async def extract(source_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.sources.extract(source_id)
    except IntakeError as exc:
        raise _error(exc) from exc


@router.post("/quantlab/sources/{source_id}/cancel")
async def cancel(source_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.sources.cancel(source_id)
    except IntakeError as exc:
        raise _error(exc) from exc


@router.post("/quantlab/sources/{source_id}/notes")
async def add_note(source_id: str, body: NoteBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.sources.add_note(source_id, body.text)
    except IntakeError as exc:
        raise _error(exc) from exc


@router.get("/quantlab/sources/{source_id}/frames/{frame_id}")
async def frame(source_id: str, frame_id: str, rt: RuntimeDep) -> FileResponse:
    try:
        path = await rt.sources.frame_file(source_id, frame_id)
    except IntakeError as exc:
        raise _error(exc) from exc
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private"})


@router.get("/quantlab/sources/{source_id}/media")
async def media(source_id: str, rt: RuntimeDep) -> FileResponse:
    try:
        path, mime = await rt.sources.media_file(source_id)
    except IntakeError as exc:
        raise _error(exc) from exc
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store"})


@router.delete("/quantlab/sources/{source_id}/media")
async def delete_media(source_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.sources.delete_media(source_id)
    except IntakeError as exc:
        raise _error(exc) from exc


@router.delete("/quantlab/sources/{source_id}")
async def delete_source(source_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        await rt.sources.delete(source_id)
    except IntakeError as exc:
        raise _error(exc) from exc
    return {"deleted": source_id}
