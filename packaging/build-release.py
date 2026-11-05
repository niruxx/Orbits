#!/usr/bin/env python3
"""Build a deterministic release archive and a checksum-pinned AUR recipe; never publish."""
import ast
import hashlib
import os
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def build(root=ROOT, output=None):
    root = Path(root)
    output = Path(output or root/'dist')
    output.mkdir(parents=True, exist_ok=True)
    tree = ast.parse((root/'orbits/__init__.py').read_text())
    version = next(node.value.value for node in tree.body if isinstance(node,ast.Assign)
                   and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in node.targets))
    archive = output/f'orbits-{version}.tar.xz'
    excluded = {'.git','__pycache__','dist','build','_build','.venv','.pytest_cache'}
    with tarfile.open(archive, 'w:xz', format=tarfile.PAX_FORMAT) as bundle:
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root)
            allowed = {'orbits','tests','data','po','packaging','.github','README.md','LICENSE','meson.build','orbits-run','install.sh','roadmap.md','.gitignore','.gitattributes'}
            if relative.parts[0] not in allowed or any(part in excluded for part in relative.parts) or path.is_dir() or path.suffix=='.pyc':
                continue
            if output.resolve() in path.resolve().parents:
                continue
            info = bundle.gettarinfo(str(path), arcname=f'orbits-{version}/{relative}')
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = int(os.environ.get('SOURCE_DATE_EPOCH', '0'))
            if path.is_file() and not path.is_symlink():
                with path.open('rb') as stream:
                    bundle.addfile(info, stream)
            else:
                bundle.addfile(info)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/(archive.name+'.sha256')).write_text(f'{digest}  {archive.name}\n')
    aur = output/'aur'
    aur.mkdir(exist_ok=True)
    recipe = (root/'packaging/aur/PKGBUILD.in').read_text().replace('@VERSION@',version).replace('@SHA256@',digest)
    (aur/'PKGBUILD').write_text(recipe)
    shutil.copyfile(root/'packaging/arch/orbits.install',aur/'orbits.install')
    import shlex
    fields = []
    for line in recipe.splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        if key not in {'pkgver','pkgrel','pkgdesc','arch','url','license','depends','makedepends','optdepends','source','sha256sums','install'}:
            continue
        value = value.strip().removeprefix('(').removesuffix(')')
        value = value.replace('$url', 'https://github.com/niruxx/Orbits').replace('$pkgver', version)
        for item in shlex.split(value):
            fields.append(f'\t{key} = {item}')
    (aur/'.SRCINFO').write_text('pkgbase = orbits\n'+'\n'.join(fields)+'\n\npkgname = orbits\n')
    return archive


if __name__ == '__main__':
    print(build())
