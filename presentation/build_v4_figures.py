"""Render the v4 optimizer figures in the Evidence Review deck's visual language.

Palette and furniture are sampled from the deck's existing figures so new
slides sit beside the old ones without a seam.
"""
from __future__ import annotations
import json, glob
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np

for p in glob.glob('/home/prarabdhas/.fonts/*.ttf'):
    try: fm.fontManager.addfont(p)
    except Exception: pass

INK="#111721"; BODY="#3C4653"; MUTED="#6C7683"; GRID="#EBEEF1"; RULE="#DEE3E8"
TEAL="#0F4C5C"; GREEN="#2E7D5B"; RED="#9C3B35"; AMBER="#B5761F"; SOFT="#ECF3F0"
MONO=["Cascadia Mono","DejaVu Sans Mono"]; SANS=["Open Sans","DejaVu Sans"]
OUT=Path("presentation/generated_assets/v4"); OUT.mkdir(parents=True, exist_ok=True)
D=json.load(open(OUT/"deck-data.json"))

plt.rcParams.update({
    "figure.facecolor":"white","axes.facecolor":"white","savefig.facecolor":"white",
    "font.family":SANS,"text.color":BODY,"axes.labelcolor":BODY,
    "xtick.color":MUTED,"ytick.color":MUTED,"axes.edgecolor":RULE,
    "axes.grid":True,"grid.color":GRID,"grid.linewidth":1.0,"axes.axisbelow":True,
    "xtick.bottom":False,"ytick.left":False,"savefig.bbox":"tight","savefig.pad_inches":0.06,
})
def frame(ax, xgrid=False):
    for s in ("top","right","left"): ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.grid(axis="x" if xgrid else "y")
    ax.grid(axis="y" if xgrid else "x", visible=False)
def mono(ax, size=11):
    for t in list(ax.get_xticklabels())+list(ax.get_yticklabels()):
        t.set_fontfamily(MONO); t.set_fontsize(size)
def save(fig, name):
    fig.savefig(OUT/name, dpi=300); plt.close(fig); print("  ", name)

# ---------- 1. cadence: identical benefit, very different churn ----------
def fig_cadence():
    c=D["cadence"]
    fig,(a1,a2)=plt.subplots(1,2,figsize=(11.6,3.05),gridspec_kw={"wspace":0.26})
    groups=["Pre-drain","Cohort MPC"]; x=np.arange(2); w=0.34
    for ax,key,ttl,fmt in ((a1,"macro","Overload removed (mean of all configurations)","{:.1f}%"),
                           (a2,"churn","Routing churn (L1 per group-hour)","{:.3f}")):
        if key=="macro":
            v10=[c["predrain_10"]["mean_macro"]*100,c["mpc_10"]["mean_macro"]*100]
            v2 =[c["predrain_2"]["mean_macro"]*100,c["mpc_2"]["mean_macro"]*100]
        else:
            v10=[c["predrain_10"]["mean_churn"],c["mpc_10"]["mean_churn"]]
            v2 =[c["predrain_2"]["mean_churn"],c["mpc_2"]["mean_churn"]]
        b1=ax.bar(x-w/2,v10,w,color=GREEN,zorder=3)
        b2=ax.bar(x+w/2,v2,w,color=AMBER,zorder=3)
        for bars,vals in ((b1,v10),(b2,v2)):
            for r,v in zip(bars,vals):
                ax.annotate(fmt.format(v),(r.get_x()+r.get_width()/2,r.get_height()),
                            ha="center",va="bottom",fontsize=10.5,fontweight="bold",
                            color=r.get_facecolor(),xytext=(0,3),textcoords="offset points",
                            fontfamily=MONO)
        ax.set_xticks(x); ax.set_xticklabels(groups,fontsize=11.5,color=BODY,fontfamily=SANS)
        ax.set_title(ttl,fontsize=11,color=MUTED,loc="left",pad=10,fontfamily=SANS)
        ax.set_ylim(0,max(v10+v2)*1.30); frame(ax); mono(ax,10)
        for t in ax.get_xticklabels(): t.set_fontfamily(SANS); t.set_fontsize(11.5); t.set_color(BODY)
    if D["cadence"]["predrain_10"]["mean_churn"]:
        r=c["predrain_2"]["mean_churn"]/c["predrain_10"]["mean_churn"]
        a2.annotate(f"{r:.1f}x the churn\nfor the same benefit",(0.5,c["predrain_2"]["mean_churn"]*0.62),
                    ha="left",va="center",fontsize=10,color=RED,fontfamily=SANS,fontweight="bold")
    h=[plt.Rectangle((0,0),1,1,color=GREEN),plt.Rectangle((0,0),1,1,color=AMBER)]
    fig.legend(h,["10-MINUTE CADENCE","2-MINUTE CADENCE"],loc="lower left",
               bbox_to_anchor=(0.005,-0.10),ncol=2,frameon=False,handlelength=0.9,
               handleheight=0.9,prop={"family":MONO,"size":9.5},labelcolor=MUTED)
    save(fig,"fig_cadence.png")

# ---------- 2. the whole campaign: gain against collateral harm ----------
def fig_frontier():
    fig,ax=plt.subplots(figsize=(8.6,3.05))
    s=D["scatter"]
    fail=[p for p in s if not p["pass"]]; ok=[p for p in s if p["pass"]]
    ax.scatter([p["harm"] for p in fail],[p["macro"]*100 for p in fail],s=26,
               facecolors="none",edgecolors="#C6CDD6",linewidths=1.0,zorder=3,label="DID NOT PASS")
    pre=[p for p in ok if p["ctl"]=="predrain"]; mp=[p for p in ok if p["ctl"]=="mpc"]
    ax.scatter([p["harm"] for p in pre],[p["macro"]*100 for p in pre],s=42,color=GREEN,zorder=4,label="PRE-DRAIN · PASSED")
    ax.scatter([p["harm"] for p in mp],[p["macro"]*100 for p in mp],s=42,color=TEAL,marker="D",zorder=4,label="COHORT MPC · PASSED")
    ax.axhline(10,color=RED,lw=1.2,ls=(0,(4,3)),zorder=2)
    ax.axvline(0.25,color=RED,lw=1.2,ls=(0,(4,3)),zorder=2)
    ax.annotate("10% minimum gain",(0.148,10.6),fontsize=9.5,color=RED,fontfamily=SANS)
    ax.annotate("harm limit 0.25",(0.256,15.5),fontsize=9.5,color=RED,fontfamily=SANS,
                rotation=90,va="bottom")
    best=max(ok,key=lambda p:p["macro"])
    ax.annotate(f"best passing: arm {best['i']}  +{best['macro']*100:.1f}%",
                (best["harm"],best["macro"]*100),xytext=(16,-3),textcoords="offset points",
                fontsize=10,color=INK,fontweight="bold",fontfamily=MONO)
    ax.set_xlabel("Overload-seconds added ÷ overload-seconds removed",fontsize=10.5,color=MUTED,labelpad=8)
    ax.set_ylabel("Overload removed (%)",fontsize=10.5,color=MUTED,labelpad=8)
    ax.set_xlim(-0.012,0.30); frame(ax); mono(ax,10)
    ax.legend(loc="upper left",bbox_to_anchor=(-0.02,-0.20),ncol=3,frameon=False,
              prop={"family":MONO,"size":9},labelcolor=MUTED,handletextpad=0.5,borderpad=0.2)
    save(fig,"fig_frontier.png")

# ---------- 3. screening vs fresh seeds ----------
def fig_shrink():
    """Paired bars, not a dumbbell: the whole point is that the gap is small,
    and two nearly-coincident dots cannot carry that."""
    fig,ax=plt.subplots(figsize=(8.1,3.0))
    cs=D["candidates"]; y=np.arange(len(cs))[::-1]; h=0.34
    disc=[c["disc_macro"]*100 for c in cs]; val=[c["val_macro"]*100 for c in cs]
    ax.barh(y+h/2,disc,h,color="#C6CDD6",zorder=3)
    for yi,c,v in zip(y,cs,val):
        ax.barh([yi-h/2],[v],h,color=GREEN if c["passed"] else RED,zorder=3)
    for yi,a,b,c in zip(y,disc,val,cs):
        ax.annotate(f"{a:.1f}",(a,yi+h/2),xytext=(6,-3.5),textcoords="offset points",
                    fontsize=10,color=MUTED,fontfamily=MONO)
        ax.annotate(f"{b:.1f}",(b,yi-h/2),xytext=(6,-3.5),textcoords="offset points",
                    fontsize=10.5,fontweight="bold",fontfamily=MONO,
                    color=GREEN if c["passed"] else RED)
    ax.axvline(10,color=RED,lw=1.2,ls=(0,(4,3)),zorder=4)
    ax.annotate("10% bar",(10.3,y[0]+h*1.5),fontsize=9.5,color=RED,fontfamily=SANS,va="center")
    ax.set_yticks(y)
    ax.set_yticklabels([f"arm {c['arm']}  {'pre-drain' if c['ctl']=='predrain' else 'cohort MPC'}"
                        for c in cs],fontsize=10.5,color=BODY)
    for t in ax.get_yticklabels(): t.set_fontfamily(MONO); t.set_fontsize(10.3)
    ax.set_ylim(y[-1]-0.75,y[0]+0.95); ax.set_xlim(0,32); ax.set_xlabel("Overload removed (%)",fontsize=10.5,color=MUTED,labelpad=8)
    frame(ax,xgrid=True); mono(ax,10)
    hs=[plt.Rectangle((0,0),1,1,color="#C6CDD6"),plt.Rectangle((0,0),1,1,color=GREEN),
        plt.Rectangle((0,0),1,1,color=RED)]
    ax.legend(hs,["SCREENING SEEDS","FRESH SEEDS · PASSED","FRESH SEEDS · BELOW BAR"],
              loc="lower right",frameon=False,prop={"family":MONO,"size":8.8},
              labelcolor=MUTED,handlelength=0.9,handleheight=0.9,borderpad=0.2)
    save(fig,"fig_shrink.png")

# ---------- 4. where it wins, ties, and can lose ----------
LABEL={"declared_maintenance":"Declared maintenance",
       "maintenance_then_stadium":"Maintenance, then surge",
       "maintenance_then_brownout":"Maintenance, then surprise",
       "surprise_brownout":"Surprise brownout",
       "surprise_demand":"Surprise demand"}
ORDER=["declared_maintenance","maintenance_then_stadium","maintenance_then_brownout","surprise_brownout"]
def fig_families():
    fig,axes=plt.subplots(1,2,figsize=(11.6,3.15),gridspec_kw={"wspace":0.06})
    # one shared scale: the panels are only comparable if a centimetre means
    # the same number of overload-seconds in both
    gmax=max(max(c["family"][f]["removed"] for f in ORDER)
             for c in D["candidates"] if c["arm"] in (62,142))/1e6
    gmin=max(max(c["family"][f]["added"] for f in ORDER)
             for c in D["candidates"] if c["arm"] in (62,142))/1e6
    for ax,arm in zip(axes,(62,142)):
        c=[x for x in D["candidates"] if x["arm"]==arm][0]
        ps=D["pairstats"][str(arm)]
        y=np.arange(len(ORDER))[::-1]; h=0.30
        rem=[c["family"][f]["removed"]/1e6 for f in ORDER]
        add=[c["family"][f]["added"]/1e6 for f in ORDER]
        ax.barh(y+h/2,rem,h,color=GREEN,zorder=3)
        ax.barh(y-h/2,[-a for a in add],h,color=RED,zorder=3)
        for yi,f,r,a in zip(y,ORDER,rem,add):
            w=ps.get(f,{}).get("worse",0); n=ps.get(f,{}).get("n",0)
            tag=f"{w}/{n} worse" if w else ("tie" if r<0.01 else "0 worse")
            ax.annotate(tag,(max(r,0.02),yi+h/2),xytext=(6,-3.5),textcoords="offset points",
                        fontsize=9.2,color=MUTED,fontfamily=MONO)
        ax.set_yticks(y)
        # categories are identical in both panels; label them once on the left
        ax.set_yticklabels([LABEL[f] for f in ORDER] if arm==62 else [""]*len(ORDER),
                           fontsize=10.5,color=BODY)
        for t in ax.get_yticklabels(): t.set_fontfamily(SANS); t.set_fontsize(10.3)
        name="Guarded pre-drain (arm 62)" if arm==62 else "Cohort MPC (arm 142)"
        ax.set_title(name,fontsize=11,color=INK,loc="left",pad=10,fontfamily=SANS,fontweight="bold")
        ax.axvline(0,color=RULE,lw=1.2,zorder=2)
        ax.set_xlim(-gmin*1.25,gmax*1.62); frame(ax,xgrid=True); mono(ax,9.5)
        ax.set_xlabel("Million overload-seconds",fontsize=10,color=MUTED,labelpad=6)
    h=[plt.Rectangle((0,0),1,1,color=GREEN),plt.Rectangle((0,0),1,1,color=RED)]
    fig.legend(h,["REMOVED","ADDED"],loc="lower left",bbox_to_anchor=(0.005,-0.09),
               ncol=2,frameon=False,handlelength=0.9,handleheight=0.9,
               prop={"family":MONO,"size":9.5},labelcolor=MUTED)
    save(fig,"fig_families.png")

# ---------- 5. value of advance notice ----------
def fig_notice():
    fig,ax=plt.subplots(figsize=(8.1,3.0))
    keys=["0.5h","1h","2h","3h","4h"]; xs=np.arange(len(keys)); w=0.36
    for off,arm,col,lab in ((-w/2,62,GREEN,"PRE-DRAIN (ARM 62)"),(w/2,142,TEAL,"COHORT MPC (ARM 142)")):
        c=[x for x in D["candidates"] if x["arm"]==arm][0]
        vals=[c["by_notice"].get(k,{}).get("aggregate_gain",0)*100 for k in keys]
        bars=ax.bar(xs+off,vals,w,color=col,zorder=3,label=lab)
        for r,v in zip(bars,vals):
            ax.annotate(f"{v:.1f}",(r.get_x()+r.get_width()/2,r.get_height()),ha="center",
                        va="bottom",fontsize=9.8,fontweight="bold",color=col,
                        xytext=(0,3),textcoords="offset points",fontfamily=MONO)
    ax.set_xticks(xs); ax.set_xticklabels(["30 min","1 hour","2 hours","3 hours","4 hours"],
                                          fontsize=11,color=BODY)
    for t in ax.get_xticklabels(): t.set_fontfamily(SANS); t.set_fontsize(11); t.set_color(BODY)
    ax.set_ylabel("Overload removed (%)",fontsize=10.5,color=MUTED,labelpad=8)
    ax.set_xlabel("Advance notice given for the maintenance window",fontsize=10.5,color=MUTED,labelpad=8)
    ax.set_ylim(0,50); frame(ax); mono(ax,10)
    ax.legend(loc="upper left",frameon=False,prop={"family":MONO,"size":9},labelcolor=MUTED,
              handlelength=0.9,handleheight=0.9,borderpad=0.2)
    save(fig,"fig_notice.png")

if __name__=="__main__":
    print("rendering v4 figures:")
    fig_cadence(); fig_frontier(); fig_shrink(); fig_families(); fig_notice()
