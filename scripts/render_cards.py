#!/usr/bin/env python3
"""Render the profile README as a stack of animated SVG cards.

    python scripts/render_cards.py             # animated cards  -> cards/*.svg + README.md
    STATIC=1 python scripts/render_cards.py    # final frame only (for screenshots)

The cards are displayed through <img> tags, so everything here is self-contained:
an inline <style> block with CSS keyframes, SMIL <animate> elements for purely
decorative loops, and fonts subset with pyftsubset and embedded as base64 WOFF.
Every CSS animation is switched off under prefers-reduced-motion and the SMIL
layer is hidden, so those readers see the final frame.

Needs: pip install fonttools   (Pillow is optional; it shrinks the portrait)
Reads: data/contributions.json (refreshed daily by the GitHub Action)
"""
from __future__ import annotations

import base64
import colorsys
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

# --------------------------------------------------------------------------- #
# Configuration: edit this block
# --------------------------------------------------------------------------- #
USER = os.environ.get("PROFILE_USER", "riokishanea")
NAME = "Riokishan EA"
TAGLINE = "CS student and freelance web developer building with Next.js, TypeScript and Tailwind CSS."

# One colour drives the whole palette (background, borders, text tints, graph).
BRAND = os.environ.get("BRAND", "#8b7cff")

# Three tiles. Replace the TODO links before you push.
TILES = [
    dict(label="Projects", sub="All my repositories", icon="repo",
         href=f"https://github.com/{USER}?tab=repositories"),
    dict(label="LinkedIn", sub="Let's connect", icon="link",
         href="https://www.linkedin.com/in/linkriokishanea"),
    dict(label="Freelance", sub="Work with me", icon="mail",
         href="mailto:workflow.riokishan@gmail.com"),
]

FEATURED: list[str] = []   # repo names to show, in order. Empty = your pinned repos.
MAX_REPOS = 6

# Optional portrait: drop assets/portrait.(png|jpg|jpeg|webp) in the repo.
PORTRAIT_GLOBS = ("portrait.png", "portrait.jpg", "portrait.jpeg", "portrait.webp")

# Fonts are variable TTFs pinned to one weight each, then subset per card.
GF = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
FONT_FILES = {
    "Inter.ttf": GF + urllib.parse.quote("inter/Inter[opsz,wght].ttf"),
    "JetBrainsMono.ttf": GF + urllib.parse.quote("jetbrainsmono/JetBrainsMono[wght].ttf"),
}
FONTS = {  # key: (file, family, weight, pinned axes)
    "sans":  ("Inter.ttf", "RioSans", 400, {"wght": 400, "opsz": 14}),
    "sansb": ("Inter.ttf", "RioSans", 700, {"wght": 700, "opsz": 14}),
    "disp":  ("Inter.ttf", "RioSans", 800, {"wght": 800, "opsz": 32}),
    "mono":  ("JetBrainsMono.ttf", "RioMono", 400, {"wght": 400}),
    "monob": ("JetBrainsMono.ttf", "RioMono", 700, {"wght": 700}),
}
FALLBACK = {"RioSans": "Inter,system-ui,-apple-system,Segoe UI,sans-serif",
            "RioMono": "'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace"}

# --------------------------------------------------------------------------- #
# Paths, mode, palette
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "contributions.json"
OUT = ROOT / "cards"
FONT_DIR = ROOT / "fonts"
CACHE = ROOT / ".fontcache"
STATIC = os.environ.get("STATIC") == "1"
W = 880


def _rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(r, g, b):
    return "#%02x%02x%02x" % tuple(round(max(0, min(1, v)) * 255) for v in (r, g, b))


def _hsl(h, s, l):
    return _hex(*colorsys.hls_to_rgb(h, l, s))


def mix(a: str, b: str, t: float) -> str:
    return _hex(*[x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b))])


_bh, _bl, _bs = colorsys.rgb_to_hls(*_rgb(BRAND))
C = dict(
    brand=BRAND,
    bg=_hsl(_bh, .38, .075),
    surface=_hsl(_bh, .32, .12),
    border=_hsl(_bh, .28, .21),
    cell0=_hsl(_bh, .26, .17),
    text=_hsl(_bh, .6, .96),
    muted=_hsl(_bh, .16, .68),
    faint=_hsl(_bh, .14, .46),
    ok="#3ddc97",
)

# --------------------------------------------------------------------------- #
# Fonts: pin, measure, subset
# --------------------------------------------------------------------------- #
class Font:
    def __init__(self, key: str):
        fname, self.family, self.weight, axes = FONTS[key]
        self.key = key
        src = FONT_DIR / fname
        if not src.exists():
            FONT_DIR.mkdir(exist_ok=True)
            print(f"downloading {fname} ...")
            urllib.request.urlretrieve(FONT_FILES[fname], src)
        CACHE.mkdir(exist_ok=True)
        self.path = CACHE / f"{key}.ttf"
        if not self.path.exists() or self.path.stat().st_mtime < src.stat().st_mtime:
            f = TTFont(src)
            if "fvar" in f:
                have = [a.axisTag for a in f["fvar"].axes]
                f = instancer.instantiateVariableFont(
                    f, {t: axes.get(t) for t in have}, inplace=False)
            f.save(self.path)
        f = TTFont(self.path)
        self.upm = f["head"].unitsPerEm
        self.cmap = f.getBestCmap()
        self.hmtx = f["hmtx"]

    def width(self, s: str, size: float) -> float:
        total = 0
        for ch in s:
            g = self.cmap.get(ord(ch))
            if g is None:
                print(f"warning: {ch!r} (U+{ord(ch):04X}) is not in {self.key}", file=sys.stderr)
                g = ".notdef"
            total += self.hmtx[g][0]
        return total * size / self.upm

    def woff_b64(self, chars: set[str]) -> str:
        text = "".join(sorted(chars | {" "}))
        tag = hashlib.sha1((self.key + text).encode("utf-8")).hexdigest()[:12]
        out = CACHE / f"{self.key}-{tag}.woff"
        if not out.exists():
            txt = CACHE / f"{tag}.txt"
            txt.write_text(text, encoding="utf-8")
            tool = shutil.which("pyftsubset")
            cmd = [tool] if tool else [sys.executable, "-m", "fontTools.subset"]
            cmd += [str(self.path), f"--text-file={txt}", f"--output-file={out}",
                    "--flavor=woff", "--no-hinting",
                    "--layout-features=kern,liga,ccmp,locl,mark,mkmk,tnum"]
            subprocess.run(cmd, check=True)
        return base64.b64encode(out.read_bytes()).decode("ascii")


_fonts: dict[str, Font] = {}


def font(key: str) -> Font:
    if key not in _fonts:
        _fonts[key] = Font(key)
    return _fonts[key]


def wrap(s: str, key: str, size: float, maxw: float, maxlines: int) -> list[str]:
    words, lines, cur = s.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if font(key).width(trial, size) <= maxw or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        last = lines[-1]
        while last and font(key).width(last + "…", size) > maxw:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines


# --------------------------------------------------------------------------- #
# SVG card builder
# --------------------------------------------------------------------------- #
CSS_ANIM = """
.a{animation-duration:.8s;animation-timing-function:cubic-bezier(.22,.8,.24,1);animation-fill-mode:both;transform-box:fill-box}
.rise{animation-name:rise}.fade{animation-name:fade}
.pop{animation-name:pop;transform-origin:50% 50%}
.grow{animation-name:grow;transform-origin:50% 100%}
.sweep{animation-name:sweep;transform-origin:0 50%}
.draw{animation-name:draw;stroke-dasharray:1;animation-duration:1.1s}
@keyframes rise{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes pop{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:none}}
@keyframes grow{from{transform:scaleY(0)}to{transform:scaleY(1)}}
@keyframes sweep{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes draw{0%{stroke-dashoffset:1;opacity:0}6%{opacity:1}100%{stroke-dashoffset:0;opacity:1}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.live{display:none}}
"""


class Card:
    def __init__(self, name: str, h: int, delay: float, label: str, w: int = W):
        self.name, self.w, self.h, self.delay, self.label = name, w, h, delay, label
        self.parts: list[str] = []
        self.defs: list[str] = []
        self.used: dict[str, set[str]] = {}

    # -- primitives ------------------------------------------------------- #
    def add(self, s: str):
        self.parts.append(s)

    def text(self, x, y, s, key="sans", size=14, fill=None, anchor=None, spacing=None) -> str:
        self.used.setdefault(key, set()).update(s)
        extra = (f' text-anchor="{anchor}"' if anchor else "") + \
                (f' letter-spacing="{spacing}"' if spacing else "")
        return (f'<text x="{x:.1f}" y="{y:.1f}" class="f-{key}" font-size="{size}" '
                f'fill="{fill or C["text"]}"{extra}>{escape(s)}</text>')

    def an(self, kind: str, d: float, inner: str, dur: float | None = None) -> str:
        if STATIC:
            return inner
        dd = f";animation-duration:{dur}s" if dur else ""
        return f'<g class="a {kind}" style="animation-delay:{self.delay + d:.2f}s{dd}">{inner}</g>'

    def draw_path(self, d_attr: str, d: float, stroke: str, width=2, extra="") -> str:
        cls = "" if STATIC else f' class="a draw" style="animation-delay:{self.delay + d:.2f}s"'
        return (f'<path d="{d_attr}" pathLength="1" fill="none" stroke="{stroke}" stroke-width="{width}" '
                f'stroke-linecap="round" stroke-linejoin="round"{cls}{extra}/>')

    @staticmethod
    def live(inner: str) -> str:
        """Decorative SMIL layer: absent from STATIC output, hidden under reduced motion."""
        return "" if STATIC else f'<g class="live">{inner}</g>'

    def background(self, rx=18):
        self.defs.append(f'<clipPath id="r"><rect width="{self.w}" height="{self.h}" rx="{rx}"/></clipPath>')
        self.add(f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="{rx}" '
                 f'fill="{C["bg"]}" stroke="{C["border"]}"/>')

    # -- output ----------------------------------------------------------- #
    def svg(self) -> str:
        faces, classes = [], []
        for key, chars in sorted(self.used.items()):
            f = font(key)
            faces.append(f"@font-face{{font-family:'{f.family}';font-weight:{f.weight};font-style:normal;"
                         f"src:url(data:font/woff;base64,{f.woff_b64(chars)}) format('woff')}}")
            classes.append(f".f-{key}{{font-family:'{f.family}',{FALLBACK[f.family]};font-weight:{f.weight}}}")
        css = "".join(faces) + "\n" + "\n".join(classes) + ("" if STATIC else CSS_ANIM)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" role="img" '
                f'aria-label="{escape(self.label, {chr(34): "&quot;"})}">'
                f'<title>{escape(self.label)}</title><defs><style>{css}</style>{"".join(self.defs)}</defs>'
                f'{"".join(self.parts)}</svg>\n')


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _walk_days(o, days: dict[dt.date, int]):
    if isinstance(o, dict):
        d = o.get("date") or o.get("day")
        c = o.get("contributionCount", o.get("count"))
        if isinstance(d, str) and ISO.match(d[:10]) and isinstance(c, int):
            days[dt.date.fromisoformat(d[:10])] = c
            return
        for k, v in o.items():
            if isinstance(v, int) and ISO.match(k):
                days[dt.date.fromisoformat(k)] = v
            else:
                _walk_days(v, days)
    elif isinstance(o, list):
        for v in o:
            _walk_days(v, days)


def _find(o, key):
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = _find(v, key)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def _repo(r: dict) -> dict:
    lang = r.get("primaryLanguage") or r.get("language")
    lname, lcolor = (lang.get("name"), lang.get("color")) if isinstance(lang, dict) else (lang, r.get("languageColor"))
    return dict(name=r["name"], desc=(r.get("description") or "").strip(),
                url=r.get("url") or f"https://github.com/{USER}/{r['name']}",
                stars=int(r.get("stargazerCount", r.get("stars", 0)) or 0),
                forks=int(r.get("forkCount", r.get("forks", 0)) or 0),
                lang=lname, color=lcolor or C["brand"])


def load_data():
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    days: dict[dt.date, int] = {}
    _walk_days(raw, days)
    if not days:
        sys.exit(f"no contribution days found in {DATA_FILE}")
    last = max(days)
    start = last - dt.timedelta(days=364)
    window = {d: c for d, c in days.items() if start <= d <= last}
    repos_raw = raw.get("repos") or raw.get("pinned") or _find(raw, "nodes") or []
    repos = [_repo(r) for r in repos_raw if isinstance(r, dict) and "name" in r]
    if FEATURED:
        order = {n: i for i, n in enumerate(FEATURED)}
        repos = sorted((r for r in repos if r["name"] in order), key=lambda r: order[r["name"]])
    return window, last, repos[:MAX_REPOS]


def fmt_date(d: dt.date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


def n(x: int) -> str:
    return f"{x:,}"


# --------------------------------------------------------------------------- #
# Icons (24x24, stroke based)
# --------------------------------------------------------------------------- #
ICONS = {
    "repo": ["M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"],
    "link": ["M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71",
             "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"],
    "mail": ["M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
             "m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"],
}


def icon(c: Card, name: str, x, y, scale, d, stroke):
    paths = "".join(c.draw_path(p, d, stroke, 2) for p in ICONS[name])
    return f'<g transform="translate({x} {y}) scale({scale})">{paths}</g>'


def star_points(cx, cy, r):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.42
        pts.append(f"{cx + rr * math.cos(a):.1f},{cy + rr * math.sin(a):.1f}")
    return " ".join(pts)


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
def find_portrait():
    for g in PORTRAIT_GLOBS:
        p = ROOT / "assets" / g
        if p.exists():
            return p
    return None


def portrait_uri(p: Path) -> str:
    try:
        from PIL import Image, ImageOps  # optional
        import io
        im = ImageOps.fit(Image.open(p).convert("RGB"), (360, 360))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=86, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        mime = {".png": "image/png", ".webp": "image/webp"}.get(p.suffix.lower(), "image/jpeg")
        return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def hero(delay: float) -> Card:
    H = 300
    c = Card("hero", H, delay, f"{NAME}. {TAGLINE}")
    c.defs.append(f'<radialGradient id="glow"><stop offset="0" stop-color="{C["brand"]}" stop-opacity=".42"/>'
                  f'<stop offset="1" stop-color="{C["brand"]}" stop-opacity="0"/></radialGradient>')
    c.defs.append(f'<pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse">'
                  f'<circle cx="1.5" cy="1.5" r="1.1" fill="{C["border"]}"/></pattern>')
    c.background()
    c.add('<g clip-path="url(#r)">'
          f'<rect width="{W}" height="{H}" fill="url(#dots)" opacity=".6"/>'
          f'<circle cx="{W - 140}" cy="40" r="300" fill="url(#glow)"/>'
          f'<circle cx="0" cy="{H + 20}" r="230" fill="url(#glow)" opacity=".5"/></g>')

    portrait = find_portrait()
    x0, maxw = 48, (520 if portrait else 720)

    c.add(c.an("rise", .10, c.text(x0, 104, f"hi, I'm", "mono", 16, C["brand"])))
    size = 80
    nw = font("disp").width(NAME, size)
    if nw > maxw:
        size = size * maxw / nw
        nw = maxw
    c.add(c.an("rise", .25, c.text(x0, 104 + 76, NAME, "disp", round(size, 1), C["text"], spacing="-1.5")))
    c.add(c.live(f'<rect x="{x0 + nw + 12:.1f}" y="{104 + 76 - size * .72:.1f}" width="6" height="{size * .72:.1f}" '
                 f'rx="1.5" fill="{C["brand"]}" opacity="0"><animate attributeName="opacity" values="1;0;1" '
                 f'keyTimes="0;.5;1" calcMode="discrete" dur="1.2s" begin="1s" repeatCount="indefinite"/></rect>'))
    c.add(c.an("sweep", .45, f'<rect x="{x0}" y="196" width="64" height="4" rx="2" fill="{C["brand"]}"/>'))
    lines = wrap(TAGLINE, "sans", 20, maxw, 2)
    for i, ln in enumerate(lines):
        c.add(c.an("rise", .6 + i * .12, c.text(x0, 238 + i * 28, ln, "sans", 20, C["muted"])))

    if portrait:
        cx, cy, R = W - 48 - 96, H / 2, 88
        c.defs.append(f'<clipPath id="pc"><circle cx="{cx}" cy="{cy}" r="{R}"/></clipPath>')
        img = (f'<image xlink:href="{portrait_uri(portrait)}" x="{cx - R}" y="{cy - R}" width="{2 * R}" '
               f'height="{2 * R}" preserveAspectRatio="xMidYMid slice" clip-path="url(#pc)"/>')
        c.add(c.an("pop", .35, img))
        ring = (f'<circle cx="{cx}" cy="{cy}" r="{R + 8}" fill="none" stroke="{C["brand"]}" stroke-width="3" '
                f'stroke-linecap="round" pathLength="1" transform="rotate(-90 {cx} {cy})"'
                + ("" if STATIC else f' class="a draw" style="animation-delay:{c.delay + .5:.2f}s"') + "/>")
        c.add(ring)
        c.add(c.live(f'<circle cx="{cx}" cy="{cy}" r="{R + 8}" fill="none" stroke="{C["brand"]}" stroke-width="2" stroke-opacity="0">'
                     f'<animate attributeName="r" values="{R + 8};{R + 36}" dur="2.8s" begin="1.6s" repeatCount="indefinite"/>'
                     f'<animate attributeName="stroke-opacity" values=".55;0" dur="2.8s" begin="1.6s" repeatCount="indefinite"/></circle>'))
    return c


def tile(i: int, t: dict, delay: float) -> Card:
    w, h = 300, 132
    c = Card(f"tile-{i}", h, delay + i * .14, f"{t['label']}: {t['sub']}", w=w)
    c.background(16)
    c.add(f'<g clip-path="url(#r)"><circle cx="{w}" cy="0" r="120" fill="{C["brand"]}" opacity=".10"/></g>')
    c.add(c.an("pop", .05, f'<rect x="22" y="20" width="44" height="44" rx="12" fill="{C["surface"]}" '
                           f'stroke="{C["border"]}"/>'))
    c.add(icon(c, t["icon"], 32, 30, .98, .25, C["brand"]))
    c.add(c.an("fade", .35, f'<g transform="translate({w - 46} 20)">'
               + c.draw_path("M7 17 17 7", .5, C["muted"]) + c.draw_path("M8 7h9v9", .6, C["muted"]) + "</g>"))
    c.add(c.an("rise", .20, c.text(24, 96, t["label"], "sansb", 22, C["text"])))
    c.add(c.an("rise", .30, c.text(24, 117, t["sub"], "mono", 12.5, C["muted"])))
    return c


def activity(window: dict[dt.date, int], last: dt.date, delay: float) -> Card:
    H = 350
    series = sorted(window.items())
    total = sum(window.values())
    # streaks
    run = best = 0
    for _, v in series:
        run = run + 1 if v > 0 else 0
        best = max(best, run)
    i = len(series) - 1
    if i >= 0 and series[i][1] == 0:
        i -= 1                       # today may still be in progress
    cur = 0
    while i >= 0 and series[i][1] > 0:
        cur += 1
        i -= 1
    active = sum(1 for _, v in series if v > 0)
    bd, bc = max(series, key=lambda kv: (kv[1], kv[0]))

    months, y, m = [], last.year, last.month
    for k in range(11, -1, -1):
        mm, yy = m - k, y
        while mm <= 0:
            mm += 12
            yy -= 1
        months.append((yy, mm))
    totals = {ym: 0 for ym in months}
    for d, v in window.items():
        if (d.year, d.month) in totals:
            totals[(d.year, d.month)] += v
    vals = [totals[ym] for ym in months]

    first_label = dt.date(*months[0], 1).strftime("%b %Y")
    c = Card("activity", H, delay,
             f"Contributions per month, {first_label} to {last.strftime('%b %Y')}. "
             f"Current streak {cur} {'day' if cur == 1 else 'days'}, {active} active days, busiest day {bc} contributions on {fmt_date(bd)}.")
    c.background()
    c.add(c.an("rise", .0, c.text(32, 46, "Contributions per month", "sansb", 19)))
    c.add(c.an("fade", .1, c.text(W - 32, 46, f"{first_label} – {last.strftime('%b %Y')}  ·  {n(sum(vals))} total",
                                  "mono", 12, C["muted"], "end")))

    stats = [("CURRENT STREAK", f"{n(cur)} {'day' if cur == 1 else 'days'}", f"longest {n(best)}"),
             ("ACTIVE DAYS", n(active), f"of {len(series)} days"),
             ("BUSIEST DAY", n(bc), fmt_date(bd))]
    gap = 16
    bw = (W - 64 - 2 * gap) / 3
    for k, (lab, val, sub) in enumerate(stats):
        x = 32 + k * (bw + gap)
        inner = (f'<rect x="{x:.1f}" y="68" width="{bw:.1f}" height="80" rx="12" fill="{C["surface"]}" stroke="{C["border"]}"/>'
                 f'<circle cx="{x + 20:.1f}" cy="89" r="3.5" fill="{C["brand"]}"/>'
                 + c.text(x + 31, 93, lab, "monob", 10.5, C["muted"], spacing="1")
                 + c.text(x + 20, 128, val, "disp", 28, C["text"])
                 + c.text(x + bw - 20, 128, sub, "mono", 12, C["muted"], "end"))
        c.add(c.an("rise", .15 + k * .12, inner))

    # bar chart
    x_l, x_r, y_top, y_bot = 32 + 34, W - 32, 190, 304
    peak = max(vals) or 0
    mag = 10 ** math.floor(math.log10(peak)) if peak > 0 else 1
    top = next((f * mag for f in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10) if peak <= f * mag), 10 * mag) if peak else 4
    top = int(math.ceil(top / 2) * 2)
    for frac in (0, .5, 1):
        yy = y_bot - (y_bot - y_top) * frac
        dash = "" if frac == 0 else ' stroke-dasharray="3 5"'
        c.add(c.an("fade", .35, f'<line x1="{x_l}" x2="{x_r}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{C["border"]}"{dash}/>'
                   + c.text(x_l - 10, yy + 3.5, n(int(top * frac)), "mono", 10.5, C["faint"], "end")))
    c.defs.append(f'<linearGradient id="bar" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{C["brand"]}"/>'
                  f'<stop offset="1" stop-color="{mix(C["brand"], C["bg"], .55)}"/></linearGradient>')
    slot = (x_r - x_l) / 12
    bwid = slot * .56
    peak_i = vals.index(max(vals)) if peak else -1
    for k, (ym, v) in enumerate(zip(months, vals)):
        cx = x_l + slot * (k + .5)
        hh = (y_bot - y_top) * v / top
        d = .45 + k * .06
        if v > 0:
            c.add(c.an("grow", d, f'<rect x="{cx - bwid / 2:.1f}" y="{y_bot - hh:.1f}" width="{bwid:.1f}" '
                                  f'height="{max(hh, 2):.1f}" rx="6" fill="url(#bar)" opacity="{1 if k == peak_i else .78}"/>', .9))
        else:
            c.add(c.an("fade", d, f'<rect x="{cx - bwid / 2:.1f}" y="{y_bot - 3}" width="{bwid:.1f}" height="3" rx="1.5" fill="{C["border"]}"/>'))
        c.add(c.an("fade", d + .5, c.text(cx, y_bot - hh - 8, n(v), "monob" if k == peak_i else "mono", 11,
                                           C["brand"] if k == peak_i else C["muted"], "middle")))
        c.add(c.an("fade", .5, c.text(cx, y_bot + 22, dt.date(*ym, 1).strftime("%b"), "mono", 11.5, C["muted"], "middle")))
    return c


def repo_card(i: int, r: dict, delay: float) -> Card:
    H = 132
    c = Card(f"repo-{i}", H, delay, f"{r['name']}: {r['desc'] or 'repository'}"
             + (f" ({r['lang']}, {r['stars']} stars)" if r["lang"] else ""))
    c.background()
    c.add('<g clip-path="url(#r)">' + c.an("grow", .1, f'<rect x="0" y="0" width="5" height="{H}" fill="{C["brand"]}"/>') + "</g>")
    c.add(icon(c, "repo", 30, 22, .8, .2, C["muted"]))
    c.add(c.an("rise", .15, c.text(60, 40, r["name"], "sansb", 20, C["brand"])))
    c.add(c.an("fade", .35, f'<g transform="translate({W - 52} 20)">' + c.draw_path("M7 17 17 7", .5, C["muted"])
               + c.draw_path("M8 7h9v9", .6, C["muted"]) + "</g>"))
    desc = wrap(r["desc"] or "No description yet.", "sans", 14.5, W - 60 - 80, 2)
    for k, ln in enumerate(desc):
        c.add(c.an("rise", .25 + k * .1, c.text(32, 70 + k * 21, ln, "sans", 14.5, C["muted"])))
    x, y = 32, 116
    meta = ""
    if r["lang"]:
        meta += f'<circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{r["color"]}"/>' + c.text(x + 16, y, r["lang"], "mono", 12, C["text"])
        x += 16 + font("mono").width(r["lang"], 12) + 24
    meta += f'<polygon points="{star_points(x + 6, y - 4.5, 7)}" fill="none" stroke="{C["muted"]}" stroke-width="1.4" stroke-linejoin="round"/>'
    meta += c.text(x + 18, y, n(r["stars"]), "mono", 12, C["muted"])
    x += 18 + font("mono").width(n(r["stars"]), 12) + 22
    meta += (f'<g transform="translate({x} {y - 12}) scale(.62)" fill="none" stroke="{C["muted"]}" stroke-width="2.3" stroke-linecap="round">'
             f'<circle cx="6" cy="5" r="3"/><circle cx="18" cy="5" r="3"/><circle cx="12" cy="20" r="3"/>'
             f'<path d="M6 8v2a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V8M12 13v4"/></g>')
    meta += c.text(x + 18, y, n(r["forks"]), "mono", 12, C["muted"])
    c.add(c.an("fade", .5, meta))
    return c


def graph(window: dict[dt.date, int], last: dt.date, delay: float) -> Card:
    H = 232
    start = last - dt.timedelta(days=364)
    first_sun = start - dt.timedelta(days=(start.weekday() + 1) % 7)
    ncols = (last - first_sun).days // 7 + 1
    total = sum(window.values())
    nz = sorted(v for v in window.values() if v > 0)

    def q(p):
        return nz[min(len(nz) - 1, int(p * len(nz)))] if nz else 0
    t1, t2, t3 = q(.25), q(.5), q(.75)
    levels = [C["cell0"]] + [mix(C["cell0"], C["brand"], t) for t in (.32, .58, .8, 1)]

    c = Card("graph", H, delay, f"Contribution graph: {n(total)} contributions in the last year")
    c.background()
    c.add(c.an("rise", 0, c.text(32, 44, "Contribution graph", "sansb", 19)))
    c.add(c.an("fade", .1, c.text(W - 32, 44, f"{n(total)} contributions in the last year", "mono", 12, C["muted"], "end")))
    gx, gy = 32 + 30, 88
    p = (W - 32 - gx) / ncols
    cell = p - 3
    for row, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.add(c.an("fade", .2, c.text(32, gy + row * p + cell - 1.5, lab, "mono", 10, C["faint"])))
    seen = -1
    for col in range(ncols):
        wk_start = first_sun + dt.timedelta(days=7 * col)
        # month label where the month changes (first column whose Sunday starts a new month)
        mo = wk_start.month
        if mo != seen and (col == 0 and wk_start.day <= 7 or col > 0):
            if col <= ncols - 3 or wk_start.day <= 7:
                c.add(c.an("fade", .2, c.text(gx + col * p, gy - 12, wk_start.strftime("%b"), "mono", 10.5, C["muted"])))
            seen = mo
        cells = []
        for row in range(7):
            d = wk_start + dt.timedelta(days=row)
            if d < start or d > last:
                continue
            v = window.get(d, 0)
            lv = 0 if v == 0 else 1 + (v > t1) + (v > t2) + (v > t3)
            cells.append(f'<rect x="{gx + col * p:.2f}" y="{gy + row * p:.2f}" width="{cell:.2f}" height="{cell:.2f}" '
                         f'rx="2.6" fill="{levels[lv]}"/>')
        c.add(c.an("fade", .3 + col * .014, "".join(cells), .5))
    lcol, lrow = (last - first_sun).days // 7, (last.weekday() + 1) % 7
    c.add(c.live(f'<rect x="{gx + lcol * p - 1.5:.2f}" y="{gy + lrow * p - 1.5:.2f}" width="{cell + 3:.2f}" height="{cell + 3:.2f}" '
                 f'rx="3.6" fill="none" stroke="{C["text"]}" stroke-width="1.4" stroke-opacity="0"><animate attributeName="stroke-opacity" '
                 f'values=".9;.1;.9" dur="2s" begin="1.8s" repeatCount="indefinite"/></rect>'))
    ly = gy + 7 * p + 22
    lx = W - 32 - font("mono").width("Less", 10.5) - 8 - 5 * (cell + 3) - 6 - font("mono").width("More", 10.5)
    leg = c.text(lx, ly, "Less", "mono", 10.5, C["muted"])
    lx2 = lx + font("mono").width("Less", 10.5) + 8
    for k, col_ in enumerate(levels):
        leg += f'<rect x="{lx2 + k * (cell + 3):.1f}" y="{ly - cell + 1.5:.1f}" width="{cell:.1f}" height="{cell:.1f}" rx="2.6" fill="{col_}"/>'
    leg += c.text(lx2 + 5 * (cell + 3) + 6, ly, "More", "mono", 10.5, C["muted"])
    c.add(c.an("fade", 1.0, leg))
    return c


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    window, last, repos = load_data()
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()

    # Page plays top to bottom: each card starts after the one above it.
    d_hero, d_tiles, d_chart, d_repos = 0.0, 0.9, 1.7, 2.6
    gap = .4
    d_graph = d_repos + len(repos) * gap + .2
    cards = [hero(d_hero)]
    cards += [tile(i, t, d_tiles) for i, t in enumerate(TILES)]
    cards.append(activity(window, last, d_chart))
    cards += [repo_card(i + 1, r, d_repos + i * gap) for i, r in enumerate(repos)]
    cards.append(graph(window, last, d_graph))

    for card in cards:
        out = OUT / f"{card.name}.svg"
        out.write_text(card.svg(), encoding="utf-8")
        print(f"  {out.relative_to(ROOT)}  {out.stat().st_size / 1024:.1f} KB")

    # README that stacks the cards
    def img(c, attrs=""):
        alt = escape(c.label, {'"': "&quot;"})
        return f'<img src="cards/{c.name}.svg" alt="{alt}" {attrs}/>'

    full, third = 'width="100%"', 'width="32.5%"'
    byname = {c.name: c for c in cards}
    rd = ['<div align="center">', "", "<p>" + img(byname["hero"], full) + "</p>", "", "<p>"]
    for i, t in enumerate(TILES):
        rd.append('<a href="%s">%s</a>' % (escape(t["href"], {'"': "&quot;"}), img(byname["tile-%d" % i], third)))
    rd += ["</p>", "", "<p>" + img(byname["activity"], full) + "</p>", ""]
    for i, r in enumerate(repos):
        rd.append('<p><a href="%s">%s</a></p>' % (escape(r["url"], {'"': "&quot;"}), img(byname["repo-%d" % (i + 1)], full)))
    rd += ["", "<p>" + img(byname["graph"], full) + "</p>", "", "</div>", ""]
    (ROOT / "README.md").write_text("\n".join(rd), encoding="utf-8")

    todo = [t["href"] for t in TILES if "TODO" in t["href"]]
    print(("STATIC " if STATIC else "") + f"rendered {len(cards)} cards for {USER} (last data day {last})")
    if todo:
        print("warning: replace the TODO links in TILES:", *todo, sep="\n  ", file=sys.stderr)


if __name__ == "__main__":
    main()
