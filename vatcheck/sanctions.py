"""Fuzzy name screening against the combined index (rapidfuzz)."""
import os, re, threading
from rapidfuzz import fuzz, process, utils
from unidecode import unidecode
from . import sanctions_lists as lists

STOP = {"the", "of", "and", "ltd", "llc", "limited", "inc", "co", "company", "corp", "corporation", "sa", "ag", "gmbh",
        "jsc", "ojsc", "pjsc", "ooo", "zao", "oao", "plc", "bv", "nv", "srl", "spa", "kft", "zrt", "sro", "as", "ab", "oy",
        "mr", "mrs", "ms", "dr", "al", "el", "bin", "ibn"}


def norm(s):
    s = unidecode(s or "").lower().replace(".", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(t for t in s.split() if t not in STOP) or s.strip()


class Screener:
    def __init__(self, index):
        self.index = index
        self.ents = index["entities"]
        self.keys, self.owner = [], []
        for i, e in enumerate(self.ents):
            for n in [e["name"]] + e["aliases"]:
                k = norm(n)
                if len(k) >= 2: self.keys.append(k); self.owner.append(i)

    @staticmethod
    def _score(q, k, **_):
        if not q or not k: return 0
        ts = fuzz.token_sort_ratio(q, k)
        nq, nk = len(q.split()), len(k.split())
        sub = 0
        if nk > nq:      # listed name is longer: all query tokens present -> small penalty per extra token
            sub = fuzz.token_set_ratio(q, k) - min(4 * (nk - nq), 12)
        elif nq > nk >= 2:  # listed name is a multi-token subset of the query
            sub = fuzz.token_set_ratio(q, k) - 8 * (nq - nk)
        return max(ts, sub, fuzz.ratio(q, k))

    def screen(self, name, threshold=85, types=None, lists_=None, birth_year=None, country=None, limit=10):
        q = norm(name)
        if not q: return []
        hits = process.extract(q, self.keys, scorer=self._score, score_cutoff=max(50, threshold - 15), limit=400)
        best = {}
        for key, sc, j in hits:
            i = self.owner[j]; e = self.ents[i]
            if types and e["type"] not in types: continue
            if lists_ and e["list"] not in lists_: continue
            adj, why = sc, []
            if birth_year and e["dob"]:
                if any(str(birth_year) in d for d in e["dob"]): adj += 5; why.append("birth year matches")
                else: adj -= 15; why.append("birth year differs")
            if country and e["nationality"]:
                c = norm(country)
                if any(c in norm(n) or norm(n) in c for n in e["nationality"]): adj += 3; why.append("nationality matches")
            adj = max(0, min(100, round(adj, 1)))
            if adj < threshold: continue
            if i not in best or adj > best[i][0]: best[i] = (adj, key, why)
        out = []
        for i, (sc, key, why) in sorted(best.items(), key=lambda x: -x[1][0])[:limit]:
            e = self.ents[i]
            out.append({"score": sc, "matched_name": key, "list": e["list"], "list_name": lists.LIST_NAMES.get(e["list"]),
                        "list_id": e["id"], "sanctioned_name": e["name"], "type": e["type"], "aliases": e["aliases"][:10],
                        "programs": e["programs"], "dob": e["dob"][:5], "nationality": e["nationality"][:5],
                        "listed_on": e["listed_on"], "remarks": e["remarks"], "score_notes": why})
        return out


_lock, _screener = threading.Lock(), None


def get_screener(max_age_hours=26, path=lists.INDEX_PATH):
    """Load the cached index; refresh from the official sources if missing or stale."""
    global _screener
    with _lock:
        if _screener and lists.age_hours(_screener.index) < max_age_hours: return _screener
        ix = None
        try:
            ix = lists.load(path)
        except (OSError, ValueError):
            pass
        # SANCTIONS_AUTO_REFRESH=0: never download at runtime (a fresh build peaks ~630MB RSS; Render free = 512MB).
        # The index is then built at deploy time and refreshed by redeploying.
        no_refresh = ix is not None and os.getenv("SANCTIONS_AUTO_REFRESH", "1") == "0"
        if not no_refresh and (ix is None or lists.age_hours(ix) >= max_age_hours):
            fresh = lists.download_all()
            if all(m["entries"] > 0 for m in fresh["lists"].values()) or ix is None:
                ix = fresh
                try: lists.save(ix, path)
                except OSError: pass
        _screener = Screener(ix)
        return _screener


def screen_names(names, threshold=85, **kw):
    s = get_screener()
    res = []
    for n in names:
        m = s.screen(n, threshold, **kw)
        res.append({"query": n, "status": "potential_match" if m else "no_match", "top_score": m[0]["score"] if m else 0,
                    "match_count": len(m), "matches": m, "lists_checked": list(s.index["lists"].keys()),
                    "lists_date": s.index["built_at"]})
    return res
