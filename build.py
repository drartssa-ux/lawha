#!/usr/bin/env python3
"""يجمع ملفات الأسئلة في index.html ويفحص التكرار والأعداد."""
import json, glob, os, re, random, sys
HERE = os.path.dirname(os.path.abspath(__file__))
cats = []
for f in sorted(glob.glob(os.path.join(HERE, "questions", "*.json"))):
    c = json.load(open(f, encoding="utf-8"))
    assert len(c["levels"]) == 5, f
    for lvl in c["levels"]:
        for qa in lvl:
            assert len(qa) == 2 and qa[0].strip() and qa[1].strip(), (f, qa)
    cats.append(c)
# فحص التكرار
norm = lambda t: re.sub(r"[\s«»\"'؟?،,.:!ـ…]+", "", t).replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه").replace("ى", "ي")
seen, dups = {}, []
for c in cats:
    for li, lvl in enumerate(c["levels"]):
        for q, a in lvl:
            k = norm(q)
            if k in seen:
                dups.append((q, seen[k], c["name"]))
            seen[k] = c["name"]
if dups:
    print("⚠️ أسئلة مكررة:")
    for d in dups:
        print("  ", d)
    sys.exit(1)
# خلط ثابت داخل كل مستوى (يبقى نفس الترتيب كل بناء)
rng = random.Random(2026)
bank = []
for c in cats:
    lv = []
    for lvl in c["levels"]:
        l2 = lvl[:]
        rng.shuffle(l2)
        lv.append(l2)
    bank.append({"name": c["name"], "ic": c["ic"], "q": lv})
tpl = open(os.path.join(HERE, "app.html"), encoding="utf-8").read()
out = tpl.replace("/*__BANK__*/[]", json.dumps(bank, ensure_ascii=False, separators=(",", ":")))
open(os.path.join(HERE, "index.html"), "w", encoding="utf-8").write(out)
counts = [(c["name"], [len(l) for l in c["levels"]]) for c in cats]
total = sum(sum(x) for _, x in counts)
print("الفئات:", len(cats), "| الأسئلة:", total)
for n, x in counts:
    print("  ", n, x)
