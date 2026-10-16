"""Create an Inno script from a product's explicit audited installation contract."""
from pathlib import Path


def write_script(product: dict, runtime: dict, tooling: Path, design: Path, payload: Path, redist: Path, final: Path, script: Path) -> None:
    kit = tooling / "packaging/windows/installer-design"
    def define(name: str, value: str) -> str:
        if '"' in value or "\n" in value:
            raise ValueError("Invalid installer preprocessor value")
        return f'#define {name} "{value}"'
    lines = [define("ProductName", product["name"]), define("ProductVersion", product["version"]),
             define("Vst3BundleName", product["vst3Bundle"]), define("OutputSlug", product["slug"]),
             define("FbKitDir", str(kit)), define("FbDesignDir", str(design))]
    lines += ["[Setup]", *product["setup"], f"OutputDir={final.parent}", "OutputBaseFilename=installer-win",
              "Compression=lzma2", "SolidCompression=yes", "PrivilegesRequired=admin", f"MinVersion={product['minimumWindows']}"]
    lines += [f'#include "{kit / "FatBerryWizard.isi"}"', "[Files]"]
    for entry in product["fileEntries"]:
        lines.append(entry.replace("@PAYLOAD@", str(payload)))
    for addition in product.get("additions", []):
        relative = Path(addition["path"])
        if relative.is_absolute() or ".." in relative.parts or '"' in str(relative) or "\n" in str(relative):
            raise ValueError("Invalid required content destination")
        lines.append(f'Source: "{payload / relative}"; DestDir: "{relative.parent}"; Flags: ignoreversion')
    lines.extend(product["additionalSections"])
    # Keep product upgrade, staging, recovery and uninstall behavior.
    # Decompiled file listings do not contain the original Pascal code.
    if product["installerCode"]:
        lines.append(product["installerCode"])
    if product["runtimeImports"]:
        lines += [define("FatBerryVcRedistPath", str(redist)), define("FatBerryVcRedistSha256", runtime["sha256"])]
        lines += [f"#define FatBerryVc{name} {value}" for name, value in zip(("Major", "Minor", "Build", "Revision"), runtime["version"].split("."))]
        lines += [f'#include "{tooling / "packaging/windows/FatBerryVcRuntime.isi"}"']
    script.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
