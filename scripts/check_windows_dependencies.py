"""One private clean-container load test of existing Windows plugin modules."""
import datetime,json,os,subprocess,zipfile,shutil
from pathlib import Path
from build_product import transfer,input_object,digest

CHECK=r'''
$ErrorActionPreference='Stop'
$request=Get-Content C:\acceptance\request.json -Raw | ConvertFrom-Json
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NativePluginLoad {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] public static extern IntPtr LoadLibraryEx(string name,IntPtr file,uint flags);
 [DllImport("kernel32.dll")] public static extern bool FreeLibrary(IntPtr handle);
 public static int Check(string name){var handle=LoadLibraryEx(name,IntPtr.Zero,8);if(handle==IntPtr.Zero)return Marshal.GetLastWin32Error();FreeLibrary(handle);return 0;}
}
'@
$rows=@()
foreach($product in $request.products){
 $module=Join-Path C:\acceptance $product.module
 if((Get-FileHash -LiteralPath $module -Algorithm SHA256).Hash.ToLowerInvariant() -ne $product.sha256){throw 'Original plugin module changed'}
 $present=@($product.runtimeImports | Where-Object {Test-Path -LiteralPath (Join-Path $env:SystemRoot\System32 $_)})
 $rows+=@{slug=$product.slug;moduleSha256=$product.sha256;runtimeDllsPresent=$present;loadError=[NativePluginLoad]::Check($module)}
}
ConvertTo-Json -InputObject @($rows) -Depth 8 | Set-Content -LiteralPath ('C:\acceptance\'+$env:FATBERRY_DEPENDENCY_PHASE+'.json') -Encoding UTF8
'''


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_OS')!='Windows' or os.environ.get('FATBERRY_RUNNER_ENVIRONMENT')!='github-hosted':
        raise ValueError('Dependency assessment requires a disposable hosted Windows runner')
    identity=os.environ['INPUT_INDEX_SHA256'];root=Path('work').resolve();root.mkdir()
    remote='output/diagnostics/'+os.environ['GITHUB_RUN_ID']+'/windows-dependencies'
    status={'status':'failed','inputIndexSha256':identity}
    log=root/'assessment.log'
    try:
        pinned=root/'request-index.json';transfer('get','input/windows-dependencies-'+identity+'.json',pinned)
        if digest(pinned)!=identity:raise ValueError('Immutable dependency assessment changed')
        index=json.loads(pinned.read_text());archive=input_object(index['archive'],root);payload=root/'payload';payload.mkdir()
        with zipfile.ZipFile(archive) as data:
            for item in data.infolist():
                relative=Path(item.filename)
                if relative.is_absolute() or '..' in relative.parts or ':' in item.filename:raise ValueError('Unsafe dependency archive path')
            data.extractall(payload)
        (payload/'request.json').write_text(json.dumps({'products':index['products']}))
        redist=payload/'VC_redist.x64.exe'
        if digest(redist)!=index['runtimeSha256']:raise ValueError('Microsoft runtime checksum changed')
        (payload/'check.ps1').write_text(CHECK)
        image=index['containerImage']
        import re
        if not re.fullmatch(r'mcr\.microsoft\.com/windows/servercore@sha256:[0-9a-f]{64}',image):raise ValueError('Pinned Microsoft base image required')
        with log.open('w',encoding='utf-8') as output:
            def run(*args,timeout=1200):subprocess.run(args,stdout=output,stderr=subprocess.STDOUT,check=True,timeout=timeout)
            run('powershell.exe','-NoProfile','-Command',"$ErrorActionPreference='Stop';$s=Get-Service docker;if($s.Status -ne 'Running'){Start-Service docker;$s.WaitForStatus('Running',[TimeSpan]::FromSeconds(30))}")
            run('docker','pull',image)
            name='fatberry-dependencies-'+os.environ['GITHUB_RUN_ID']
            run('docker','create','--name',name,'--network','none','--isolation','process','--mount','type=bind,source='+str(payload)+',target=C:\acceptance',image,'cmd.exe','/c','ping -t 127.0.0.1 > nul')
            try:
                run('docker','start',name)
                run('docker','exec','-e','FATBERRY_DEPENDENCY_PHASE=before',name,'powershell.exe','-NoProfile','-File','C:\acceptance\check.ps1')
                run('docker','exec',name,'powershell.exe','-NoProfile','-Command',"$p=Start-Process C:\acceptance\VC_redist.x64.exe -ArgumentList '/install','/quiet','/norestart' -Wait -PassThru;if($p.ExitCode -notin @(0,3010)){throw 'Microsoft runtime installation failed'}")
                run('docker','exec','-e','FATBERRY_DEPENDENCY_PHASE=after',name,'powershell.exe','-NoProfile','-File','C:\acceptance\check.ps1')
            finally:run('docker','rm','-f',name,timeout=60)
        before={p['slug']:p for p in json.loads((payload/'before.json').read_text(encoding='utf-8-sig'))}
        after={p['slug']:p for p in json.loads((payload/'after.json').read_text(encoding='utf-8-sig'))}
        results=[]
        for p in index['products']:
            b=before[p['slug']];a=after[p['slug']]
            if b['runtimeDllsPresent']:raise ValueError('Base image already has a required VC++ DLL')
            results.append({'slug':p['slug'],'moduleSha256':p['sha256'],'beforeLoadError':b['loadError'],
                'afterLoadError':a['loadError'],'requiredRuntimeCorrection':b['loadError']==126 and a['loadError']==0})
        status.update(status='passed',products=results,containerImage=image,network='none')
    finally:
        status['updatedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        path=root/'status.json';path.write_text(json.dumps(status,indent=2)+'\n');transfer('put',remote+'/status.json',path)
        if log.exists():transfer('put',remote+'/assessment.log',log)


if __name__=='__main__':main()
