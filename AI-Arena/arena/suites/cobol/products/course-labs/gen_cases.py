"""Generate the course-labs test-case library (inputs only), deterministically.

Each case: cases/<name>/{cmd, checks, input/accounts.txt}. Expected outputs
are produced by build.py --regen.

usage: python gen_cases.py
"""
import os
import random
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "cases")

LAST = ["WASHINGTON", "ADAMS", "JEFFERSON", "MADISON", "MONROE", "JACKSON", "VAN BUREN",
        "HARRISON", "TYLER", "POLK", "TAYLOR", "FILLMORE", "PIERCE", "BUCHANAN", "LINCOLN",
        "JOHNSON", "GRANT", "HAYES", "GARFIELD", "ARTHUR", "CLEVELAND", "MCKINLEY", "ROOSEVELT",
        "TAFT", "WILSON", "HARDING", "COOLIDGE", "HOOVER", "TRUMAN", "EISENHOWER", "KENNEDY",
        "NIXON", "FORD", "CARTER", "REAGAN", "BUSH", "CLINTON", "OBAMA", "OKONKWO-BRANDTSTETTER",
        "DE LA CRUZ", "O'NEILL", "VANDERBILT-ASHFORD", "MACGILLIVRAY", "SCHWARZENEGGER"]
FIRST = ["George", "John", "Thomas", "James", "Andrew", "Martin", "William", "Zachary", "Millard",
         "Franklin", "Abraham", "Ulysses S.", "Rutherford B.", "Chester", "Grover", "Benjamin",
         "Theodore", "Woodrow", "Warren", "Calvin", "Herbert", "Harry", "Dwight", "Lyndon",
         "Richard", "Gerald", "Jimmy", "Ronald", "Barack H.", "Maria", "Aiko", "Chidi"]
STATES = ["Virginia", "Massachusetts", "New York", "Ohio", "North Carolina", "Texas",
          "California", "Kentucky", "Vermont", "New Jersey", "Iowa", "Georgia", "virginia",
          "Pennslyvania", "Hawaii", "Illinois"]
CITIES = ["Westmoreland County", "Braintree", "Albemarle County", "Columbia", "Hyde Park",
          "Plains", "Hope", "Honolulu", "Cincinnati", "Staunton", "Point Pleasant", "Omaha"]
NOTES = ["retired to his farm", "independent treasury system", "new birth of freedom",
         "quietly stood for the full dinner pail", "peace through strength",
         "planning of a United Nations", "dark horse nominee", "opposed the compromise",
         "high-pitched voice, jutting jaw, and pounding fist", ""]


def money(c):
    return "%s%07d.%02d" % ("-" if c < 0 else "+", abs(c) // 100, abs(c) % 100)


def acct_line(no, limit, bal, last, first, street, city, state, comment):
    line = (no + money(limit) + money(bal) + last.ljust(20)[:20] + first.ljust(15)[:15]
            + street.ljust(25)[:25] + city.ljust(20)[:20] + state.ljust(15)[:15] + " " * 7
            + comment.ljust(50)[:50])
    assert len(line) == 182
    return line


def random_accounts(rnd, n, force_no=None, force_last=None):
    nos = sorted(rnd.sample(range(10000000, 99999999), n))
    nos = ["%08d" % x for x in nos]
    if force_no:
        nos[rnd.randrange(n)] = force_no
        nos.sort()
    lines = []
    for i, no in enumerate(nos):
        limit = rnd.choice([100000, 500000, 1000000, 2500000, 10000000, 50000000, 99999999])
        r = rnd.random()
        if r < 0.25:
            bal = limit + rnd.randint(1, 500000)          # over the limit
        elif r < 0.3:
            bal = limit                                    # exactly at the limit
        else:
            bal = rnd.randint(0, limit)
        bal = min(bal, 999999999)
        last = rnd.choice(LAST)
        lines.append(acct_line(no, limit, bal, last, rnd.choice(FIRST),
                               "%d %s St." % (rnd.randint(1, 999), rnd.choice(["Oak", "Main", "Elm", "Lake"])),
                               rnd.choice(CITIES), rnd.choice(STATES), rnd.choice(NOTES)))
    if force_last:
        k = rnd.randrange(n)
        lines[k] = lines[k][:30] + force_last.ljust(20) + lines[k][50:]
    return lines


def write_case(name, cmd, checks, accounts=None):
    d = os.path.join(OUT, name)
    os.makedirs(os.path.join(d, "input"))
    with open(os.path.join(d, "cmd"), "w", newline="\n") as f:
        f.write(cmd + "\n")
    with open(os.path.join(d, "checks"), "w", newline="\n") as f:
        f.write("\n".join(checks) + "\n")
    if accounts is not None:
        with open(os.path.join(d, "input", "accounts.txt"), "w", newline="\n") as f:
            f.write("".join(l + "\n" for l in accounts))


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    rep = ["prtline.txt", "rc.txt"]
    for p in ("CBL0001", "CBL0006", "CBL0011"):
        write_case("%s_base" % p.lower(), "run " + p, rep)
        for k in (1, 2):
            rnd = random.Random("%s-%d" % (p, k))
            write_case("%s_mix%d" % (p.lower(), k), "run " + p, rep, random_accounts(rnd, rnd.randint(30, 60)))
    # CBL0006 with an explicit state on the command line
    rnd = random.Random("state")
    acc = random_accounts(rnd, 50)
    write_case("cbl0006_state_ohio", "run CBL0006 Ohio", rep, acc)
    write_case("cbl0006_state_newyork", "run CBL0006 New York", rep, acc)
    write_case("cbl0006_state_virginia", "run CBL0006 Virginia", rep, acc)
    # binary search: target present at different positions / absent
    srch = ["sysout.txt", "rc.txt"]
    write_case("srchbin_base", "run SRCHBIN", srch)
    for k in (1, 2, 3):
        rnd = random.Random("srch-%d" % k)
        write_case("srchbin_mix%d" % k, "run SRCHBIN", srch,
                   random_accounts(rnd, rnd.randint(20, 44), force_no="18011809"))
    rnd = random.Random("srch-missing")
    write_case("srchbin_missing", "run SRCHBIN", srch, random_accounts(rnd, 40))
    rnd = random.Random("srch-small")
    write_case("srchbin_small", "run SRCHBIN", srch, random_accounts(rnd, 5, force_no="18011809"))
    print("cases:", sorted(os.listdir(OUT)))


if __name__ == "__main__":
    main()
