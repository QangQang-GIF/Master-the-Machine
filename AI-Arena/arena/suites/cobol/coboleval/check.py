"""Verifier for COBOLEval-derived tasks (runs inside arena-cobol:1).

Port of the comparison logic of BloopAI/COBOLEval scripts/evaluation.py
(parse / parse_* / is_equal / check_correctness): every test caller is
compiled together with the candidate program using
    cobc -w -fformat=variable -x <caller> <program>
the executable is run, the result file <PROGRAM-ID>.TXT it writes is read
and parsed according to the expected result type.

usage: python3 check.py <tests-dir> <program-path> <output-json>
"""
import json
import math
import os
import shutil
import subprocess
import sys


class ParseError(Exception):
    pass


def parse_bool(res):
    return res.strip() == "1"


def parse_int(res):
    res = res.strip()
    if res.startswith("p") or res.startswith("y"):
        return -int(res[1:])
    return int(res)


def parse_float(res):
    res = res.strip()
    if res.startswith("p") or res.startswith("y"):
        res = res[1:]
        return -float(res)
    return float(res)


def parse_string(res):
    return res.strip()


def parse(result, result_type, true):
    try:
        if result_type == "Bool":
            return parse_bool(result[0])
        if result_type == "Int":
            return parse_int(result[0])
        if result_type == "Float":
            return parse_float(result[0])
        if result_type == "String":
            return parse_string(result[0])
        if result_type == {"List": "Int"}:
            return [parse_int(x) for x in result][: len(true)]
        if result_type == {"List": "Float"}:
            return [parse_float(x) for x in result][: len(true)]
        if result_type == {"List": "String"}:
            return [parse_string(x) for x in result][: len(true)]
        raise ParseError("Invalid result type: %r" % (result_type,))
    except Exception as e:  # noqa: BLE001 - mirror upstream behaviour
        raise ParseError("Result %r of type %r failed with: %s" % (result, result_type, e))


def is_equal(result_type, result, true):
    if result_type == "Float":
        return math.isclose(result, true, abs_tol=0.001)
    if result_type == {"List": "Float"}:
        return all(math.isclose(r, t, abs_tol=0.001) for r, t in zip(result, true))
    return result == true


def run(cmd, cwd, timeout):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout + p.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return False, "timeout"


def main():
    tests_dir, program, out_json = sys.argv[1:4]
    spec = json.load(open(os.path.join(tests_dir, "tests.json")))
    name = spec["entry_point"]
    result_file = name.upper().replace("_", "-") + ".TXT"
    work = "/tmp/coboleval-work"
    results = []
    for test in spec["tests"]:
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work)
        call_path = os.path.join(work, "call_%s.cbl" % name)
        prog_path = os.path.join(work, "%s.cbl" % name)
        shutil.copy(os.path.join(tests_dir, test["caller"]), call_path)
        status, detail = "FAILED", ""
        if not os.path.isfile(program):
            detail = "program file %s not found" % program
        else:
            shutil.copy(program, prog_path)
            ok, log = run(["cobc", "-w", "-fformat=variable", "-x", call_path, prog_path], work, 120)
            if not ok:
                detail = "compile error: " + log
            else:
                ok, log = run(["./call_%s" % name], work, 10)
                rpath = os.path.join(work, result_file)
                try:
                    true = eval(test["result"]["value"])  # values come from the dataset
                    if isinstance(true, tuple):
                        true = list(true)
                    with open(rpath) as f:
                        result = f.readlines()
                    if result:
                        type_ = test["result"]["type_"]
                        parsed = parse(result, type_, true)
                        if is_equal(type_, parsed, true):
                            status = "PASSED"
                        else:
                            detail = "expected %r got %r" % (true, parsed)
                    else:
                        detail = "empty result file"
                except Exception as e:  # noqa: BLE001
                    detail = "evaluation failed: %s (run: %s)" % (e, log)
        results.append({"name": test["name"], "status": status, "detail": detail[:500]})
    json.dump({"tests": results}, open(out_json, "w"), indent=1)


if __name__ == "__main__":
    main()
