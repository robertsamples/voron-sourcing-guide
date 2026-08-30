"""Shared normalisation helpers: item names, vendors, URLs, quantities."""
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, unquote

# ---------------------------------------------------------------- item names

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {0x2018: "'", 0x2019: "'", 0x201c: '"', 0x201d: '"', 0x00d7: "x"}


def clean_text(s):
    """Cosmetic clean-up that is safe to show to a human."""
    if s is None:
        return None
    if not isinstance(s, str):
        s = str(s)
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_DASHES).translate(_QUOTES)
    s = s.replace(" ", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" ,;:-–")
    return s or None


def name_key(s):
    """Aggressive key used to decide two rows name the same component."""
    s = clean_text(s)
    if s is None:
        return None
    s = s.lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\b(pcs|pieces|pack|packs|qty)\b", " ", s)
    s = re.sub(r"[^a-z0-9.]+", " ", s)
    # m3 x 8 -> m3x8 ; 3 x 7 x 0.5 -> 3x7x0.5
    s = re.sub(r"(?<=\d)\s+x\s+(?=\d)", "x", s)
    s = re.sub(r"\b(\d+)\.0\b", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


# Misumi extrusion part numbers: HFSB5-2020-370-AH45-BH325
#   HFSB5      series          -> profile family
#   2020       profile         -> HFSB5-2020
#   370        length in mm    -> HFSB5-2020-370, the product you order
#   AH45-BH325 machining codes -> tapped/blind holes cut into that same product
_MISUMI = re.compile(
    r"(HFSB\d)-(\d{4})-(\d+)((?:-[A-Z]{2,3}\d*)*)", re.I)


def misumi_part(name):
    """-> (full part no, base product, machining) or None.

    Machining suffixes describe holes drilled into an otherwise identical
    extrusion, so `base` is what actually gets sourced.
    """
    if not name:
        return None
    m = _MISUMI.search(name)
    if not m:
        return None
    series, profile, length, mach = m.groups()
    base = "%s-%s-%s" % (series.upper(), profile, length)
    mach = (mach or "").strip("-").upper()
    return (base + ("-" + mach if mach else ""), base, mach or None)


def clean_size(v):
    """Normalise the build-size column: `250³` -> `250`, True/False -> All/None."""
    if v is None:
        return None
    s = str(v).replace("³", "")          # strip before NFKC turns it into "3"
    s = clean_text(s)
    if s is None:
        return None
    if s.lower() in ("true", "all", "all sizes"):
        return "All"
    if s.lower() == "false":
        return None
    return s


# ------------------------------------------------------------------- vendors

# host suffix -> (vendor id, display name). Longest suffix wins.
VENDOR_HOSTS = [
    ("s.click.aliexpress.com", "aliexpress", "AliExpress"),
    ("aliexpress.com", "aliexpress", "AliExpress"),
    ("aliexpress.us", "aliexpress", "AliExpress"),
    ("amzn.to", "amazon", "Amazon"),
    ("amazon.com", "amazon", "Amazon"),
    ("amazon.co.uk", "amazon-uk", "Amazon UK"),
    ("amazon.de", "amazon-de", "Amazon DE"),
    ("amazon.ca", "amazon-ca", "Amazon CA"),
    ("mcmaster.com", "mcmaster", "McMaster-Carr"),
    ("boltdepot.com", "boltdepot", "Bolt Depot"),
    ("misumi.eu", "misumi", "Misumi"),
    ("misumi-ec.com", "misumi", "Misumi"),
    ("us.misumi-ec.com", "misumi", "Misumi"),
    ("robotdigg.com", "robotdigg", "RobotDigg"),
    ("filastruder.com", "filastruder", "Filastruder"),
    ("e3d-online.com", "e3d", "E3D"),
    ("bondtech.se", "bondtech", "Bondtech"),
    ("trianglelab.net", "trianglelab", "Trianglelab"),
    ("digikey.com", "digikey", "Digi-Key"),
    ("mouser.com", "mouser", "Mouser"),
    ("sdp-si.com", "sdp-si", "SDP/SI"),
    ("shop.sdp-si.com", "sdp-si", "SDP/SI"),
    ("omc-stepperonline.com", "stepperonline", "StepperOnline"),
    ("zyltech.com", "zyltech", "Zyltech"),
    ("west3d.com", "west3d", "West3D"),
    ("kb-3d.com", "kb3d", "KB-3D"),
    ("fabreeko.com", "fabreeko", "Fabreeko"),
    ("printedsolid.com", "printedsolid", "Printed Solid"),
    ("matterhackers.com", "matterhackers", "MatterHackers"),
    ("paramount-3d.com", "paramount3d", "Paramount 3D"),
    ("ldomotors.com", "ldo", "LDO Motors"),
    ("meanwell.com", "meanwell", "Mean Well"),
    ("digitmakers.ca", "digitmakers", "Digitmakers"),
    ("3dprintingcanada.com", "3dpc", "3D Printing Canada"),
    ("keenovo.com", "keenovo", "Keenovo"),
    ("duet3d.com", "duet3d", "Duet3D"),
    ("filastruder.com", "filastruder", "Filastruder"),
    ("pif.voron.dev", "pif", "Print It Forward"),
    ("voron.dev", "voron", "Voron Design"),
    ("grainger.com", "grainger", "Grainger"),
    ("zoro.com", "zoro", "Zoro"),
    ("ebay.com", "ebay", "eBay"),
    ("banggood.com", "banggood", "Banggood"),
    ("hobbytown.com", "hobbytown", "HobbyTown"),
    ("wago.com", "wago", "Wago"),
    ("biqu.equipment", "biqu", "BIQU"),
    ("bigtree-tech.com", "btt", "BIGTREETECH"),
    ("mellow3d.com", "mellow", "Mellow"),
    ("fysetc.com", "fysetc", "FYSETC"),
    ("formbot3d.com", "formbot", "Formbot"),
    ("siboor.com", "siboor", "Siboor"),
    ("taobao.com", "taobao", "Taobao"),
    ("tmall.com", "taobao", "Tmall"),
]

AFFILIATE_HOSTS = {"s.click.aliexpress.com", "amzn.to"}
AFFILIATE_PARAMS = {"tag", "aff_platform", "aff_trace_key", "aff_fcid", "af",
                    "aff_fsk", "aff_short_key", "linkCode", "ascsubtag"}


def host_of(url):
    try:
        h = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    if h.startswith("www."):
        h = h[4:]
    return h.split(":")[0]


def vendor_of(url, label=None):
    h = host_of(url or "")
    best = None
    for suffix, vid, disp in VENDOR_HOSTS:
        if h == suffix or h.endswith("." + suffix):
            if best is None or len(suffix) > len(best[0]):
                best = (suffix, vid, disp)
    if best:
        return best[1], best[2]
    if h:
        return h, h
    return None, None


# ---------------------------------------------------------------------- URLs

TRACKING_PARAMS = {
    "spm", "pvid", "gps-id", "scm", "scm-url", "scm_id", "scm-id",
    "ad_pvid", "algo_pvid", "algo_expid", "btsid", "ws_ab_test",
    "curpagelogauid", "curpagelogid", "_randl_currency", "_randl_shipto",
    "srcsns", "businesstype", "templateid", "pageid", "netw", "terminal_id",
    "afref", "sk", "s", "ad_pvid", "aem_p4p_detail", "pdp_npi", "utparam",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "mc_cid", "mc_eid",
    "psc", "keywords", "qid", "sr", "th", "ref", "ref_", "dchild", "smid",
    "pd_rd_i", "pd_rd_r", "pd_rd_w", "pd_rd_wg", "pf_rd_p", "pf_rd_r",
    "linkid", "crid", "sprefix", "ie",
}

_ALI_ITEM = re.compile(r"/item/(?:[^/]*?/)?(?:.*?-)?(\d{6,})\.html", re.I)
# older store URLs: /store/product/<slug>/<storeid>_<itemid>.html
_ALI_STORE = re.compile(r"/store/product/.*?/\d+_(\d{6,})\.html", re.I)
_AMZ_ASIN = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)", re.I)
_MCM_PN = re.compile(r"^/([0-9A-Za-z]+)/?$")


def unwrap(url):
    """Undo Google redirect wrappers that Sheets bakes into exported links."""
    seen = 0
    while url and seen < 4:
        seen += 1
        sp = urlsplit(url)
        if sp.netloc.endswith("google.com") and sp.path.startswith("/url"):
            q = dict(parse_qsl(sp.query))
            nxt = q.get("q") or q.get("url")
            if nxt:
                url = unquote(nxt)
                continue
        break
    return url


def canonical_url(url, keep_www=True):
    """Cleaned, still-clickable form of a link.

    Tracking noise is dropped and the well-known marketplaces are reduced to
    their product-id form so the same product always yields the same string.
    Pass keep_www=False for the dedup key.
    """
    if not url:
        return None
    url = unwrap(url.strip())
    try:
        sp = urlsplit(url)
    except ValueError:
        return url
    if not sp.scheme:
        sp = urlsplit("https://" + url)
    host = sp.netloc.lower().split(":")[0]
    bare = host[4:] if host.startswith("www.") else host
    if not keep_www:
        host = bare
    path, query = sp.path, sp.query

    if "aliexpress" in bare and bare != "s.click.aliexpress.com":
        m = _ALI_ITEM.search(path) or _ALI_STORE.search(path)
        if m:
            return "https://www.aliexpress.com/item/%s.html" % m.group(1)
    if bare in ("amazon.com", "amzn.to") or bare.startswith("amazon."):
        m = _AMZ_ASIN.search(path)
        if m:
            return "https://www.%s/dp/%s" % (bare if bare != "amzn.to" else "amazon.com",
                                             m.group(1).upper())
    if bare == "mcmaster.com":
        m = _MCM_PN.match(path)
        if m:
            return "https://www.mcmaster.com/%s/" % m.group(1).upper()

    keep = [(k, v) for k, v in parse_qsl(query, keep_blank_values=False)
            if k.lower() not in TRACKING_PARAMS]
    keep.sort()
    path = path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(keep), ""))


def url_key(url):
    """Dedup identity for a link -- ignores www. and http/https differences."""
    cu = canonical_url(url, keep_www=False)
    return cu.lower() if cu else None


def is_affiliate(url, label=None):
    if label and re.search(r"affi?li?ate", label, re.I):
        return True
    if not url:
        return False
    url = unwrap(url)
    if host_of(url) in AFFILIATE_HOSTS:
        return True
    q = {k.lower() for k, _ in parse_qsl(urlsplit(url).query)}
    return bool(q & {p.lower() for p in AFFILIATE_PARAMS})


# ---------------------------------------------------------------- quantities

_DATEISH = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T]00:00:00$")


def clean_qty(v):
    """Return (display, numeric_or_None). Sheets mangles ranges into dates."""
    if v is None:
        return None, None
    if isinstance(v, (int, float)):
        n = float(v)
        return (str(int(n)) if n == int(n) else str(n)), n
    s = clean_text(str(v))
    if s is None:
        return None, None
    m = _DATEISH.match(s)
    if m:  # "2022-02-04 00:00:00" was typed as "2-4" (a range)
        return "%d-%d" % (int(m.group(2)), int(m.group(3))), None
    m = re.match(r"^(\d+(?:\.\d+)?)$", s)
    if m:
        n = float(m.group(1))
        return (str(int(n)) if n == int(n) else str(n)), n
    return s, None
