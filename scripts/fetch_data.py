#!/usr/bin/env python3
"""Refresh data/contributions.json from the GitHub GraphQL API.

Run by the scheduled Action. Needs GH_TOKEN. The default Actions token only sees public
contributions; add a PROFILE_TOKEN secret (a classic PAT with read:user) to include private ones.
"""
import json, os, sys, urllib.request
from pathlib import Path

USER = os.environ.get("PROFILE_USER", "riokishanea")
QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    contributionsCollection { contributionCalendar { totalContributions
      weeks { contributionDays { date contributionCount } } } }
    pinnedItems(first: 6, types: REPOSITORY) { nodes { ... on Repository {
      name description url stargazerCount forkCount primaryLanguage { name color } } } }
  }
}"""

req = urllib.request.Request(
    "https://api.github.com/graphql",
    data=json.dumps({"query": QUERY, "variables": {"login": USER}}).encode(),
    headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=60) as r:
    payload = json.load(r)
if payload.get("errors") or not payload.get("data", {}).get("user"):
    sys.exit(f"GraphQL error: {payload.get('errors') or 'user not found'}")

u = payload["data"]["user"]
cal = u["contributionsCollection"]["contributionCalendar"]
out = {"login": u["login"], "total": cal["totalContributions"], "weeks": cal["weeks"],
       "pinned": u["pinnedItems"]["nodes"]}
path = Path(__file__).resolve().parent.parent / "data" / "contributions.json"
path.parent.mkdir(exist_ok=True)
path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
print(f"wrote {path} ({cal['totalContributions']} contributions, {len(out['pinned'])} pinned repos)")
