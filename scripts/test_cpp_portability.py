"""Run a small hash-pinned private C++ regression before full plugin builds."""
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
from build_product import transfer, digest


def main():
    if os.environ.get('RUNNER_OS') != 'macOS':
        raise ValueError('This portability check requires the standard Mac runner')
    identity = os.environ['INPUT_INDEX_SHA256']
    if not re.fullmatch('[0-9a-f]{64}', identity):
        raise ValueError('A frozen private portability index is required')
    root = Path('work').resolve(); root.mkdir()
    status = {'status':'failed','stage':'cpp-portability','inputIndexSha256':identity}
    log_path = root / 'portability.log'
    remote = 'output/diagnostics/' + os.environ['GITHUB_RUN_ID'] + '/portability'
    try:
        transfer('get','input/portability-'+identity+'.json',root/'index.json')
        if digest(root/'index.json') != identity: raise ValueError('Portability input changed')
        index = json.loads((root/'index.json').read_text())
        status.update(slug=index['slug'],sourceSnapshot=index['source']['sourceSnapshot'])
        archive = root/'source.tar.gz'
        transfer('get','input/'+index['source']['file'],archive)
        if digest(archive)!=index['source']['sha256']: raise ValueError('Private source changed')
        with tarfile.open(archive) as data: data.extractall(root/'source',filter='data')
        with log_path.open('w') as log:
            command = [part.replace('{source}',str(root/'source')).replace('{output}',str(root/'test'))
                       for part in index['compileArguments']]
            subprocess.run(['xcrun','clang++','-std=c++20','-arch','arm64','-arch','x86_64',
                            '-mmacosx-version-min='+index['minimumOS'],*command],
                           check=True,stdout=log,stderr=log,timeout=180)
            for architecture in ['arm64','x86_64']:
                log.write('ARCHITECTURE: '+architecture+'\n');log.flush()
                subprocess.run(['arch','-'+architecture,str(root/'test')],check=True,
                               stdout=log,stderr=log,timeout=30)
        status['status']='passed'
    except Exception as error:
        with log_path.open('a') as log:log.write('\n'+type(error).__name__+': '+str(error)+'\n')
    finally:
        (root/'status.json').write_text(json.dumps(status)+'\n')
        for path in [log_path,root/'status.json']:transfer('put',remote+'/'+path.name,path)
    if status['status']!='passed':raise SystemExit(1)
    print('Private C++ portability regression passed on both architectures')


if __name__=='__main__':main()
