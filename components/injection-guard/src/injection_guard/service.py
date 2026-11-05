"""HTTP front for services written in other languages or processes. Never logs the scanned text."""

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from injection_guard.models import ScanResult, Verdict
from injection_guard.outbound import OutboundResult, check_outbound
from injection_guard.scanner import MAX_CHARS, scan
from injection_guard.spotlight import SYSTEM_GUIDANCE, wrap_untrusted

log = logging.getLogger("injection_guard")
app = FastAPI(title="sis injection-guard")


class ScanRequest(BaseModel):
    text: str = Field(max_length=MAX_CHARS)
    source: str = "unknown"
    ref: str = ""
    wrap: bool = False
    datamark: bool = False


class ScanResponse(ScanResult):
    wrapped: str | None = None  # present only when wrap=true and the verdict is not block


class OutboundRequest(BaseModel):
    text: str = Field(max_length=MAX_CHARS)
    allowed_hosts: list[str] = []
    canaries: list[str] = []  # note: no `literals` field on purpose; real secrets must not cross the network


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/v1/guidance")
def guidance() -> dict[str, str]:
    return {"system_guidance": SYSTEM_GUIDANCE}


@app.post("/v1/scan", response_model=ScanResponse)
def post_scan(req: ScanRequest) -> ScanResponse:
    result = scan(req.text, req.source)
    log.info("scan source=%s verdict=%s score=%d rules=%s", req.source, result.verdict, result.score, [f.rule_id for f in result.findings])
    wrapped = wrap_untrusted(req.text, req.source, req.ref, datamark=req.datamark) if req.wrap and result.verdict is not Verdict.BLOCK else None
    return ScanResponse(**result.model_dump(), wrapped=wrapped)


@app.post("/v1/check-output", response_model=OutboundResult)
def post_check_output(req: OutboundRequest) -> OutboundResult:
    result = check_outbound(req.text, allowed_hosts={h.lower() for h in req.allowed_hosts}, canaries=req.canaries)
    log.info("outbound ok=%s rules=%s", result.ok, [f.rule_id for f in result.findings])
    return result


@app.exception_handler(ValueError)
def value_error(_, exc: ValueError):  # oversized input from scan()
    raise HTTPException(413, str(exc))
