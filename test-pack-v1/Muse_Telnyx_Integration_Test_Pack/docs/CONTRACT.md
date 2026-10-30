# Contract and scope

## Source-backed Telnyx Voice v2 contract

- Create: POST /v2/calls; required body fields connection_id, from, to. Request uses JSON `from`; the Python SDK calls its argument `from_`.
- Correlation: client_state is Base64 text. Use non-sensitive attempt metadata only; it is not authentication.
- Result identities: data.call_control_id, call_leg_id, call_session_id. Commands address call_control_id; session IDs may span multiple legs.
- Hangup: POST /v2/calls/{call_control_id}/actions/hangup. Reuse the same persisted command_id for the same intent and leg. The documented duplicate scope is the same command_id and call_control_id; do not infer global create-call idempotence.
- Webhooks: data.record_type, id, event_type, occurred_at, payload; meta carries delivery attempt information. Deduplicate the canonical DATA, not the raw envelope, because delivery metadata can change.
- Authentication: verify Telnyx-Signature-Ed25519 and Telnyx-Timestamp over timestamp + pipe + exact raw body. Pinned SDK helper uses Base64 32-byte public keys, 64-byte signatures, and a 300-second freshness window.

## Deliberately narrower reference policies

One E.164 destination, no SIP/arrays; 64-KiB webhook body cap; no duplicate JSON members or duplicate signing headers; non-sensitive UUID attempt metadata. These limits are application choices, not universal Telnyx limits.

The parser stores occurred_at. For its three supported lifecycle events it uses a monotonic initiated/answered/hangup rank; hangup is absorbing. It does NOT implement reversible hold/unhold ordering or arbitrary event state machines. Other valid events are stored without executing behavior.

The local 60-second hangup request-reuse limit is conservative test policy, not a measured provider guarantee. No retries are sent by this pack. The live retry policy must be checked against current provider documentation and reconciled call state.
