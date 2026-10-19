"""Test product-owned resource lookup before spending time on a plugin build."""
from pathlib import Path,PurePosixPath
import os
import re
import sys

def validate(source,work,checks,run):
    for index,check in enumerate(checks):
        header=PurePosixPath(check['header']);symbol=check['function'];resource=check['resource']
        if header.is_absolute()or '..'in header.parts or not re.fullmatch(r'[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*',symbol)or Path(resource).name!=resource:
            raise ValueError('Unsafe resource locator test contract')
        if not(source/header).is_file():raise ValueError('Resource locator implementation is missing')
        folder=work/('resource-locator-'+str(index));folder.mkdir()
        binary_dir=folder/'Installed Bank Ω.vst3/Contents'/('MacOS'if sys.platform=='darwin'else'x86_64-win')
        binary_dir.mkdir(parents=True);resources=binary_dir.parent/'Resources';resources.mkdir()
        expected=resources/resource
        if check.get('kind')=='mapped-pack':
            content=PurePosixPath(check['contentPath'])
            if content.is_absolute()or '..'in content.parts:raise ValueError('Unsafe mapped-bank test input')
            os.link(source/content,expected)
        else:expected.write_bytes(b'this-bundle-only')
        unrelated=folder/'host-working-directory';unrelated.mkdir();(unrelated/resource).write_bytes(b'unrelated-bank')
        code=folder/'check.cpp'
        if check.get('kind')=='mapped-pack':
            body='const auto actual='+symbol+'::defaultPath();\nif(!std::filesystem::equivalent(actual,expected))return 3;\n'+symbol+' pack;\nif(!pack.open(actual,{}))return 4;\n'+symbol+' moved(std::move(pack));\nif(!moved.loaded())return 5;\nmoved.close();\nif(!pack.open(actual,{}))return 6;\npack.close();return 0;\n}\n'
        else:body='for(const auto& candidate:'+symbol+'()) {\nconst auto file=std::filesystem::u8path(candidate);\nif(std::filesystem::exists(file))return std::filesystem::equivalent(file,expected)?0:3;\n}\nreturn 4;\n}\n'
        code.write_text('#include <filesystem>\n#include <utility>\n#include "'+str(header)+'"\n'
            '#ifdef _WIN32\nint wmain(int argc,wchar_t** argv) {\nconst std::filesystem::path expected{argv[1]};\n'
            '#else\nint main(int argc,char** argv) {\nconst auto expected=std::filesystem::u8path(argv[1]);\n#endif\n'
            'if(argc!=2)return 2;\n'+body,encoding='utf-8')
        binary=binary_dir/('probe'if sys.platform=='darwin'else'probe.exe')
        includes=[source,source/'src',source/check.get('includeDirectory','.')]
        if sys.platform=='darwin':
            run('clang++','-std=c++23','-Wall','-Wextra','-Werror','-arch','arm64','-arch','x86_64',*['-I'+str(p)for p in includes],str(code),'-o',str(binary))
            for arch in ['arm64','x86_64']:run('arch','-'+arch,str(binary),str(expected),cwd=unrelated)
        elif sys.platform=='win32':
            run('cl.exe','/nologo','/std:c++latest','/EHsc','/utf-8',*['/I'+str(p)for p in includes],str(code),'/Fe:'+str(binary),'/Fo:'+str(folder/'check.obj'))
            run(str(binary),str(expected),cwd=unrelated)
        else:raise ValueError('Resource locator check requires a release platform')
