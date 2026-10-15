"""Stable compile inputs; packaging-only changes do not invalidate bundles."""
import hashlib
import json


def fingerprint(index: dict, product: dict) -> str:
    fields = ("slug", "version", "sourceSnapshot", "framework", "minimumOS", "vst3Target", "auTarget",
              "vst3Bundle", "auBundle", "manufacturerCode", "pluginCode", "auType", "cmakeArguments",
              "keygenProductId", "keygenPolicyId", "licensingClientCommit")
    value = {field: product[field] for field in fields}
    value["dependencies"] = {d["directory"]: d.get("sourceSnapshot", d["sha256"])
                             for d in [*index["dependencies"], *product.get("dependencies", [])]
                             if d["directory"] not in {"pluginval", "fat-berry-release-tooling"}}
    value.update(toolingCompileSha256=index["toolingCompileSha256"], sodiumSha256=index["sodiumSha256"],
                 keygenPublicKey=index["keygenPublicKey"], licensingApiOrigin=index["licensingApiOrigin"],
                 toolchain="macos-15-Xcode16.4-cmake4.2.3-universal-v1")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
