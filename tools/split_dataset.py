from __future__ import annotations

import json
import argparse
from collections import defaultdict
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
INPUT_PATH = PROJECT_DIR / "datasets" / "prompts.expanded.120.json"
OUT_DIR = PROJECT_DIR / "datasets" / "splits"


def main() -> int:
    parser = argparse.ArgumentParser(description="Split prompt dataset into DEV/PUBLIC/HOLD grouped by template_id.")
    parser.add_argument("--in", dest="input_path", default=str(INPUT_PATH))
    parser.add_argument("--outdir", default=str(OUT_DIR))
    args = parser.parse_args()

    input_path = Path(args.input_path).resolve()
    out_dir = Path(args.outdir).resolve()
    rows = json.loads(input_path.read_text(encoding="utf-8"))
    by_category_templates: dict[str, list[str]] = defaultdict(list)
    by_template_rows: dict[str, list[dict]] = defaultdict(list)

    for r in rows:
        category = r["category"]
        template_id = r.get("template_id")
        if not template_id:
            raise ValueError("Missing template_id in dataset row; regenerate dataset first.")
        by_template_rows[template_id].append(r)
        if template_id not in by_category_templates[category]:
            by_category_templates[category].append(template_id)

    dev: list[dict] = []
    public_test: list[dict] = []
    private_hold: list[dict] = []

    # Deterministic grouped split per category:
    # templates 0,1 -> DEV ; 2,3 -> PUBLIC ; 4,5 -> PRIVATE
    for category in sorted(by_category_templates.keys()):
        templates = sorted(by_category_templates[category])
        if len(templates) < 6:
            raise ValueError(f"Category {category} has {len(templates)} templates; expected >=6.")
        dev_templates = templates[0:2]
        pub_templates = templates[2:4]
        hold_templates = templates[4:6]

        for tid in dev_templates:
            dev.extend(by_template_rows[tid])
        for tid in pub_templates:
            public_test.extend(by_template_rows[tid])
        for tid in hold_templates:
            private_hold.extend(by_template_rows[tid])

    def resequence(items: list[dict]) -> list[dict]:
        out = []
        for i, r in enumerate(sorted(items, key=lambda x: x["id"]), start=1):
            item = dict(r)
            item["split_id"] = i
            out.append(item)
        return out

    dev = resequence(dev)
    public_test = resequence(public_test)
    private_hold = resequence(private_hold)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dev.json").write_text(json.dumps(dev, indent=2), encoding="utf-8")
    (out_dir / "public_test.json").write_text(json.dumps(public_test, indent=2), encoding="utf-8")
    (out_dir / "private_hold.json").write_text(json.dumps(private_hold, indent=2), encoding="utf-8")
    meta = {
        "source": str(input_path),
        "counts": {"dev": len(dev), "public_test": len(public_test), "private_hold": len(private_hold)},
        "rule": "Grouped by template_id per category: first2 dev, middle2 public, last2 private.",
    }
    (out_dir / "SPLIT-META.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote={out_dir}")
    print(json.dumps(meta["counts"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

