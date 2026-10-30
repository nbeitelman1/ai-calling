"""Production configuration and approval gates for Muse's Telnyx adapter.

This module is Muse's own implementation. It is not the reference test
pack, and it carries no live credentials: credential *references* (env-var
names / secure-provisioning refs) are the most this layer ever holds.
Values are supplied only through supported secure provisioning at deploy
time, never in chat, files, or code.

Gates enforced here, upstream of any carrier request:
- live_enabled is False by default. A fake transport is the only
  transport that runs without an explicit live enablement.
- Every dial needs a separate Approval object: job id, exact destination,
  max minutes, and an estimated cost. Approval is not inferred from a
  job, an attempt id, or a previous call.
- Budget is reserved at prepare time, not spent at dial time. The
  monthly cap ($20.00) and per-call soft cap ($5.00) come from the
  Oct 27 scaffold and are only changed by Nathan, explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Approval:
    """One explicit, per-job approval. Created by the operator (Nathan)
    approving an exact job; the adapter only checks it, never invents it."""

    job_id: str
    destination: str
    max_minutes: int
    estimated_cost_cents: int
    approver_ref: str = "nathan"  # who approved, as a reference, not a secret


@dataclass
class AdapterConfig:
    live_enabled: bool = False
    max_concurrent_calls: int = 1
    # Credential references only -- never values.
    api_credential_ref: str | None = None
    webhook_public_key_ref: str | None = None
    monthly_cap_cents: int = 2000  # $20.00
    per_call_cap_cents: int = 500  # $5.00
    authorized_destinations: tuple[str, ...] = ()
    # Conservative local policy, not a provider guarantee: a persisted
    # hangup command may be re-sent only inside this window. After it,
    # reconciliation is required -- no new command is invented.
    hangup_reuse_window_seconds: int = 60
    webhook_body_cap_bytes: int = 65536
    webhook_freshness_seconds: int = 300
    # Telnyx Voice API v2, pinned for this implementation.
    api_base_path: str = "/v2/calls"
    api_version: str = "v2"
    # SDK pin is for signature-parity reference only; this adapter does
    # not import the Telnyx SDK. See STATUS.md.
    sdk_reference_commit: str = "269609c283e4ecd9774127a1686c41f695654708"
    sdk_reference_file_sha256: str = (
        "2c5135b23b772c86af79c9ab621f17b67f55228a40e33bc520db2c2c2d5e4bed"
    )
    cryptography_version_pin: str = "46.0.6"
