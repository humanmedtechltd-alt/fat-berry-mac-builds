"""Bounded disposable-VM diagnostics; this never creates release acceptance."""
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import time
from build_product import transfer, digest
from macos_delivery_checks import exercise_recovery


def main():
    identity = os.environ['INPUT_INDEX_SHA256']
    if os.environ.get('RUNNER_OS') != 'macOS' or not re.fullmatch('[0-9a-f]{64}', identity):
        raise ValueError('A Mac runner and exact private input are required')
    root = Path('work').resolve(); root.mkdir()
    status = {'status': 'failed', 'stage': 'mac-delivery-diagnostics', 'inputIndexSha256': identity, 'observations': {}}
    log_path = root / 'delivery.log'; mounted = False
    try:
        transfer('get', 'input/activation-acceptance-' + identity + '.json', root / 'index.json')
        if digest(root / 'index.json') != identity: raise ValueError('Input changed')
        index = json.loads((root / 'index.json').read_text())
        candidate = index['platforms']['macos']['candidate']; product = index['product']
        status.update(slug=product['slug'], installerSha256=candidate['sha256'])
        transfer('get', 'input/' + candidate['file'], root / 'installer.dmg')
        if digest(root / 'installer.dmg') != candidate['sha256']: raise ValueError('Installer changed')
        with log_path.open('w') as log:
            def run(command, timeout=60):
                subprocess.run(command, check=True, stdout=log, stderr=log, timeout=timeout)
            mount = root / 'mounted'; mount.mkdir()
            run(['hdiutil', 'attach', str(root / 'installer.dmg'), '-nobrowse', '-readonly', '-mountpoint', str(mount)])
            mounted = True
            run(['sudo', 'installer', '-pkg', str(next(mount.glob('*.pkg'))), '-target', '/'], timeout=180)
            bundles = [Path('/Library/Audio/Plug-Ins/VST3') / product['vst3Bundle'],
                       Path('/Library/Audio/Plug-Ins/Components') / product['auBundle']]
            component = plistlib.loads((bundles[1] / 'Contents/Info.plist').read_bytes())['AudioComponents'][0]
            codes = [component[k] for k in ('type', 'subtype', 'manufacturer')]
            def observe(stage):
                log.write('\nSTAGE ' + stage + '\n'); log.flush()
                result = subprocess.run(['arch', '-arm64', '/usr/bin/auval', '-a'], capture_output=True, text=True, timeout=45)
                log.write(result.stdout + result.stderr); log.flush()
                present = any(line.split()[:3] == codes for line in result.stdout.splitlines())
                status['observations'][stage] = {'discovered': present, 'exitCode': result.returncode}
                return present
            observe('actual-install')
            exercise_recovery(mount, bundles, run)
            observe('delivered-recovery')
            subprocess.run(['killall', '-9', 'AudioComponentRegistrar'], stdout=log, stderr=log, timeout=30)
            run(['sudo', 'touch', str(bundles[1]), str(bundles[1].parent)])
            time.sleep(3)
            observe('user-registrar-restart')
            subprocess.run(['sudo', 'killall', '-9', 'AudioComponentRegistrar'], stdout=log, stderr=log, timeout=30)
            time.sleep(3)
            observe('all-registrar-restart')
            for cache in [Path.home() / 'Library/Caches/AudioUnitCache', Path('/Library/Caches/AudioUnitCache')]:
                if cache.exists():
                    run(['sudo', 'mv', str(cache), str(cache) + '.fatberry-discovery-diagnostics'])
            subprocess.run(['sudo', 'killall', '-9', 'AudioComponentRegistrar'], stdout=log, stderr=log, timeout=30)
            time.sleep(3)
            if observe('cache-relocated'):
                for architecture in ['arm64', 'x86_64']:
                    run(['arch', '-' + architecture, '/usr/bin/auval', '-v', *codes], timeout=120)
            run(['ps', '-axo', 'user,pid,comm'])
            # Registration diagnostics contain no QA credentials; none are issued.
            subprocess.run(['log', 'show', '--last', '5m', '--style', 'compact', '--predicate',
                            'process == "AudioComponentRegistrar"'], stdout=log, stderr=log, timeout=30)
        status['status'] = 'diagnostics-collected'
    except Exception as error:
        with log_path.open('a') as log: log.write(type(error).__name__ + ': ' + str(error) + '\n')
    finally:
        if mounted: subprocess.run(['hdiutil', 'detach', str(root / 'mounted')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        (root / 'status.json').write_text(json.dumps(status, indent=2) + '\n')
        remote = 'output/diagnostics/' + os.environ['GITHUB_RUN_ID'] + '/mac-delivery'
        for path in [root / 'status.json', log_path]: transfer('put', remote + '/' + path.name, path)
    if status['status'] == 'failed': raise SystemExit(1)
    print('Private installer discovery diagnostics collected; no release acceptance granted')


if __name__ == '__main__': main()
