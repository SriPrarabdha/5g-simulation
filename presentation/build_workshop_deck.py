#!/usr/bin/env python3
"""Build the evidence-scoped 14-slide, 90-minute C-DOT workshop deck."""
from __future__ import annotations
from pathlib import Path
import build_deck as b

ROOT=Path(__file__).resolve().parents[1]

def label(deck,page,text,dark=False): deck.text(page,b.mm(10),b.mm(31),b.mm(240),b.mm(5),text,8,b.CYAN if dark else b.TEAL,bold=True,font=b.MONO,spacing=1.2)
def card(deck,page,x,y,w,h,head,body,color=b.TEAL,dark=False):
    deck.card(page,b.mm(x),b.mm(y),b.mm(w),b.mm(h),fill=0x193541 if dark else b.WHITE,line=0x3D5B67 if dark else b.LINE,accent=color)
    deck.text(page,b.mm(x+6),b.mm(y+7),b.mm(w-12),b.mm(7),head,11,color,bold=True,font=b.MONO)
    deck.text(page,b.mm(x+6),b.mm(y+20),b.mm(w-12),b.mm(h-25),body,9,b.WHITE if dark else b.INK,bold=False)
def scope(deck,page,text,dark=False):
    deck.card(page,b.mm(10),b.mm(39),b.mm(316),b.mm(13),fill=0x203D48 if dark else b.PALE_AMBER,line=None,accent=b.AMBER)
    deck.text(page,b.mm(17),b.mm(43),b.mm(302),b.mm(5),f"EVIDENCE SCOPE · {text}",8,b.AMBER,bold=True,font=b.MONO)
def standard(deck,index,title,section,cards,footer,dark=False,scope_text=None):
    page=deck.new_slide(title,section,dark=dark); label(deck,page,f"{index:02d} · 90-MINUTE WORKSHOP",dark)
    if scope_text: scope(deck,page,scope_text,dark)
    y=58 if scope_text else 48; width=(316-9*(len(cards)-1))/len(cards)
    for i,(head,body,color) in enumerate(cards): card(deck,page,10+i*(width+9),y,width,105 if scope_text else 116,head,body,color,dark)
    deck.footer(page,footer,dark=dark)

def build(deck:b.Deck):
    page=deck.new_slide("","",dark=True)
    deck.text(page,b.mm(10),b.mm(10),b.mm(280),b.mm(6),"C-DOT · 5G DIGITAL TWIN · 90-MINUTE CONTROL-ROOM WORKSHOP",9,b.CYAN,bold=True,font=b.MONO)
    deck.text(page,b.mm(10),b.mm(38),b.mm(185),b.mm(27),"You are the\nnetwork controller.",30,b.WHITE,bold=True)
    deck.text(page,b.mm(10),b.mm(76),b.mm(185),b.mm(15),"A synthetic stadium surge is approaching. Vote before the result is revealed.",13,0xC5D9DF)
    for i,(letter,name,color) in enumerate((("A","Keep Static",b.TEAL),("B","React after overload",b.AMBER),("C","Steer predictively",b.PURPLE))):
        card(deck,page,10+i*105,132,97,29,letter,name,color,True)
    deck.text(page,b.mm(215),b.mm(49),b.mm(104),b.mm(52),"JOIN JUPYTERHUB\n\nOpen your assigned notebook.\nRun Stage 01 preflight.\n\nReveal at minute 72.",13,b.WHITE,bold=True)
    deck.footer(page,"Synthetic simulation · no live C-DOT traffic · no SMF actuation",dark=True)

    standard(deck,2,"What this digital twin represents","Scope",[
      ("REPRESENTS","Synthetic traffic, topology, capacity, telemetry, forecast uncertainty, optimization, policy gates and later evidence.",b.TEAL),
      ("DOES NOT REPRESENT","Live C-DOT traffic, measured geography, autonomous production control, or established-session migration.",b.RED),
      ("CONTROL SCOPE","Recommendations change only the placement of sessions that arrive after an accepted policy epoch.",b.PURPLE)],"Minute 6–14 · participant preflight")
    standard(deck,3,"Implemented here; external integration remains gated","Readiness boundary",[
      ("IMPLEMENTED","24-UPF/96-group synthetic model · deterministic simulator · HiGHS · policy gate · PBS campaign jobs · dashboard · replay.",b.GREEN),
      ("CLUSTER-PREFLIGHT","SCIP, PySCIPOpt, ParaSCIP/UG, MPI/PALS and PBS launch syntax must pass on the actual cluster ≥7 days before delivery.",b.AMBER),
      ("EXTERNAL-PENDING","C-DOT telemetry mapping · calibrated envelopes · security · SMF/EMS future-session hook · publication/rollback runbook.",b.RED)],"No silent solver substitution · ParaSCIP readiness failure blocks the live track")
    standard(deck,4,"One causal control loop—from pressure to later evidence","End-to-end loop",[
      ("OBSERVE → FORECAST","Offered demand and quality flags enter a closed-history p50/p90 forecast. Target data never enters features.",b.TEAL),
      ("OPTIMIZE → CERTIFY","Capacity, eligibility, health, locality and churn gates evaluate a candidate. Unsafe means last-safe/static.",b.PURPLE),
      ("PLACE → MEASURE","Only future sessions use new weights. Carried traffic, overload and loss become later evidence—not rewritten history.",b.GREEN)],"Causal markers are carried into twin-replay/1.0")
    standard(deck,5,"A synthetic national topology creates repeatable stress","Topology + traffic",[
      ("24 UPFs","Heterogeneous UL/DL/session capacity, safe envelopes, health and failure-domain events.",b.TEAL),
      ("96 GROUPS","Zone × DNN × S-NSSAI demand classes with eligibility and deterministic offered-load generation.",b.PURPLE),
      ("TRAFFIC SEMANTICS","Offered demand is attempted load. Carried is an outcome. Their constrained gap is loss/overload.",b.AMBER)],"Synthetic spatial layout · not C-DOT geography")
    standard(deck,6,"The 160-node cluster buys breadth—not one giant simulation","PBS architecture",[
      ("RIGHT UNIT","One scenario × controller × seed is one bounded one-node, one-CPU PBS job.",b.GREEN),
      ("CAMPAIGN BREADTH","Independent jobs explore seeds, events, controllers and parameter regimes concurrently across the cluster.",b.TEAL),
      ("ISOLATION","$USER + PBS job ID + seed identify private outputs. No shared writable participant campaigns.",b.AMBER)],"Minute 14–22 · 24 UPFs · 96 groups · arrays are a scenario factory")
    standard(deck,7,"The teaching LP turns demand into safe routing weights","Continuous allocation",[
      ("VARIABLES","xᵤ = new-session Mbps assigned to eligible UPF u · sᵤ = explicit overload slack.",b.PURPLE),
      ("CONSTRAINTS","Σxᵤ = demand · xᵤ ≤ residual capacityᵤ + sᵤ · xᵤ = 0 when ineligible.",b.TEAL),
      ("OBJECTIVE + OUTPUT","Minimize latency-weighted allocation + severe slack penalty. Normalize wᵤ = xᵤ / demand.",b.GREEN)],"Minute 22–32 · edit demand/capacity · solve with HiGHS")
    standard(deck,8,"SCIP validates the LP; ParaSCIP demonstrates the larger MIP","Solver + PBS model",[
      ("INDIVIDUAL","Same LP in HiGHS and SCIP; feasible objectives must agree within tolerance. One-node SCIP job per participant.",b.TEAL),
      ("WHY A MIP","24-UPF/96-group assignment uses binary group→UPF and UPF-activation variables. The tiny LP cannot teach distributed search.",b.PURPLE),
      ("PRESENTER ONLY","One frozen-seed, two-node ParaSCIP/UG job in a reservation. Participants watch status/result; they never submit ParaSCIP.",b.RED)],"Minute 32–52 · strict 4 GB / 5–10 minute ceilings")
    standard(deck,9,"Offered demand and carried throughput answer different questions","Traffic accounting",[
      ("OFFERED","What active and newly arriving sessions attempted to transmit—independent of whether capacity served it.",b.PURPLE),
      ("CARRIED","What crossed the modeled service path after capacity, queueing, rejection and drops.",b.TEAL),
      ("DO NOT CONFUSE","A saturated carried-throughput series can hide true demand. Forecast from offered demand with quality flags.",b.RED)],"Minute 52–62 · safety drill while PBS jobs run")
    standard(deck,10,"Forecast uncertainty changes what ‘safe’ means","Causality + p50/p90",[
      ("CAUSAL INPUT","Only closed windows at or before issue time. Scheduled events enter only when known by then.",b.TEAL),
      ("p50 vs p90","p50 is central expectation; p90 is a conservative planning quantile—not a guarantee against surprise flash crowds.",b.PURPLE),
      ("MARK EVERY FRAME","Source window, target window, policy apply step and anchored-session marker preserve replay semantics.",b.GREEN)],"Forecasts are synthetic model outputs, not live traffic predictions")
    standard(deck,11,"Certification makes rejection visible and fail-safe","Policy gate",[
      ("CHECK","Causality · finite normalized weights · eligibility · health · locality · UL/DL/session headroom · churn.",b.TEAL),
      ("REJECT","Unknown destination, non-normalized weight, unsafe projection or migration request fails certification.",b.RED),
      ("FALL BACK","Retain last-safe/static policy with reason, status, audit and expiry. Never publish slack or silently switch solvers.",b.GREEN)],"Participant notebooks have neither credentials nor publication access")
    standard(deck,12,"The guided three-UPF story is positive within its matched scope","Positive guided evidence",[
      ("+10.52%","Matched 30-pair guided result in the frozen three-UPF showcase evidence. Present as a scoped modeled improvement.",b.GREEN),
      ("WHAT CHANGED","Accepted weights changed future-session placement; established sessions stayed anchored.",b.PURPLE),
      ("WHAT IT DOES NOT PROVE","No live-network benefit, no universal overload prevention, no production recommendation.",b.RED)],"demo_api/data/cohort_mpc_full_campaign_evidence_v1.json",scope_text="Frozen guided three-UPF showcase · 30 matched pairs · positive result")
    standard(deck,13,"The later national result does not promote MPC","National control science",[
      ("LATER EVIDENCE","Broader national-scale campaign evaluates the controller under more regimes and guardrails than the guided story.",b.TEAL),
      ("DECISION","MPC fails promotion. Static remains the production-safe controller conclusion.",b.RED),
      ("HONEST HANDOFF","The older +10.52% remains useful teaching evidence, but is not the latest production recommendation.",b.AMBER)],"configs/control_science_v1.json · promotion conclusion is authoritative",scope_text="Later national-scale control-science campaign · production decision")
    standard(deck,14,"From dashboard and 3D replay to a bounded advisory pilot","Experience + close",[
      ("EXPERIENCE","Presenter dashboard streams the guided story; participant iframe replays completed Parquet in the full Three.js world.",b.PURPLE),
      ("OPERATIONAL GATES","35-user rehearsal · reserved short queue · offline assets · WebGL2 · storage pressure · 90-minute run + 5-minute margin.",b.AMBER),
      ("C-DOT PATH","Map telemetry → calibrate envelopes → shadow/advisory replay → secure audited future-session pilot → rollback exercise.",b.GREEN)],"Minute 87–90 · export WorkshopReport · close the advisory-pilot sentence",dark=True)

def main():
    b.PPTX=b.OUT/"CDOT_5G_Digital_Twin_Workshop_14_Slides.pptx"; b.PDF=b.OUT/"CDOT_5G_Digital_Twin_Workshop_14_Slides.pdf"
    deck=b.Deck(b.connect()); build(deck); deck.save(); print(b.PPTX); print(b.PDF); return 0
if __name__=="__main__": raise SystemExit(main())
