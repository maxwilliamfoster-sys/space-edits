"""Read-only view numbers per post and per format, from Buffer's sent posts. Writes library/analytics.json.
Nothing here posts or changes a post. Formats come from gate_log.json, joined to Buffer through posted.json."""
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIB = ROOT / "library"
OUT = LIB / "analytics.json"
MATURE_H = 72   # a TikTok's first push takes about 3 days, so younger posts are shown but never judged


def _age_h(sent_at, now):
    return (now - datetime.fromisoformat(sent_at.replace("Z", "+00:00"))).total_seconds() / 3600


def rows(sent, posted, gate_log, now=None):
    """One dict per sent post, newest first. A post this bot did not log has template None."""
    now = now or datetime.now(timezone.utc)
    by_post = {v["post"]: eid for eid, v in posted.items() if v.get("post")}
    template = {e["id"]: e.get("template") for e in gate_log}
    hook = {e["id"]: e.get("hook") for e in gate_log}
    out = []
    for p in sent:
        when = p.get("sentAt") or p.get("dueAt")
        if not when:
            continue
        m = p.get("metrics") or {}
        eid = by_post.get(p["id"])
        out.append(dict(id=eid, post=p["id"], sent=when, age_h=round(_age_h(when, now), 1), template=template.get(eid),
                        hook=hook.get(eid), views=m.get("views"), likes=m.get("likes"), comments=m.get("comments"),
                        shares=m.get("shares"), saves=m.get("saves"), avg_watch_s=m.get("averageTimeWatched"),
                        link=p.get("externalLink")))
    return out


def summary(table):
    """{template: n, median views, median avg watch} over posts past MATURE_H. Medians, never pooled totals."""
    groups = {}
    for r in table:
        if r["age_h"] >= MATURE_H and r["views"] is not None:
            groups.setdefault(r["template"] or "unlogged", []).append(r)
    out = {}
    for name, g in sorted(groups.items()):
        watch = [r["avg_watch_s"] for r in g if r["avg_watch_s"] is not None]
        out[name] = dict(n=len(g), median_views=statistics.median(r["views"] for r in g),
                         median_avg_watch_s=round(statistics.median(watch), 1) if watch else None)
    return out


def main():
    import buffer_client
    import publish
    org, chan = publish.tiktok_channel()
    sent = buffer_client.BufferClient().sent_posts(org, chan, first=100, max_pages=3)
    read = lambda name: json.loads((LIB / name).read_text(encoding="utf-8")) if (LIB / name).exists() else None
    table = rows(sent, read("posted.json") or {}, read("gate_log.json") or [])
    summ = summary(table)
    OUT.write_text(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(timespec="minutes"), summary=summ, posts=table), indent=1),
                   encoding="utf-8")
    print(f"{'sent':17} {'age_h':>6} {'views':>6} {'likes':>5} {'watch':>6}  template")
    for r in table:
        print(f"{r['sent'][:16]:17} {r['age_h']:6.0f} {str(r['views']):>6} {str(r['likes']):>5} {str(r['avg_watch_s']):>6}  {r['template']}")
    print(json.dumps(summ, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
