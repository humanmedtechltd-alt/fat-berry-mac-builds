"""Inspect exact private installer payload exports on a disposable Mac."""
import json
import os
from pathlib import Path
import re
import subprocess
from build_product import transfer, digest


def main():
    if os.environ.get('RUNNER_OS') != 'macOS' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('Symbol audit requires a disposable Mac runner')
    root=Path('work').resolve();root.mkdir();results=[]
    identity=os.environ['INPUT_INDEX_SHA256']
    if not re.fullmatch('[0-9a-f]{64}',identity):raise ValueError('Immutable audit identity required')
    transfer('get','input/symbols-'+identity+'.json',root/'index.json')
    if digest(root/'index.json')!=identity:raise ValueError('Audit input changed')
    request=json.loads((root/'index.json').read_text())
    with (root/'symbols.log').open('w') as log:
        if request.get('linkerSelfTest'):
            source=root/'boundary.cpp'
            source.write_text('namespace juce { __attribute__((visibility("default"))) int privateImplementation() { return 7; } }\n'
                              'extern "C" __attribute__((visibility("default"))) int formatEntry() { return juce::privateImplementation(); }\n')
            symbols=root/'private.txt';symbols.write_text('__Z*4juce*\n')
            library=root/'boundary.dylib'
            subprocess.run(['xcrun','clang++','-dynamiclib','-arch','arm64','-arch','x86_64','-fvisibility=hidden',
                            '-Wl,-unexported_symbols_list,'+str(symbols),str(source),'-o',str(library)],check=True,stdout=log,stderr=log,timeout=60)
            exports=subprocess.check_output(['nm','-gjU',str(library)],text=True,stderr=log,timeout=20)
            if '_formatEntry' not in exports or re.search(r'_+Z(N[KVO]?4juce|T[VIS]N4juce)',exports):
                raise ValueError('The Mac linker did not preserve the format entry and hide private symbols')
            host=root/'host.cpp';host.write_text('#include <dlfcn.h>\nint main(int n,char** v){if(n!=2)return 1;auto h=dlopen(v[1],RTLD_NOW|RTLD_LOCAL);if(!h)return 2;auto f=(int(*)())dlsym(h,"formatEntry");return !f||f()!=7||dlsym(h,"_ZN4juce21privateImplementationEv")!=nullptr?3:0;}\n')
            binary=root/'host'
            subprocess.run(['xcrun','clang++','-arch','arm64','-arch','x86_64',str(host),'-o',str(binary)],check=True,stdout=log,stderr=log,timeout=60)
            for architecture in ('arm64','x86_64'):
                subprocess.run(['arch','-'+architecture,str(binary),str(library)],check=True,stdout=log,stderr=log,timeout=20)
        for entry in request['products']:
            if not re.fullmatch('[a-z0-9-]+',entry['slug']):raise ValueError('Invalid product slug')
            work=root/entry['slug'];work.mkdir();dmg=work/'installer.dmg';mount=work/'mounted';mount.mkdir()
            try:
                transfer('get',entry['path'],dmg)
                if digest(dmg)!=entry['sha256']:raise ValueError('Actual installer changed')
                subprocess.run(['hdiutil','attach',str(dmg),'-readonly','-nobrowse','-mountpoint',str(mount)],check=True,stdout=log,stderr=log,timeout=60)
                packages=list(mount.glob('*.pkg'))
                if len(packages)!=1:raise ValueError('Expected one actual PKG')
                expanded=work/'expanded'
                subprocess.run(['pkgutil','--expand-full',str(packages[0]),str(expanded)],check=True,stdout=log,stderr=log,timeout=120)
                found=[]
                for module in expanded.rglob('*'):
                    if not module.is_file() or module.parent.name!='MacOS':continue
                    if not any(p.suffix in {'.vst3','.component'} for p in module.parents):continue
                    if digest(module) not in entry['moduleHashes']:raise ValueError('Expanded module differs from accepted payload')
                    found.append(digest(module))
                    exports=subprocess.check_output(['nm','-gjU',str(module)],text=True,stderr=log,timeout=30)
                    leaked=[line for line in exports.splitlines() if re.search(r'_+Z(N[KVO]?4juce|T[VIS]N4juce)',line)]
                    results.append(dict(slug=entry['slug'],bundle=module.parent.parent.parent.name,
                                        moduleSha256=digest(module),privateJuceExports=leaked))
                if sorted(found)!=sorted(entry['moduleHashes']):raise ValueError('Actual installer format inventory differs')
            finally:
                subprocess.run(['hdiutil','detach',str(mount)],stdout=log,stderr=log,timeout=60,check=False)
    (root/'result.json').write_text(json.dumps({'status':'failed' if any(p['privateJuceExports'] for p in results) else 'passed','products':results},indent=2)+'\n')
    remote='output/diagnostics/'+os.environ['GITHUB_RUN_ID']+'/symbols/'
    for path in (root/'symbols.log',root/'result.json'):transfer('put',remote+path.name,path)
    print('Actual installer symbol audit complete; details remain private')
    if any(p['privateJuceExports'] for p in results):raise SystemExit(1)


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        root=Path('work').resolve();root.mkdir(exist_ok=True)
        result=root/'result.json';result.write_text(json.dumps({'status':'failed','errorType':type(error).__name__})+'\n')
        remote='output/diagnostics/'+os.environ['GITHUB_RUN_ID']+'/symbols/'
        for path in (root/'symbols.log',result):
            if path.is_file():transfer('put',remote+path.name,path)
        print('Actual installer symbol audit failed; diagnostics remain private')
        raise SystemExit(1)
