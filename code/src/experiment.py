"""Run matched Struct2SL training and compare predictions on the shared test set."""
import argparse
import copy
import importlib.metadata
import json
import os
import platform
import random
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from sklearn.metrics import average_precision_score, f1_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from prepare import SEED, checksum

VARIANTS = ("full", "no_structure")
METRICS = ("auroc", "ap", "f1_at_05")
BATCH_SIZE = 64
MAX_EPOCHS = 10_000
PATIENCE = 15


def load_snapshot(snapshot):
    manifest = json.loads((snapshot / "data_manifest.json").read_text())
    for name, digest in manifest["snapshot_sha256"].items():
        if checksum(snapshot / name) != digest:
            raise ValueError(f"Snapshot file changed: {name}")
    pairs = pd.read_csv(snapshot / "pairs.csv", keep_default_na=False).set_index("pair_id")
    outer = pd.read_csv(snapshot / "outer_split.csv").set_index("pair_id")
    folds = pd.read_csv(snapshot / "fold_assignments.csv").set_index("pair_id")
    if not (pairs.index.is_unique and pairs.index.equals(outer.index) and pairs.index.equals(folds.index)):
        raise ValueError("Pair pool and split IDs disagree")
    if not np.array_equal(outer.split == "test", folds.validation_fold == 0):
        raise ValueError("Test assignment disagrees with fold assignments")
    pairs = pairs.join(outer).join(folds).sort_values("order", kind="stable")
    return manifest, pairs


def pair_matrix(snapshot, pairs, variant):
    schema = json.loads((snapshot / "feature_schema.json").read_text())
    with np.load(snapshot / "gene_features.npz") as stored:
        genes = pd.Index(stored["genes"])
        groups = ("structure", "sequence", "ppi") if variant == "full" else ("sequence", "ppi")
        features = [stored[group] for group in groups]
        columns = [f"{side}.{group}.{i}" for side in ("a", "b")
                   for group, matrix in zip(groups, features) for i in range(matrix.shape[1])]
        assert columns == schema[variant]
        nonstructural = [col for col in schema["full"] if col.split(".")[1] != "structure"]
        assert schema["no_structure"] == nonstructural
        indices = [genes.get_indexer(pairs[f"gene_{side}"]) for side in ("a", "b")]
        assert all((index >= 0).all() for index in indices)
        matrix = np.empty((len(pairs), len(columns)), dtype=np.float32)
        offset = 0
        for index in indices:
            for feature in features:
                width = feature.shape[1]
                matrix[:, offset:offset + width] = feature[index]
                offset += width
    return matrix


def train_fold(model_cls, x, pairs, fold, output, device, epochs):
    started = time.monotonic()
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    output.mkdir(parents=True)
    assignments = pairs.validation_fold.to_numpy()
    train = (assignments != 0) & (assignments != fold)
    validation = assignments == fold
    test = assignments == 0
    assert np.all(train.astype(int) + validation + test == 1)
    assert train.any() and validation.any() and test.any()
    labels = pairs.label.to_numpy(dtype=np.float32)
    scaler = StandardScaler().fit(x[train])
    assert scaler.n_samples_seen_ == train.sum()

    def loader(mask, shuffle=False):
        inputs = torch.from_numpy(scaler.transform(x[mask], copy=False))
        truth = torch.from_numpy(labels[mask]).unsqueeze(1)
        return DataLoader(TensorDataset(inputs, truth), batch_size=BATCH_SIZE, shuffle=shuffle,
                          generator=torch.Generator().manual_seed(SEED))

    training = loader(train, shuffle=True)
    validating = loader(validation)
    model = model_cls(x.shape[1], [256, 128, 64], 1, dropout=0.5)
    model.sigmoid = nn.Identity()
    model.to(device)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.004, weight_decay=0.001)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, factor=0.5, patience=5)
    best_loss = float("inf")
    best_epoch = 0
    best_state = None
    with (output / "train.jsonl").open("w", buffering=1) as log:
        for epoch in range(1, epochs + 1):
            model.train()
            train_loss = 0.0
            for inputs, truth in training:
                optimizer.zero_grad()
                loss = criterion(model(inputs.to(device)), truth.to(device))
                loss += 0.001 * sum(parameter.norm(2) for parameter in model.parameters())
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                train_loss += loss.item()
            model.eval()
            with torch.inference_mode():
                val_loss = sum(criterion(model(inputs.to(device)), truth.to(device)).item()
                               for inputs, truth in validating) / len(validating)
            if not np.isfinite(val_loss):
                raise ValueError(f"Nonfinite validation loss at {output}, epoch {epoch}")
            scheduler.step(val_loss)
            log.write(json.dumps({"epoch": epoch, "train_loss": train_loss / len(training),
                                  "validation_loss": val_loss, "lr": optimizer.param_groups[0]["lr"]}) + "\n")
            if val_loss < best_loss:
                best_loss, best_epoch = val_loss, epoch
                best_state = copy.deepcopy(model.state_dict())
            elif epoch - best_epoch >= PATIENCE:
                break
    model.load_state_dict(best_state)
    torch.save({"state_dict": {key: value.cpu() for key, value in best_state.items()},
                "input_size": x.shape[1], "best_epoch": best_epoch, "validation_loss": best_loss},
               output / "checkpoint.pt")
    np.savez(output / "scaler.npz", mean=scaler.mean_, scale=scaler.scale_, n_samples=scaler.n_samples_seen_)
    with torch.inference_mode():
        prob = torch.cat([model(inputs.to(device)).sigmoid().cpu().flatten()
                          for inputs, _ in loader(test)]).numpy()
    pairs.loc[test, ["gene_a", "gene_b", "label"]].assign(probability=prob).to_csv(output / "test_predictions.csv")
    (output / "selection.json").write_text(json.dumps({"epoch": best_epoch, "validation_loss": best_loss,
        "epochs_run": epoch, "seconds": time.monotonic() - started}, indent=2) + "\n")
    print(f"{output}: selected epoch {best_epoch}, validation loss {best_loss:.5f}", flush=True)


def compare(output, pairs):
    expected = pairs.loc[pairs.validation_fold == 0, "label"]
    counts = {"positive": int(expected.sum()), "negative": int((expected == 0).sum())}
    rows = []
    for variant in VARIANTS:
        for fold in range(1, 6):
            pred = pd.read_csv(output / variant / f"fold_{fold}" / "test_predictions.csv").set_index("pair_id")
            if not pred.label.equals(expected) or not pred.probability.between(0, 1).all():
                raise ValueError(f"Unexpected predictions for {variant}, fold {fold}")
            y, p = pred.label, pred.probability
            rows.append({"variant": variant, "fold": fold, **counts,
                         "auroc": roc_auc_score(y, p), "ap": average_precision_score(y, p),
                         "f1_at_05": f1_score(y, p >= 0.5, zero_division=0)})
    results = pd.DataFrame(rows)
    paired = results.pivot(index="fold", columns="variant", values=list(METRICS))
    delta = paired.xs("full", axis=1, level=1) - paired.xs("no_structure", axis=1, level=1)
    directory = output / "comparison"
    directory.mkdir(exist_ok=True)
    results.to_csv(directory / "fold_metrics.csv", index=False)
    paired.columns = [f"{metric}_{variant}" for metric, variant in paired.columns]
    paired.join(delta.add_suffix("_delta")).to_csv(directory / "paired_metrics.csv")
    samples = {variant: results.loc[results.variant == variant, list(METRICS)] for variant in VARIANTS}
    samples["full_minus_no_structure"] = delta
    summary = [{"variant": variant, "metric": metric, "mean": values[metric].mean(),
                "std_ddof_0": values[metric].std(ddof=0), **counts}
               for variant, values in samples.items() for metric in METRICS]
    pd.DataFrame(summary).to_csv(directory / "summary.csv", index=False)
    fig, ax = plt.subplots(figsize=(7, 4), layout="constrained")
    for metric in METRICS:
        ax.plot(delta.index, delta[metric], marker="o", label=metric)
    ax.axhline(0, color="black", linewidth=1)
    ax.set(xlabel="Training fold", ylabel="Full minus no structure",
           xticks=list(delta.index), title="Paired differences on the shared test set")
    ax.legend()
    fig.savefig(directory / "paired_deltas.png", dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=Path("../data/processed/struct2sl"))
    parser.add_argument("--upstream", type=Path, default=Path("environment/upstream"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    manifest, pairs = load_snapshot(args.snapshot)
    if manifest["upstream_script_sha256"] != checksum(args.upstream / "Struct2SL.py"):
        raise ValueError("Upstream model source differs from the snapshot")
    sys.path.insert(0, str(args.upstream.resolve()))
    from Struct2SL import MLP

    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    device = torch.device(args.device)
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable inside the container")
    torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "1")))
    torch.use_deterministic_algorithms(True)
    epochs = 1 if args.smoke else MAX_EPOCHS
    args.output.mkdir(parents=True)
    run = {
        "snapshot": str(args.snapshot), "snapshot_manifest_sha256": checksum(args.snapshot / "data_manifest.json"),
        "upstream_revision": manifest["revision"], "seed": SEED, "epochs": epochs, "smoke": args.smoke,
        "software": {name: importlib.metadata.version(name)
                     for name in ("torch", "numpy", "pandas", "scikit-learn", "scipy", "matplotlib")},
        "python": sys.version, "platform": platform.platform(), "host": platform.node(),
        "device": torch.cuda.get_device_name(device) if args.device == "cuda" else "cpu",
        "cuda": torch.version.cuda, "job_id": os.environ.get("PBS_JOBID"),
        "hidden_sizes": [256, 128, 64], "dropout": 0.5, "batch_size": BATCH_SIZE,
        "optimizer": "Adam: lr=0.004, weight_decay=0.001", "loss": "BCEWithLogitsLoss",
        "norm_penalty": 0.001, "gradient_clip": 1.0,
        "scheduler": "ReduceLROnPlateau: factor=0.5, patience=5", "early_stop_patience": PATIENCE,
        "selection": "minimum validation loss", "scaling": "StandardScaler fit on training fold only",
        "test_threshold": 0.5, "test_protocol": "five models per variant on one shared outer test set",
        "implementation_sha256": {Path(file).name: checksum(Path(file))
                                  for file in (__file__, Path(__file__).with_name("prepare.py"))},
        "initialization": "upstream torch Linear defaults, reset shared seed before each fold",
    }
    path = args.output / "run_manifest.json"
    path.write_text(json.dumps(run, indent=2) + "\n")
    for variant in VARIANTS:
        x = pair_matrix(args.snapshot, pairs, variant)
        for fold in range(1, 6):
            train_fold(MLP, x, pairs, fold, args.output / variant / f"fold_{fold}", device, epochs)
        del x
    compare(args.output, pairs)
    run["seconds"] = time.monotonic() - started
    path.write_text(json.dumps(run, indent=2) + "\n")


if __name__ == "__main__":
    main()
