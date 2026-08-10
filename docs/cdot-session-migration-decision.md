# C-DOT established-session migration decision

Status: **external confirmation required; simulator remains new-session-only**

Last reviewed: 2026-08-06

## What is confirmed

C-DOT's public 5G-SA product brief identifies a 3GPP Release-16 standalone core
with SMF, UPF, control/user-plane separation, MEC, and network slicing. It does
not claim a northbound interface for arbitrary load-driven relocation of
already-established PDU sessions:

- [C-DOT Bharat 5G standalone product brief](https://www.cdot.in/cdotweb/assets/docs/products/wireless/bharat5G-standalone.pdf)

A security-assurance requirement hosted by C-DOT describes the general 5GC
SMF role as creating a PDU session or updating the UPF for an existing PDU
session. That establishes that existing-session UPF updates exist in the wider
standards architecture; it does not prove that the target C-DOT deployment
implements, exposes, or permits arbitrary traffic-engineering migration:

- [Indian Telecom Security Assurance Requirements for 5G core](https://nsso.cdot.in/public/itsar/ITSAR111092408.pdf)

3GPP material also ties some UPF relocation procedures to topology, mobility,
I-SMF/I-UPF, and SSC-mode behavior. Standards possibility must not be presented
as a deployed operational capability.

## Current implemented boundary

The simulator and proposed SMF hook control **new-session placement only**.
They do not move an active PDU session, change its anchor, preserve its address
across relocation, or model the signaling/interruption cost of doing so.

Every new simulation summary now records:

```json
{
  "control_scope": "new_session_placement_only",
  "session_migration_supported": false
}
```

The one-day pilot found that only 3.36% of UL and 2.71% of DL offered bytes were
new-session traffic at a decision instant. Most outage overload was attached
to surviving sessions and therefore outside the current actuator's immediate
control.

## Questions C-DOT must answer

1. Can the deployed SMF change the PSA UPF or insert/change an I-UPF for an
   already-established PDU session for load-balancing reasons, not only during
   mobility or recovery?
2. Which SSC modes, PDU session types, DNNs, slices, and UE capabilities permit
   that operation, and is IP address/session continuity preserved?
3. What supported northbound or internal interface triggers the operation?
   Provide the exact API/PFCP/Nsmf procedure, authorization model, idempotency
   key, completion indication, and rollback behavior.
4. What interruption, packet loss, signaling rate, cooldown, and simultaneous
   migration limits apply?
5. Can the testbed expose the current UPF anchor per PDU session and an audit of
   migration success/failure so recommendations can be evaluated causally?
6. Is this capability supported in the exact release and build planned for the
   demonstration, rather than only allowed by a generic 3GPP procedure?

## Decision rule

Until C-DOT supplies affirmative build-specific answers and a safe test
interface, all optimizer evidence and demo language must remain
new-session-only. If migration is confirmed, implement it as a separately
versioned actuator with migration budgets, hold times, continuity constraints,
failure rollback, and an independently reported cost. Do not silently add it
to the present controller or compare it with old results under the same name.
