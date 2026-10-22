"""Suite adapters. Each turns one source benchmark into normalised rows and grades patches.

  setup(args)       download the source, write data/sources/<name>.jsonl, return the row count
  export(row, dest) put the handed-over code (base state) in dest
  check(row, patch) dev-time run for the agent: MVS test statuses + log (CR/BUG only)
  grade(row, patch) hidden-test verdict: {"resolved": bool, "log": str, ...}
"""
from arena import core


class Suite:
    name = ""
    title = ""
    source = ""

    def setup(self, args) -> int:
        raise NotImplementedError

    def images(self, row) -> list[str]:
        return [row["image"]]

    def prepare(self, row, container):
        """Put the base code into a fresh container when the image does not hold it."""

    def export(self, row, dest):
        return core.export_repo(row["image"], row["workdir"], dest)

    def check(self, row, patch: str) -> dict:
        raise NotImplementedError

    def grade(self, row, patch: str) -> dict:
        raise NotImplementedError


def registry() -> dict[str, Suite]:
    from arena.adapters.swebench import SweBench
    from arena.adapters.harbor import SwePro, Cobol
    from arena.adapters.multiswe import MultiSwe
    suites = [SweBench("swe-verified"), SweBench("swe-multilingual"), SwePro(), MultiSwe(), Cobol()]
    return {s.name: s for s in suites}
