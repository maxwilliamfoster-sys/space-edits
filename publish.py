"""Get a finished edit onto TikTok with the PC off (same method as escape-cup).

host_video(): Buffer needs a public direct HTTPS URL it fetches at publish time, so the video is pushed to this repo's
orphan `media` branch (served by GitHub Pages at https://<owner>.github.io/<repo>/v/<file>.mp4); only the last KEEP are kept.
The branch is rewritten each time (it is a throwaway orphan), after the existing videos have been fetched and carried over.
schedule_tiktok(): Buffer GraphQL createPost for the mcfiddler.bob channel (BUFFER_CHANNEL_ID).
"""
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone

import requests

BUFFER_API = "https://api.buffer.com"
KEEP = 12
PAGES_WAIT_S = 600
PAGES_RETRIGGER_S = 300
WHO = ["-c", "user.name=github-actions[bot]", "-c", "user.email=github-actions[bot]@users.noreply.github.com"]


class PublishError(RuntimeError):
    pass


def _git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        err = r.stderr.strip()
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            err = err.replace(token, "***")
        raise PublishError(f"git {' '.join(args[:2])} failed: {err[:300]}")
    return r.stdout


def pages_base():
    owner, name = os.environ["GITHUB_REPOSITORY"].split("/")
    return f"https://{owner.lower()}.github.io/{name}"


def prune_plan(present, order, filename, protect=(), keep=None):
    """(files to delete, new upload order ending with filename). Oldest uploads go first; protected files are never deleted."""
    keep = keep or KEEP
    order = sorted(set(present) - set(order) - {filename}) + [f for f in order if f in present and f != filename]
    removable = [f for f in order if f not in set(protect)]
    delete = []
    while len(order) > keep - 1 and removable:
        f = removable.pop(0)
        order.remove(f)
        delete.append(f)
    return delete, order + [filename]


def host_video(path, filename, protect=()):
    """protect = file names still needed by scheduled posts (never pruned). Returns the live public URL."""
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        raise PublishError("GITHUB_TOKEN / GITHUB_REPOSITORY not set (hosting only runs in CI)")
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
    tmp = tempfile.mkdtemp()
    try:
        _git(["init", "-q"], tmp)
        _git(["checkout", "-q", "--orphan", "media"], tmp)
        vdir = os.path.join(tmp, "v")
        os.makedirs(vdir, exist_ok=True)
        exists = bool(_git(["ls-remote", "--heads", remote, "media"], tmp).strip())
        fetched = subprocess.run(["git", "fetch", "-q", "--depth", "1", remote, "media"], cwd=tmp, capture_output=True).returncode == 0
        if fetched:
            fetched = subprocess.run(["git", "checkout", "-q", "FETCH_HEAD", "--", "."], cwd=tmp, capture_output=True).returncode == 0
        if exists and not fetched:
            # the branch is replaced below, so never do that blind: it would delete videos a scheduled post still needs
            raise PublishError("media branch exists but could not be fetched - not replacing it")
        manifest = os.path.join(vdir, "order.txt")
        order = []
        if os.path.exists(manifest):
            with open(manifest, encoding="utf-8") as fh:
                order = [ln.strip() for ln in fh if ln.strip()]
        present = {f for f in os.listdir(vdir) if f.endswith(".mp4")}
        missing = sorted(set(protect) - present - {filename})
        if missing:
            raise PublishError(f"scheduled video {missing[0]} missing from media branch - not replacing it")
        delete, order = prune_plan(present, order, filename, protect)
        for f in delete:
            os.remove(os.path.join(vdir, f))
        shutil.copy(path, os.path.join(vdir, filename))
        with open(manifest, "w", encoding="utf-8") as fh:
            fh.write("\n".join(order) + "\n")
        open(os.path.join(tmp, ".nojekyll"), "w").close()
        with open(os.path.join(tmp, "index.html"), "w") as fh:
            fh.write("<!doctype html><title>space-edits media</title>")
        _git(["add", "-A"], tmp)
        _git([*WHO, "commit", "-q", "-m", f"media: {filename}"], tmp)
        _git(["push", "-q", "--force", remote, "media"], tmp)
        url = f"{pages_base()}/v/{filename}"
        size = os.path.getsize(path)
        start, retriggered = time.time(), False
        while time.time() < start + PAGES_WAIT_S:
            try:
                r = requests.head(url, timeout=20, allow_redirects=False)
                if r.status_code == 200 and int(r.headers.get("content-length", 0)) == size:
                    print(f"[host] live: {url}")
                    return url
            except requests.RequestException:
                pass
            if not retriggered and time.time() - start >= PAGES_RETRIGGER_S:
                retriggered = True
                print("[host] Pages still not serving the video - retriggering the deploy once")
                _git([*WHO, "commit", "--allow-empty", "-q", "-m", "media: retrigger Pages"], tmp)
                _git(["push", "-q", remote, "media"], tmp)
            time.sleep(15)
        raise PublishError(f"Pages never served {url} (is Pages enabled on the media branch?)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _gql(query, variables=None):
    key = os.environ.get("BUFFER_API_KEY")
    if not key:
        raise PublishError("BUFFER_API_KEY not set")
    r = requests.post(BUFFER_API, json={"query": query, "variables": variables or {}},
                      headers={"Authorization": f"Bearer {key}"}, timeout=60)
    if r.status_code != 200:
        raise PublishError(f"Buffer HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    if body.get("errors"):
        raise PublishError(f"Buffer error: {body['errors'][0].get('message')}")
    return body["data"]


def tiktok_channel():
    """(organization_id, channel_id) of the channel in BUFFER_CHANNEL_ID (never guesses: the account holds other projects' TikToks)."""
    want = os.environ.get("BUFFER_CHANNEL_ID")
    if not want:
        raise PublishError("BUFFER_CHANNEL_ID not set")
    for org in _gql("query { account { organizations { id name } } }")["account"]["organizations"]:
        chans = _gql("query($o: OrganizationId!) { channels(input: {organizationId: $o}) { id name service isQueuePaused } }",
                     {"o": org["id"]})["channels"]
        for c in chans:
            if c["id"] == want:
                if c["service"] != "tiktok":
                    raise PublishError(f"channel {c['name']} is not TikTok")
                if c.get("isQueuePaused"):
                    raise PublishError(f"Buffer queue for {c['name']} is paused")
                print(f"[buffer] channel {c['name']}")
                return org["id"], c["id"]
    raise PublishError("BUFFER_CHANNEL_ID not found in any Buffer organization")


def pending_posts(org_id, channel_id):
    data = _gql("""query($o: OrganizationId!, $c: [ChannelId!]) {
      posts(first: 20, input: {organizationId: $o, filter: {status: [scheduled], channelIds: $c}}) {
        edges { node { id dueAt text } } } }""", {"o": org_id, "c": [channel_id]})
    return [e["node"] for e in data["posts"]["edges"]]


def schedule_tiktok(channel_id, video_url, caption, due_at, thumb_ms=2500, notify=False):
    """notify=True: Buffer pings the phone at due time and the post is finished in the TikTok app (needed to add a TikTok sound)."""
    now = datetime.now(timezone.utc)
    mode = "customScheduled" if (due_at - now).total_seconds() > 300 else "shareNow"
    inp = {
        "text": caption, "channelId": channel_id, "schedulingType": "notification" if notify else "automatic", "mode": mode, "needsApproval": False,
        "assets": [{"video": {"url": video_url, "metadata": {"thumbnailOffset": thumb_ms}}}],
        # real NASA/ESA footage edited together, not generative AI
        "metadata": {"tiktok": {"isAiGenerated": False}},
    }
    if mode == "customScheduled":
        inp["dueAt"] = due_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    data = _gql("""mutation($i: CreatePostInput!) { createPost(input: $i) {
        ... on PostActionSuccess { post { id status dueAt } }
        ... on MutationError { message } } }""", {"i": inp})
    res = data["createPost"]
    if "post" not in res:
        raise PublishError(f"Buffer rejected the post: {res.get('message')}")
    print(f"[buffer] {mode}: {res['post']}")
    return res["post"]
