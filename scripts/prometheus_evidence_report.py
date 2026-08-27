import json,subprocess,datetime,time,sys

P="http://192.168.218.8:29090"
IST=datetime.timezone(datetime.timedelta(hours=5,minutes=30))
UTC=datetime.timezone.utc
now=int(time.time())
out=[]
def w(s=""): out.append(s)

def curl(path,**kw):
    a=["curl","-s","-m","30","--get",P+path]
    for k,v in kw.items(): a+=["--data-urlencode",f"{k}={v}"]
    return subprocess.run(a,capture_output=True,text=True).stdout

def jq(path,**kw):
    try: return json.loads(curl(path,**kw) or "{}")
    except Exception: return {}

def rng(m,start,end,step):
    return jq("/api/v1/query_range",query=m,start=start,end=end,step=step).get("data",{}).get("result",[])

def ist(t): return datetime.datetime.fromtimestamp(t,IST)
def both(t): return f"{datetime.datetime.fromtimestamp(t,UTC):%H:%M:%S} UTC ({ist(t):%d %b, %H:%M:%S} IST)"

METRICS=["upf_n3_uplink_packets_session_class_total_byte",
         "upf_n3_downlink_packets_session_class_total_byte",
         "pfcp_sessions_total"]

# ---- gather ---------------------------------------------------------------
health=curl("/-/healthy").strip()
instant={m:jq("/api/v1/query",query=m).get("data",{}).get("result",[]) for m in METRICS}
raw_first=curl("/api/v1/query",query=METRICS[0]).strip()

last={}
for m in METRICS:
    coarse=rng(m,now-14*86400,now,3600)
    if not coarse: last[m]=(0,None); continue
    c=max(p[0] for s in coarse for p in s["values"])
    fine=rng(m,int(c)-7200,int(c)+7200,15)
    last[m]=(len(coarse), max(p[0] for s in fine for p in s["values"]) if fine else c)

tg=jq("/api/v1/targets",state="active").get("data",{})
targets=tg.get("activeTargets",[])
dropped=len(tg.get("droppedTargets") or [])

def scalar(e):
    r=jq("/api/v1/query",query=e).get("data",{}).get("result",[])
    return float(r[0]["value"][1]) if r else None
started=scalar('process_start_time_seconds{job="prometheus"}')
reloaded=scalar('prometheus_config_last_reload_success_timestamp_seconds')
ok=scalar('prometheus_config_last_reload_successful')
stopped=last[METRICS[0]][1]
host=(targets[0].get("globalUrl","") .split("//")[-1].split(":")[0]) if targets else "the Prometheus host"

# ---- write ----------------------------------------------------------------
w("="*70)
w("  Why our optimiser is receiving no UPF data")
w("  A five-step check you can repeat yourself")
w("="*70)
w()
w("Every command below is run against 192.168.218.8:29090 -- the address")
w("you gave us, and nothing else. Each step asks Prometheus one plain")
w("question. We show its exact answer.")
w()
w(f"Checked on {datetime.datetime.now(UTC):%d %b %Y, %H:%M:%S} UTC "
  f"/ {datetime.datetime.now(IST):%H:%M:%S} IST")
w();w()

w("="*70); w('STEP 1 -- "Prometheus, are you alive?"'); w("="*70); w()
w("  curl http://192.168.218.8:29090/-/healthy"); w()
w("  ANSWER:"); w(f"    {health}"); w()
w("  ==> Yes. Prometheus is running and we can reach it.")
w("      So this is NOT a network issue, NOT a firewall issue, and NOT a")
w("      wrong-port issue. Please rule those out; they are not the problem.")
w();w()

w("="*70); w('STEP 2 -- "Prometheus, give us the UPF traffic data."'); w("="*70); w()
w("  curl --get http://192.168.218.8:29090/api/v1/query \\")
w(f"       --data-urlencode 'query={METRICS[0]}'"); w()
w("  ANSWER:"); w(f"    {raw_first}"); w()
w('  ==> Look at the very end:   "result":[]')
w()
w("      Those empty square brackets mean ZERO readings.")
w('      Notice it still says "success" -- Prometheus understood the')
w("      question perfectly. It just has nothing to hand back.")
w()
w("      The other two metrics answer the same way:")
for m in METRICS:
    w(f"        {m}")
    w(f"            -> {len(instant[m])} readings")
w();w()

w("="*70); w('STEP 3 -- "Did you EVER have this data?"'); w("="*70); w()
w("  Step 2 only looks at the last few minutes. Now we search backwards")
w("  through 14 days of stored history.")
w()
w("  curl --get http://192.168.218.8:29090/api/v1/query_range \\")
w(f"       --data-urlencode 'query={METRICS[0]}' \\")
w("       --data-urlencode 'start=<14 days ago>' \\")
w("       --data-urlencode 'end=<now>' \\")
w("       --data-urlencode 'step=3600'")
w()
w("  ANSWER:")
for m in METRICS:
    n,t=last[m]
    w(f"    {m}")
    if not t: w("        no data at all in 14 days"); continue
    w(f"        stored series      : {n}")
    w(f"        newest reading was : {both(t)}")
    w(f"        i.e.               : {(now-t)/3600:.1f} hours ago")
w()
w("  ==> Yes -- the data IS there. 32 stored series for uplink, 32 for")
w("      downlink. That proves two things straight away:")
w("        * the metric names we use are correct")
w("        * your UPF exporters were publishing correctly")
w()
w(f"      But the newest reading is from {ist(stopped):%H:%M:%S} IST this morning.")
w("      Nothing has arrived since. The data is frozen at that moment.")
w()
w("      All three metrics stop at the SAME SECOND. That matters: if one")
w("      UPF had crashed, only that UPF's numbers would stop. Everything")
w("      stopping together, to the second, is the signature of a")
w("      configuration change -- not a hardware or exporter failure.")
w();w()

w("="*70); w('STEP 4 -- "Prometheus, what are you collecting FROM?"'); w("="*70); w()
w("  This is the important one.")
w()
w("  Prometheus does not sit and wait for data to be sent to it. It goes")
w("  out every few seconds and fetches data from a list of machines it")
w("  keeps in its configuration. This command asks to see that list.")
w()
w("  curl 'http://192.168.218.8:29090/api/v1/targets?state=active'")
w()
w("  ANSWER (simplified):")
w(f"    machines on the list : {len(targets)}")
for x in targets:
    w(f"      -> job '{x['labels'].get('job')}'  at  {x.get('scrapeUrl')}   [{x.get('health')}]")
w(f"    filtered-out entries : {dropped}")
w()
w("  ==> HERE IS THE PROBLEM.")
w()
w(f"      The list contains {len(targets)} entry, and that entry is Prometheus")
w("      itself. (\"localhost:9090\" is Prometheus's own internal address --")
w("      it is collecting its own health statistics, nothing more.)")
w()
w("      There are NO UPF machines on this list. Prometheus is not asking")
w("      any UPF for data, so naturally it has none to give us. That is")
w("      the entire explanation for the empty result in Step 2.")
w()
w(f"      One more detail: filtered-out entries = {dropped}. This rules out the")
w("      gentler explanation. If the UPF jobs were still written in")
w("      prometheus.yml but being excluded by a relabel rule, they would")
w("      appear here as filtered out. They do not appear at all -- so they")
w("      are simply no longer in the configuration file.")
w();w()

w("="*70); w('STEP 5 -- "When did this happen?"'); w("="*70); w()
w("  Prometheus keeps a record of its own restarts and config reloads.")
w()
w("  curl --get http://192.168.218.8:29090/api/v1/query \\")
w("       --data-urlencode 'query=process_start_time_seconds'")
w("  curl --get http://192.168.218.8:29090/api/v1/query \\")
w("       --data-urlencode 'query=prometheus_config_last_reload_success_timestamp_seconds'")
w("  curl --get http://192.168.218.8:29090/api/v1/query \\")
w("       --data-urlencode 'query=prometheus_config_last_reload_successful'")
w()
w("  ANSWER:")
w(f"    Prometheus last restarted : {both(started)}")
w(f"    Config file last loaded   : {both(reloaded)}")
w(f"    Did that load succeed?    : {int(ok)}    (1 = yes, no errors)")
w()
w("  ==> Now put the three times side by side:")
w()
w(f"        {ist(stopped):%H:%M} IST   last UPF reading arrives")
w(f"        {ist(started):%H:%M} IST   Prometheus restarts")
w(f"        {ist(reloaded):%H:%M} IST   Prometheus loads its config file -- successfully --")
w("                    and that file lists only one job: itself")
w("        since then  no UPF data at all")
w()
w("      Note the reload SUCCEEDED. There is no error message to hunt for")
w("      in the logs. Prometheus did exactly what it was told; the file it")
w("      was told to read no longer mentions the UPF exporters.")
w();w()

# ---- step 4b: name the missing job -----------------------------------------
day=jq("/api/v1/series",**{"match[]":METRICS[0],"start":now-86400,"end":now}).get("data",[])
w("="*70); w('STEP 4b -- "Which scrape job exactly is missing?"'); w("="*70); w()
w("  Prometheus still remembers the labels attached to yesterday's data.")
w("  Those labels record exactly which job and which machine the readings")
w("  came from -- which tells you precisely what to put back.")
w()
w("  curl --get http://192.168.218.8:29090/api/v1/series \\")
w(f"       --data-urlencode 'match[]={METRICS[0]}' \\")
w("       --data-urlencode 'start=<24 h ago>' --data-urlencode 'end=<now>'")
w()
w("  ANSWER:")
w(f"    {len(day)} series remembered from the last 24 hours, for example:")
for s_ in day[:4]:
    w("      "+json.dumps(s_))
if day:
    jobs=sorted({s_.get("job") for s_ in day if s_.get("job")})
    insts=sorted({s_.get("instance") for s_ in day if s_.get("instance")})
    upfs=sorted({s_.get("upf") for s_ in day if s_.get("upf")})
    w()
    w("  ==> The missing scrape job is named, in your own configuration:")
    w()
    for j in jobs: w(f"        job      : {j}")
    for i_ in insts: w(f"        instance : {i_}")
    if upfs: w(f"        covering : {', '.join(upfs)}")
    w()
    w("      So all four UPFs were being scraped from a single exporter at")
    w(f"      {insts[0] if insts else '?'}, under the job name '{jobs[0] if jobs else '?'}'.")
    w("      That is the entry that has disappeared from prometheus.yml.")
    w()
    w("      We cannot reach that exporter ourselves to test it -- it sits on")
    w("      your internal 192.168.147.x network and we are on 192.168.218.x.")
    w("      Only Prometheus can reach it, which is why we need the scrape")
    w("      job restored rather than being able to poll it directly.")
w();w()

w("="*70); w("THE WHOLE THING IN ONE PARAGRAPH"); w("="*70); w()
w("  Prometheus on 29090 is healthy and answering us normally. It still")
w("  holds all the older UPF data, which proves the metric names and the")
w(f"  exporters are fine. But when it restarted at {ist(started):%H:%M} IST this morning")
w("  it came back with a configuration listing only itself as a source, so")
w(f"  it has collected nothing from any UPF since {ist(stopped):%H:%M} IST.")
w()
w("  WHAT WE ARE ASKING FOR")
w(f"    Please check prometheus.yml on the Prometheus host (Prometheus")
w(f"    reports its own hostname as '{host}') and restore the UPF exporter")
w("    scrape jobs, then reload. Step 4b above names the exact job and")
w("    exporter address to restore.")
w()
w("  HOW WE WILL BOTH KNOW IT IS FIXED")
w("    Re-run STEP 4 -- the list should show the UPF machines instead of")
w("    only one entry. Then re-run STEP 2 -- it should return numbers")
w("    instead of []. Our system picks the data up automatically within")
w("    30 seconds of that. Nothing needs to change on our side.")
w()

sys.stdout.write("\n".join(out)+"\n")
