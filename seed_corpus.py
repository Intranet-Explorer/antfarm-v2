"""
Build the world.

The corpus is still a junk archive — hundreds of fabricated records, badly
filed. v2 adds a second layer the agents are never told about: a few real
fragments of the things they are looking for, sitting in places only another
desk (or a wander) would reveal.

The scorer uses the same markers. Prompts never see this file's contents.
"""
import os
import random

import config

random.seed(1978)

BRANDS = ["Corvane", "Ashgrove", "Melbourne Ridge", "Deltrix", "Fairholm",
          "Northway", "Sable & Crane", "Halbeck", "Roan Industrial", "Trestle",
          "Winward", "Pemberton", "Castleford", "Vireo", "Ockham Supply"]

NOUNS = ["coupling", "bracket", "regulator", "manifold", "cartridge", "spindle",
         "gasket kit", "damper", "relay module", "bushing", "flange", "housing",
         "sensor array", "drive belt", "retainer clip", "valve seat"]

ADJ = ["heavy-duty", "low-profile", "reinforced", "sealed", "modular",
       "high-tolerance", "compact", "insulated", "spring-loaded"]

DEPTS = ["Receiving", "Fabrication", "Quality", "Shipping", "Maintenance",
         "Purchasing", "Tooling", "Warehouse B", "Warehouse C"]

NAMES = ["R. Alderman", "T. Sokolov", "M. Ferreira", "J. Whitlock", "D. Nakashima",
         "P. Ebersole", "C. Marchetti", "L. Okonkwo", "S. Brandt", "H. Vasquez"]


def part_number():
    return f"{random.choice('ABCDEFGHJKLMNPRSTVWX')}{random.choice('ABCDEFGHJKLMNPRSTVWX')}" \
           f"-{random.randint(100, 9999)}"


def product_line():
    return (f"{part_number()}\t{random.choice(BRANDS)} {random.choice(ADJ)} "
            f"{random.choice(NOUNS)}\tqty {random.randint(0, 480)}\t"
            f"loc {random.choice('ABCDEF')}{random.randint(1, 24)}-"
            f"{random.randint(1, 60)}")


def inventory_file(n):
    lines = [f"# INVENTORY EXTRACT {n:04d}",
             f"# generated {random.randint(1994, 2011)}-"
             f"{random.randint(1,12):02d}-{random.randint(1,28):02d}",
             "# part\tdescription\tquantity\tlocation", ""]
    lines += [product_line() for _ in range(random.randint(40, 220))]
    return "\n".join(lines)


def memo_file(n):
    return "\n".join([
        f"MEMORANDUM {random.randint(60, 99)}-{random.randint(1000, 9999)}",
        f"TO: {random.choice(DEPTS)}",
        f"FROM: {random.choice(NAMES)}",
        f"RE: {random.choice(['stock reconciliation', 'shipment variance', 'tooling request', 'cycle count', 'vendor correspondence', 'return authorisation'])}",
        "",
        f"Reference {part_number()} was flagged during the "
        f"{random.choice(['quarterly', 'annual', 'spot', 'supplemental'])} review. "
        f"Counts do not reconcile against the {random.choice(DEPTS).lower()} ledger. "
        f"Discrepancy of {random.randint(2, 90)} units noted.",
        "",
        f"Please advise by {random.choice(['end of week', 'the 15th', 'next cycle'])}.",
        "",
        f"-- {random.choice(NAMES)}",
    ])


def ledger_file(n):
    rows = [f"{random.randint(1,12):02d}/{random.randint(1,28):02d}\t"
            f"{part_number()}\t{random.choice(['IN','OUT','ADJ','RET'])}\t"
            f"{random.randint(1, 300)}\t{random.choice(NAMES)}"
            for _ in range(random.randint(30, 150))]
    return f"LEDGER PAGE {n:04d}\ndate\tpart\ttype\tqty\tclerk\n" + "\n".join(rows)


def _blocked():
    """Anything that would leak a target or a plant marker into the junk."""
    extra = [
        "kellinger", "halcyon duplex", "verrick", "osgood lattice",
        "brantwood", "pellhurst", "kv-3140", "hd8b-", "vt-2290",
        "ph-sg19", "memo-77-4412", "restickered",
    ]
    out = [t.lower() for t in config.TARGETS]
    out.extend(extra)
    for item in config.ITEMS:
        out.extend(m.lower() for m in item["markers"])
    return out


def _write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body)


def _junk_corpus(count):
    corpus = os.path.join(config.WORKSPACE, "corpus")
    os.makedirs(corpus, exist_ok=True)

    subdirs = ["", "", "", "archive", "archive/old", "unsorted",
               "unsorted/scans", "receiving", "misc"]
    for sub in subdirs:
        os.makedirs(os.path.join(corpus, sub), exist_ok=True)

    blocked = _blocked()
    written = 0
    for i in range(count):
        kind = random.choices(["inv", "memo", "ledger"], [5, 3, 2])[0]
        body = {"inv": inventory_file, "memo": memo_file,
                "ledger": ledger_file}[kind](i)

        low = body.lower()
        if any(b in low for b in blocked):
            continue

        name = {
            "inv": f"inventory_{i:04d}.txt",
            "memo": f"memo_{random.randint(60,99)}_{random.randint(1000,9999)}.txt",
            "ledger": f"ledger_p{i:04d}.tsv",
        }[kind]
        path = os.path.join(corpus, random.choice(subdirs), name)
        with open(path, "w") as fh:
            fh.write(body)
        written += 1

    return written


def _plants():
    """Place the complementary fragments. Invisible to prompts."""
    ws = config.WORKSPACE
    home = lambda name: os.path.join(ws, "agents", name)

    for spec in config.AGENTS:
        os.makedirs(home(spec["name"]), exist_ok=True)

    # Kellinger-Voss: buried in the archive. Indexer can finish this alone
    # if they actually search. Marker KV-3140-RA-8821 is the transfer tell.
    _write(os.path.join(ws, "corpus", "archive", "old", "errata_1999_q3.txt"),
           "\n".join([
               "ERRATA — Q3 1999 cycle count",
               "Warehouse C, cage 14",
               "",
               "The rotary assembly filed under 'KV rotary' is the Kellinger-Voss 3140.",
               "Internal stock code: KV-3140-RA-8821",
               "Qty on hand: 2. One unit tagged damaged, do not ship.",
               "Supersedes KV-3130. Drawings were never migrated off the microfiche.",
               "",
               "-- R. Alderman, cycle count",
           ]))

    # Brantwood memo: a real memo with that number, misfiled as a ledger.
    _write(os.path.join(ws, "corpus", "misc", "ledger_p0771.tsv"),
           "\n".join([
               "MEMORANDUM MEMO-77-4412",
               "TO: Purchasing",
               "FROM: H. Vasquez",
               "RE: vendor correspondence",
               "",
               "Brantwood will not reissue. Treat MEMO-77-4412 as closed.",
               "Original is in the offsite box; this is the file copy.",
               "",
               "-- H. Vasquez",
           ]))

    # Pellhurst split, corpus side: the old number without the brand.
    # Grep for "Pellhurst" will not hit this. Grep for "SG-19" will.
    _write(os.path.join(ws, "corpus", "receiving", "night_shift_crate.txt"),
           "\n".join([
               "RECEIVING EXCEPTION",
               "crate unmarked, left on dock after night shift",
               "",
               "contents: 4x SG-19 damper coupling",
               "manufacturer field: (blank)",
               "loc C12-8",
               "no packing slip. do not put in archive until identified.",
           ]))

    # Pellhurst split, clerk side: the brand and the new number.
    _write(os.path.join(home("clerk"), "found_on_desk.txt"),
           "\n".join([
               "found this by the printer, nobody claimed it",
               "",
               "the SG-19 is Pellhurst. they restickered everything to PH-SG19-C last year",
               "warehouse said the crate is still on the dock, not in the archive",
               "",
               "if this is yours please take it",
           ]))

    # Halcyon: leftover datasheet in the searcher's directory. Looks like
    # a previous occupant downloaded it and forgot. Not in the corpus.
    _write(os.path.join(home("searcher"), "scratch", "HD8B_datasheet.txt"),
           "\n".join([
               "HALCYON INDUSTRIAL — internal scan, 12 Mar 2009",
               "Datasheet: Duplex Model 8B",
               "",
               "Status: DISCONTINUED 2009",
               "Supercedes Model 8A. Not listed in the current catalogue.",
               "Serial range: HD8B-1000 through HD8B-4400",
               "Service parts: none stocked after 2011.",
               "",
               "This scan is not for distribution.",
           ]))

    # Osgood and Verrick are intentionally unplanted.


def pulse(tick):
    """A new non-target file appears in receiving. The world is not frozen."""
    body = memo_file(10000 + tick)
    blocked = _blocked()
    if any(b in body.lower() for b in blocked):
        body = inventory_file(10000 + tick)
    name = f"pulse_{tick:04d}.txt"
    path = os.path.join(config.WORKSPACE, "corpus", "receiving", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body)
    return os.path.relpath(path, config.WORKSPACE)


def build(count=420):
    n = _junk_corpus(count)
    _plants()
    return os.path.join(config.WORKSPACE, "corpus"), n


if __name__ == "__main__":
    path, n = build()
    print(f"wrote {n} junk files plus plants to {path}")
