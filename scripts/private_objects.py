"""Immutable private release objects; never log credentials or source bytes."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY='humanmedtechltd-alt/fat-berry-release-tooling'
CHUNK_BYTES=1024**3

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class Store:
    def __init__(self,token=None,opener=None):
        self.token=token or os.environ['FATBERRY_PRIVATE_ASSET_TOKEN']
        self.opener=opener or urllib.request.build_opener(NoRedirect())

    def request(self,url,*,body=None,accept='application/vnd.github+json',headers=None):
        base={'Authorization':'Bearer '+self.token,'Accept':accept,'X-GitHub-Api-Version':'2022-11-28','User-Agent':'FatBerry-Immutable-Release'}
        request=urllib.request.Request(url,data=body,headers={**base,**(headers or {})})
        return self.opener.open(request,timeout=120)

    def api(self,path,body=None):
        for attempt in range(2):
            try:
                with self.request('https://api.github.com/'+path,body=json.dumps(body).encode()if body is not None else None,
                                  headers={'Content-Type':'application/json'}if body is not None else None)as stream:
                    return json.load(stream)
            except urllib.error.HTTPError as error:
                if body is not None or error.code not in [500,502,503,504]or attempt:raise
                time.sleep(1)

    def private(self):
        value=self.api('repos/'+REPOSITORY)
        if value.get('private')is not True:raise ValueError('Private installer storage visibility check failed')
        self.default_branch=value['default_branch']

    def release(self,sha):
        tag='installer-store-v1-'+sha[:2]
        def find():
            page=1
            while True:
                values=self.api(f'repos/{REPOSITORY}/releases?per_page=100&page={page}')
                selected=next((r for r in values if r['tag_name']==tag),None)
                if selected:return selected
                if len(values)<100:return None
                page+=1
        found=find()
        if found:return found
        try:return self.api('repos/'+REPOSITORY+'/releases',{'tag_name':tag,'target_commitish':self.default_branch,'draft':True,'name':tag,
            'body':'Private immutable installer and compilation objects; customer access stays licensed through fatberrydsp.com.'})
        except urllib.error.HTTPError as error:
            if error.code not in [422,500,502,503,504]:raise
            found=find()
            if found:return found
            if error.code==422:raise
            return self.api('repos/'+REPOSITORY+'/releases',{'tag_name':tag,'target_commitish':self.default_branch,'draft':True,'name':tag,
                'body':'Private immutable release objects.'})

    def asset(self,file,sha):
        self.private()
        size=file.stat().st_size
        if not 0<size<2*CHUNK_BYTES:raise ValueError('Private object exceeds release asset bounds')
        release=self.release(sha);name='sha256-'+sha+'.bin'
        def existing():
            page=1
            while True:
                values=self.api(f'repos/{REPOSITORY}/releases/{release["id"]}/assets?per_page=100&page={page}')
                value=next((v for v in values if v['name']==name),None)
                if value:return value
                if len(values)<100:return None
                page+=1
        value=existing()
        if value is None:
            try:
                with file.open('rb')as data,self.request(f'https://uploads.github.com/repos/{REPOSITORY}/releases/{release["id"]}/assets?name='+name,
                    body=data,headers={'Content-Type':'application/octet-stream','Content-Length':str(size)})as response:
                    value=json.load(response)
            except urllib.error.HTTPError as error:
                if error.code!=422:raise
                value=existing()
        if not value or value['size']!=size or value.get('digest')!='sha256:'+sha:raise ValueError('Private object provider digest differs')
        return {'assetId':value['id'],'sha256':sha,'bytes':size}

    def upload(self,file):
        file=Path(file);self.private();parts=[];whole=hashlib.sha256()
        with tempfile.TemporaryDirectory(prefix='private-object-',dir=file.parent)as directory,file.open('rb')as source:
            while True:
                path=Path(directory)/'part.bin';size=0;part_hash=hashlib.sha256()
                with path.open('wb')as output:
                    while size<CHUNK_BYTES:
                        block=source.read(min(1024**2,CHUNK_BYTES-size))
                        if not block:break
                        output.write(block);whole.update(block);part_hash.update(block);size+=len(block)
                if not size:break
                parts.append(self.asset(path,part_hash.hexdigest()));path.unlink()
        if not parts:raise ValueError('Empty private release object')
        return {'schemaVersion':1,'repository':REPOSITORY,'sizeBytes':file.stat().st_size,'sha256':whole.hexdigest(),'parts':parts}

    def download(self,location,destination):
        if location.get('repository')!=REPOSITORY or location.get('schemaVersion')!=1:raise ValueError('Private object repository differs')
        destination=Path(destination);temporary=destination.with_suffix(destination.suffix+'.private-next');whole=hashlib.sha256();total=0
        try:
            with temporary.open('wb')as output:
                for part in location['parts']:
                    metadata=self.api(f'repos/{REPOSITORY}/releases/assets/{part["assetId"]}')
                    if metadata['size']!=part['bytes']or metadata.get('digest')!='sha256:'+part['sha256']:raise ValueError('Private object metadata differs')
                    try:response=self.request(f'https://api.github.com/repos/{REPOSITORY}/releases/assets/{part["assetId"]}',accept='application/octet-stream')
                    except urllib.error.HTTPError as error:
                        if error.code!=302:raise
                        url=urllib.parse.urlparse(error.headers['Location'])
                        if url.scheme!='https'or url.hostname not in ['release-assets.githubusercontent.com','objects.githubusercontent.com']:raise ValueError('Unsafe private object redirect')
                        response=self.opener.open(urllib.request.Request(url.geturl()),timeout=120)
                    checksum=hashlib.sha256();size=0
                    with response:
                        while block:=response.read(1024**2):
                            size+=len(block);total+=len(block)
                            if size>part['bytes']or total>location['sizeBytes']:raise ValueError('Private object exceeds expected size')
                            output.write(block);whole.update(block);checksum.update(block)
                    if size!=part['bytes']or checksum.hexdigest()!=part['sha256']:raise ValueError('Private object part checksum differs')
            if total!=location['sizeBytes']or whole.hexdigest()!=location['sha256']:raise ValueError('Private object full checksum differs')
            temporary.replace(destination)
        finally:
            if temporary.exists():temporary.unlink()
