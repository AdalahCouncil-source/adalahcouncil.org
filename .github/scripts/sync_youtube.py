#!/usr/bin/env python3
"""Add new uploads from the Adalah Council YouTube channel to the Media page.

Runs on GitHub Actions (see .github/workflows/youtube-sync.yml). Reads YouTube's
public feeds, so no API key is needed:
  UULF... = regular uploads   -> _data/videos.yml
  UULV... = past live streams -> _data/videos.yml (only once they have aired)
  UUSH... = Shorts            -> _data/shorts.yml

New items go to the top of each list. Existing entries are never changed, so
anything edited in Pages CMS stays as written. Every video ID the script has
seen is remembered in .github/youtube-seen.txt, so a video you delete from the
list is not added back.
"""
import json, os, re, sys, time, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime

CHANNEL_ID = "UCur5qJUZYuo9ueA_04MT1SQ"   # youtube.com/@adalahcouncil
SPEAKER = "Adalah Council"
VIDEOS_FILE = "_data/videos.yml"
SHORTS_FILE = "_data/shorts.yml"
SEEN_FILE = ".github/youtube-seen.txt"
MAX_DESC = 320

NS = {"a": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}
FEED_URL = "https://www.youtube.com/feeds/videos.xml?playlist_id={}"
ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/|/v/)([A-Za-z0-9_-]{11})")

def playlist(prefix):
    return prefix + CHANNEL_ID[2:]

def fetch(prefix):
    """Return feed XML text, or None if the feed is unavailable."""
    local = os.environ.get("FEED_DIR")          # used only for testing
    if local:
        p = os.path.join(local, prefix + ".xml")
        return open(p, encoding="utf-8").read() if os.path.exists(p) else None
    url = FEED_URL.format(playlist(prefix))
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "adalah-site-sync/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  {prefix}: no feed (404), skipping"); return None
            print(f"  {prefix}: HTTP {e.code}, retrying")
        except Exception as e:  # network hiccup
            print(f"  {prefix}: {e}, retrying")
        time.sleep(5 * (attempt + 1))
    print(f"  {prefix}: unavailable after 3 tries, skipping"); return None

def parse(xml_text):
    out = []
    root = ET.fromstring(xml_text)
    for e in root.findall("a:entry", NS):
        vid = (e.findtext("yt:videoId", "", NS) or "").strip()
        if not vid:
            continue
        g = e.find("media:group", NS)
        desc = g.findtext("media:description", "", NS) if g is not None else ""
        stats = e.find("media:group/media:community/media:statistics", NS)
        views = int(stats.get("views", "0")) if stats is not None and stats.get("views", "").isdigit() else None
        out.append({"id": vid,
                    "title": re.sub(r"(\s+#\w+)+\s*$", "", (e.findtext("a:title", "", NS) or "").strip()),
                    "published": e.findtext("a:published", "", NS),
                    "description": desc or "",
                    "views": views})
    return out

def nice_date(iso):
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return f"{d:%B} {d.day}, {d.year}"
    except Exception:
        return ""

def short_desc(text):
    text = (text or "").strip()
    first = re.split(r"\n\s*\n", text)[0]                    # first paragraph
    first = re.sub(r"https?://\S+", "", first)              # drop links
    first = re.sub(r"(?:^|\s)#\w+", "", first)              # drop hashtags
    first = re.sub(r"\s+", " ", first).strip()
    if len(first) > MAX_DESC:
        first = first[:MAX_DESC].rsplit(" ", 1)[0].rstrip(",;:.") + "…"
    return first

def q(s):
    """A YAML-safe double-quoted string."""
    return json.dumps(s or "", ensure_ascii=False)

def ids_in(path):
    if not os.path.exists(path):
        return set()
    text = open(path, encoding="utf-8").read()
    found = set(ID_RE.findall(text))
    found |= set(re.findall(r"^\s*-?\s*youtube:\s*[\"']?([A-Za-z0-9_-]{11})[\"']?\s*$", text, re.M))
    return found

def prepend(path, blocks):
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    new = "".join(blocks)
    if re.fullmatch(r"\s*(\[\s*\])?\s*", text):
        text = new
    else:
        m = re.search(r"^-", text, re.M)
        text = text[:m.start()] + new + text[m.start():] if m else text.rstrip("\n") + "\n" + new
    open(path, "w", encoding="utf-8").write(text)

def video_block(v):
    return (f"- title: {q(v['title'])}\n"
            f"  speaker: {q(SPEAKER)}\n"
            f"  date: {q(nice_date(v['published']))}\n"
            f"  duration: \"\"\n"
            f"  topic: \"\"\n"
            f"  youtube: {q('https://www.youtube.com/watch?v=' + v['id'])}\n"
            f"  description: {q(short_desc(v['description']))}\n")

def short_block(v):
    return (f"- title: {q(v['title'])}\n"
            f"  youtube: {q('https://www.youtube.com/shorts/' + v['id'])}\n")

def main():
    seen = set()
    if os.path.exists(SEEN_FILE):
        seen = {l.strip() for l in open(SEEN_FILE, encoding="utf-8") if l.strip() and not l.startswith("#")}
    known = seen | ids_in(VIDEOS_FILE) | ids_in(SHORTS_FILE)

    feeds = {}
    for prefix in ("UULF", "UULV", "UUSH"):
        xml_text = fetch(prefix)
        if xml_text is None:
            continue
        try:
            feeds[prefix] = parse(xml_text)
            print(f"  {prefix}: {len(feeds[prefix])} items in feed")
        except ET.ParseError as e:
            print(f"  {prefix}: could not read feed ({e}), skipping")
    if not feeds:
        print("No YouTube feeds could be read this run."); sys.exit(1)

    shorts_ids = {v["id"] for v in feeds.get("UUSH", [])}
    new_videos, new_shorts, newly_seen = [], [], set()
    for prefix in ("UULF", "UULV"):
        for v in feeds.get(prefix, []):
            if v["id"] in known or v["id"] in shorts_ids or v["id"] in newly_seen:
                continue
            if prefix == "UULV" and not v["views"]:
                continue      # scheduled stream that hasn't aired yet; pick it up later
            new_videos.append(v); newly_seen.add(v["id"])
    for v in feeds.get("UUSH", []):
        if v["id"] in known or v["id"] in newly_seen:
            continue
        new_shorts.append(v); newly_seen.add(v["id"])

    new_videos.sort(key=lambda v: v["published"], reverse=True)
    new_shorts.sort(key=lambda v: v["published"], reverse=True)
    if new_videos:
        prepend(VIDEOS_FILE, [video_block(v) for v in new_videos])
    if new_shorts:
        prepend(SHORTS_FILE, [short_block(v) for v in new_shorts])

    all_seen = seen | newly_seen | ids_in(VIDEOS_FILE) | ids_in(SHORTS_FILE)
    if all_seen != seen:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            f.write("# Video IDs already handled by the YouTube sync. Do not edit unless you want a video re-added.\n")
            f.write("\n".join(sorted(all_seen)) + "\n")

    for v in new_videos: print(f"  + video: {v['title']}")
    for v in new_shorts: print(f"  + short: {v['title']}")
    print(f"Added {len(new_videos)} video(s) and {len(new_shorts)} short(s).")

if __name__ == "__main__":
    main()
