"""Generate the CardDemo test-case library (inputs only).

Deterministic: running it twice produces identical files. Each case is
written to cases/<name>/ with
    cmd        job name + arguments, e.g. "posttran" or "intcalc 2022071800"
    checks     output files (under <data>/out) compared with the expected copy
    input/     files that replace the repo's app/data/ASCII files for the case
Expected outputs are produced separately (build.py --regen) by running the
reference version of the programs in the arena-cobol image.

usage: python gen_cases.py
"""
import os
import random
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "repo", "app", "data", "ASCII")
OUT = os.path.join(HERE, "cases")

POS = "{ABCDEFGHI"
NEG = "}JKLMNOPQR"


def zoned(value_cents, digits):
    """Signed zoned decimal with EBCDIC-style overpunch (as in the source data)."""
    neg = value_cents < 0
    s = str(abs(value_cents)).rjust(digits, "0")
    assert len(s) == digits, (value_cents, digits)
    last = int(s[-1])
    return s[:-1] + (NEG[last] if neg else POS[last])


def unzoned(s):
    last = s[-1]
    if last in POS:
        return int(s[:-1] + str(POS.index(last)))
    if last in NEG:
        return -int(s[:-1] + str(NEG.index(last)))
    return int(s)


def read(name):
    with open(os.path.join(BASE, name), encoding="ascii") as f:
        return [l.rstrip("\n") for l in f]


def pad(s, n):
    assert len(s) <= n, (s, n)
    return s.ljust(n)


# ---------------------------------------------------------------- records
def acct_rec(acct_id, status, bal, limit, cash, open_d, exp_d, reissue,
             cyc_cr, cyc_db, zip_, group):
    r = (str(acct_id).rjust(11, "0") + status + zoned(bal, 12) + zoned(limit, 12)
         + zoned(cash, 12) + open_d + exp_d + reissue + zoned(cyc_cr, 12)
         + zoned(cyc_db, 12) + pad(zip_, 10) + pad(group, 10))
    return pad(r, 300)


def acct_parse(line):
    return dict(id=int(line[0:11]), status=line[11], bal=unzoned(line[12:24]),
                limit=unzoned(line[24:36]), cash=unzoned(line[36:48]),
                open=line[48:58], exp=line[58:68], reissue=line[68:78],
                cyc_cr=unzoned(line[78:90]), cyc_db=unzoned(line[90:102]),
                zip=line[102:112], group=line[112:122])


def acct_from(d):
    return acct_rec(d["id"], d["status"], d["bal"], d["limit"], d["cash"], d["open"],
                    d["exp"], d["reissue"], d["cyc_cr"], d["cyc_db"], d["zip"].strip(),
                    d["group"].strip())


def xref_rec(card, cust, acct):
    return pad(card + str(cust).rjust(9, "0") + str(acct).rjust(11, "0"), 36)


def tran_rec(tid, type_cd, cat, source, desc, amt, merch_id, mname, mcity, mzip,
             card, orig_ts, proc_ts=""):
    r = (pad(tid, 16) + type_cd + str(cat).rjust(4, "0") + pad(source, 10)
         + pad(desc, 100) + zoned(amt, 11) + str(merch_id).rjust(9, "0")
         + pad(mname, 50) + pad(mcity, 50) + pad(mzip, 10) + card
         + pad(orig_ts, 26) + pad(proc_ts, 26))
    return pad(r, 350)


def tran_parse(line):
    return dict(id=line[0:16], type=line[16:18], cat=int(line[18:22]),
                source=line[22:32].strip(), desc=line[32:132].strip(),
                amt=unzoned(line[132:143]), merch_id=int(line[143:152]),
                mname=line[152:202].strip(), mcity=line[202:252].strip(),
                mzip=line[252:262].strip(), card=line[262:278],
                orig=line[278:304].strip(), proc=line[304:330].strip())


def tran_from(d):
    return tran_rec(d["id"], d["type"], d["cat"], d["source"], d["desc"], d["amt"],
                    d["merch_id"], d["mname"], d["mcity"], d["mzip"], d["card"],
                    d["orig"], d["proc"])


def tcat_rec(acct, type_cd, cat, bal):
    return pad(str(acct).rjust(11, "0") + type_cd + str(cat).rjust(4, "0")
               + zoned(bal, 11), 50)


def disc_rec(group, type_cd, cat, rate_hundredths):
    return pad(pad(group, 10) + type_cd + str(cat).rjust(4, "0")
               + zoned(rate_hundredths, 6), 50)


# ---------------------------------------------------------------- writer
def write_case(name, cmd, checks, files):
    d = os.path.join(OUT, name)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(os.path.join(d, "input"))
    with open(os.path.join(d, "cmd"), "w", newline="\n") as f:
        f.write(cmd + "\n")
    with open(os.path.join(d, "checks"), "w", newline="\n") as f:
        f.write("\n".join(checks) + "\n")
    for fname, lines in files.items():
        with open(os.path.join(d, "input", fname), "w", newline="\n", encoding="ascii") as f:
            f.write("".join(l + "\n" for l in lines))


POST_CHECKS = ["transact.txt", "dalyrejs.txt", "acctdata.txt", "tcatbal.txt",
               "rc.txt", "sysout.txt"]
INT_CHECKS = ["systran.txt", "acctdata.txt", "rc.txt"]
REPT_CHECKS = ["tranrept.txt", "rc.txt"]

BASE_ACCT = read("acctdata.txt")
BASE_XREF = read("cardxref.txt")
BASE_DALY = read("dailytran.txt")
BASE_TCAT = read("tcatbal.txt")
BASE_DISC = read("discgrp.txt")


def ts(day, hh=12, mm=0):
    return "%s %02d:%02d:00.000000" % (day, hh, mm)


def card_no(n):
    return ("4%015d" % n)[:16]


# ================================================================ POSTTRAN
def posttran_cases():
    write_case("posttran_base", "posttran", POST_CHECKS, {})

    # Hand-built edge cases ------------------------------------------------
    accts = [
        # id status bal limit cash open exp reissue cyc_cr cyc_db zip group
        acct_rec(101, "Y", 0, 100000, 50000, "2019-01-01", "2026-01-31", "2026-01-31", 0, 0, "10001", "A000000000"),
        acct_rec(102, "Y", 25000, 500000, 100000, "2018-05-05", "2022-06-10", "2022-06-10", 0, 0, "10002", "A000000000"),
        acct_rec(103, "Y", 120050, 300000, 100000, "2017-03-12", "2027-03-31", "2027-03-31", 20000, -5000, "10003", "A000000000"),
        acct_rec(104, "N", 4500, 200000, 50000, "2016-07-01", "2026-07-31", "2026-07-31", 0, 0, "10004", "A000000000"),
        acct_rec(105, "Y", 990000, 1000000, 200000, "2015-02-02", "2028-02-29", "2028-02-29", 150000, -40000, "10005", "A000000000"),
        acct_rec(107, "Y", 0, 250000, 50000, "2020-10-10", "2022-06-11", "2022-06-11", 0, 0, "10007", "A000000000"),
    ]
    xrefs = [
        xref_rec(card_no(101), 9001, 101), xref_rec(card_no(102), 9002, 102),
        xref_rec(card_no(103), 9003, 103), xref_rec(card_no(104), 9004, 104),
        xref_rec(card_no(105), 9005, 105), xref_rec(card_no(106), 9006, 106),
        xref_rec(card_no(107), 9007, 107),
    ]
    tcats = [
        tcat_rec(103, "01", 1, 15000), tcat_rec(103, "03", 1, -2500),
        tcat_rec(105, "01", 1, 900000),
    ]
    n = [0]

    def t(card, amt, day, type_cd="01", cat=1, src="POS TERM", desc="Purchase", merch="Corner Store"):
        n[0] += 1
        return tran_rec("%016d" % (7000000000 + n[0]), type_cd, cat, src,
                        "%s at %s" % (desc, merch), amt, 800000000 + n[0], merch,
                        "Springfield", "62701", card, ts(day, 9 + n[0] % 10, n[0] % 60))
    daly = [
        t(card_no(101), 60000, "2022-06-10"),            # 600.00
        t(card_no(101), 40000, "2022-06-10"),            # reaches the limit exactly
        t(card_no(101), 1, "2022-06-10"),                # one cent over the limit
        t(card_no(102), 12345, "2022-06-10"),            # on the expiration day
        t(card_no(102), 2000, "2022-06-11"),             # day after expiration
        t(card_no(103), -5000, "2022-06-10", "03", 1, "OPERATOR", "Return item", "Book Barn"),
        t(card_no(103), -2525, "2022-06-10", "03", 1, "OPERATOR", "Return item", "Book Barn"),
        t(card_no(103), 7799, "2022-06-10"),
        t(card_no(103), 1001, "2022-06-10", "01", 2, "ATM", "Cash advance", "ATM 22"),
        t(card_no(103), 1999, "2022-06-10", "01", 2, "ATM", "Cash advance", "ATM 22"),
        t(card_no(104), 3300, "2022-06-10"),             # inactive account
        t(card_no(104), -1200, "2022-06-10", "03", 1, "OPERATOR", "Return item", "Shoe Hub"),
        t("9999000011112222", 5000, "2022-06-10"),        # unknown card
        t(card_no(106), 5000, "2022-06-10"),             # card without account
        t(card_no(105), 100000, "2022-06-10"),           # over limit
        t(card_no(105), -10000, "2022-06-10", "03", 1, "OPERATOR", "Return item", "Garden Co"),
        t(card_no(105), 7000, "2022-06-10"),
        t(card_no(107), 0, "2022-06-11", "04", 1, "WEB", "Zero dollar authorization", "Web Shop"),
        t(card_no(107), 24999, "2022-06-11"),
        t(card_no(107), 2, "2022-06-11"),
        t(card_no(101), -100, "2022-06-11", "03", 1, "OPERATOR", "Return item", "Corner Store"),
        t(card_no(101), 50, "2022-06-11"),
    ]
    write_case("posttran_edge", "posttran", POST_CHECKS,
               {"acctdata.txt": accts, "cardxref.txt": xrefs, "tcatbal.txt": tcats,
                "dailytran.txt": daly})

    # Variations of the shipped data set -----------------------------------
    for seed in (1, 2, 3):
        rnd = random.Random(1000 + seed)
        accts = [acct_parse(l) for l in BASE_ACCT]
        for a in accts:
            r = rnd.random()
            if r < 0.15:
                a["limit"] = rnd.randint(5, 40) * 10000
            if rnd.random() < 0.15:
                a["exp"] = "2022-06-%02d" % rnd.choice([9, 10, 11])
            if rnd.random() < 0.12:
                a["status"] = "N"
            if rnd.random() < 0.3:
                a["cyc_cr"] = rnd.randint(0, 200000)
                a["cyc_db"] = -rnd.randint(0, 150000)
        trans = [tran_parse(l) for l in BASE_DALY]
        rnd.shuffle(trans)
        trans = trans[: rnd.randint(120, 200)]
        for i, tr in enumerate(trans):
            tr["id"] = "%016d" % (seed * 100000000 + i)
            r = rnd.random()
            if r < 0.04:
                tr["card"] = "%016d" % rnd.randint(10**15, 10**16 - 1)
            if rnd.random() < 0.3:
                tr["amt"] = rnd.randint(-30000, 150000)
            if rnd.random() < 0.25:
                tr["orig"] = ts("2022-06-%02d" % rnd.choice([9, 10, 11, 12]), rnd.randint(0, 23), rnd.randint(0, 59))
            if rnd.random() < 0.1:
                tr["type"], tr["cat"] = rnd.choice([("01", 2), ("01", 4), ("02", 1), ("03", 2)])
        tcats = [tcat_rec(int(l[0:11]), l[11:13], int(l[13:17]), rnd.randint(-5000, 90000))
                 for l in BASE_TCAT]
        write_case("posttran_mix%d" % seed, "posttran", POST_CHECKS,
                   {"acctdata.txt": [acct_from(a) for a in accts],
                    "dailytran.txt": [tran_from(tr) for tr in trans],
                    "tcatbal.txt": tcats})


# ================================================================ INTCALC
def intcalc_cases():
    write_case("intcalc_base", "intcalc 2022071800", INT_CHECKS, {})

    rnd = random.Random(2024)
    accts = [acct_parse(l) for l in BASE_ACCT]
    for a in accts:
        a["cyc_cr"] = rnd.randint(0, 300000)
        a["cyc_db"] = -rnd.randint(0, 200000)
    combos = [("01", 1), ("01", 2), ("01", 3), ("01", 4), ("02", 1), ("03", 1), ("04", 3), ("05", 1)]
    tcats = []
    for a in accts:
        for type_cd, cat in sorted(rnd.sample(combos, rnd.randint(1, 4))):
            bal = -rnd.randint(100, 80000) if rnd.random() < 0.25 else rnd.randint(0, 900000)
            tcats.append(tcat_rec(a["id"], type_cd, cat, bal))
    write_case("intcalc_balances", "intcalc 2022071800", INT_CHECKS,
               {"acctdata.txt": [acct_from(a) for a in accts], "tcatbal.txt": tcats})

    # disclosure groups: own group, group without its own rates (DEFAULT used),
    # zero-rate group
    rnd = random.Random(77)
    accts = [acct_parse(l) for l in BASE_ACCT][:24]
    groups = ["A000000000", "GOLD", "PLATINUM", "NEWCUST"]
    for i, a in enumerate(accts):
        a["group"] = groups[i % 4]
        a["cyc_cr"] = rnd.randint(0, 100000)
        a["cyc_db"] = -rnd.randint(0, 100000)
    disc = list(BASE_DISC)
    for type_cd, cat, rate in [("01", 1, 999), ("01", 2, 1999), ("01", 3, 1250), ("01", 4, 2475),
                               ("02", 1, 0), ("03", 1, 0)]:
        disc.append(disc_rec("GOLD", type_cd, cat, rate))
    for type_cd, cat in [("01", 1), ("01", 2), ("01", 3), ("01", 4)]:
        disc.append(disc_rec("PLATINUM", type_cd, cat, 0))
    tcats = []
    for a in accts:
        for type_cd, cat in [("01", 1), ("01", 2), ("01", 4), ("03", 1)]:
            if rnd.random() < 0.8:
                tcats.append(tcat_rec(a["id"], type_cd, cat, rnd.randint(1000, 500000)))
    write_case("intcalc_groups", "intcalc 2022093000", INT_CHECKS,
               {"acctdata.txt": [acct_from(a) for a in accts], "tcatbal.txt": tcats,
                "discgrp.txt": disc})

    # small balances: monthly interest below one dollar / fractions of a cent
    accts = [acct_parse(l) for l in BASE_ACCT][:12]
    tcats = []
    small = [1, 79, 80, 399, 1999, 3999, 4000, 4001, 7999, 15000, 39999, 100000]
    for i, a in enumerate(accts):
        tcats.append(tcat_rec(a["id"], "01", 1, small[i]))
        tcats.append(tcat_rec(a["id"], "01", 2, small[-1 - i] * 3 + 7))
    write_case("intcalc_small", "intcalc 2022103100", INT_CHECKS,
               {"acctdata.txt": [acct_from(a) for a in accts], "tcatbal.txt": tcats})


# ================================================================ TRANREPT
def master_from_daily(rnd, days, n=None):
    cards = set(l[:16] for l in BASE_XREF)
    trans = [tran_parse(l) for l in BASE_DALY]
    trans = [t for t in trans if t["card"] in cards]
    if n:
        trans = trans[:n]
    for tr in trans:
        d = rnd.choice(days)
        tr["proc"] = "%s-%02d.%02d.%02d.000000" % (d, rnd.randint(0, 23), rnd.randint(0, 59), rnd.randint(0, 59))
    trans.sort(key=lambda x: x["id"])
    return trans


def tranrept_cases():
    rnd = random.Random(31)
    days = ["2022-06-%02d" % d for d in range(25, 31)] + ["2022-07-%02d" % d for d in range(1, 11)]
    trans = master_from_daily(rnd, days)
    lines = [tran_from(t) for t in trans]
    write_case("tranrept_week", "tranrept", REPT_CHECKS,
               {"transact.txt": lines, "dateparm.txt": ["2022-07-01 2022-07-06"]})
    write_case("tranrept_all", "tranrept", REPT_CHECKS,
               {"transact.txt": lines, "dateparm.txt": ["2022-01-01 2022-12-31"]})
    write_case("tranrept_oneday", "tranrept", REPT_CHECKS,
               {"transact.txt": lines, "dateparm.txt": ["2022-06-30 2022-06-30"]})

    # large amounts and many transactions on one card (page breaks inside
    # an account)
    rnd = random.Random(55)
    xref = [l for l in BASE_XREF]
    cards = [l[:16] for l in xref]
    trans = []
    k = 0
    big_card = cards[3]
    for i in range(46):
        k += 1
        amt = rnd.choice([rnd.randint(100, 99999), rnd.randint(100000000, 2500000000),
                          -rnd.randint(100000, 300000000)])
        type_cd, cat = ("03", 1) if amt < 0 else rnd.choice([("01", 1), ("01", 2), ("01", 4), ("07", 1)])
        trans.append(dict(id="%016d" % (500000 + k), type=type_cd, cat=cat, source="BRANCH",
                          desc="Manual posting %d" % k, amt=amt, merch_id=0, mname="", mcity="",
                          mzip="", card=big_card, orig=ts("2022-07-02"),
                          proc="2022-07-02-08.%02d.00.000000" % (k % 60)))
    for c in cards[10:22]:
        for j in range(rnd.randint(1, 4)):
            k += 1
            amt = rnd.choice([rnd.randint(100, 999999), rnd.randint(100000000, 2500000000)])
            trans.append(dict(id="%016d" % (500000 + k), type="01", cat=rnd.choice([1, 2, 3]),
                              source="POS TERM", desc="Purchase %d" % k, amt=amt,
                              merch_id=800000000 + k, mname="Mega Store", mcity="Austin",
                              mzip="73301", card=c, orig=ts("2022-07-02"),
                              proc="2022-07-0%d-10.00.00.000000" % rnd.choice([1, 2, 3])))
    trans.sort(key=lambda x: x["id"])
    write_case("tranrept_large", "tranrept", REPT_CHECKS,
               {"transact.txt": [tran_from(t) for t in trans],
                "dateparm.txt": ["2022-07-01 2022-07-03"]})


if __name__ == "__main__":
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    posttran_cases()
    intcalc_cases()
    tranrept_cases()
    print("cases:", sorted(os.listdir(OUT)))
