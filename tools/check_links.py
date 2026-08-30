"""Check every link in the master and record whether it still resolves.

Writes a cache at data/link_status.json keyed by url. tools/unify.py folds that
cache into the master as a `status` block on each source, so checking is
optional and the pipeline still runs offline.

Verdicts are deliberately cautious:

  yes    the page loaded and does not look like a dead listing
  no     proven gone: 404/410, or a marketplace page that says the item was
         removed, or a redirect that dumped us on the site's front page
  maybe  anything we could not prove either way - login walls, bot checks,
         rate limits, 5xx, timeouts, DNS failures, blocked hosts

Only `no` means "fix this link". `maybe` means "a human has to look".

A rerun only visits links that are unchecked or `maybe`. A settled `yes` or
`no` is an answer and is left alone unless you ask for it with --recheck or
--max-age.

Sites that serve bot checks (AliExpress does, after a handful of requests) stop
the run rather than filling the cache with `maybe`. Run it from a terminal and
--interactive is on by default: the page opens in your browser, you clear the
check, and the run carries on. Pasting the site Cookie header at that prompt
lets every remaining link on that host reuse the solved session.

Usage:
  python tools/check_links.py                 # unchecked + previous maybes
  python tools/check_links.py --recheck       # ignore the cache entirely
  python tools/check_links.py --max-age 30    # also redo yes/no older than 30d
  python tools/check_links.py --status no     # revisit just the dead ones
  python tools/check_links.py --only aliexpress --workers 1
  python tools/check_links.py --cookies-file ali.txt --no-interactive
"""
import argparse
import json
import os
import random
import re
import sys
import threading
import time
import webbrowser
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalize import host_of  # noqa: E402

MASTER = "data/voron_sourcing_master.json"
CACHE = "data/link_status.json"
REPORT = "data/link_status.csv"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "close",
}

# Be polite: at most one request per host at a time, spaced out.
PER_HOST_DELAY = 1.5
SLOW_HOSTS = {"aliexpress.com": 3.0, "s.click.aliexpress.com": 3.0,
              "amazon.com": 3.0, "amzn.to": 3.0}

# ---------------------------------------------------------------- dead pages

# Pages that prove nothing either way: bot walls, login gates, interstitials.
# Matched against the VISIBLE text. Matching raw html here was a mistake: an
# ordinary shop page that loads google's recaptcha script contains the string
# "captcha" and would be written off as a bot wall.
UNCERTAIN_PATTERNS = [
    r"enter the characters you see below",        # amazon captcha
    r"to discuss automated access to amazon data",
    r"are you a robot", r"unusual traffic", r"captcha",
    r"access denied", r"just a moment\.\.\.",      # cloudflare
    r"please (sign|log) in", r"sign in to continue",
    r"security check", r"verify you are a human",
]

# Proof a listing is gone, per marketplace. Narrow on purpose: a false `no` is
# worse than a `maybe`, so anything not on this list falls through to `maybe`.
AMAZON_DEAD = [
    r"we couldn'?t find that page",
    r"dogs of amazon",                            # amazon's 404 mascot page
    r"the web address you entered is not a functioning page",
]
GENERIC_DEAD = [
    r"<title>[^<]{0,80}\b(404|page not found|not found)\b[^<]{0,80}</title>",
    r"\bthis (product|item|listing) (has been|was) removed\b",
    r"\bproduct (is )?no longer available\b",
]

# Everything a shop's javascript mentions is not what the page says. Amazon
# ships the string "currently unavailable" inside script blocks on every
# product page, and AliExpress ships "the page you requested can not be found"
# in its i18n bundle even when the listing is live. Dead-page checks run
# against the visible text only; bot-wall checks stay on the raw html, because
# over-detecting a wall costs a `maybe` while under-detecting it costs a wrong
# `no`.
_SCRIPTS = re.compile(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>",
                      re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")


def visible_text(html):
    return re.sub(r"\s+", " ", _TAGS.sub(" ", _SCRIPTS.sub(" ", html or ""))).lower()


_OG_TITLE = re.compile(
    r"""<meta[^>]+property=["']og:title["'][^>]*content=["']([^"']*)""", re.I)

# A vendor whose domain lapsed now serves a parking page. That is as dead as a
# 404, but it answers 200 with a page full of text, and parking pages often
# carry a captcha widget that would otherwise read as a bot wall and stall an
# interactive run waiting for a check nobody can solve.
PARKED_HOSTS = {
    "sedoparking.com", "sedo.com", "dan.com", "undeveloped.com",
    "afternic.com", "hugedomains.com", "bodis.com", "parkingcrew.net",
    "above.com", "buydomains.com", "domainmarket.com", "squadhelp.com",
    "atom.com", "namecheap.com",
}
PARKED_TEXT = [
    r"\bthis domain (name )?is for sale\b",
    r"\bbuy this domain\b",
    r"\bthe domain .{0,40} is for sale\b",
    r"\bdomain (name )?for sale\b",
    r"\binquire about this domain\b",
    r"\bthe owner of this domain\b.{0,60}\bsell\b",
    r"\bthis (web )?page is parked\b",
    r"\bparked (free )?(courtesy of|by)\b",
]


def looks_parked(final_url, visible):
    host = host_of(final_url or "")
    if any(host == p or host.endswith("." + p) for p in PARKED_HOSTS):
        return True
    head = visible[:4000]
    return any(re.search(p, head) for p in PARKED_TEXT)

BLOCKED_HOSTS = {           # refuse plain http, but worth a try in --browser
    "mcmaster.com", "misumi-ec.com", "us.misumi-ec.com",
}

# Hosts whose bot check cannot be cleared even by hand, so there is nothing to
# gain by fetching them or pausing on them. Recorded as `maybe` and skipped in
# both modes. Add more with --skip-host.
SKIP_HOSTS = {
    "cnckitchenus.store",   # check cannot be passed without closing the window
    "tapplastics.com",      # hard block for automation; the shop itself is fine
    "harborfreight.com",    # same
}


class HostLimiter:
    """One in-flight request per host, spaced by a per-host delay."""

    def __init__(self):
        self._locks = defaultdict(threading.Lock)
        self._last = defaultdict(float)
        self._guard = threading.Lock()

    def lock_for(self, host):
        with self._guard:
            return self._locks[host]

    def wait(self, host):
        delay = SLOW_HOSTS.get(host, PER_HOST_DELAY)
        elapsed = time.time() - self._last[host]
        if elapsed < delay:
            time.sleep(delay - elapsed + random.uniform(0, 0.4))
        self._last[host] = time.time()


def session():
    s = requests.Session()
    s.headers.update(HEADERS)
    a = HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0)
    s.mount("http://", a)
    s.mount("https://", a)
    return s


def marketplace(host):
    if "aliexpress" in host:
        return "aliexpress"
    if "amazon" in host or host == "amzn.to":
        return "amazon"
    return None


def judge(url, final_url, code, body, page_title=None):
    """-> (verdict, reason). Only return 'no' when the page proves it.

    page_title is the rendered document.title, available in browser mode; it
    stands in for og:title on sites that fill the title in with javascript.
    """
    host = host_of(final_url or url)
    low = (body or "")[:400000].lower()
    vis = visible_text((body or "")[:400000])

    if code in (404, 410):
        return "no", "http %d" % code
    if code in (401, 402, 403, 407, 429) or (code or 0) >= 500:
        return "maybe", "http %d" % code
    if looks_parked(final_url or url, vis):
        return "no", "domain is parked or for sale"

    # A real wall is a small page that exists only to stop you, or a redirect
    # to a challenge endpoint. Signals that need the raw html are kept narrow
    # for that reason.
    challenge_url = re.search(r"_____tmd_____|/punish|x5secdata|/cdn-cgi/challenge",
                              final_url or url, re.I)
    tiny_challenge = len(low) < 15000 and re.search(r"captcha|slider", low)
    if challenge_url or tiny_challenge:
        return "maybe", "bot check or login wall"

    if "aliexpress" in host:
        # AliExpress renders the product client-side and answers every item id
        # with the same 200 shell, so the HTML body says nothing. og:title is
        # the one server-rendered field: filled in for a live listing, empty
        # for one that is gone (a made-up item id returns the same empty page).
        if "/item/" not in (final_url or url):
            return "no", "redirected off the product page"
        m = _OG_TITLE.search(body or "")
        if m and m.group(1).strip():
            return "yes", "listing has a title"
        rendered = (page_title or "").strip()
        if rendered and rendered.lower() not in ("aliexpress", "aliexpress.com"):
            return "yes", "rendered page has a product title"
        if m or page_title is not None:
            return "no", "listing has no title (removed)"
        return "maybe", "no og:title tag - page format may have changed"

    if "amazon" in host or host == "amzn.to":
        if re.search(r"enter the characters you see below|"
                     r"to discuss automated access to amazon data", vis):
            return "maybe", "bot check or login wall"
        for pat in AMAZON_DEAD:
            if re.search(pat, vis):
                return "no", "amazon 404 page"
        if "producttitle" in low or "add to cart" in low:
            # The listing exists, so the link is not dead, but a listing you
            # cannot buy from is no use for sourcing. Said in the reason rather
            # than counted as dead.
            if re.search(r"currently unavailable", vis):
                return "yes", "product page - currently unavailable"
            return "yes", "product page"
        return "maybe", "not recognisably a product page"

    if "digikey" in host or "mouser" in host or "arrow.com" in host:
        # Distributors keep the page up after a part dies. "No longer
        # available" means gone; an Obsolete/Discontinued part status means the
        # page works but nobody can buy the part, which is worth saying without
        # calling the link dead.
        if re.search(r"no longer available", vis):
            return "no", "distributor says the part is no longer available"
        if re.search(r"part status[^a-z]{0,40}(obsolete|discontinued|"
                     r"last time buy|not for new designs)", vis):
            return "yes", "product page - part status is obsolete/discontinued"

    # Generic wall wording, only for sites we have no specific rule for. A real
    # product page mentions "please sign in" in its reviews widget, so this
    # cannot run before the branches above.
    for pat in UNCERTAIN_PATTERNS:
        if re.search(pat, vis):
            return "maybe", "bot check or login wall"

    for pat in GENERIC_DEAD:
        if re.search(pat, low if "<title>" in pat else vis):
            return "no", "page says the listing is gone"
    if code != 200:
        return "maybe", "http %d" % code
    if len(low) < 500:
        return "maybe", "response too short to judge"
    return "yes", "http 200"


# ------------------------------------------------------- getting past a wall

# Cookies handed to us for a host, either from --cookie-header/--cookies-file
# or pasted at the interactive prompt. Read on every request, so a cookie
# supplied mid-run takes effect immediately in every worker thread.
EXTRA_COOKIES = {}


def cookie_for(host):
    # most specific domain wins; "" is the catch-all
    best = None
    for domain, value in EXTRA_COOKIES.items():
        if domain and not (host == domain or host.endswith("." + domain)):
            continue
        if best is None or len(domain) > len(best[0]):
            best = (domain, value)
    return best[1] if best else None


def load_cookie_file(path):
    """Accept a Netscape cookies.txt, or a file holding a raw Cookie header."""
    text = open(path, encoding="utf-8").read().strip()
    if not text:
        return {}
    if not text.lstrip().startswith("#") and "\t" not in text:
        return {"": text}                     # a bare header line, any host
    jar = defaultdict(list)
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            domain = parts[0].lstrip(".")
            jar[domain].append("%s=%s" % (parts[5], parts[6]))
    return {d: "; ".join(v) for d, v in jar.items()}


class Gate:
    """Pauses every worker when a site starts serving bot checks.

    Without this the run just burns through the remaining links collecting
    `maybe`, and hammering a site that has already asked us to stop.
    """

    def __init__(self, interactive):
        self.interactive = interactive
        self.open = threading.Event()
        self.open.set()
        self.lock = threading.Lock()
        self.hits = Counter()
        self.given_up = set()
        self.solved_at = {}

    def wait(self):
        self.open.wait()

    def blocked(self, url, host):
        """-> True if the caller should retry the url, False to record maybe."""
        with self.lock:
            self.hits[host] += 1
            if host in self.given_up:
                return False
            if time.time() - self.solved_at.get(host, 0) < 30:
                # somebody just cleared this host while other workers were
                # already in flight - retry those quietly instead of asking again
                return True
            if not self.interactive:
                if self.hits[host] == 5:
                    print("\n  %s is serving bot checks - the rest of its links "
                          "will come back `maybe`.\n  Rerun with --interactive "
                          "to solve the check and continue." % host, flush=True)
                    self.given_up.add(host)
                return False
            self.open.clear()
            try:
                return self._prompt(url, host)
            finally:
                self.open.set()

    def _prompt(self, url, host):
        print("\n" + "=" * 70)
        print("  %s is asking for a bot check." % host)
        print("  Opening the blocked page in your browser." )
        print()
        print("  1. solve the check there (slider, captcha, whatever it wants)")
        print("  2. come back here and either:")
        print("       - press Enter to retry with the same session, or")
        print("       - paste a fresh Cookie header for the site and press Enter")
        print("         (devtools > Network > any request > Request Headers >")
        print("          Cookie, copy the whole value)")
        print("     or type `skip` to stop asking for %s." % host)
        print("=" * 70)
        try:
            webbrowser.open(url)
        except Exception:                              # noqa: BLE001
            print("  (could not open a browser - visit the url above yourself)")
        try:
            answer = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("  no input available - continuing without solving")
            self.given_up.add(host)
            return False
        if answer.lower() in ("skip", "s", "n", "no"):
            self.given_up.add(host)
            return False
        self.solved_at[host] = time.time()
        if answer:
            if answer.lower().startswith("cookie:"):
                answer = answer.split(":", 1)[1]
            EXTRA_COOKIES[host] = answer.strip()
            print("  cookie stored for %s (%d chars), reused for every "
                  "remaining link on that host" % (host, len(answer)))
        return True


def skipped(host):
    return any(host == h or host.endswith("." + h) for h in SKIP_HOSTS)


def check(url, limiter, sess, gate=None):
    host = host_of(url)
    if skipped(host):
        return {"status": "maybe", "reason": "skipped - bot check cannot be "
                "cleared", "http": None, "final_url": None}
    if host in BLOCKED_HOSTS:
        return {"status": "maybe", "reason": "host refuses automated requests",
                "http": None, "final_url": None}
    if gate:
        gate.wait()
    with limiter.lock_for(host):
        limiter.wait(host)
        try:
            headers = {}
            jar = cookie_for(host)
            if jar:
                headers["Cookie"] = jar
            r = sess.get(url, timeout=25, allow_redirects=True, stream=True,
                         headers=headers or None)
            body = r.raw.read(400000, decode_content=True) or b""
            r.close()
        except requests.exceptions.SSLError as e:
            return {"status": "maybe", "reason": "ssl error: %s" % e.__class__.__name__,
                    "http": None, "final_url": None}
        except requests.exceptions.ConnectionError:
            return {"status": "maybe", "reason": "connection failed",
                    "http": None, "final_url": None}
        except requests.exceptions.Timeout:
            return {"status": "maybe", "reason": "timeout",
                    "http": None, "final_url": None}
        except Exception as e:                       # noqa: BLE001
            return {"status": "maybe", "reason": "error: %s" % e.__class__.__name__,
                    "http": None, "final_url": None}
    try:
        text = body.decode(r.encoding or "utf-8", "replace")
    except (LookupError, TypeError):
        text = body.decode("utf-8", "replace")
    verdict, reason = judge(url, r.url, r.status_code, text)
    return {"status": verdict, "reason": reason, "http": r.status_code,
            "final_url": r.url if r.url != url else None}


def check_confirmed(url, limiter, sess, gate=None):
    """One request, plus a second one when the first says something drastic.

    A `no` is only reported if a repeat agrees - rate limiting and geo
    interstitials can make a live page look dead, and a wrong `no` sends
    somebody off to re-source a part that was fine.

    A bot check is not an answer at all, so the gate gets a chance to have a
    human clear it and the url is tried again.
    """
    res = check(url, limiter, sess, gate)
    if gate and res["reason"].startswith("bot check"):
        if gate.blocked(url, host_of(url)):
            res = check(url, limiter, sess, gate)
    if res["status"] != "no":
        return res
    time.sleep(2.0)
    again = check(url, limiter, sess, gate)
    if again["status"] == "no":
        return again
    again["reason"] += " (first attempt looked dead)"
    return again


# ------------------------------------------------------------- browser mode

class Browser:
    """Drives a real Chrome so a person can clear bot checks by hand.

    AliExpress punishes plain HTTP clients after a handful of requests, and its
    punish page is solvable but only in a browser. Running the checks through
    one means the check gets solved once, in a window you can see, and the
    session carries on into every following link. The profile directory is
    persistent, so a later run starts out already trusted.
    """

    WALL_URL = re.compile(r"_____tmd_____|/punish|x5secdata|/cdn-cgi/challenge",
                          re.I)
    WALL_TEXT = re.compile(r"enter the characters you see below|are you a robot|"
                           r"unusual traffic|verify you are a human|"
                           r"security check|just a moment|access denied|"
                           r"slide to verify|please slide", re.I)

    def __init__(self, profile, headless=False, binary=None):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        o = Options()
        if binary:
            o.binary_location = binary
        if headless:
            o.add_argument("--headless=new")
        o.add_argument("--user-data-dir=" + os.path.abspath(profile))
        o.add_argument("--window-size=1200,950")
        o.add_argument("--disable-blink-features=AutomationControlled")
        o.add_experimental_option("excludeSwitches", ["enable-automation"])
        o.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        self.driver = webdriver.Chrome(options=o)
        self.driver.set_page_load_timeout(45)

    def _document_status(self):
        """The main document's HTTP status, read out of the devtools log."""
        status = None
        try:
            for entry in self.driver.get_log("performance"):
                msg = json.loads(entry["message"])["message"]
                if (msg.get("method") == "Network.responseReceived"
                        and msg["params"].get("type") == "Document"):
                    status = msg["params"]["response"]["status"]
        except Exception:                                  # noqa: BLE001
            pass
        return status

    def fetch(self, url, settle=6.0):
        self.driver.get(url)
        # give a javascript-rendered title a moment to appear: an empty title
        # is how we decide a listing is gone, so reading it too early would
        # condemn a live page
        deadline = time.time() + settle
        while not (self.driver.title or "").strip() and time.time() < deadline:
            time.sleep(0.4)
        return (self.driver.current_url, self.driver.page_source,
                self._document_status(), self.driver.title)

    def walled(self, final_url, html):
        vis = visible_text(html[:120000])
        if looks_parked(final_url, vis):
            return False        # a parked domain is an answer, not a wall
        if self.WALL_URL.search(final_url or ""):
            return True
        # a challenge page is short and says so; a shop page that merely loads
        # recaptcha for its contact form is not a wall
        return bool(self.WALL_TEXT.search(vis[:6000])
                    or (len(html) < 15000 and re.search(r"captcha", html, re.I)))

    def wait_until_solved(self, host, timeout):
        """Watch the window until the check goes away.

        Deliberately not an input() prompt: the run may have been started from
        somewhere without a terminal to type into, and watching the page is
        what we actually care about anyway. Solve it in the window and the run
        picks itself up.
        """
        print()
        print("=" * 70)
        print("  %s is showing a bot check in the Chrome window." % host)
        print("  Solve it there. This will carry on by itself within a few")
        print("  seconds of the check clearing (waiting up to %ds)." % timeout)
        print("  If it is a hard block rather than a solvable check, leave it -")
        print("  the host gets recorded as `maybe` and the run moves on.")
        print("=" * 70, flush=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2.5)
            try:
                if not self.walled(self.driver.current_url,
                                   self.driver.page_source):
                    print("  check cleared - carrying on", flush=True)
                    return True
            except Exception:                              # noqa: BLE001
                return False
        print("  no solve within %ds - recording maybe and moving on"
              % timeout, flush=True)
        return False

    def close(self):
        try:
            self.driver.quit()
        except Exception:                                  # noqa: BLE001
            pass


def find_chrome():
    local = os.path.expandvars(
        "%LOCALAPPDATA%" + os.sep + "Google" + os.sep + "Chrome"
        + os.sep + "Application" + os.sep + "chrome.exe")
    for p in ["C:/Program Files/Google/Chrome/Application/chrome.exe",
              "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
              local]:
        if os.path.exists(p):
            return p
    return None


def run_browser(todo, cache, targets, args):
    """Sequential pass through a real browser, pausing on bot checks."""
    try:
        br = Browser(args.browser_profile, args.browser_headless,
                     args.browser_binary or find_chrome())
    except Exception as e:                                 # noqa: BLE001
        print("could not start Chrome: %s: %s" % (type(e).__name__, str(e)[:200]))
        print("pass --browser-binary with the path to chrome.exe, or drop "
              "--browser and use --interactive instead")
        return

    limiter, done, given_up = HostLimiter(), 0, set()
    fresh = {}
    print("browser mode: %d links, one at a time" % len(todo))
    try:
        for url in todo:
            host = host_of(url)
            if skipped(host):
                cache[url] = fresh[url] = {
                    "status": "maybe", "http": None, "final_url": None,
                    "reason": "skipped - bot check cannot be cleared",
                    "checked_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds")}
                done += 1
                continue
            limiter.wait(host)
            res = None
            for _attempt in range(3):
                try:
                    final, html, code, title = br.fetch(url)
                except Exception as e:                     # noqa: BLE001
                    res = {"status": "maybe", "http": None, "final_url": None,
                           "reason": "browser error: " + type(e).__name__}
                    break
                if br.walled(final, html) and host not in given_up:
                    if not args.interactive:
                        res = {"status": "maybe", "http": code,
                               "final_url": final,
                               "reason": "bot check or login wall"}
                        break
                    if not br.wait_until_solved(host, args.solve_timeout):
                        given_up.add(host)
                        res = {"status": "maybe", "http": code,
                               "final_url": final,
                               "reason": "bot check or login wall"}
                        break
                    continue
                verdict, reason = judge(url, final, code or 200, html,
                                        page_title=title)
                if verdict == "no":
                    # never condemn a link on one load
                    time.sleep(2.0)
                    final2, html2, code2, title2 = br.fetch(url)
                    verdict2, reason2 = judge(url, final2, code2 or 200, html2,
                                              page_title=title2)
                    if verdict2 != "no":
                        verdict, reason = verdict2, reason2 + " (first load looked dead)"
                res = {"status": verdict, "reason": reason, "http": code,
                       "final_url": final if final != url else None}
                break
            if res is None:
                res = {"status": "maybe", "http": None, "final_url": None,
                       "reason": "still behind a bot check after 3 tries"}
            res["checked_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds")
            cache[url] = fresh[url] = res
            done += 1
            if res["status"] == "no":
                print("  DEAD %s" % url[:100], flush=True)
            if done % 10 == 0 or done == len(todo):
                print("  %d/%d" % (done, len(todo)), flush=True)
                save(cache, targets, fresh)
    except KeyboardInterrupt:
        print()
        print("interrupted - saving what has been checked so far")
    finally:
        br.close()
        save(cache, targets, fresh)


def urls_from_master():
    """Every link the master points a builder at, product and affiliate."""
    m = json.load(open(MASTER, encoding="utf-8"))
    out = {}
    for i in m["items"]:
        for s in i["sources"]:
            for u in [s["url"]] + s["affiliate_urls"]:
                out.setdefault(u, set()).add(i["id"])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recheck", action="store_true", help="ignore the cache")
    ap.add_argument("--max-age", type=int, default=0, metavar="DAYS",
                    help="recheck entries older than this many days")
    ap.add_argument("--status", action="append", metavar="V",
                    help="recheck only cached entries with this verdict "
                         "(repeatable, e.g. --status maybe)")
    ap.add_argument("--only", metavar="SUBSTR",
                    help="only urls whose host contains this")
    ap.add_argument("--exclude", metavar="SUBSTR",
                    help="skip urls whose host contains this")
    ap.add_argument("--limit", type=int, default=0, help="stop after N urls")
    ap.add_argument("--workers", type=int, default=6,
                    help="parallel requests across different hosts")
    ap.add_argument("--interactive", dest="interactive", action="store_true",
                    default=None,
                    help="when a site serves a bot check, pause, open the page "
                         "in your browser so you can solve it, then carry on "
                         "(default: on when run from a terminal)")
    ap.add_argument("--no-interactive", dest="interactive", action="store_false",
                    help="never pause for a bot check")
    ap.add_argument("--cookie-header", metavar="STR",
                    help="Cookie header to send, e.g. copied from devtools; "
                         "use with --cookie-host")
    ap.add_argument("--cookie-host", metavar="HOST", default="",
                    help="host the --cookie-header belongs to "
                         "(default: send it everywhere)")
    ap.add_argument("--browser", action="store_true",
                    help="run the checks through a real Chrome window, so you "
                         "can solve bot checks yourself and the solved session "
                         "carries into the rest of the run")
    ap.add_argument("--skip-host", action="append", metavar="HOST", default=[],
                    help="never fetch or pause on this host; record it as "
                         "`maybe` and move on (repeatable)")
    ap.add_argument("--solve-timeout", type=int, default=120, metavar="SEC",
                    help="how long browser mode waits for you to clear a bot "
                         "check before giving up on that host (default 300)")
    ap.add_argument("--browser-headless", action="store_true",
                    help="browser mode without a visible window (only useful "
                         "once the profile is already trusted)")
    ap.add_argument("--browser-binary", metavar="PATH",
                    help="path to chrome.exe if it is not where we look")
    ap.add_argument("--browser-profile", metavar="DIR",
                    default="data/.link-check-profile",
                    help="persistent Chrome profile, so a solved check is "
                         "remembered next run")
    ap.add_argument("--cookies-file", metavar="PATH",
                    help="Netscape cookies.txt export, or a file holding a "
                         "single Cookie header line")
    args = ap.parse_args()

    SKIP_HOSTS.update(h.strip().lower() for h in args.skip_host if h.strip())
    if args.interactive is None:
        args.interactive = sys.stdin is not None and sys.stdin.isatty()
    if args.cookie_header:
        EXTRA_COOKIES[args.cookie_host] = args.cookie_header.strip()
    if args.cookies_file:
        EXTRA_COOKIES.update(load_cookie_file(args.cookies_file))
    if EXTRA_COOKIES:
        print("cookies loaded for: %s"
              % ", ".join(h or "(any host)" for h in EXTRA_COOKIES))

    targets = urls_from_master()
    manual = {}
    if os.path.exists("data/link_status_manual.json"):
        manual = json.load(open("data/link_status_manual.json",
                                encoding="utf-8")).get("links", {})
        if manual:
            print("%d link(s) settled by hand in data/link_status_manual.json "
                  "- not re-checked" % len(manual))
    cache = {}
    if os.path.exists(CACHE) and not args.recheck:
        cache = json.load(open(CACHE, encoding="utf-8")).get("links", {})

    now = datetime.now(timezone.utc)

    def stale(entry):
        """A settled verdict is never revisited without being asked.

        `yes` and `no` are answers; only `maybe` means the check did not get
        through, so that is the one retried by default.
        """
        if args.status:
            return entry.get("status") in args.status
        if entry.get("status") == "maybe":
            return True
        if not args.max_age:
            return False
        try:
            when = datetime.fromisoformat(entry["checked_at"])
        except (KeyError, ValueError):
            return True
        return (now - when).days >= args.max_age

    todo = [u for u in targets
            if u not in manual and (u not in cache or stale(cache[u]))]
    if args.only:
        todo = [u for u in todo if args.only.lower() in host_of(u)]
    if args.exclude:
        todo = [u for u in todo if args.exclude.lower() not in host_of(u)]
    todo.sort(key=host_of)
    if args.limit:
        todo = todo[:args.limit]

    print("%d links in master, %d cached, %d to check"
          % (len(targets), len(cache), len(todo)))
    if not todo:
        print("nothing to do (use --recheck, --max-age or --status)")

    if args.browser:
        if not todo:
            return
        run_browser(todo, cache, targets, args)
        counts = Counter(v["status"] for v in cache.values())
        for v in ("yes", "maybe", "no"):
            print("%-6s %s" % (v, counts.get(v, 0)))
        print("run tools/unify.py to fold the verdicts into the master")
        return

    limiter, done, fresh = HostLimiter(), 0, {}
    gate = Gate(args.interactive)
    if args.interactive:
        print("interactive: a bot check will pause the run and open the page "
              "in your browser")
    sessions = threading.local()

    def work(url):
        if not hasattr(sessions, "s"):
            sessions.s = session()
        res = check_confirmed(url, limiter, sessions.s, gate)
        res["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return url, res

    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futures = {ex.submit(work, u): u for u in todo}
            for fut in as_completed(futures):
                url, res = fut.result()
                cache[url] = fresh[url] = res
                done += 1
                if done % 20 == 0 or done == len(todo):
                    print("  %d/%d" % (done, len(todo)), flush=True)
                    save(cache, targets, fresh)   # checkpoint, runs are slow
                if res["status"] == "no":
                    print("  DEAD %-9s %s" % (res["reason"][:9], url[:100]),
                          flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted - saving what has been checked so far")

    save(cache, targets, fresh)
    counts = Counter(v["status"] for v in cache.values())
    print("\n%-6s %s" % ("yes", counts.get("yes", 0)))
    print("%-6s %s" % ("maybe", counts.get("maybe", 0)))
    print("%-6s %s" % ("no", counts.get("no", 0)))
    print("wrote %s and %s" % (CACHE, REPORT))
    print("run tools/unify.py to fold the verdicts into the master")


def save(cache, targets, fresh=None):
    """Write the cache and the csv.

    Called on every progress tick as well as at the end: a full run takes half
    an hour, and an interrupted one should not throw away what it checked.

    `fresh` is what this process actually checked. Only those entries are
    written over the file, because our copy of everything else was read at
    startup and may be older than what another pass has since written.
    """
    # merge with whatever is on disk first, so two passes running side by side
    # (say, a plain-http sweep and a browser pass on one site) do not clobber
    # each other's results
    merged = {}
    if os.path.exists(CACHE):
        try:
            merged = json.load(open(CACHE, encoding="utf-8")).get("links", {})
        except (ValueError, OSError):
            merged = {}
    # A `maybe` carries no information, so it must never displace a settled
    # verdict: a plain-http pass over a host that blocks plain http would
    # otherwise wipe out good answers a browser pass had already collected.
    incoming = cache if fresh is None else fresh
    for url, res in incoming.items():
        prior = merged.get(url)
        if (res.get("status") == "maybe" and prior
                and prior.get("status") in ("yes", "no")):
            continue
        merged[url] = res
    cache.update(merged)
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump({"checked_with": "tools/check_links.py",
                   "links": dict(sorted(merged.items()))},
                  fh, indent=1, ensure_ascii=False)

    import csv
    with open(REPORT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["status", "http", "reason", "vendor_host", "url",
                    "final_url", "checked_at", "items"])
        order = {"no": 0, "maybe": 1, "yes": 2}
        for url, res in sorted(cache.items(),
                               key=lambda kv: (order.get(kv[1]["status"], 3),
                                               host_of(kv[0]))):
            w.writerow([res["status"], res.get("http") or "", res["reason"],
                        host_of(url), url, res.get("final_url") or "",
                        res.get("checked_at", ""),
                        " ".join(sorted(targets.get(url, ())))])


if __name__ == "__main__":
    main()
