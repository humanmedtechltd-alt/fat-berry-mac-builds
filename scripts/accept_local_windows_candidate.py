"""Clean-runner acceptance of exact bytes already packaged on the local host."""
import json
from pathlib import Path
import subprocess
from build_product import digest,input_object,transfer,output_prefix
from windows_payload import expected_payload,verify_payload

def accept(product,index,root,log,checkpoint):
    slug=product['slug'];candidate=product['localCandidate'];final=root/'release/installer-win.exe';final.parent.mkdir()
    incoming=input_object(candidate,root);incoming.replace(final)
    if final.stat().st_size!=candidate['bytes']or digest(final)!=candidate['sha256']:
        raise ValueError('Locally packaged installer differs from immutable candidate')
    expected=expected_payload(product)
    if candidate.get('payloadFiles')!=list(expected.values())or candidate.get('validation')!={
        'archiveIntegrity':'passed','payloadHashes':'passed','microsoftRuntimeSignatures':'passed'}:
        raise ValueError('Local packaging proof is incomplete')
    checkpoint('verify-final-payload')
    innounp=root/'tools/innounp.exe';verified=root/'verified'
    for args in [(str(innounp),'-t','-b','-q',str(final)),
                 (str(innounp),'-x','-a','-m','-y','-b','-d'+str(verified),str(final))]:
        subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    verify_payload(verified,expected)
    module_sha=product.get('compiled',{}).get('moduleSha256',product['original']['vst3Sha256'])
    if digest(verified/product['modulePath'])!=module_sha:raise ValueError('Clean-runner plugin module differs')
    if product['runtimeImports']and product['needsRepackage']and digest(verified/'{tmp}/FatBerry-VC_redist.x64.exe')!=index['runtime']['sha256']:
        raise ValueError('Delivered VC++ runtime differs')
    if product.get('webView2Runtime')and digest(verified/'{tmp}/FatBerry-WebView2Setup.exe')!=product['webView2Runtime']['sha256']:
        raise ValueError('Delivered browser runtime differs')
    proof={'slug':slug,'sha256':digest(final),'sizeBytes':final.stat().st_size,'buildInputSha256':product['buildInputSha256'],
        'payloadFiles':list(expected.values()),'vst3Sha256':module_sha,'compilation':product.get('compiled'),
        'packagingFingerprint':candidate['packagingFingerprint'],'validation':{'archiveIntegrity':'passed','payloadHashes':'passed'}}
    (final.parent/'candidate.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    for file in [final,final.parent/'candidate.json']:transfer('put',output_prefix('windows',slug)+'/'+file.name,file)
    checkpoint('install-upgrade-scan-uninstall')
    from test_windows_install import validate_install
    from test_windows_wizard import validate_wizard
    validator=next(p for p in(root/'pluginval').rglob('pluginval.exe')if p.is_file())
    installation=validate_install(product,final,root,log,validator)
    installation.update(validate_wizard(final,root,log))
    record={**product['original'],**{k:proof[k]for k in ['sha256','sizeBytes','payloadFiles','vst3Sha256','compilation','packagingFingerprint']},
        'sourceCommit':product.get('compiled',{}).get('sourceCommit',product['original']['sourceCommit']),
        'sourceSnapshot':product.get('compiled',{}).get('sourceSnapshot',product['original']['sourceSnapshot']),
        'inputIndexSha256':__import__('os').environ['INPUT_INDEX_SHA256'],'auditedBuildInputSha256':product['buildInputSha256'],
        'runtimeIncluded':bool(product['runtimeImports']),
        'validation':{**proof['validation'],'moduleHash':'passed','currentBuildInputsMatch':True,**installation,'activation':'pending'}}
    if product.get('ownerApprovedBinary'):
        record['ownerApprovedBinary']=product['ownerApprovedBinary']
        record['validation'].pop('currentBuildInputsMatch')
        record['validation'].update(ownerApprovedModuleHash='passed',currentSourceCompilationClaim=False)
    (final.parent/'release.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    checkpoint('upload')
    for file in final.parent.iterdir():transfer('put',output_prefix('windows',slug)+'/'+file.name,file)
    checkpoint('release-acceptance','candidate')
