"""Audit author data and publish one immutable pair pool and split snapshot."""
import argparse
import csv
import hashlib
import json
import math
import os
import pickle
import random
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from sklearn.model_selection import KFold, train_test_split

SEED = 42
SOURCE = "https://github.com/hyr-hit/Struct2SL"
DATA = "https://doi.org/10.6084/m9.figshare.33439123.v2"
RAW = Path("../data/Struct2SL_process_data/Struct2SL")


def checksum(path):
    with path.open("rb") as stream:
        sha = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def load_features(raw):
    with ZipFile(raw / "struct_features.zip") as archive:
        structure = {}
        for name in archive.namelist():
            path = Path(name)
            if path.parent != Path("struct_features") or path.suffix != ".txt":
                continue
            if path.stem in structure:
                raise ValueError(f"Duplicate structure entry: {name}")
            with archive.open(name) as stream:
                structure[path.stem] = np.asarray(stream.readline().split(), dtype=np.float32)

    def read_pickle(name):
        with (raw / name).open("rb") as stream:
            return pickle.load(stream)

    return {
        "structure": structure,
        "sequence": read_pickle("sequence_features.npz"),
        "ppi": read_pickle("ppi_features.npz"),
    }


def read_pairs(path, delimiter):
    counts = {"rows": 0, "missing_gene": 0, "self_pairs": 0, "duplicates": 0,
              "missing_by_column": {}}
    pairs = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=delimiter)
        counts["columns"] = reader.fieldnames
        for row in reader:
            counts["rows"] += 1
            for column in reader.fieldnames:
                if not row[column]:
                    missing = counts["missing_by_column"]
                    missing[column] = missing.get(column, 0) + 1
            a, b = (row[key].strip() for key in ("n1.name", "n2.name"))
            if not a or not b:
                counts["missing_gene"] += 1
            elif a == b:
                counts["self_pairs"] += 1
            else:
                pair = tuple(sorted((a, b)))
                counts["duplicates"] += int(pair in pairs)
                pairs.add(pair)
    counts["unique_pairs"] = len(pairs)
    counts["unique_genes"] = len({gene for pair in pairs for gene in pair})
    return pairs, counts


def write_csv(path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def prepare(raw, out, upstream):
    if out.exists():
        raise FileExistsError(f"Snapshot already exists: {out}. Reuse it for both variants.")
    revision = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"],
                                       text=True).strip()
    script = upstream / "Struct2SL.py"
    if subprocess.check_output(["git", "-C", str(upstream), "show", "HEAD:Struct2SL.py"]) != script.read_bytes():
        raise ValueError("Upstream training script differs from its recorded commit")
    features = load_features(raw)
    genes = sorted(set.intersection(*(set(group) for group in features.values())))
    matrices = {}
    widths = {}
    for name, group in features.items():
        rows = [np.asarray(group[gene], dtype=np.float32).ravel() for gene in genes]
        widths[name] = len(rows[0])
        if not widths[name] or any(len(row) != widths[name] for row in rows):
            raise ValueError(f"Inconsistent {name} vector widths")
        matrices[name] = np.stack(rows)
        if not np.isfinite(matrices[name]).all():
            raise ValueError(f"Non-finite values in {name}")

    observed, observed_audit = read_pairs(raw / "Human_SL.csv", ",")
    filtered, positive_audit = read_pairs(raw / "Human_SL_ff.csv", "\t")
    known_neg, negative_audit = read_pairs(raw / "Human_nonSL.csv", ",")
    if not filtered <= observed:
        raise ValueError("Filtered positives must be a subset of the complete SL list")
    valid = set(genes)

    def eligible(pairs):
        return {pair for pair in pairs if valid.issuperset(pair)}

    conflicts = observed & known_neg
    positives = sorted(eligible(filtered - conflicts))
    negatives = sorted(eligible(known_neg - observed))
    excluded = eligible(observed | known_neg)
    unobserved_count = math.comb(len(genes), 2) - len(excluded)
    if len(positives) < 5 or len(negatives) + unobserved_count < len(positives):
        raise ValueError(f"Cannot construct 1:1 pool: {len(positives)} positives, "
                         f"{len(negatives)} known and {unobserved_count} unobserved candidates")
    rng = random.Random(SEED)
    if len(negatives) > len(positives):
        negatives = sorted(rng.sample(negatives, len(positives)))
    assumed = set()
    while len(assumed) < len(positives) - len(negatives):
        pair = tuple(sorted(rng.sample(genes, 2)))
        if pair not in excluded:
            assumed.add(pair)
    rows = [(a, b, label, source)
            for pairs, label, source in [(positives, 1, "filtered_sl"),
                                         (negatives, 0, "reported_non_sl"),
                                         (sorted(assumed), 0, "assumed_negative")]
            for a, b in pairs]
    ids = [json.dumps([a, b], separators=(",", ":")) for a, b, _, _ in rows]
    assert len(set(ids)) == len(ids)
    outer_train, outer_test = train_test_split(np.arange(len(rows)), test_size=0.2, random_state=SEED)
    ranks = np.empty(len(rows), dtype=int)
    ranks[outer_train] = np.arange(len(outer_train))
    ranks[outer_test] = np.arange(len(outer_test))
    folds = np.zeros(len(rows), dtype=int)
    for fold, (_, val) in enumerate(KFold(5, shuffle=True, random_state=SEED).split(outer_train), 1):
        folds[outer_train[val]] = fold
    assert np.array_equal(np.flatnonzero(folds == 0), np.sort(outer_test))

    columns = {side: {group: [f"{side}.{group}.{i}" for i in range(width)]
                      for group, width in widths.items()} for side in ("a", "b")}
    schema = {variant: [col for side in columns.values() for group, cols in side.items()
                        if variant == "full" or group != "structure" for col in cols]
              for variant in ("full", "no_structure")}
    names = ["Human_SL.csv", "Human_SL_ff.csv", "Human_nonSL.csv", "struct_features.zip",
             "sequence_features.npz", "ppi_features.npz", "filter.py", "Human_SL_f.csv",
             "gene_sl_gene.tsv"]
    manifest = {
        "upstream": SOURCE, "revision": revision, "upstream_script_sha256": checksum(script),
        "data_source": DATA, "download_date": None, "sha256": {name: checksum(raw / name) for name in names},
        "tables": {"all_sl": observed_audit, "filtered_sl": positive_audit, "non_sl": negative_audit},
        "filter": "Human_SL_ff.csv (TSV): exclude sources containing Computational Prediction; "
                  "retain blank source; require complete features at both endpoints",
        "pair_key": "trim case-sensitive gene symbols and lexicographically order both endpoints",
        "conflict_rule": "exclude any disagreement between the complete SL list and reported non-SL",
        "conflicting_pairs": len(conflicts), "eligible_conflicts": len(eligible(conflicts)),
        "eligible_filtered_before_conflicts": len(eligible(filtered)),
        "negative_pool": "prioritize eligible reported non-SL, fill uniformly without replacement "
                         "from unordered pairs of complete-feature genes excluding both source lists",
        "negative_to_positive_ratio": 1,
        "eligible_reported_negative_candidates": len(eligible(known_neg - observed)),
        "unobserved_candidates": unobserved_count,
        "counts": {"complete_feature_genes": len(genes), "used_genes": len({g for a,b,_,_ in rows for g in (a,b)}),
                   "positive": len(positives), "reported_non_sl": len(negatives),
                   "assumed_negative": len(assumed), "total": len(rows)},
        "feature_source_gene_counts": {name: len(group) for name, group in features.items()},
        "widths": widths, "seed": SEED, "split": "unstratified train_test_split 0.2; shuffled KFold(5)",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as temporary:
        staging = Path(temporary) / "snapshot"
        staging.mkdir()
        write_csv(staging / "pairs.csv", ["pair_id", "gene_a", "gene_b", "label", "source"],
                  ((key, *row) for key, row in zip(ids, rows)))
        write_csv(staging / "sampled_pairs.csv", ["pair_id", "gene_a", "gene_b", "label", "source"],
                  ((key, *row) for key, row in zip(ids, rows) if row[2] == 0))
        write_csv(staging / "outer_split.csv", ["pair_id", "split", "order"],
                  ((key, "test" if fold == 0 else "train", rank)
                   for key, fold, rank in zip(ids, folds, ranks)))
        write_csv(staging / "fold_assignments.csv", ["pair_id", "validation_fold"], zip(ids, folds))
        write_csv(staging / "excluded_conflicts.csv", ["gene_a", "gene_b"], sorted(conflicts))
        np.savez(staging / "gene_features.npz", genes=np.array(genes), **matrices)
        (staging / "feature_schema.json").write_text(json.dumps(schema, indent=2) + "\n")
        manifest["snapshot_sha256"] = {path.name: checksum(path) for path in sorted(staging.iterdir())}
        (staging / "data_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.rename(staging, out)
    print(json.dumps(manifest["counts"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=RAW)
    parser.add_argument("--upstream", type=Path, default=Path("environment/upstream"))
    parser.add_argument("--out", type=Path, default=Path("../data/processed/struct2sl"))
    args = parser.parse_args()
    prepare(args.raw, args.out, args.upstream)
