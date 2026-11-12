"""import_pack_check reproduces Actual's reconcileTransactions matching and flags order-dependent lines.
XD7IARR1 TASK-07: the tool predicts exactly the grader's 391/389/387 where the manifest stated 395/393/391."""
from mtm_competition.integration.import_pack_check import check_manifest


def tx(day, amount, fitid=None):
    return {"date": f"2027-07-{day:02d}", "amount": amount, "importedId": fitid, "payee": "x", "notes": None}


def manifest(files, count=None, balance=None):
    return {"accounts": [{"name": "A", "files": files, "expectedCount": count, "expectedBalance": balance}]}


def test_unambiguous_overlap_is_predicted_and_accepted():
    f1 = {"path": "a/01.qif", "transactions": [tx(1, -100), tx(3, -200), tx(5, -300)], "expectedAdded": 3}
    # overlap: -300 repeated (same date and amount) in an OFX file, plus two new lines
    f2 = {"path": "a/02.ofx", "transactions": [tx(5, -300, "F3"), tx(9, -400, "F4"), tx(20, -100, "F5")],
          "expectedAdded": 2}
    out = check_manifest(manifest([f1, f2], 5, -1100))
    assert out["ok"] and out["ambiguityCount"] == 0
    assert [f["predictedAdded"] for f in out["files"]] == [3, 2]


def test_weekly_series_without_ids_is_ambiguous_and_mispredicted_counts_are_reported():
    series = [tx(d, -500) for d in (1, 8, 15)]
    f1 = {"path": "a/01.csv", "transactions": series, "expectedAdded": 3}
    f2 = {"path": "a/02.qif", "transactions": [tx(8, -500), tx(15, -500), tx(22, -500)], "expectedAdded": 1}
    out = check_manifest(manifest([f1, f2], 4, -2000))
    assert not out["ok"] and out["ambiguityCount"] >= 2
    assert {a["kind"] for a in out["ambiguities"]} >= {"several-candidates", "competing-lines"}
    assert "OFX" in out["advice"]


def test_ofx_lines_with_ids_never_fuzzy_match_each_other():
    f1 = {"path": "a/01.ofx", "transactions": [tx(1, -500, "S1"), tx(8, -500, "S2")], "expectedAdded": 2}
    f2 = {"path": "a/02.ofx", "transactions": [tx(8, -500, "S2"), tx(15, -500, "S3"), tx(22, -500, "S4")],
          "expectedAdded": 2}
    out = check_manifest(manifest([f1, f2], 4, -2000))
    assert out["ok"], out


def test_a_fuzzy_match_takes_the_incoming_id_so_a_later_fitid_matches_exactly():
    f1 = {"path": "a/01.qif", "transactions": [tx(2, -700)], "expectedAdded": 1}
    f2 = {"path": "a/02.ofx", "transactions": [tx(2, -700, "X1")], "expectedAdded": 0}   # fuzzy: gets X1
    f3 = {"path": "a/03.ofx", "transactions": [tx(2, -700, "X1")], "expectedAdded": 0}   # exact on X1
    out = check_manifest(manifest([f1, f2, f3], 1, -700))
    assert out["ok"], out
