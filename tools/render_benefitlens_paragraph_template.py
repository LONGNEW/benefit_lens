"""Preview editorial writing structures without invoking a model or writing to DB.

Inputs are slot values selected by a reviewer. This tool checks structure and
renders a preview. Official scope, AI policy, and factual approval remain in the
writing handoff. This command cannot certify a submission.
"""
import argparse
import json
from pathlib import Path
import re


CATALOGUE = Path(__file__).resolve().parents[1] / "pipeline/config/paragraph_writing_templates_v1.json"
SLOT = re.compile(r"\{\{([a-z][a-z_0-9]*)\}\}")
PARTICLE = re.compile(SLOT.pattern + r"([을를이가은는]|으로)(?=[\s.,!?]|$)")
PARTICLE_PAIRS = {"을": ("을", "를"), "를": ("을", "를"),
                  "이": ("이", "가"), "가": ("이", "가"),
                  "은": ("은", "는"), "는": ("은", "는")}


def sentences(paragraph):
    return [paragraph["opening"], *paragraph["body"], paragraph["closing"]]


def load_catalogue(path=CATALOGUE):
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, template in data["patterns"].items():
        rows = template["paragraphs"]
        if len(rows) < 2 or not template["material_selection"] or not template["references"]:
            raise ValueError(f"{key}: missing paragraphs, material selection or references")
        for i, row in enumerate(rows):
            if not all(row.get(field) for field in ("purpose", "opening_intent", "closing_intent", "body")):
                raise ValueError(f"{key}: paragraph {i + 1} missing purpose or body")
            bridge = row["bridge_to_next"]
            if i + 1 == len(rows):
                if bridge is not None:
                    raise ValueError(f"{key}: last paragraph must close")
            elif not bridge or bridge not in SLOT.findall(row["closing"]) or bridge not in SLOT.findall(rows[i + 1]["opening"]):
                raise ValueError(f"{key}: disconnected paragraphs {i + 1} and {i + 2}")
    return data


def preview(template, facts=None):
    facts = facts or {}
    needed = set(SLOT.findall(" ".join(s for p in template["paragraphs"] for s in sentences(p))))
    if set(facts) - needed:
        raise ValueError("Unknown slots: " + ", ".join(sorted(set(facts) - needed)))
    if any(not isinstance(v, str) or not v.strip() or "\n" in v or SLOT.search(v) for v in facts.values()):
        raise ValueError("Each supplied value must be a single, concrete text phrase")

    def particle(match):
        key, suffix = match.groups()
        if key not in facts:
            return match[0]
        value = facts[key].strip()
        last = value[-1]
        if not "가" <= last <= "힣":
            return match[0]
        final = (ord(last) - 0xAC00) % 28
        actual = ("으로" if final not in (0, 8) else "로") if suffix == "으로" else PARTICLE_PAIRS[suffix][not final]
        return value + actual

    paragraphs = []
    for p in template["paragraphs"]:
        line = " ".join(sentences(p))
        formed = PARTICLE.sub(particle, line)
        # Values inserted by the particle pass have already been checked not to
        # contain a slot placeholder. Unknown slots remain visible.
        paragraphs.append(SLOT.sub(lambda m: facts.get(m[1], "[" + m[1] + "]").strip(), formed))
    return {"title": template["title"], "paragraphs": paragraphs,
            "missing_slots": sorted(needed - facts.keys()), "is_submission": False,
            "source_facts_validated_by_renderer": False}


def main():
    data = load_catalogue()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("template", choices=data["patterns"])
    ap.add_argument("--facts", type=Path)
    args = ap.parse_args()
    facts = json.loads(args.facts.read_text(encoding="utf-8")) if args.facts else {}
    print(json.dumps(preview(data["patterns"][args.template], facts), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
