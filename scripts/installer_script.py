"""Create an Inno script from a product's explicit audited installation contract."""
from pathlib import Path
import re
from windows_payload import archive_path


def preserve_customer_directories(section: str, bundle: str) -> str:
    """Inno removes its own recorded files; shared preset folders must survive."""
    if not section.lstrip().lower().startswith(('[uninstalldelete]', '[installdelete]')):
        return section
    lines=[]
    for line in section.splitlines():
        match=re.search(r'Type:\s*filesandordirs\s*;\s*Name:\s*"([^"]+)"',line,re.I)
        if match:
            target=match[1].replace('/','\\').rstrip('\\')
            if target.rsplit('\\',1)[-1].lower()!=bundle.lower():
                if '*'in target or '?'in target:raise ValueError('Review wildcard uninstall deletion outside the plugin bundle')
                line=re.sub(r'Type:\s*filesandordirs', 'Type: dirifempty',line,flags=re.I)
        lines.append(line)
    return '\n'.join(lines)+'\n'


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
        relative = Path(archive_path(addition))
        if relative.is_absolute() or ".." in relative.parts or '"' in str(relative) or "\n" in str(relative):
            raise ValueError("Invalid required content destination")
        lines.append(f'Source: "{payload / relative}"; DestDir: "{relative.parent}"; Flags: ignoreversion')
    lines.extend(preserve_customer_directories(section,product['vst3Bundle'])for section in product["additionalSections"])
    # Keep product upgrade, staging, recovery and uninstall behavior.
    # Decompiled file listings do not contain the original Pascal code.
    if product["installerCode"]:
        lines.append(product["installerCode"])
    if product["runtimeImports"]:
        lines += [define("FatBerryVcRedistPath", str(redist)), define("FatBerryVcRedistSha256", runtime["sha256"])]
        lines += [f"#define FatBerryVc{name} {value}" for name, value in zip(("Major", "Minor", "Build", "Revision"), runtime["version"].split("."))]
        lines += [f'#include "{tooling / "packaging/windows/FatBerryVcRuntime.isi"}"']
    if product.get("webView2Runtime"):
        webview = product["webView2Runtime"]
        bootstrapper = tooling / webview["toolingPath"]
        if not bootstrapper.resolve().is_relative_to(tooling.resolve()):
            raise ValueError("WebView2 bootstrapper escapes the private tooling snapshot")
        lines += [define("FatBerryWebView2BootstrapperPath", str(bootstrapper)),
                  define("FatBerryWebView2BootstrapperSha256", webview["sha256"]),
                  f'#define FatBerryWebView2MinimumMajor {int(webview["minimumMajor"])}',
                  f'#include "{tooling / "packaging/windows/FatBerryWebView2Runtime.isi"}"']
    script.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
