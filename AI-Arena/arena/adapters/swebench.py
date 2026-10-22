"""SWE-bench Verified and SWE-bench Multilingual (same schema, same graders).

Rows carry the prebuilt image and eval script, so grading runs the eval script in our own
container and scores the log with the swebench package (same parsers and resolution rules
as `swebench.harness.run_evaluation`, without its CRLF problem on Windows).
"""
import json

from arena import core
from arena.adapters import Suite

DATASETS = {
    "swe-verified": ("SWE-bench/SWE-bench_Verified", "SWE-bench Verified"),
    "swe-multilingual": ("SWE-bench/SWE-bench_Multilingual", "SWE-bench Multilingual"),
}
# Multilingual has no language column; its repos map to one language each.
REPO_LANGUAGE = {
    "preactjs/preact": "js", "projectlombok/lombok": "java", "rubocop/rubocop": "ruby",
    "caddyserver/caddy": "go", "laravel/framework": "php", "fluent/fluentd": "ruby", "redis/redis": "c",
    "fmtlib/fmt": "c++", "briannesbitt/carbon": "php", "php-cs-fixer/php-cs-fixer": "php",
    "phpoffice/phpspreadsheet": "php", "apache/lucene": "java", "google/gson": "java", "jqlang/jq": "c",
    "tokio-rs/tokio": "rust", "gin-gonic/gin": "go", "prometheus/prometheus": "go", "sharkdp/bat": "rust",
    "astral-sh/ruff": "rust", "fastlane/fastlane": "ruby", "gohugoio/hugo": "go", "tokio-rs/axum": "rust",
    "axios/axios": "js", "apache/druid": "java", "babel/babel": "ts", "facebook/docusaurus": "ts",
    "hashicorp/terraform": "go", "jekyll/jekyll": "ruby", "micropython/micropython": "c",
    "nushell/nushell": "rust", "uutils/coreutils": "rust", "vuejs/core": "ts", "valkey-io/valkey": "c",
    "mrdoob/three.js": "js", "burntsushi/ripgrep": "rust", "faker-ruby/faker": "ruby",
    "immutable-js/immutable-js": "ts", "javaparser/javaparser": "java", "jordansissel/fpm": "ruby",
    "nlohmann/json": "c++", "reactivex/rxjava": "java",
}
KEEP = ("base_commit", "version", "eval_script", "log_parser", "eval_type", "FAIL_TO_PASS",
        "PASS_TO_PASS", "test_patch", "patch", "image")
MVS_TESTS = 2


class SweBench(Suite):
    def __init__(self, name):
        self.name = name
        self.dataset, self.title = DATASETS[name]
        self.source = f"huggingface.co/datasets/{self.dataset}"

    def setup(self, args) -> int:
        from datasets import load_dataset
        rows = []
        for r in load_dataset(self.dataset, split="test"):
            ps = r["problem_statement"]
            typ = core.classify(core.title_of(ps), ps)
            f2p = core.as_list(r["FAIL_TO_PASS"])
            rows.append({
                "instance_id": r["instance_id"], "suite": self.name, "repo": r["repo"],
                "language": "python" if self.name == "swe-verified" else REPO_LANGUAGE.get(r["repo"], "?"),
                "type": typ, "title": core.title_of(ps), "description": ps,
                "pi_description": core.pi_description(ps),
                "workdir": "/testbed",
                "mvs": {"tests": f2p[:MVS_TESTS], "test_patch": r["test_patch"]} if typ in ("CR", "BUG") else None,
                **{k: r[k] for k in KEEP},
            })
        return core.write_jsonl(core.SOURCES / f"{self.name}.jsonl", rows)

    def _run(self, row, patch):
        from swebench.harness.utils import make_test_spec
        spec = make_test_spec(row)
        code, log = core.run_with_patch(row["image"], row["workdir"], patch,
                                        "#!/bin/bash\nset -uxo pipefail\n"  # xtrace prints the markers
                                        + "\n".join(spec.eval_script_list) + "\n", timeout=3600)
        return spec, log

    def check(self, row, patch):
        from swebench.harness.grading import get_logs_eval
        spec, log = self._run(row, patch)
        path = core.tmpdir() / "eval.log"
        path.write_text(log, encoding="utf-8")
        status, ok = get_logs_eval(spec, str(path))
        tests = {t: status.get(t, "MISSING") for t in (row["mvs"] or {}).get("tests", [])}
        return {"ok": ok and all(s == "PASSED" for s in tests.values()), "tests": tests, "log": log}

    def grade(self, row, patch):
        from swebench.harness.grading import get_eval_report
        spec, log = self._run(row, patch)
        path = core.tmpdir() / "eval.log"
        path.write_text(log, encoding="utf-8")
        rep = get_eval_report(spec, {"instance_id": row["instance_id"], "model_name_or_path": "agent",
                                     "model_patch": patch}, str(path), include_tests_status=True)
        r = rep[row["instance_id"]]
        return {"resolved": bool(r.get("resolved")), "log": log,
                "detail": json.dumps({k: v for k, v in r.items() if k != "tests_status"})}
